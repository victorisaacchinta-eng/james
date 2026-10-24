"""Maintenance investigation trace (iteration 2, P0-E and section 9). Run on the Mac with Ollama running:

    python -m tools.maintenance_trace            # writes records/trace_<time>.md and prints it

No camera, no microphone: the requests are typed text, labelled as such. Everything else is the real path:
ECHO (local model, else rules), the FOREMAN loop (the local model picks each next action when it runs),
PAGE / PULSE / LEDGER, the session state machine, WARDEN, the append-only ledger and the repair record.
Sample data only. The header says whether the local model was up, and each round says who decided it."""
from __future__ import annotations

import time
from datetime import datetime

import config
from agents import llm
from app import App
from james_core.session import State


def run() -> str:
    live = llm.available(config.CHAT_MODEL)
    mi = llm.model_info() if live else {}
    totals = {"model_attempts": 0, "model_accepted": 0, "rules": 0, "rules_fallback": 0}
    app = App(sim=True)
    from tools.build_id import build_id
    bid, nfiles = build_id()
    out = [f"# JAMES maintenance trace, {datetime.now():%Y-%m-%d %H:%M}",
           "",
           f"- Build: {bid} ({nfiles} files, `python -m tools.build_id`)",
           f"- Local model: {config.CHAT_MODEL} {'RUNNING' if live else 'NOT RUNNING: every decision below is made by the rules'}"
           + (f" · digest {mi.get('digest')} · {mi.get('parameter_size', '')} {mi.get('quantization', '')} · {mi.get('settings')}"
              if live else ""),
           "- Origin per round: [local model] = the model's choice was valid and used; [rules] = model not asked; "
           "[rules (model reply unusable)] = asked, reply refused, reason shown",
           f"- Pack: {config.PACK.hud_label} snapshot {getattr(config.PACK, 'snapshot', '?')}",
           f"- Manual search: {app.manual.backend}",
           "- Input: typed text (no microphone); SAMPLE DATA, demo rules, not an OEM manual",
           ""]

    def say(title, text):
        out.append(f"## {title}\n\nRequest (typed): \"{text}\"\n")

    def ask(text):
        app.q.put(("heard", text, 0.95, time.perf_counter(), "TYPED"))
        app.drain()

    def show_plan():
        p = app.plan
        if not p:
            return
        out.append(f"ECHO: {app.echo_backend}  ·  status **{p.status}**  ·  {p.cause_backend}"
                   + (f"  ·  cause **{p.cause.replace('_', ' ')}**" if p.status == 'ready' else ""))
        out.append("```")
        out.extend([r.line() for r in p.trace])
        out.extend([f"   fallback reason: {r.fallback}" for r in p.trace if r.fallback][:3])
        out.extend([f"excluded: {x}" for x in p.excluded])
        out.append(f"stop: {p.stop_reason} · decisions {p.decisions}")
        for k in totals:
            totals[k] += p.decisions.get(k, 0)
        if p.question:
            out.append(f"QUESTION: {p.question.text}  (why: {p.question.why}; from {p.question.source})")
        out.extend([f"conflict surfaced: {c.detail}" for c in p.bundle.conflicts])
        out.append("```")

    def start():
        app.new_session(); app.toggle_lock(); app.target("P-3", "asset_tag", 0.97); app.pinch()   # PPE confirmed

    def warden_line():
        d = app.session.decision
        st = app.session.proposal.step.text if app.session.proposal else "-"
        return f"- step '{st}': WARDEN {d.decision.value if d else '-'}" + (f" ({', '.join(d.reasons)})" if d and d.reasons else "")

    # 1. evidence-dependent tool choice + WARDEN veto + confirmation + work record
    start()
    say("1. Normal job: FOREMAN checks the sensor's cause against the manual, WARDEN vetoes, pinch confirms",
        "Pump 3 is vibrating more than usual")
    ask("Pump 3 is vibrating more than usual"); show_plan()
    app.pinch()                                                   # step 1 confirmed
    app.skip(); app.skip()                                        # skip lockout and zero energy on purpose
    out.append(warden_line() + "  <- lockout and zero energy were skipped")
    while app.state == State.SAFETY_BLOCKED and app.missing_group():
        g = app.missing_group(); app.pinch(); out.append(f"- technician confirmed: {g[2]}")
    out.append(warden_line())
    while app.state == State.AWAITING_CONFIRMATION:
        app.pinch()
    out.append(f"- job state: {app.state.value}; record {app.last_file}")
    app.approve()
    job = app.ledger.events("ledger")[-1]["payload"].get("job_id", "?")
    out.append(f"- senior approved {job} (demo role)\n")

    # 2. reviewed history reused on a second job, then the approval is revoked
    start()
    say("2. Second job on the same pump: the approved job is reused", "Pump 3 is vibrating more than usual")
    ask("Pump 3 is vibrating more than usual"); show_plan()
    used = [e.ref for e in app.plan.bundle.items if e.ref.startswith("job:")]
    out.append(f"- similar case used: {', '.join(used) or 'none'}")
    app.ledger.revoke_approval(job, "senior-demo", "fix did not hold (demo)")
    start(); ask("Pump 3 is vibrating more than usual")
    used = [e.ref for e in app.plan.bundle.items if e.ref.startswith("job:")]
    out.append(f"- after revoking {job}: similar case used: {', '.join(used) or 'none'}\n")

    # 3. missing observation -> the technician is asked, and the answer changes the conclusion
    app.foreman.use_sensors = False
    start()
    say("3. Sensor log unavailable: FOREMAN asks the technician", "Pump 3 is vibrating more than usual")
    ask("Pump 3 is vibrating more than usual"); show_plan()
    out.append("- technician answers: no")
    app.answer(False); show_plan()
    app.foreman.use_sensors = True

    # 4. weak search -> narrower searches -> abstain -> escalation
    start()
    say("4. A fault the manual does not cover: narrower searches, then abstain", "Pump 3 is leaking")
    ask("Pump 3 is leaking"); show_plan()
    app.escalate()
    out.append(f"- escalated: {app.last_file}\n")

    out.append("## Totals\n")
    out.append(f"- Model decisions accepted: {totals['model_accepted']} of {totals['model_attempts']} asked; "
               f"rule decisions: {totals['rules']}; rule fallbacks after an unusable reply: {totals['rules_fallback']}")
    if live and totals["model_accepted"] == 0:
        out.append("- WARNING: the model was running but none of its decisions was used. This trace does NOT show "
                   "model-driven orchestration.")
    text = "\n".join(out) + "\n"
    config.RECORDS.mkdir(exist_ok=True)
    path = config.RECORDS / f"trace_{datetime.now():%Y%m%d_%H%M%S}.md"
    path.write_text(text)
    print(text)
    print("Written:", path)
    return text


if __name__ == "__main__":
    run()
