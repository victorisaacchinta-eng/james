"""JAMES: touch-free, multi-agent maintenance copilot (demo build).

    python app.py              # live: camera + mic
    python app.py --no-voice   # live camera, use key 1 for the scripted request
    python app.py --sim        # no camera, no mic: scripted run, prints readouts, saves HUD frames

Keys: U unlock/lock | V start/stop voice | 1 scripted request (labelled) | T confirm target
      C confirm by key (fallback, logged) | S skip step | E escalate | A senior approves last job
      L/Z/P confirm lockout / zero energy / PPE | R new job | Q quit
"""
from __future__ import annotations

import argparse
import csv
import queue
import sys
import threading
import time
from datetime import datetime, timedelta

import yaml

import config
from agents import llm
from agents.echo import Recorder, Transcriber, parse_intent
from agents.foreman import Foreman, Plan
from agents.page import ManualIndex
import jsil as J
from hud import AMBER, GREEN, RED, Hud
from james_core import egress
from james_core.actions import ActionRegistry
from james_core.ledger import Ledger
from james_core.readout import blocked_readout, step_readout
from james_core.safety_guard import load_policy
from james_core.schemas import Confirmation, Proposal, Target
from james_core.session import (Complete, Escalate, EvidenceReady, FactsUpdated, GatherEvidence,
                                IntentParsed, Lock, NextStep, ObservationRecorded, Pinch, ProposalMade, Refused,
                                Session, Skip, State, TargetConfirmedByTechnician, TargetSeen, Unlock)

SCRIPTED = "Pump 3 is vibrating more than usual"
KEYS = ("U lock | V voice | 1 scripted | T target | pinch = confirm | C key-confirm | S skip | E escalate | A approve | "
        "L Z P facts | Y/N answer FOREMAN | O sensor log on/off | R new job | F telemetry | H hints | Q quit")
FACT_GROUPS = [
    ("loto_confirmed", ("loto_confirmed",), "Lockout/tagout applied?"),
    ("zero_energy_verified", ("zero_energy_verified",), "Zero energy verified with a try-start?"),
    ("ppe", ("ppe_gloves", "ppe_eye"), "Gloves and eye protection on?"),
]


class App:
    def __init__(self, sim: bool = False):
        egress.set_mode("maintenance")                  # iteration 2 P0-D: local only, consumer web tools refused
        for d in (config.DATA, config.RECORDS):
            d.mkdir(exist_ok=True)
        self.sim = sim
        self.ledger = Ledger(":memory:" if sim else str(config.DB))
        if self.ledger.job_count() == 0:
            for j in yaml.safe_load(config.SEEDED_JOBS.read_text())["jobs"]:
                self.ledger.add_job(j["job_id"], j["asset_id"], j["symptoms"], j["fix"], j["approved_by"],
                                    cause=j["cause"])
        self.policy = load_policy()
        self.manual = ManualIndex()
        self.foreman = Foreman(self.manual, self.ledger, self.policy)
        self.actions = ActionRegistry(self.policy)
        for a in self.policy.allowlisted_actions:
            self.actions.register(a, lambda step: None)   # HUD draws the step; nothing touches a machine
        self.hud = Hud()
        self.show_telemetry = False
        self.q: queue.Queue = queue.Queue()
        self.recorder, self.transcriber = Recorder(), Transcriber()
        self.echo_backend = "-"
        self.new_session()

    # ---------- session helpers ----------
    def new_session(self):
        if getattr(self, "foreman", None) is not None:
            self.foreman.cancel.set()                     # an investigation still running belongs to the old job
        self.session = Session(self.ledger, self.policy, actions=self.actions)
        self.session.IDLE_LOCK = timedelta(minutes=5)     # demo talk time; tune for the floor
        self.plan: Plan | None = None
        self.step_i = 0
        self.transcript = ""
        self.t_request: float | None = None
        self.latency: float | None = None
        self.input_label = ""
        self.busy = ""
        self.blocks = 0
        self.last_file = ""
        self.confirmed_at = ""
        self.question = None                            # FOREMAN's open question (iteration 2, P0-E)
        self.answers: dict[str, bool] = {}
        self.intent = None

    def safe(self, event) -> bool:
        try:
            self.session.handle(event)
            return True
        except Refused as r:
            self.hud.flash(str(r), RED)
            return False

    @property
    def state(self) -> State:
        return self.session.state

    # ---------- inputs ----------
    def toggle_lock(self):
        if self.state == State.LOCKED:
            self.safe(Unlock(presenter_id="presenter"))
        else:
            self.safe(Lock(reason="presenter"))
            self.new_session()

    def target(self, asset_id: str, source: str, conf: float):
        if self.state == State.TARGET_PENDING:
            pend = self.session.pending_target
            if pend and pend.asset_id == asset_id and pend.confidence >= conf:
                return
            self.safe(TargetSeen(target=Target(asset_id=asset_id, source=source, confidence=conf)))

    def voice_toggle(self):
        if self.recorder.active:
            audio = self.recorder.stop()
            self.busy = "Transcribing (faster-whisper, local)..."
            threading.Thread(target=self._transcribe, args=(audio,), daemon=True).start()
        elif self.state == State.TARGET_CONFIRMED or self.question:
            try:
                self.recorder.start()
                self.busy = "Listening... press V to stop"
            except Exception as e:  # noqa: BLE001
                self.hud.flash(f"Microphone unavailable: {e}. Use key 1.", RED)
        else:
            self.hud.flash("Point at a machine tag first", AMBER)

    def _transcribe(self, audio):
        t_end = time.perf_counter()
        try:
            text, conf = self.transcriber.transcribe(audio)
        except Exception as e:  # noqa: BLE001
            self.q.put(("error", f"Speech to text failed: {e}"))
            return
        self.q.put(("heard", text, conf, t_end, "ECHO"))

    def scripted(self):
        if self.state != State.TARGET_CONFIRMED:
            self.hud.flash("Point at a machine tag first", AMBER)
            return
        self.q.put(("heard", SCRIPTED, 0.95, time.perf_counter(), "SCRIPTED"))

    def _on_heard(self, text, conf, t_end, label):
        self.busy = ""
        if not text:
            self.hud.flash("Didn't catch that. Press V and try again.", AMBER)
            return
        if self.question:                                  # a spoken yes/no to FOREMAN's question
            low = " ".join(text.lower().replace(",", " ").replace(".", " ").split())
            yes = low.startswith(("yes", "yeah", "yep", "it is", "correct"))
            no = low.startswith(("no", "nope", "it isn't", "it is not", "not "))
            if conf >= 0.5 and yes != no:
                self.answer(yes)
            else:
                self.hud.flash("Answer yes or no (or pinch = yes, N = no).", AMBER)
            return
        intent, self.echo_backend = parse_intent(text, conf, self.session.target.asset_id)
        tag = " [SCRIPTED INPUT]" if label == "SCRIPTED" else ""
        self.transcript = f'Heard: "{text}"{tag} | conf {conf:.2f} | symptoms: {", ".join(intent.symptoms) or "none"}'
        if not self.safe(IntentParsed(intent=intent)):
            self.transcript += " | Not clear enough. Press V and say it again, or press 1."
            return
        self.t_request, self.input_label = t_end, label
        self.safe(GatherEvidence())
        self.intent = intent
        self.investigate()

    def investigate(self):
        """Run FOREMAN's loop (again, after an answer). In sim it runs inline so the sim stays deterministic."""
        self.busy = "FOREMAN is investigating: PAGE, PULSE and LEDGER, then the next step it picks..."
        changed = config.PACK.drift()                     # iteration 2 ST-17: the folder changed after start-up
        if changed:
            self.ledger.append(self.session.id, "pack_changed_on_disk", {
                "files": changed, "using_snapshot": config.PACK.snapshot})
            self.hud.flash(f"Pack files changed on disk ({', '.join(changed[:2])}). This job keeps the verified "
                           f"snapshot {config.PACK.snapshot}; restart JAMES to load the new version.", AMBER, 8)
        intent, answers = self.intent, dict(self.answers)
        if self.sim:
            self._on_plan(self.foreman.plan(intent, answers))
        else:
            sid = self.session.id
            threading.Thread(target=lambda: self.q.put(("plan", self.foreman.plan(intent, answers), sid)),
                             daemon=True).start()

    def answer(self, yes: bool):
        """The technician answers FOREMAN's question (Y/N keys, pinch = yes, or voice). Logged, then FOREMAN goes on."""
        q = self.question
        if not q:
            self.hud.flash("No question is waiting", AMBER)
            return
        if not self.safe(ObservationRecorded(question_id=q.id, question=q.text, answer=yes)):
            return
        self.question = None
        self.answers[q.id] = yes
        self.transcript = f"You answered '{'yes' if yes else 'no'}' to: {q.text}"
        self.investigate()

    def key_card_seen(self):
        """Iteration 2 P1-C / ST-15: a card with the owner-key id was shown as if it were a machine tag. Before
        2026-09-26 pump P-3's printed tag used that id. It is never a target; say what to do instead."""
        now = time.monotonic()
        if now - getattr(self, "_key_card_t", -99) < 10:
            return
        self._key_card_t = now
        p3 = {v: k for k, v in config.ASSET_TAGS.items()}.get("P-3")
        self.hud.flash(f"That card is ArUco id {config.OWNER_KEY_ID}: the JAMES owner key, not a machine tag. "
                       + (f"Pump P-3 now uses tag {p3}; print it with: python -m tools.make_marker_sheet" if p3 is not None else ""),
                       AMBER, 8)
        self.ledger.append(self.session.id, "owner_key_shown_as_tag", {"aruco_id": config.OWNER_KEY_ID})

    def toggle_sensors(self):
        self.foreman.use_sensors = not self.foreman.use_sensors
        self.hud.flash("Sensor log ON: PULSE reads the sample log." if self.foreman.use_sensors else
                       "Sensor log OFF (demo): FOREMAN must work without PULSE and may ask you instead.", AMBER, 5)

    def _on_plan(self, plan: Plan):
        self.busy = ""
        self.plan = plan
        self.ledger.append(self.session.id, "investigation", {
            "status": plan.status, "cause": plan.cause, "backend": plan.cause_backend, "why": plan.why,
            "rounds": [r.line() for r in plan.trace], "excluded": plan.excluded,
            "question": plan.question.text if plan.question else None, "pack_snapshot": plan.pack_snapshot,
            "stop_reason": plan.stop_reason, "decisions": plan.decisions,
            "origins": [r.origin for r in plan.trace if r.round > 0]})
        if plan.status == "question":                     # missing observation: ask, then continue with the answer
            self.question = plan.question
            return
        if plan.sensor_facts:
            self.safe(FactsUpdated(facts=plan.sensor_facts))
        if not self.safe(EvidenceReady(bundle=plan.bundle)):
            return
        if not plan.steps:
            self.hud.flash(f"FOREMAN has no conclusion: {plan.why}. Press E to escalate.", AMBER, 8)
            return
        self.step_i = 0
        self.propose()

    def propose(self):
        st = self.plan.steps[self.step_i]
        p = Proposal(bundle_id=self.plan.bundle.id, step=st, step_index=self.step_i + 1,
                     step_total=len(self.plan.steps),
                     surfaced_conflicts=tuple(c.id for c in self.plan.bundle.conflicts))
        self.safe(ProposalMade(proposal=p))
        if self.state == State.SAFETY_BLOCKED:
            self.blocks += 1
        if self.latency is None and self.t_request is not None:
            self.latency = time.perf_counter() - self.t_request
            self._log_run()

    def missing_group(self):
        d = self.session.decision
        if not d:
            return None
        for key, facts, q in FACT_GROUPS:
            if any(f in d.missing or (f in self.session.facts and self.session.facts[f] is not True) for f in facts):
                return key, facts, q
        return None

    def pinch(self, gesture="pinch_hold"):
        s = self.state
        if self.question and s == State.EVIDENCE_GATHERING:
            self.answer(True)                             # pinch = yes to FOREMAN's question
            return
        if s == State.TARGET_CONFIRMED:
            self.set_facts(("ppe_gloves", "ppe_eye"))
        elif s == State.TARGET_PENDING and self.session.pending_target:
            self.safe(TargetConfirmedByTechnician(target_id=self.session.pending_target.id))
        elif s == State.AWAITING_CONFIRMATION:
            if (not self.sim and gesture == "pinch_hold"
                    and (self.session.clock() - self.session.proposal_at).total_seconds() < 1.5):
                self.hud.flash("Read the step first, then pinch", AMBER)   # new step on screen: no instant confirms
                return
            if self.session.clock() - self.session.proposal_at > self.session.STALE_AFTER:
                self.propose()          # stale: re-issue the same step so WARDEN checks it again
                self.hud.flash("Step was stale. Re-checked by WARDEN. Pinch again to confirm.", AMBER)
                return
            self.confirm(gesture)
        elif s == State.SAFETY_BLOCKED:
            g = self.missing_group()
            if g:
                self.set_facts(g[1])
            else:
                self.hud.flash("Blocked by a rule a pinch can't clear (see reason). Skip or escalate.", RED)
        elif s == State.LOCKED:
            self.safe(Pinch(confirmation=Confirmation(proposal_id="none", gesture=gesture, hold_ms=800)))
        else:
            self.hud.flash("Nothing to confirm right now", AMBER)

    def set_facts(self, facts):
        self.safe(FactsUpdated(facts={f: True for f in facts}))
        self.hud.flash(f"Recorded: {', '.join(facts)}. WARDEN checked again.", GREEN)

    def confirm(self, gesture):
        p = self.session.proposal
        if not self.safe(Pinch(confirmation=Confirmation(proposal_id=p.id, gesture=gesture, hold_ms=800))):
            return
        self.confirmed_at = datetime.now().strftime("%H:%M:%S")
        sets = self.plan.sets_facts.get(p.step.id, ())
        if sets:
            self.safe(FactsUpdated(facts={f: True for f in sets}))
        self.hud.flash(f"Step {self.step_i + 1} confirmed and logged" + (" (key fallback)" if gesture != "pinch_hold" else ""), GREEN)
        self.advance()

    def advance(self):
        if self.step_i + 1 < len(self.plan.steps):
            if self.state == State.LOGGED:
                self.safe(NextStep())
            self.step_i += 1
            self.propose()
        elif self.state == State.LOGGED:
            if self.safe(Complete()):
                self.finish_job()
        else:
            self.hud.flash("Last step was skipped. Press E to escalate or R for a new job.", AMBER, 6)

    def skip(self):
        if self.safe(Skip()):
            self.hud.flash(f"Step {self.step_i + 1} skipped (logged, not confirmed)", AMBER)
            self.advance()

    def escalate(self):
        self.foreman.cancel.set()
        if self.safe(Escalate(reason="technician escalated")):
            path = config.RECORDS / f"escalation_{self.session.id}.md"
            path.write_text(self.ledger.repair_record(self.session.id).replace("# Repair record", "# Escalation ticket"))
            self.last_file = str(path.relative_to(config.ROOT))

    def finish_job(self):
        if getattr(self, "_finished", None) == self.session.id:   # iteration 2 ST-13: one work record per job
            return
        self._finished = self.session.id
        path = config.RECORDS / f"repair_record_{self.session.id}.md"
        path.write_text(self.ledger.repair_record(self.session.id))
        self.last_file = str(path.relative_to(config.ROOT))
        job_id = f"J-{100 + self.ledger.job_count()}"
        proc = next((e.claim for e in self.plan.bundle.items if e.claim.startswith("Procedure")), "procedure")
        self.ledger.add_job(job_id, self.session.target.asset_id, list(self.session.intent.symptoms),
                            f"{proc.replace('Procedure ', '').split(' (')[0]} done; cause {self.plan.cause.replace('_', ' ')}", None, cause=self.plan.cause)
        self.hud.flash(f"Saved {job_id}. Waiting for senior approval (A) before JAMES reuses it.", GREEN, 6)

    def approve(self):
        j = self.ledger.latest_pending()
        if not j:
            self.hud.flash("No job waiting for approval", AMBER)
            return
        self.ledger.approve_job(j["job_id"], "senior-demo")
        self.hud.flash(f"{j['job_id']} approved by senior (demo). It can now be recalled.", GREEN)

    def _log_run(self):
        if self.sim:   # sim latency is ~0 s by design; keep it out of the file claims are quoted from
            return
        new = not config.RUNS_CSV.exists()
        with open(config.RUNS_CSV, "a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["time", "session", "input", "fault_to_first_step_s", "cause", "cause_backend", "echo", "orchestrator", "sample_data"])
            w.writerow([datetime.now().isoformat(timespec="seconds"), self.session.id, self.input_label, f"{self.latency:.2f}",
                        self.plan.cause, self.plan.cause_backend, self.echo_backend, self.plan.orchestrator, "yes"])

    # ---------- what the HUD shows ----------
    def view(self) -> dict:
        s, sess = self.state, self.session
        v = {"state": s.value, "keys": KEYS, "echo_backend": self.echo_backend.split(" (")[0],
             "page_backend": self.manual.backend.split(" (")[0].replace(" + nomic-embed-text in Qdrant", " + Qdrant"),
             "orchestrator": self.foreman.orchestrator.split(" (")[0], "pack": config.PACK.hud_label}
        pend = sess.pending_target
        heads = {
            State.LOCKED: "Locked. Presenter presses U to unlock.",
            State.TARGET_PENDING: (f"Is this {pend.asset_id}? Confidence {pend.confidence:.2f}. Pinch to confirm, or point at its tag."
                                   if pend else "Point at the machine's asset tag."),
            State.TARGET_CONFIRMED: (f"{sess.target.asset_id} confirmed ({sess.target.source}). Press V and describe the fault."
                                     + ("" if sess.facts.get("ppe_gloves") else " Gloves and eye protection on? Pinch = yes.")) if sess.target else "",
            State.INTENT_CAPTURED: "Request captured.",
            State.EVIDENCE_GATHERING: ("FOREMAN needs one answer from you."
                                       if self.question else
                                       ("No conclusion. Press E to escalate." if self.plan and
                                        self.plan.status == "no_match" else "Gathering evidence...")),
            State.AWAITING_CONFIRMATION: "Pinch and hold to confirm this step.",
            State.SAFETY_BLOCKED: "WARDEN blocked this step.",
            State.COMPLETE: f"Job complete. Repair record: {self.last_file}",
            State.ESCALATED: f"Escalated to the senior technician. Ticket: {self.last_file}",
        }
        v["headline"] = self.busy or heads.get(s, "")
        v["headline_color"] = RED if s == State.SAFETY_BLOCKED else (GREEN if s == State.COMPLETE else AMBER)
        if s in (State.AWAITING_CONFIRMATION, State.SAFETY_BLOCKED, State.LOGGED, State.COMPLETE) and sess.proposal:
            step = step_readout(sess, self.confirmed_at)
            if s == State.SAFETY_BLOCKED:
                b = blocked_readout(sess)
                v["readout"] = [b[0], b[2], b[3], step[0], step[2]]          # header, reason, rule, step, source
            else:
                keep = ("STEP", "Source", "Sensor", "Similar", "Sources disagree", "Limitation", "Safety", "Human", "Data")
                v["readout"] = [l for l in step if l.startswith(keep)]
        if s == State.SAFETY_BLOCKED:
            g = self.missing_group()
            v["prompt"] = f"CONFIRM: {g[2]} Pinch = yes (or key {'LZP'[[k for k, *_ in FACT_GROUPS].index(g[0])]})." if g \
                else "This rule can't be cleared by a confirmation. Skip (S) or escalate (E)."
        if self.plan and s not in (State.LOCKED, State.SAFETY_BLOCKED):
            v["evidence"] = [f"Likely cause: {self.plan.cause.replace('_', ' ')} ({self.plan.cause_backend}). {self.plan.why}"]
        if self.question and s == State.EVIDENCE_GATHERING:
            v["prompt"] = f"{self.question.text}  Pinch or Y = yes, N = no. (Asked because: {self.question.why}.)"
        if self.plan and s == State.EVIDENCE_GATHERING and self.plan.trace:
            v["readout"] = [f"Round {r.round}: {r.agent.title()} ({r.decided_by}): {r.action}. {r.result.split(' | ')[0]}"
                            for r in self.plan.trace if r.round > 0][-4:] + \
                [f"Left out: {x}" for x in self.plan.excluded[:2]]
        v["transcript"] = self.transcript
        if self.latency is not None:
            how = "scripted input, no speech to text" if self.input_label == "SCRIPTED" else "from end of speech"
            v["latency"] = f"Fault to first sourced step: {self.latency:.1f} s ({how}; lab, sample data)"
        return v

    # ---------- queue ----------
    def drain(self):
        while not self.q.empty():
            kind, *args = self.q.get()
            if kind == "heard":
                self._on_heard(*args)
            elif kind == "plan":
                plan, sid = args[0], (args[1] if len(args) > 1 else self.session.id)
                if sid != self.session.id:                 # the job it was for is gone (new job, lock)
                    self.ledger.append(sid, "investigation_ignored", {"reason": "job ended before the result came",
                                                                      "stop_reason": plan.stop_reason})
                    continue
                self._on_plan(plan)
            elif kind == "error":
                self.busy = ""
                self.hud.flash(args[0], RED)

    def key(self, k: str) -> bool:
        acts = {"u": self.toggle_lock, "v": self.voice_toggle, "1": self.scripted, "c": lambda: self.pinch("key_fallback"),
                "s": self.skip, "e": self.escalate, "a": self.approve,
                "y": lambda: self.answer(True), "n": lambda: self.answer(False), "o": self.toggle_sensors,
                "l": lambda: self.set_facts(("loto_confirmed",)), "z": lambda: self.set_facts(("zero_energy_verified",)),
                "p": lambda: self.set_facts(("ppe_gloves", "ppe_eye")),
                "t": lambda: self.pinch("key_fallback") if self.state == State.TARGET_PENDING else None,
                "r": lambda: (self.new_session(), self.safe(Unlock(presenter_id="presenter"))),
                "f": lambda: setattr(self, "show_telemetry", not self.show_telemetry),
                "h": lambda: setattr(self.hud, "show_hints", not self.hud.show_hints)}
        if k == "q":
            return False
        if k in acts:
            acts[k]()
        return True


# ---------------- live loop ----------------
def run_live(voice: bool):
    import cv2
    from agents.lens import Lens
    app = App()
    print(banner(app))
    if voice:
        threading.Thread(target=app.transcriber.load, daemon=True).start()   # warm up whisper
    cap = cv2.VideoCapture(config.CAMERA_INDEX)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, config.FRAME_W)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, config.FRAME_H)
    if not cap.isOpened():
        sys.exit("Camera not available. Allow camera access for Terminal in System Settings > Privacy & Security.")
    lens = Lens()
    t0, pinch_start, fired, cooldown_until = time.monotonic(), None, False, 0.0
    timer = J.FrameTimer()                      # measured telemetry (F shows it)
    cv2.namedWindow("JAMES", cv2.WINDOW_NORMAL)
    try:
        while True:
            timer.start()
            ok, frame = cap.read()
            if not ok:
                break
            timer.mark("capture")
            ts = int((time.monotonic() - t0) * 1000)
            r = lens.process(frame, ts)
            timer.mark("perception")
            app.drain()
            # target: pointed tag (high confidence) or a lone visible tag (needs confirmation)
            if app.state == State.TARGET_PENDING:
                if r.pointed and r.pointed.asset_id and r.point_conf >= 0.85:
                    app.target(r.pointed.asset_id, "asset_tag", r.point_conf)
                elif len(r.tags) == 1 and r.tags[0].asset_id and not app.session.pending_target:
                    app.target(r.tags[0].asset_id, "asset_tag", 0.80)
                elif not r.tags and getattr(r, "keys", None):
                    app.key_card_seen()
            # pinch-and-hold
            progress = 0.0
            now = time.monotonic()
            if r.pinching and now >= cooldown_until and not fired:
                pinch_start = pinch_start or now
                held = (now - pinch_start) * 1000
                progress = held / app.session.PINCH_HOLD_MS
                if held >= app.session.PINCH_HOLD_MS:
                    fired = True
                    cooldown_until = now + 1.2          # one confirm per deliberate pinch
                    app.pinch("pinch_hold")
            elif not r.pinching:
                pinch_start, fired = None, False
            v = app.view()
            v["pinch_progress"] = progress
            if app.recorder.active:
                v["headline"] = "Listening... press V to stop"
            if config.MIRROR:                       # tags are read on the raw frame; only the view is flipped
                frame, r = cv2.flip(frame, 1), mirror_result(r, frame.shape[1])
            v["telemetry"], v["show_telemetry"] = timer, app.show_telemetry
            out = app.hud.draw(frame, v, r)
            J.splash(out, time.monotonic() - t0)       # brand splash for the first ~1.6 s only
            timer.mark("render")
            cv2.imshow("JAMES", out)
            k = cv2.waitKey(1) & 0xFF
            timer.mark("display")
            timer.end()
            if k != 255 and not app.key(chr(k).lower()):
                break
    finally:
        lens.close(); cap.release(); cv2.destroyAllWindows()


def mirror_result(r, width):
    """Flip LENS coordinates to match a mirrored view."""
    import copy
    m = copy.copy(r)
    fx = lambda p: (width - 1 - p[0], p[1])
    tags = []
    for t in r.tags:
        c = t.corners.copy(); c[:, 0] = width - 1 - c[:, 0]
        nt = copy.copy(t); nt.corners, nt.center = c, fx(t.center)
        tags.append(nt)
        if r.pointed is t:
            m.pointed = nt
    m.tags = tags
    keys = []
    for t in getattr(r, "keys", []) or []:
        c = t.corners.copy(); c[:, 0] = width - 1 - c[:, 0]
        nt = copy.copy(t); nt.corners, nt.center = c, fx(t.center)
        keys.append(nt)
    m.keys = keys
    m.hand = [fx(p) for p in r.hand] if r.hand else None
    m.fingertip = fx(r.fingertip) if r.fingertip else None
    m.others = [[fx(p) for p in h] for h in getattr(r, "others", [])]
    return m


def banner(app: App) -> str:
    from tools.build_id import build_id
    print(f"JAMES build {build_id()[0]}  ·  pack snapshot {config.PACK.snapshot}")
    return "\n".join([
        f"JAMES demo build (sample data, demo rules {app.policy.label}, cloud OFF)",
        f"  pack         : {config.PACK.hud_label}, by {config.PACK.manifest.publisher}",
        f"  local model  : {config.CHAT_MODEL} {'OK' if llm.available(config.CHAT_MODEL) else 'NOT RUNNING -> keyword rules'}",
        f"  PAGE search  : {app.manual.backend}",
        f"  FOREMAN      : {app.foreman.orchestrator}",
        f"  ledger       : {config.DB}", ""])


# ---------------- sim (no camera, no mic) ----------------
def run_sim(save_frames: bool = True) -> App:
    import numpy as np
    app = App(sim=True)
    print(banner(app))
    out = config.DATA / "sim_frames"
    out.mkdir(exist_ok=True)
    bg = np.full((config.FRAME_H, config.FRAME_W, 3), (70, 74, 78), np.uint8)
    n = [0]

    def snap(name):
        v = app.view()
        print(f"\n--- {name} [{v['state']}] ---\n{v['headline']}")
        for line in v.get("readout", []):
            print("  " + line)
        if v.get("prompt"):
            print("  " + v["prompt"])
        if save_frames:
            import cv2
            n[0] += 1
            cv2.imwrite(str(out / f"{n[0]:02d}_{name}.png"), app.hud.draw(bg.copy(), v))

    snap("locked")
    app.toggle_lock()
    app.target("P-3", "asset_tag", 0.97); snap("target")
    app.pinch()                                      # PPE on
    app.q.put(("heard", SCRIPTED, 0.95, time.perf_counter(), "SCRIPTED")); app.drain(); snap("step1")
    app.pinch(); snap("step2")
    app.skip(); app.skip(); snap("blocked")          # skip lockout and zero energy on purpose
    while app.state == State.SAFETY_BLOCKED and app.missing_group():
        app.pinch()
    snap("cleared")
    app.pinch(); snap("step5")
    app.pinch(); snap("complete")
    app.approve()
    # run it again: the approved job is recalled first
    app.new_session(); app.toggle_lock(); app.target("P-3", "asset_tag", 0.97); app.pinch()
    app.q.put(("heard", SCRIPTED, 0.95, time.perf_counter(), "SCRIPTED")); app.drain(); snap("rerun")
    print("\nRepair record written:", app.last_file or "(second run in progress)")
    return app


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--sim", action="store_true")
    ap.add_argument("--no-voice", action="store_true")
    a = ap.parse_args()
    run_sim() if a.sim else run_live(voice=not a.no_voice)
