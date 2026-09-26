import sqlite3

import pytest

from helpers import Clock, manual, memory, pinch, propose, sensor, session_at_evidence
from james_core.actions import ActionNotAllowed, ActionRegistry
from james_core.evidence import build_bundle
from james_core.ledger import Ledger
from james_core.safety_guard import load_policy
from james_core.schemas import Target
from james_core.session import (Complete, Escalate, FactsUpdated, IntentParsed, NextStep, Pinch,
                                ProposalMade, Refused, Session, State, TargetConfirmedByTechnician,
                                TargetSeen, Unlock)
from james_core.schemas import Intent

SAFE = {"loto_confirmed": True, "zero_energy_verified": True, "ppe_gloves": True, "ppe_eye": True}


def test_happy_path_logs_confirmation_with_sources():
    s, b, _ = session_at_evidence()
    s.handle(FactsUpdated(facts=SAFE))
    st, p = propose(s, b)
    assert st == State.AWAITING_CONFIRMATION
    assert s.handle(Pinch(confirmation=pinch(p))) == State.LOGGED
    conf = [e for e in s.ledger.events(s.id) if e["kind"] == "confirmed"][0]["payload"]
    assert "manual:pump-oem.pdf#p42" in conf["sources"] and conf["policy"] == "PUMP-UTIL v0.1"
    assert s.handle(Complete()) == State.COMPLETE
    assert "CONFIRMED by pinch" in s.ledger.repair_record(s.id)


def test_missing_prerequisite_blocks_with_rule_then_recovers():
    s, b, _ = session_at_evidence()
    st, p = propose(s, b)
    assert st == State.SAFETY_BLOCKED and "LOTO-01" in s.decision.rule_ids
    with pytest.raises(Refused):
        s.handle(Pinch(confirmation=pinch(p)))
    assert s.handle(FactsUpdated(facts=SAFE)) == State.AWAITING_CONFIRMATION
    assert s.handle(Pinch(confirmation=pinch(p))) == State.LOGGED


def test_pinch_while_locked_is_refused_and_logged():
    s = Session(Ledger(), load_policy())
    with pytest.raises(Refused):
        s.handle(Pinch(confirmation=pinch(type("P", (), {"id": "prp-x"})())))
    assert s.state == State.LOCKED
    assert s.ledger.events(s.id)[-1]["accepted"] is False


def test_short_pinch_and_wrong_proposal_refused():
    s, b, _ = session_at_evidence()
    s.handle(FactsUpdated(facts=SAFE))
    _, p = propose(s, b)
    with pytest.raises(Refused, match="needs"):
        s.handle(Pinch(confirmation=pinch(p, hold=200)))
    other = pinch(p).model_copy(update={"proposal_id": "prp-old"})
    with pytest.raises(Refused, match="different or old"):
        s.handle(Pinch(confirmation=other))
    assert s.state == State.AWAITING_CONFIRMATION


def test_stale_proposal_refused():
    s, b, clock = session_at_evidence()
    s.handle(FactsUpdated(facts=SAFE))
    _, p = propose(s, b)
    clock.advance(100)
    with pytest.raises(Refused, match="stale"):
        s.handle(Pinch(confirmation=pinch(p)))


def test_facts_change_before_pinch_reblocks():
    s, b, _ = session_at_evidence()
    s.handle(FactsUpdated(facts=SAFE))
    _, p = propose(s, b)
    s.handle(FactsUpdated(facts={"loto_confirmed": False}))
    assert s.state == State.SAFETY_BLOCKED
    with pytest.raises(Refused):
        s.handle(Pinch(confirmation=pinch(p)))


def test_untyped_model_text_is_refused():
    s, b, _ = session_at_evidence()
    with pytest.raises(Refused, match="untyped"):
        s.handle("ALLOW: proceed with step 3")
    with pytest.raises(Refused):
        s.handle({"decision": "ALLOW"})


def test_low_confidence_target_needs_technician():
    s = Session(Ledger(), load_policy())
    s.handle(Unlock(presenter_id="owner"))
    t = Target(asset_id="P-3", source="vision", confidence=0.62)
    assert s.handle(TargetSeen(target=t)) == State.TARGET_PENDING
    with pytest.raises(Refused):
        s.handle(IntentParsed(intent=Intent(asset_id="P-3", request="diagnose", transcript="x", confidence=0.9)))
    assert s.handle(TargetConfirmedByTechnician(target_id=t.id)) == State.TARGET_CONFIRMED


def test_unclear_voice_and_wrong_machine_refused():
    s = Session(Ledger(), load_policy())
    s.handle(Unlock(presenter_id="owner"))
    s.handle(TargetSeen(target=Target(asset_id="P-3", source="asset_tag", confidence=0.97)))
    with pytest.raises(Refused, match="Unclear"):
        s.handle(IntentParsed(intent=Intent(asset_id="P-3", request="diagnose", transcript="...", confidence=0.3)))
    with pytest.raises(Refused, match="confirmed target"):
        s.handle(IntentParsed(intent=Intent(asset_id="P-7", request="diagnose", transcript="x", confidence=0.9)))


def test_conflict_must_be_surfaced():
    items = [manual(topic="likely_cause", stance="bearing_wear"), sensor(),
             memory(topic="likely_cause", stance="misalignment")]
    s, b, _ = session_at_evidence(items=items)
    assert len(b.conflicts) == 1
    with pytest.raises(Refused, match="disagreement"):
        propose(s, b, text="Review the vibration trend", touches=())
    st, _ = propose(s, b, text="Review the vibration trend", touches=(), surfaced=(b.conflicts[0].id,))
    assert st == State.AWAITING_CONFIRMATION


def test_missing_sensor_is_a_limitation_not_invented():
    s, b, _ = session_at_evidence(items=[manual(), memory()])
    kinds = [l.kind.value for l in b.limitations]
    assert "MISSING_SENSOR" in kinds
    assert all(e.kind.value != "SENSOR" for e in b.items)


def test_uncited_evidence_ids_refused():
    s, b, _ = session_at_evidence()
    from james_core.schemas import Proposal, ProposedStep
    p = Proposal(bundle_id=b.id, step_index=1, step_total=1,
                 step=ProposedStep(action_id="SHOW_STEP", text="x", evidence_ids=("ev-made-up",)))
    with pytest.raises(Refused, match="isn't in the bundle"):
        s.handle(ProposalMade(proposal=p))


def test_idle_autolock_and_restart_come_back_locked():
    s, b, clock = session_at_evidence()
    clock.advance(200)
    with pytest.raises(Refused):
        s.handle(FactsUpdated(facts=SAFE))
    assert s.state == State.LOCKED
    r = Session.restore(s.ledger, s.policy, s.id)
    assert r.state == State.LOCKED and r.proposal is None and r.facts == {}


def test_escalation_carries_evidence_and_limitations():
    s, b, _ = session_at_evidence(items=[manual()])
    assert s.handle(Escalate(reason="fault not resolved")) == State.ESCALATED
    esc = [e for e in s.ledger.events(s.id) if e["kind"] == "escalated"][0]["payload"]
    assert esc["sources"] == ["manual:pump-oem.pdf#p42"] and esc["limitations"]


def test_next_step_loop():
    s, b, _ = session_at_evidence()
    s.handle(FactsUpdated(facts=SAFE))
    _, p = propose(s, b)
    s.handle(Pinch(confirmation=pinch(p)))
    assert s.handle(NextStep()) == State.EVIDENCE_GATHERING
    st, _ = propose(s, b, text="Inspect the coupling", touches=("coupling_guard",))
    assert st == State.AWAITING_CONFIRMATION


def test_ledger_is_append_only():
    led = Ledger()
    led.append("ses-1", "x", {})
    with pytest.raises(sqlite3.DatabaseError):
        led.db.execute("UPDATE events SET kind='y'")
    with pytest.raises(sqlite3.DatabaseError):
        led.db.execute("DELETE FROM events")


def test_only_approved_jobs_recalled():
    led = Ledger()
    led.add_job("018", "P-3", ["vibration", "heat"], "Replaced drive-end bearing", approved_by="senior-01")
    led.add_job("019", "P-3", ["vibration"], "Unreviewed guess", approved_by=None)
    got = led.recall_approved("P-3", ["vibration"])
    assert [j["job_id"] for j in got] == ["018"]


def test_action_registry_rejects_non_allowlisted():
    reg = ActionRegistry(load_policy())
    with pytest.raises(ActionNotAllowed):
        reg.register("WRITE_PLC_SETPOINT", lambda step: None)
    shown = []
    reg.register("SHOW_STEP", lambda step: shown.append(step.text))
    s, b, _ = session_at_evidence()
    s.actions = reg
    s.handle(FactsUpdated(facts=SAFE))
    _, p = propose(s, b)
    s.handle(Pinch(confirmation=pinch(p)))
    assert shown == ["Remove the coupling guard"]
