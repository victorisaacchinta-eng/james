"""LEDGER recall: similar past jobs, senior-approved only, and only when they apply (iteration 2, P1-D).

A past job is offered as evidence only if all of these hold, and every job it skips is named in a note:
  * approved by a senior, and the approval has not been revoked
  * the same equipment class as the target (a seal job on another kind of machine says nothing here)
  * its cause is one this pack's fault list knows
  * it is not older than config.MEMORY_MAX_AGE_DAYS
Memory is the weakest source: FOREMAN never decides a cause on memory alone."""
from __future__ import annotations

from datetime import datetime, timezone

import config
from james_core.ledger import Ledger
from james_core.schemas import AgentId, Evidence, EvidenceKind


def _age_days(created: str) -> float | None:
    try:
        t = datetime.fromisoformat(created)
    except (TypeError, ValueError):
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - t).total_seconds() / 86400


def recall_checked(ledger: Ledger, asset_id: str, symptoms: tuple[str, ...], pack=None,
                   max_age_days: float | None = None) -> tuple[list[Evidence], list[str]]:
    """(evidence, notes). At most one job is used; the notes say why others were left out."""
    pack = pack or config.PACK
    max_age = max_age_days if max_age_days is not None else getattr(config, "MEMORY_MAX_AGE_DAYS", 730)
    cls = pack.class_of(asset_id)
    causes = set(pack.faults(cls).causes) if cls else set()
    out, notes = [], []
    for j in ledger.recall_approved(asset_id, symptoms):
        jcls = pack.class_of(j["asset_id"])
        age = _age_days(j.get("created", ""))
        if jcls != cls:
            notes.append(f"job {j['job_id']}: {j['asset_id']} is not the same kind of machine")
        elif j.get("cause") and j["cause"] not in causes:
            notes.append(f"job {j['job_id']}: cause '{j['cause']}' is not in this pack's fault list")
        elif age is None:
            notes.append(f"job {j['job_id']}: no date, so its freshness can't be checked")
        elif age < -1:
            notes.append(f"job {j['job_id']}: dated in the future ({-age:.0f} days ahead)")
        elif age > max_age:
            notes.append(f"job {j['job_id']}: {age:.0f} days old (limit {max_age:.0f})")
        elif not out:
            out.append(Evidence(agent=AgentId.LEDGER, kind=EvidenceKind.MEMORY, asset_id=asset_id,
                                claim=f"{j['fix']} (approved by {j['approved_by']})", ref=f"job:{j['job_id']}",
                                confidence=0.7, topic="likely_cause" if j.get("cause") else None,
                                stance=j.get("cause"), sample_data=j["sample_data"]))
    return out, notes


def recall(ledger: Ledger, asset_id: str, symptoms: tuple[str, ...]) -> list[Evidence]:
    return recall_checked(ledger, asset_id, symptoms)[0]
