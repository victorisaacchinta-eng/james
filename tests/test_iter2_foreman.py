"""Iteration 2 P0-E / P1-D: FOREMAN investigates (observe -> decide -> act), and history is filtered before use.
ST-07 weak retrieval, ST-08 stale/incompatible case, ST-09 unrelated citation, ST-10 missing prerequisite,
ST-11 old confirmation, ST-13 duplicate work record, ST-19 revoked approval, ST-20 offline labelling."""
import time
from datetime import datetime, timedelta, timezone

import pytest
import yaml

import config
from agents import llm
from agents.echo import parse_intent
from agents.foreman import Foreman
from agents.page import ManualIndex
from james_core.ledger import Ledger
from james_core.safety_guard import load_policy
from james_core.schemas import EvidenceKind


@pytest.fixture(scope="module")
def manual():
    return ManualIndex(use_embeddings=False)


def _ledger(extra=()):
    led = Ledger(":memory:")
    for j in yaml.safe_load(config.SEEDED_JOBS.read_text())["jobs"]:
        led.add_job(j["job_id"], j["asset_id"], j["symptoms"], j["fix"], j["approved_by"], cause=j["cause"])
    for j in extra:
        led.add_job(**j)
    return led


def _plan(manual, text, led=None, sensors=True, answers=None):
    f = Foreman(manual, led or _ledger(), load_policy(), use_sensors=sensors)
    return f.plan(parse_intent(text, 0.9, "P-3")[0], answers)


@pytest.fixture(autouse=True)
def _no_model(monkeypatch):
    monkeypatch.setattr(llm, "available", lambda *a, **k: False)


def test_evidence_dependent_tool_choice(manual):
    p = _plan(manual, "Pump 3 is vibrating more than usual")
    acts = [(r.round, r.agent, r.action) for r in p.trace if r.round > 0]
    assert acts == [(1, "PAGE", "check bearing wear"), (2, "FOREMAN", "conclude bearing wear")]
    assert p.status == "ready" and p.cause == "bearing_wear" and len(p.steps) == 5
    # every step cites the procedure page and the evidence for the cause, not every item in the bundle
    proc = next(e for e in p.bundle.items if e.claim.startswith("Procedure"))
    for st in p.steps:
        cited = {e.id: e for e in p.bundle.items if e.id in st.evidence_ids}
        assert proc.id in st.evidence_ids
        assert all(e.stance in (None, "bearing_wear") for e in cited.values())


def test_missing_observation_asks_then_uses_the_answer(manual):
    p = _plan(manual, "Pump 3 is vibrating more than usual", sensors=False)
    assert p.status == "question" and p.steps == [] and p.question.id == "bearing_hot"
    assert "p. 4" in p.question.source and p.question.why == "no sensor log"
    yes = _plan(manual, "Pump 3 is vibrating more than usual", sensors=False, answers={"bearing_hot": True})
    no = _plan(manual, "Pump 3 is vibrating more than usual", sensors=False, answers={"bearing_hot": False})
    assert (yes.cause, no.cause) == ("bearing_wear", "misalignment")
    assert any(e.kind == EvidenceKind.OBSERVATION for e in no.bundle.items)
    assert [c.detail for c in no.bundle.conflicts] == ["bearing_wear vs misalignment"]   # memory disagrees: shown


def test_memory_alone_never_decides(manual):
    p = _plan(manual, "Pump 3 is vibrating more than usual", sensors=False)
    assert p.cause == "unknown"                                              # job 018 says bearing: not enough


def test_st07_weak_retrieval_retries_then_abstains(manual):
    p = _plan(manual, "Pump 3 is leaking")
    acts = [r.action.split(":")[0] for r in p.trace if r.round > 0]
    assert acts == ["search 2", "search 3", "abstain"] and p.status == "no_match" and p.steps == []


def test_st08_stale_or_incompatible_case_is_left_out(manual):
    old = (datetime.now(timezone.utc) - timedelta(days=1500)).isoformat()
    led = Ledger(":memory:")
    led.add_job("OLD", "P-3", ["vibration"], "Old bearing job", "senior-01", cause="bearing_wear", created=old)
    led.add_job("SEAL", "P-3", ["vibration"], "Seal job", "senior-01", cause="seal_wear")
    led.add_job("P7", "P-7", ["vibration"], "Other machine", "senior-01", cause="bearing_wear")
    p = _plan(manual, "Pump 3 is vibrating more than usual", led)
    assert not any(e.kind == EvidenceKind.MEMORY for e in p.bundle.items)
    text = " ".join(p.excluded)
    assert "OLD" in text and "days old" in text and "seal_wear" in text and "P-7 is not the same kind" in text


def test_st09_a_step_not_on_the_cited_page_is_dropped(manual, monkeypatch):
    real = manual.procedure

    def fake(cause, asset):
        ev, steps = real(cause, asset)
        steps[1].text = "Replace the impeller with a larger one."           # not in the manual
        return ev, steps
    monkeypatch.setattr(manual, "procedure", fake)
    p = _plan(manual, "Pump 3 is vibrating more than usual")
    assert len(p.steps) == 4 and not any("impeller" in s.text for s in p.steps)
    assert any("not on the cited page" in x for x in p.excluded)


def test_st09_a_cause_the_manual_does_not_name_is_not_used(manual, monkeypatch):
    monkeypatch.setattr(manual, "check_cause", lambda cause, asset: None)
    p = _plan(manual, "Pump 3 is vibrating more than usual")
    assert p.status != "ready" or p.cause != "bearing_wear"
    assert any("no row in the manual" in x for x in p.excluded)


def test_st19_revoked_approval_is_no_longer_recalled():
    led = _ledger()
    assert led.recall_approved("P-3", ["vibration"])[0]["job_id"] == "018"
    with pytest.raises(ValueError):
        led.revoke_approval("018", "senior-02", " ")
    led.revoke_approval("018", "senior-02", "the fix did not hold")
    assert all(j["job_id"] != "018" for j in led.recall_approved("P-3", ["vibration"]))
    assert any(e["kind"] == "job_revoked" for e in led.events("ledger"))
    with pytest.raises(Exception):
        led.db.execute("DELETE FROM job_reviews")                         # reviews are append-only


def test_model_decision_is_used_only_when_it_is_a_valid_option(monkeypatch, manual):
    monkeypatch.setattr(llm, "available", lambda *a, **k: True)
    monkeypatch.setattr(llm, "embed", lambda *a, **k: (_ for _ in ()).throw(llm.LLMUnavailable("x")))
    picks = iter([{"option": "finish_misalignment", "why": "invented"}])

    def fake(system, *a, **k):
        if "FOREMAN" in system:
            return next(picks)
        raise llm.LLMUnavailable("only FOREMAN's decisions are faked here")   # ECHO falls back to its rules
    monkeypatch.setattr(llm, "chat_json", fake)
    p = _plan(manual, "Pump 3 is vibrating more than usual")
    rows = [r for r in p.trace if r.round > 0]
    assert rows[0].decided_by == "rules"                        # one valid action: the model is not asked
    assert rows[1].decided_by == "rules (model reply unusable)" and p.cause == "bearing_wear"   # invented option refused

def test_st20_offline_run_is_labelled(manual):
    p = _plan(manual, "Pump 3 is vibrating more than usual")
    assert all(r.decided_by in ("fan-out", "rules") for r in p.trace) and "rules" in p.cause_backend


# ---- the app around it ----

def _app():
    from app import App
    a = App(sim=True)
    a.toggle_lock(); a.target("P-3", "asset_tag", 0.97); a.pinch()
    return a


def test_app_question_flow_is_logged(monkeypatch):
    from james_core.session import State
    a = _app()
    a.foreman.use_sensors = False
    a.q.put(("heard", "Pump 3 is vibrating more than usual", 0.95, time.perf_counter(), "TYPED")); a.drain()
    assert a.question and a.state == State.EVIDENCE_GATHERING and "bearing housing" in a.view()["prompt"]
    a.pinch()                                                                # pinch = yes
    assert a.question is None and a.plan.cause == "bearing_wear" and a.state in (State.AWAITING_CONFIRMATION,
                                                                                  State.SAFETY_BLOCKED)
    kinds = [e["kind"] for e in a.ledger.events(a.session.id)]
    assert kinds.count("investigation") == 2 and "observation" in kinds
    rec = a.ledger.repair_record(a.session.id)
    assert "TECHNICIAN answered: Is the bearing housing hotter than usual? -> yes" in rec and "FOREMAN investigation" in rec


def test_st10_missing_prerequisite_blocks_the_dependent_step():
    from james_core.session import State
    a = _app()
    a.q.put(("heard", "Pump 3 is vibrating more than usual", 0.95, time.perf_counter(), "TYPED")); a.drain()
    a.pinch(); a.skip(); a.skip()                                           # lockout + zero energy skipped
    assert a.state == State.SAFETY_BLOCKED and "coupling guard" in a.session.proposal.step.text
    assert a.missing_group()[0] == "loto_confirmed"                          # recordable prerequisite


def test_st11_old_confirmation_is_refused():
    from james_core.schemas import Confirmation
    from james_core.session import Pinch, Refused
    a = _app()
    a.q.put(("heard", "Pump 3 is vibrating more than usual", 0.95, time.perf_counter(), "TYPED")); a.drain()
    old = a.session.proposal.id
    a.pinch()                                                               # step 1 confirmed, step 2 proposed
    with pytest.raises(Refused):
        a.session.handle(Pinch(confirmation=Confirmation(proposal_id=old, gesture="pinch_hold", hold_ms=900)))


def test_st13_one_work_record_per_job():
    a = _app()
    a.q.put(("heard", "Pump 3 is vibrating more than usual", 0.95, time.perf_counter(), "TYPED")); a.drain()
    while a.missing_group() or a.session.proposal and a.state.value in ("AWAITING_CONFIRMATION", "SAFETY_BLOCKED"):
        a.pinch()
        if a.state.value == "COMPLETE":
            break
    n = a.ledger.job_count()
    a.finish_job(); a.advance()                                             # a double submit changes nothing
    assert a.ledger.job_count() == n
