"""WARDEN: deterministic policy checks against an approved rule set.

Plain Python on purpose. This module must never import a model client
(tests/test_warden.py enforces that). Same input, same decision, every time.

Decision rules:
  BLOCK           a fact is known and violates a rule, the action is not
                  allowlisted, or the step has no source
  REQUIRE_PREREQ  a fact the rule needs has not been confirmed yet
  ALLOW           every matching rule is satisfied
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

from .schemas import Decision, PolicyDecision, Proposal


@dataclass(frozen=True)
class Policy:
    suite: str
    version: str
    status: str
    allowlisted_actions: frozenset[str]
    rules: tuple[dict, ...]
    hazard_keywords: dict = None
    fact_steps: dict = None

    def tag(self, text: str) -> tuple[str, ...]:
        """Hazard tags for a step, by keyword. Deterministic."""
        t = text.lower()
        return tuple(tag for tag, kws in (self.hazard_keywords or {}).items() if any(k in t for k in kws))

    def facts_set_by(self, text: str) -> tuple[str, ...]:
        t = text.lower()
        return tuple(f for f, kws in (self.fact_steps or {}).items() if any(k in t for k in kws))

    @property
    def label(self) -> str:
        return f"{self.suite} v{self.version}"


def load_policy(path: str | Path | bytes | None = None) -> Policy:
    """Load a rule set. With no path, the active industry pack's rules are used, from the bytes the pack
    verified at load time (never re-read from disk). A bytes argument is used as is."""
    if path is None:
        from .pack import active_pack
        path = active_pack().rules_bytes()
    raw = yaml.safe_load(path.decode("utf-8") if isinstance(path, bytes) else Path(path).read_text())
    return Policy(
        suite=raw["suite"],
        version=str(raw["version"]),
        status=raw.get("status", "unknown"),
        allowlisted_actions=frozenset(raw["allowlisted_actions"]),
        rules=tuple(raw["rules"]),
        hazard_keywords=raw.get("hazard_keywords", {}),
        fact_steps=raw.get("fact_steps", {}),
    )


def _matches(rule: dict, touches: set[str]) -> bool:
    scope = rule.get("when_touches_any")
    return True if scope is None else bool(touches & set(scope))


def evaluate(proposal: Proposal, facts: Mapping[str, Any], policy: Policy) -> PolicyDecision:
    """Return ALLOW, BLOCK or REQUIRE_PREREQ for one proposed step."""
    step = proposal.step
    touches = set(step.touches)
    blocks: list[tuple[str, str]] = []
    prereqs: list[tuple[str, str, str]] = []

    for rule in policy.rules:
        kind, rid, reason = rule["kind"], rule["id"], rule["reason"]
        if kind == "allowlist":
            if step.action_id not in policy.allowlisted_actions:
                blocks.append((rid, f"{reason}: {step.action_id}"))
        elif kind == "requires_evidence":
            if not step.evidence_ids:
                blocks.append((rid, reason))
        elif not _matches(rule, touches):
            continue
        elif kind == "requires_true":
            for fact in rule["facts"]:
                if fact not in facts:
                    prereqs.append((rid, reason, fact))
                elif facts[fact] is not True:
                    blocks.append((rid, reason))
        elif kind == "max_value":
            fact, limit = rule["fact"], rule["max"]
            if fact not in facts:
                prereqs.append((rid, f"{fact} not measured", fact))
            elif float(facts[fact]) > float(limit):
                blocks.append((rid, f"{reason} ({facts[fact]} > {limit})"))
        else:  # unknown rule kinds fail closed
            blocks.append((rid, f"Unknown rule kind '{kind}'"))

    def dedupe(xs):
        return tuple(dict.fromkeys(xs))

    if blocks:
        return PolicyDecision(
            proposal_id=proposal.id, decision=Decision.BLOCK,
            rule_ids=dedupe(r for r, _ in blocks), reasons=dedupe(m for _, m in blocks),
            missing=dedupe(f for _, _, f in prereqs),
            policy_suite=policy.suite, policy_version=policy.version)
    if prereqs:
        return PolicyDecision(
            proposal_id=proposal.id, decision=Decision.REQUIRE_PREREQ,
            rule_ids=dedupe(r for r, _, _ in prereqs), reasons=dedupe(m for _, m, _ in prereqs),
            missing=dedupe(f for _, _, f in prereqs),
            policy_suite=policy.suite, policy_version=policy.version)
    return PolicyDecision(
        proposal_id=proposal.id, decision=Decision.ALLOW,
        policy_suite=policy.suite, policy_version=policy.version)
