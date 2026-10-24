"""Typed messages between agents. Every object is frozen and rejects unknown fields,
so nothing downstream can act on free text a model happened to emit."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AgentId(str, Enum):
    LENS = "LENS"
    ECHO = "ECHO"
    PULSE = "PULSE"
    PAGE = "PAGE"
    LEDGER = "LEDGER"
    FOREMAN = "FOREMAN"
    WARDEN = "WARDEN"
    PINCH = "PINCH"
    TECHNICIAN = "TECHNICIAN"   # an answer the technician gave when FOREMAN asked for an observation


class Target(Strict):
    """LENS output. Low confidence is data, not a fact."""
    id: str = Field(default_factory=lambda: new_id("tgt"))
    agent: Literal[AgentId.LENS] = AgentId.LENS
    asset_id: str
    source: Literal["asset_tag", "vision", "technician"]
    confidence: float = Field(ge=0.0, le=1.0)
    ts: datetime = Field(default_factory=utcnow)


class Intent(Strict):
    """ECHO output. A structured request, never an instruction to execute."""
    id: str = Field(default_factory=lambda: new_id("int"))
    agent: Literal[AgentId.ECHO] = AgentId.ECHO
    asset_id: str
    request: Literal["diagnose", "next_step", "escalate"]
    symptoms: tuple[str, ...] = ()
    transcript: str
    confidence: float = Field(ge=0.0, le=1.0)
    ts: datetime = Field(default_factory=utcnow)


class EvidenceKind(str, Enum):
    MANUAL = "MANUAL"   # PAGE
    SENSOR = "SENSOR"   # PULSE
    MEMORY = "MEMORY"   # LEDGER recall
    OBSERVATION = "OBSERVATION"   # the technician's yes/no answer to a question FOREMAN asked


class Evidence(Strict):
    """One sourced claim. `ref` must point at something a human can open:
    manual:<file>#p<page>, log:<file>[<start>..<end>], job:<id>."""
    id: str = Field(default_factory=lambda: new_id("ev"))
    agent: AgentId
    kind: EvidenceKind
    asset_id: str
    claim: str
    ref: str
    confidence: float = Field(ge=0.0, le=1.0)
    topic: Optional[str] = None      # e.g. "likely_cause"
    stance: Optional[str] = None     # e.g. "bearing_wear" vs "misalignment"
    sample_data: bool = True         # stays True until real plant data is used
    ts: datetime = Field(default_factory=utcnow)


class LimitationKind(str, Enum):
    MISSING_MANUAL = "MISSING_MANUAL"
    MISSING_SENSOR = "MISSING_SENSOR"
    MISSING_MEMORY = "MISSING_MEMORY"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"


class Limitation(Strict):
    kind: LimitationKind
    detail: str


class Conflict(Strict):
    id: str = Field(default_factory=lambda: new_id("cf"))
    topic: str
    evidence_ids: tuple[str, ...]
    detail: str


class EvidenceBundle(Strict):
    id: str = Field(default_factory=lambda: new_id("bdl"))
    asset_id: str
    items: tuple[Evidence, ...]
    limitations: tuple[Limitation, ...] = ()
    conflicts: tuple[Conflict, ...] = ()
    ts: datetime = Field(default_factory=utcnow)

    def ids(self) -> set[str]:
        return {e.id for e in self.items}


class ProposedStep(Strict):
    id: str = Field(default_factory=lambda: new_id("stp"))
    action_id: str                         # must be on the policy allowlist
    text: str
    evidence_ids: tuple[str, ...] = ()     # uncited steps are blocked by EVID-01
    touches: tuple[str, ...] = ()          # hazard tags, e.g. "coupling_guard"
    source_step: str = ""                  # the numbered manual step it is, e.g. "6.3 step 2, p. 5" (iteration 3, item G)


class Proposal(Strict):
    """FOREMAN output: one step at a time."""
    id: str = Field(default_factory=lambda: new_id("prp"))
    agent: Literal[AgentId.FOREMAN] = AgentId.FOREMAN
    bundle_id: str
    step: ProposedStep
    step_index: int = Field(ge=1)
    step_total: int = Field(ge=1)
    surfaced_conflicts: tuple[str, ...] = ()   # conflict ids shown to the technician
    ts: datetime = Field(default_factory=utcnow)


class Decision(str, Enum):
    ALLOW = "ALLOW"
    BLOCK = "BLOCK"
    REQUIRE_PREREQ = "REQUIRE_PREREQ"


class PolicyDecision(Strict):
    """WARDEN output. Only safety_guard.evaluate() creates these."""
    agent: Literal[AgentId.WARDEN] = AgentId.WARDEN
    proposal_id: str
    decision: Decision
    rule_ids: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    policy_suite: str
    policy_version: str
    ts: datetime = Field(default_factory=utcnow)


class Confirmation(Strict):
    """Pinch gate input."""
    agent: Literal[AgentId.PINCH] = AgentId.PINCH
    proposal_id: str
    gesture: Literal["pinch_hold", "key_fallback"]
    hold_ms: int = Field(ge=0)
    ts: datetime = Field(default_factory=utcnow)
