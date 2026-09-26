"""End-to-end checks of the app logic without camera or mic."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from agents.echo import parse_intent  # noqa: E402
from agents.page import ManualIndex  # noqa: E402
from agents.pulse import analyze  # noqa: E402
from james_core.safety_guard import load_policy  # noqa: E402
from james_core.session import State  # noqa: E402


def test_page_cites_troubleshooting_and_procedure():
    m = ManualIndex(use_embeddings=False)
    ev, _ = m.troubleshoot(("vibration",), "Pump 3 is vibrating", "P-3")
    assert ev and ev[0].ref.endswith("#p4") and "bearing wear" in ev[0].claim
    proc, steps = m.procedure("bearing_wear", "P-3")
    assert proc.ref.endswith("#p5") and len(steps) == 5
    assert m.search("6205-2RS")[0][1].page in (3, 5)


def test_pulse_reads_log_and_never_invents():
    ev, facts = analyze("P-3")
    assert "7.1 mm/s" in ev[0].claim and "baseline 2.8" in ev[0].claim and facts["casing_temp_c"] < 60
    assert analyze("P-9") == ([], {})


def test_steps_are_hazard_tagged_deterministically():
    pol = load_policy()
    assert pol.tag("Remove the coupling guard.") == ("coupling_guard",)
    assert pol.facts_set_by("apply your personal lock and tag") == ("loto_confirmed",)


def test_unclear_speech_gets_low_confidence():
    i, _ = parse_intent("uh what", 0.9, "P-3")
    assert i.confidence < 0.6


def test_full_sim_run_blocks_then_completes_and_learns():
    from app import run_sim
    app = run_sim(save_frames=False)
    ev = app.ledger.events("ledger")
    assert any(e["kind"] == "job_approved" for e in ev)
    # second run recalls the newly approved job first
    assert any(e.ref.startswith("job:J-") for e in app.plan.bundle.items)
    assert app.state == State.AWAITING_CONFIRMATION
