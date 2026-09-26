"""WARDEN for the Lab: a thin adapter over james_core.safety_guard (the same deterministic
engine the main JAMES app uses). No AI. Policy state belongs to each step, not the job.

A recorded prerequisite is an operator attestation inside a demo. WARDEN cannot observe
isolation, PPE or machine safety; it only checks that the attestation was recorded."""
from __future__ import annotations

from typing import Mapping

from james_core.safety_guard import Policy, evaluate
from james_core.schemas import Decision, Proposal, ProposedStep

from .page import CatalogStep
from .schemas import StepPolicy

STATE = {Decision.ALLOW: "allowed", Decision.BLOCK: "blocked", Decision.REQUIRE_PREREQ: "needs_information"}


def check(step: CatalogStep, facts: Mapping, evidence_ids: tuple[str, ...], policy: Policy) -> StepPolicy:
    ps = ProposedStep(action_id="SHOW_STEP", text=step.text, evidence_ids=evidence_ids, touches=step.touches)
    d = evaluate(Proposal(bundle_id="lab", step=ps, step_index=1, step_total=1), dict(facts), policy)
    return StepPolicy(step_id=step.step_id, state=STATE[d.decision], rule_ids=d.rule_ids, reasons=d.reasons,
                      missing=d.missing, policy=policy.label)
