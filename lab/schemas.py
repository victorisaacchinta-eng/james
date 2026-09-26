"""Typed contracts between the Lab's modules. Frozen, unknown fields refused.

Provenance is explicit on every piece of evidence. There is no confidence percentage:
the Lab shows what evidence exists and where it came from, not a made-up probability."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

Provenance = Literal["synthetic_sensor", "synthetic_document", "operator_report", "reviewed_history"]
Freshness = Literal["fresh", "stale", "unknown"]
Flag = Literal["up", "down", "borderline", "within_band", "unavailable", "not_applicable"]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Evidence(Strict):
    """The shared evidence contract (PDD section 6)."""
    evidence_id: str
    source_type: Literal["sensor_channel", "document_section", "history_case"]
    asset_id: Optional[str] = None
    job_id: Optional[str] = None
    source_id: str                    # dataset hash prefix, document id, or job id
    source_version: str               # content hash or document version
    locator: str                      # rows/time range/channel, or section id
    observed_at: Optional[str] = None
    ingested_at: Optional[str] = None
    freshness: Freshness = "unknown"
    value: Optional[float] = None
    units: Optional[str] = None
    quality: Optional[str] = None
    provenance: Provenance
    summary: str


class ChannelSummary(Strict):
    channel: str
    units: Optional[str]
    flag: Flag
    baseline_n: int = 0
    current_n: int = 0
    baseline_mean: Optional[float] = None
    current_mean: Optional[float] = None
    current_latest: Optional[float] = None
    abs_change: Optional[float] = None
    pct_change: Optional[float] = None
    threshold_pct: Optional[float] = None
    evidence_id: Optional[str] = None
    note: Optional[str] = None


class SensorEvidence(Strict):
    """PULSE output for one asset and window."""
    asset_id: str
    dataset_id: str
    dataset_hash: str
    scenario_clock: str
    window_start: str
    window_end: str
    baseline_end: str
    current_start: str
    newest_sample: Optional[str]
    freshness: Freshness
    rows: tuple[int, ...] = ()        # CSV data-row numbers used (1 = first data row)
    excluded_rows: int = 0            # rows dropped for a non-ok quality flag
    channels: tuple[ChannelSummary, ...]
    limitations: tuple[str, ...] = ()
    evidence: tuple[Evidence, ...] = ()
    synthetic: bool = True


class Observation(Strict):
    text: str
    evidence_ids: tuple[str, ...]


class PossibleCause(Strict):
    cause_id: str
    label: str
    why: str
    evidence_ids: tuple[str, ...]
    status: Literal["possible"] = "possible"     # never "confirmed" in this prototype


class HistoryRef(Strict):
    job_id: str
    asset_id: str
    summary: str
    outcome: Optional[str]
    review: str
    seeded: bool
    why: str                          # why it matched, or why it is not recommended
    evidence_id: str


class GuidanceProposal(Strict):
    proposal_id: str = Field(default_factory=lambda: new_id("prp"))
    job_id: str
    version: int = Field(ge=1)
    mode: Literal["fixture", "local_ai"]
    mode_label: str
    asset_id: str
    observations: tuple[Observation, ...] = ()
    possible_causes: tuple[PossibleCause, ...] = ()
    unknowns: tuple[str, ...] = ()
    questions: tuple[str, ...] = ()
    steps: tuple[str, ...] = ()       # step ids from the procedure catalog only
    escalate: bool = False
    escalation_reason: Optional[str] = None
    recommended_history: tuple[HistoryRef, ...] = ()
    other_history: tuple[HistoryRef, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    created_at: str = Field(default_factory=lambda: utcnow().isoformat())


class StepPolicy(Strict):
    step_id: str
    state: Literal["allowed", "blocked", "needs_information"]
    rule_ids: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    policy: str
