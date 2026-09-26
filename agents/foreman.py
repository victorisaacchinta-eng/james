"""FOREMAN: the coordinator. An investigation loop, not a single pass (iteration 2, P0-E).

Round 0  PAGE, PULSE and LEDGER recall run in parallel (a LangGraph fan-out).
Round 1+ FOREMAN looks at what came back and picks ONE next action from the actions that are valid right now:
           page_search   the manual match was weak: search again with a narrower query (bounded, 3 searches)
           page_check    a sensor, an answer or a past job points at a cause: check the manual's table says so
           ask           the evidence can't separate the causes: ask the technician one yes/no question
           finish        one cause is backed by the manual AND a sensor reading or the technician's answer
           abstain       no manual match, or not enough evidence: say so and escalate, never guess
         The local model picks among the valid options when it is running (its pick is checked against the
         list); otherwise a fixed rule order picks. Every round is written to the trace with who decided.
Then     PAGE's procedure for that cause becomes typed, hazard-tagged steps. A step is kept only if the cited
         page really contains it. Each step cites the procedure page and the evidence for the cause.

Rules FOREMAN keeps (iteration 2, P1-D):
  * A past job (memory) never decides a cause on its own. It must apply (same class, known cause, not stale,
    not revoked: see agents/recall.py) and it only adds weight.
  * A cause the manual never names is not used, whoever suggests it.
  * When strong sources disagree, FOREMAN asks a question; if none is left it escalates.
Nothing here can approve a step. WARDEN does that inside the session."""
from __future__ import annotations

import operator
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Annotated, Optional, TypedDict

import config
from agents import llm, pulse, recall
from agents.page import CAUSE_WORDS, SYNONYMS, ManualIndex
from james_core.evidence import build_bundle
from james_core.ledger import Ledger
from james_core.safety_guard import Policy
from james_core.schemas import AgentId, Evidence, EvidenceBundle, EvidenceKind, Intent, ProposedStep

STRONG = (EvidenceKind.SENSOR, EvidenceKind.OBSERVATION)     # can back a cause; MEMORY only adds weight


@dataclass
class Question:
    id: str
    text: str
    if_yes: str
    if_no: Optional[str]
    source: str
    why: str


ORIGINS = ("fan-out", "technician", "model", "rules", "rules-fallback")


@dataclass
class TraceRow:
    """One round. origin says WHICH system made the accepted decision (iteration 3, item B):
    model           the local model's choice was valid and was used
    rules           the model was not asked (not running, or only one valid action)
    rules-fallback  the model was asked but its reply was unusable; `fallback` says why; the rules chose
    fan-out / technician: round 0 gathering, and the technician's answers."""
    round: int
    agent: str
    decided_by: str
    action: str
    result: str
    ms: float = 0.0
    origin: str = ""
    model_tried: bool = False
    fallback: str = ""
    evidence: tuple = ()               # ids of evidence this round added

    def line(self) -> str:
        return f"R{self.round} {self.agent:<8} [{self.decided_by}] {self.action} -> {self.result} ({self.ms:.0f} ms)"


@dataclass
class Plan:
    bundle: EvidenceBundle
    sensor_facts: dict
    cause: str
    why: str
    cause_backend: str
    steps: list[ProposedStep]
    sets_facts: dict[str, tuple[str, ...]] = field(default_factory=dict)   # step id -> facts
    notes: list[str] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)
    orchestrator: str = ""
    status: str = "ready"                      # ready | question | no_match
    question: Optional[Question] = None
    trace: list[TraceRow] = field(default_factory=list)
    excluded: list[str] = field(default_factory=list)
    pack_snapshot: str = ""
    stop_reason: str = ""                     # why the loop ended (concluded, question, abstained, a budget, ...)
    decisions: dict = field(default_factory=dict)   # counts: model_attempts, model_accepted, rules, rules_fallback


class GState(TypedDict, total=False):
    intent: Intent
    evidence: Annotated[list, operator.add]
    facts: Annotated[list, operator.add]
    notes: Annotated[list, operator.add]
    timings: Annotated[list, operator.add]


def _label(cause: str) -> str:
    return cause.replace("_", " ")


class Foreman:
    MAX_ROUNDS = 6          # decision rounds after the fan-out, whatever the actions are
    PAGE_MAX = 3            # manual searches per investigation, first one included
    MAX_TOOL_CALLS = 6      # PAGE searches + PAGE checks after the fan-out (iteration 3, item D)
    TIME_BUDGET_S = 60.0    # the whole investigation, model calls included; checked before every round

    def __init__(self, manual: ManualIndex, ledger: Ledger, policy: Policy, pack=None, use_sensors: bool = True):
        self.manual, self.ledger, self.policy = manual, ledger, policy
        self.pack = pack or config.PACK
        self.use_sensors = use_sensors          # False: the demo "sensor log unavailable" case (key O in app.py)
        import threading
        self.cancel = threading.Event()         # set by the app (lock, new job, escalate): the loop stops next round
        self.orchestrator = "LangGraph (parallel) + FOREMAN loop"
        try:
            self._graph = self._build_graph()
        except Exception as e:  # noqa: BLE001
            self._graph, self.orchestrator = None, f"thread pool ({type(e).__name__}) + FOREMAN loop"

    # ---- round 0: evidence agents as graph nodes ----
    def _page(self, s: GState):
        t = time.perf_counter()
        ev, notes = self.manual.troubleshoot(s["intent"].symptoms, s["intent"].transcript, s["intent"].asset_id,
                                             retry=False)
        return {"evidence": ev, "notes": [f"PAGE {n}" for n in notes],
                "timings": [("PAGE", time.perf_counter() - t)]}

    def _pulse(self, s: GState):
        t = time.perf_counter()
        if not self.use_sensors:
            return {"notes": ["PULSE sensor log unavailable (switched off for this run)"],
                    "timings": [("PULSE", time.perf_counter() - t)]}
        ev, facts = pulse.analyze(s["intent"].asset_id)
        return {"evidence": ev, "facts": [facts], "timings": [("PULSE", time.perf_counter() - t)]}

    def _recall(self, s: GState):
        t = time.perf_counter()
        ev, notes = recall.recall_checked(self.ledger, s["intent"].asset_id, s["intent"].symptoms, self.pack)
        return {"evidence": ev, "notes": [f"LEDGER {n}" for n in notes],
                "timings": [("LEDGER", time.perf_counter() - t)]}

    def _build_graph(self):
        from langgraph.graph import END, START, StateGraph
        g = StateGraph(GState)
        g.add_node("page", self._page)
        g.add_node("pulse", self._pulse)
        g.add_node("recall", self._recall)
        g.add_node("join", lambda s: {})
        for n in ("page", "pulse", "recall"):
            g.add_edge(START, n)
        g.add_edge(["page", "pulse", "recall"], "join")
        g.add_edge("join", END)
        return g.compile()

    def _gather(self, intent: Intent) -> GState:
        if self._graph:
            return self._graph.invoke({"intent": intent, "evidence": [], "facts": [], "notes": [], "timings": []})
        s: GState = {"intent": intent, "evidence": [], "facts": [], "notes": [], "timings": []}
        with ThreadPoolExecutor(3) as ex:
            for part in ex.map(lambda f: f(s), (self._page, self._pulse, self._recall)):
                for k, v in part.items():
                    s[k] = s.get(k, []) + v
        return s

    # ---- what FOREMAN can see after each round ----
    def _observe(self, ev: list[Evidence], checked_ok: set[str]) -> dict:
        manuals = [e for e in ev if e.kind == EvidenceKind.MANUAL and e.stance is None]
        manual = max(manuals, key=lambda e: e.confidence) if manuals else None
        ok = bool(manual) and manual.confidence >= 0.6
        cands = []
        if ok:
            for label, cid in CAUSE_WORDS.items():
                if re.search(rf"\b{re.escape(label)}\b", manual.claim):
                    cands.append(cid)
        strong: dict[str, list[str]] = {}
        weak: dict[str, list[str]] = {}
        for e in ev:
            if e.topic == "likely_cause" and e.stance:
                if e.kind in STRONG:
                    strong.setdefault(e.stance, []).append(e.agent.value)
                elif e.kind == EvidenceKind.MEMORY:
                    weak.setdefault(e.stance, []).append(e.agent.value)
        supported = {c for c in strong if c in checked_ok}
        return {"manual": manual, "manual_ok": ok, "candidates": cands, "strong": strong, "weak": weak,
                "supported": supported, "sensor": any(e.kind == EvidenceKind.SENSOR for e in ev)}

    def _observations(self) -> list:
        return list(self.pack.faults(self._cls).observations)

    def _questions(self, relevant: set[str], answers: dict[str, bool]) -> list:
        out = []
        for o in self._observations():
            if o.id in answers:
                continue
            if o.if_yes in relevant or (o.if_no and o.if_no in relevant):
                out.append(o)
        return out

    def _options(self, o: dict, answers: dict[str, bool], page_tries: int, checked: set[str]) -> list[tuple]:
        """The actions that are valid now, in the order the rules would pick them. (id, kind, arg, text)"""
        # Abstaining is offered only when no cheap evidence step is left. Live trace 2026-09-26 04:05 (qwen3-vl:4b):
        # offered 'abstain' next to 'check the manual', the model quit twice before looking (after the technician
        # had answered, and before any narrower search). Stopping without evidence is not a decision to leave
        # to the model; the budgets still end every loop.
        opts: list[tuple] = []
        if not o["manual_ok"]:
            if page_tries < self.PAGE_MAX:
                return [(f"page_search_{page_tries + 1}", "page_search", page_tries + 1,
                         "search the manual again with a narrower query")]
            return [("abstain", "abstain", "no manual section matches this fault",
                     "stop: no manual section matches; escalate to the senior technician")]
        pointed = list(dict.fromkeys(list(o["strong"]) + list(o["weak"])))
        for c in pointed:
            if c not in checked:
                opts.append((f"page_check_{c}", "page_check", c,
                             f"check the manual's troubleshooting table for {_label(c)}"))
        if opts:
            return opts
        sup, strong, cands = o["supported"], o["strong"], set(o["candidates"])
        # disagreement: two strong sources point to different causes, or the strong source points away from
        # what the manual lists for the symptom the technician described (e.g. 'noise' vs a bearing pattern)
        disagree = len(strong) > 1 or bool(cands and sup and not (sup & cands))
        relevant = cands | set(strong) | set(o["weak"])
        qs = self._questions(relevant, answers)
        qs.sort(key=lambda q: (q.if_yes not in cands, q.if_yes not in strong))   # first: test what the manual lists
        if len(sup) == 1 and not disagree:
            c = next(iter(sup))
            opts.append((f"finish_{c}", "finish", c, f"conclude {_label(c)}: manual + " + ", ".join(strong[c])))
        if (not sup or disagree) and qs:
            for q in qs:
                opts.append((f"ask_{q.id}", "ask", q, f"ask the technician: {q.question}"))
        if not opts and len(cands) == 1 and not strong:
            c = next(iter(cands))
            opts.append((f"finish_{c}", "finish", c,
                         f"conclude {_label(c)}: the only cause the manual gives for this symptom (no sensor or answer)"))
        reason = ("sources disagree and no question is left" if disagree
                  else "stopped although a supported cause was available; a senior should review"
                  if any(x[1] == "finish" for x in opts)
                  else "not enough evidence to pick a cause (memory alone never decides)")
        opts.append(("abstain", "abstain", reason, f"stop: {reason}; escalate"))
        return opts

    def _decide(self, o: dict, ev: list[Evidence], opts: list[tuple]) -> tuple[tuple, str, str]:
        """(option, decided_by, why). The local model picks when it runs; its answer must be one of the options.
        Sets self._last = (origin, model_tried, fallback reason) for the trace."""
        self._last = ("rules", False, "")
        if len(opts) > 1 and llm.available(config.CHAT_MODEL):
            lines = "\n".join(f"- [{e.agent.value} {e.ref} conf {e.confidence:.2f}] {e.claim}" for e in ev)
            menu = "\n".join(f"{x[0]}: {x[3]}" for x in opts)
            try:
                out = llm.chat_json(
                    "You are FOREMAN, coordinating a pump fault investigation. Pick the next action from the "
                    "options only. Conclude a cause only when the manual and a sensor reading or the technician's "
                    "answer support it; a past job alone is not enough. Reply JSON {\"option\": id, \"why\": short}.",
                    f"Evidence so far:\n{lines or '- none'}\n\nOptions:\n{menu}",
                    {"type": "object", "properties": {"option": {"enum": [x[0] for x in opts]},
                                                      "why": {"type": "string"}}, "required": ["option", "why"]})
                pick = next((x for x in opts if x[0] == out.get("option")), None)
                if pick:
                    self._last = ("model", True, "")
                    return pick, "local model", str(out.get("why", ""))[:140]
                reason = f"option {str(out.get('option'))[:40]!r} is not one of the valid actions"
            except Exception as e:  # noqa: BLE001  model down or bad JSON: the rules decide, and the trace says so
                reason = f"{type(e).__name__}: {str(e)[:60]}"
            self._last = ("rules-fallback", True, reason)
            return opts[0], "rules (model reply unusable)", ""
        return opts[0], "rules", ""

    def _scoped_query(self, intent: Intent, n: int) -> str:
        words = [SYNONYMS.get(t, t) for t in intent.symptoms]
        if n == 2:                                    # the symptom words only: no filler that matches every row
            return " ".join(dict.fromkeys(words)) or intent.transcript
        return self.manual._rewrite(" ".join(words) + " " + intent.transcript)   # model rewrite, else synonyms

    # ---- the investigation ----
    def plan(self, intent: Intent, answers: Optional[dict[str, bool]] = None) -> Plan:
        answers = dict(answers or {})
        self._cls = self.pack.class_of(intent.asset_id) or self.pack.primary_class
        t0 = time.perf_counter()
        trace: list[TraceRow] = []
        s = self._gather(intent)
        ev: list[Evidence] = [e for e in s["evidence"] if e.asset_id == intent.asset_id]
        facts = {k: v for d in s.get("facts", []) for k, v in d.items()}
        notes = list(s.get("notes", []))
        excluded = [n.replace("LEDGER ", "") for n in notes if n.startswith("LEDGER job ")]
        tm = dict(s.get("timings", []))
        for agent, kinds in (("PAGE", (EvidenceKind.MANUAL,)), ("PULSE", (EvidenceKind.SENSOR,)),
                             ("LEDGER", (EvidenceKind.MEMORY,))):
            got = [e for e in ev if e.kind in kinds]
            res = "; ".join(f"{e.claim[:70]} ({e.ref}, {e.confidence:.2f})" for e in got) or "nothing"
            extra = [n.split(" ", 1)[1] for n in notes if n.startswith(agent + " ")]
            trace.append(TraceRow(0, agent, "fan-out", "gather", res + (f" | {'; '.join(extra)}" if extra else ""),
                                  1000 * tm.get(agent, 0.0)))
        for qid, yes in answers.items():                              # the technician's answers so far
            o = next((x for x in self._observations() if x.id == qid), None)
            if not o:
                continue
            stance = o.if_yes if yes else o.if_no
            ev.append(Evidence(agent=AgentId.TECHNICIAN, kind=EvidenceKind.OBSERVATION, asset_id=intent.asset_id,
                               claim=f"Technician: '{o.question}' {'yes' if yes else 'no'}", ref=f"tech:{qid}",
                               confidence=0.8, topic="likely_cause" if stance else None, stance=stance))
            trace.append(TraceRow(0, "TECH", "technician", f"answered {qid}", "yes" if yes else "no"))

        for r in trace:
            r.origin = "technician" if r.agent == "TECH" else "fan-out"
        page_tries, checked, checked_ok = 1, set(), set()
        status, cause, why, by, question = "no_match", "unknown", "", "rules", None
        self.cancel.clear()
        tool_calls, done_actions, stop = 0, set(), ""
        stats = {"model_attempts": 0, "model_accepted": 0, "rules": 0, "rules_fallback": 0}
        for rnd in range(1, self.MAX_ROUNDS + 1):
            t = time.perf_counter()
            if self.cancel.is_set():
                stop, why = "cancelled", "the job was cancelled"
                break
            if time.perf_counter() - t0 > self.TIME_BUDGET_S:
                stop, why = "time_budget", f"stopped after {self.TIME_BUDGET_S:.0f} s"
                break
            before_ids = {e.id for e in ev}
            o = self._observe(ev, checked_ok)
            opts = self._options(o, answers, page_tries, checked)
            if tool_calls >= self.MAX_TOOL_CALLS:
                cut = [x for x in opts if x[1] not in ("page_search", "page_check")]
                if len(cut) < len(opts):               # the budget removed an action: say so in the reason
                    cut = [(x[0], x[1], f"tool budget used up ({x[2]})", x[3]) if x[1] == "abstain" else x
                           for x in cut]
                opts = cut or [("abstain", "abstain", "tool budget used up", "stop")]
            (oid, kind, arg, text), by, mwhy = self._decide(o, ev, opts)
            origin, tried, fb = self._last
            stats["model_attempts"] += int(tried)
            stats["model_accepted"] += int(origin == "model")
            stats["rules"] += int(origin == "rules")
            stats["rules_fallback"] += int(origin == "rules-fallback")
            key = (kind, str(getattr(arg, "id", arg)))
            if key in done_actions:                    # the same action again, with nothing new to act on
                stop, why = "repeat_without_new_evidence", f"{kind} {key[1]} was already done"
                trace.append(TraceRow(rnd, "FOREMAN", by, "stop", why, 0.0, origin, tried, fb))
                break
            done_actions.add(key)
            tool_calls += int(kind in ("page_search", "page_check"))
            if kind == "page_search":
                q = self._scoped_query(intent, arg)
                new, n2 = self.manual.troubleshoot(intent.symptoms, intent.transcript, intent.asset_id, query=q,
                                                   retry=False)
                page_tries += 1
                best = max(new, key=lambda e: e.confidence) if new else None
                if best and (not o["manual"] or best.confidence > o["manual"].confidence):
                    ev = [e for e in ev if e is not o["manual"]] + [best]
                res = (f"{best.claim[:60]} ({best.confidence:.2f})" if best else "no match") + \
                      (f" | {'; '.join(n2[1:])}" if len(n2) > 1 else "")
                trace.append(TraceRow(rnd, "PAGE", by, f"search {arg}: '{q[:50]}'", res,
                                      1000 * (time.perf_counter() - t), origin, tried, fb,
                                      tuple(e.id for e in ev if e.id not in before_ids)))
            elif kind == "page_check":
                e = self.manual.check_cause(arg, intent.asset_id)
                checked.add(arg)
                if e:
                    checked_ok.add(arg)
                    ev.append(e)
                    res = f"{e.claim} ({e.ref})"
                else:
                    excluded.append(f"{_label(arg)}: no row in the manual's troubleshooting table names it")
                    res = "the manual does not name this cause: not used"
                trace.append(TraceRow(rnd, "PAGE", by, f"check {_label(arg)}", res, 1000 * (time.perf_counter() - t), origin, tried, fb,
                                      tuple(e.id for e in ev if e.id not in before_ids)))
            elif kind == "ask":
                question = Question(arg.id, arg.question, arg.if_yes, arg.if_no, arg.source,
                                    "no sensor log" if not o["sensor"] else "the sources point to different causes")
                trace.append(TraceRow(rnd, "FOREMAN", by, "ask technician", arg.question + (f" | {mwhy}" if mwhy else ""),
                                      1000 * (time.perf_counter() - t), origin, tried, fb,
                                      tuple(e.id for e in ev if e.id not in before_ids)))
                status, stop = "question", "question_for_technician"
                break
            elif kind == "finish":
                status, cause, stop = "ready", arg, "concluded"
                why = mwhy or text.split(": ", 1)[-1]
                trace.append(TraceRow(rnd, "FOREMAN", by, f"conclude {_label(arg)}", why,
                                      1000 * (time.perf_counter() - t), origin, tried, fb,
                                      tuple(e.id for e in ev if e.id not in before_ids)))
                break
            else:
                why, stop = arg, f"abstained: {arg}"
                trace.append(TraceRow(rnd, "FOREMAN", by, "abstain", arg, 1000 * (time.perf_counter() - t), origin, tried, fb,
                                      tuple(e.id for e in ev if e.id not in before_ids)))
                break
        else:
            stop, why = "round_budget", f"no conclusion after {self.MAX_ROUNDS} rounds"

        steps_out, sets = [], {}
        if status == "ready":
            proc_ev, steps = self.manual.procedure(cause, intent.asset_id)
            kept = []
            for st in steps:
                if self.manual.step_on_page(st.text, st.page):
                    kept.append(st)
                else:
                    excluded.append(f"step '{st.text[:40]}' is not on the cited page {st.page}: dropped")
            if proc_ev and kept:
                ev.append(proc_ev)
                support = tuple(e.id for e in ev if e.stance == cause or e.kind == EvidenceKind.SENSOR)
                cite = (proc_ev.id,) + support
                for st in kept:
                    p = ProposedStep(action_id="SHOW_STEP", text=st.text, evidence_ids=cite,
                                     touches=self.policy.tag(st.text),
                                     source_step=f"{st.section} step {st.number}, p. {st.page}")
                    steps_out.append(p)
                    sets[p.id] = self.policy.facts_set_by(st.text)
            else:
                status, why = "no_match", f"no cited procedure for {_label(cause)}"
        bundle = build_bundle(intent.asset_id, ev)
        timings = {k: round(v, 3) for k, v in s.get("timings", [])}
        timings["FOREMAN total"] = round(time.perf_counter() - t0, 3)
        backend = {"ready": f"evidence-supported, decided by {by}", "question": f"needs an answer ({by})",
                   "no_match": f"no conclusion ({by})"}[status]
        return Plan(bundle=bundle, sensor_facts=facts, cause=cause if status == "ready" else "unknown", why=why,
                    cause_backend=backend, steps=steps_out, sets_facts=sets, notes=notes, timings=timings,
                    orchestrator=self.orchestrator, status=status, question=question, trace=trace,
                    excluded=excluded, pack_snapshot=getattr(self.pack, "snapshot", ""), stop_reason=stop,
                    decisions=stats)
