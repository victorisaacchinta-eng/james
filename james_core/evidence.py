"""Build the evidence bundle FOREMAN plans from.

Missing sources become visible limitations (never invented evidence).
Evidence that disagrees on the same topic becomes a Conflict that the
proposal must surface to the technician."""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from .schemas import (Conflict, Evidence, EvidenceBundle, EvidenceKind, Limitation,
                      LimitationKind)

MISSING = {
    EvidenceKind.MANUAL: (LimitationKind.MISSING_MANUAL, "No manual page matched this fault"),
    EvidenceKind.SENSOR: (LimitationKind.MISSING_SENSOR, "No sensor log for this machine and window"),
    EvidenceKind.MEMORY: (LimitationKind.MISSING_MEMORY, "No approved past job matched"),
}


def build_bundle(asset_id: str, items: Iterable[Evidence], min_confidence: float = 0.5) -> EvidenceBundle:
    items = tuple(e for e in items if e.asset_id == asset_id)
    limitations: list[Limitation] = []
    kinds = {e.kind for e in items}
    for kind, (lk, msg) in MISSING.items():
        if kind not in kinds:
            limitations.append(Limitation(kind=lk, detail=msg))
    for e in items:
        if e.confidence < min_confidence:
            limitations.append(Limitation(kind=LimitationKind.LOW_CONFIDENCE,
                                          detail=f"{e.agent.value} {e.ref} confidence {e.confidence:.2f}"))

    by_topic: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for e in items:
        if e.topic and e.stance:
            by_topic[e.topic][e.stance].append(e.id)
    conflicts = []
    for topic, stances in by_topic.items():
        if len(stances) > 1:
            ids = tuple(i for group in stances.values() for i in group)
            conflicts.append(Conflict(topic=topic, evidence_ids=ids,
                                      detail=" vs ".join(sorted(stances))))
    return EvidenceBundle(asset_id=asset_id, items=items,
                          limitations=tuple(limitations), conflicts=tuple(conflicts))
