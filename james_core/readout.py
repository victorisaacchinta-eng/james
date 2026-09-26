"""HUD text for the two demo readouts. Keeps sourced, uncertain and
human-confirmed information visibly separate."""
from __future__ import annotations

from .schemas import Decision, EvidenceKind
from .session import Session, State


def blocked_readout(s: Session) -> list[str]:
    d = s.decision
    if not d or d.decision == Decision.ALLOW:
        return []
    kind = "rule violated" if d.decision == Decision.BLOCK else "prerequisite not confirmed"
    return ["BLOCKED BY SAFETY POLICY",
            f"Type: {kind}",
            f"Reason: {'; '.join(d.reasons)}",
            f"Rule: {', '.join(d.rule_ids)} · {d.policy_suite} v{d.policy_version}",
            f"Missing: {', '.join(d.missing) or 'none'}"]


def _similar(cited, bundle) -> str:
    """A past job that supports this step, or one that exists but points elsewhere (never hide it)."""
    if cited:
        return f"{cited.ref} · {cited.claim}"
    other = next((e for e in bundle.items if e.kind == EvidenceKind.MEMORY), None)
    if other:
        where = f" ({other.stance.replace('_', ' ')})" if other.stance else ""
        return f"{other.ref} points elsewhere{where}; not used for this step"
    return "none approved"


def step_readout(s: Session, confirmed_at: str | None = None) -> list[str]:
    if not s.proposal or not s.bundle:
        return []
    p, b = s.proposal, s.bundle
    cited = {e.id: e for e in b.items if e.id in p.step.evidence_ids}
    def first(kind):
        return next((e for e in cited.values() if e.kind == kind), None)
    manuals = [e for e in cited.values() if e.kind == EvidenceKind.MANUAL]
    m = next((e for e in manuals if e.claim.startswith("Procedure")), manuals[0] if manuals else None)
    sen, mem = first(EvidenceKind.SENSOR), first(EvidenceKind.MEMORY)
    t = s.target
    lines = [f"STEP {p.step_index} OF {p.step_total} · {p.step.text.rstrip('.').upper()}",
             f"Target: {t.asset_id} · {t.source} · confidence {t.confidence:.2f}",
             f"Source: {m.ref if m else 'NONE (limitation)'}"
             + (f" · {p.step.source_step}" if getattr(p.step, "source_step", "") else ""),
             f"Sensor evidence: {sen.claim if sen else 'none available'}",
             f"Similar case: {_similar(mem, b)}"]
    for c in b.conflicts:
        lines.append(f"Sources disagree: {c.detail}")
    for l in b.limitations:
        lines.append(f"Limitation: {l.detail}")
    d = s.decision
    lines.append(f"Safety policy: {d.policy_suite} v{d.policy_version} · {d.decision.value}" if d else "Safety policy: not checked")
    lines.append(f"Human confirmed: {'yes' + (', ' + confirmed_at if confirmed_at else '') if s.state in (State.LOGGED, State.COMPLETE) else 'not yet'}")
    lines.append("Action: allowlisted guidance and logging only")
    if any(e.sample_data for e in b.items):
        lines.append("Data: SAMPLE (not plant data)")
    return lines
