"""james_core/readout.py: the safety HUD text. Blocked steps must say why, and a past job that points
elsewhere must be shown, not hidden. The readouts only read session attributes, so a plain namespace
stands in for a Session and no ledger is opened."""
from types import SimpleNamespace

from james_core.readout import _similar, blocked_readout
from james_core.schemas import (AgentId, Decision, Evidence, EvidenceBundle, EvidenceKind,
                                PolicyDecision)


def _decision(kind, missing=()):
    return PolicyDecision(proposal_id="prp-1", decision=kind, rule_ids=("LOTO-01",),
                          reasons=("Lockout/tagout not confirmed",), missing=missing,
                          policy_suite="pump-safety", policy_version="1.2")


def _memory(ref="job:J-104", stance=None):
    return Evidence(agent=AgentId.LEDGER, kind=EvidenceKind.MEMORY, asset_id="P-101",
                    claim="Replaced worn bearing", ref=ref, confidence=0.8, stance=stance)


def _bundle(*items):
    return EvidenceBundle(asset_id="P-101", items=items)


def test_no_decision_and_allow_give_no_blocked_readout():
    assert blocked_readout(SimpleNamespace(decision=None)) == []
    assert blocked_readout(SimpleNamespace(decision=_decision(Decision.ALLOW))) == []


def test_block_says_rule_violated():
    lines = blocked_readout(SimpleNamespace(decision=_decision(Decision.BLOCK, missing=("loto",))))
    assert lines == ["BLOCKED BY SAFETY POLICY",
                     "Type: rule violated",
                     "Reason: Lockout/tagout not confirmed",
                     "Rule: LOTO-01 · pump-safety v1.2",
                     "Missing: loto"]


def test_require_prereq_says_prerequisite_not_confirmed_and_missing_none():
    lines = blocked_readout(SimpleNamespace(decision=_decision(Decision.REQUIRE_PREREQ)))
    assert "Type: prerequisite not confirmed" in lines
    assert lines[-1] == "Missing: none"


def test_similar_returns_the_cited_job():
    cited = _memory()
    assert _similar(cited, _bundle(cited)) == "job:J-104 · Replaced worn bearing"


def test_similar_shows_an_uncited_job_that_points_elsewhere():
    other = _memory(ref="job:J-077", stance="misalignment_fix")
    assert _similar(None, _bundle(other)) == \
        "job:J-077 points elsewhere (misalignment fix); not used for this step"
    assert _similar(None, _bundle(_memory(ref="job:J-078"))) == \
        "job:J-078 points elsewhere; not used for this step"


def test_similar_says_none_approved_without_memory():
    manual = Evidence(agent=AgentId.PAGE, kind=EvidenceKind.MANUAL, asset_id="P-101",
                      claim="Procedure 6.3", ref="manual:pump.pdf#p5", confidence=0.9)
    assert _similar(None, _bundle(manual)) == "none approved"
    assert _similar(None, _bundle()) == "none approved"
