"""The session state machine.

LOCKED -> TARGET_PENDING -> TARGET_CONFIRMED -> INTENT_CAPTURED
-> EVIDENCE_GATHERING -> PROPOSAL_READY -> SAFETY_BLOCKED | AWAITING_CONFIRMATION
-> CONFIRMED -> LOGGED -> ESCALATED | COMPLETE

Rules that hold everywhere:
  * Only typed events are accepted. A string from a model is refused and logged.
  * WARDEN is called by the session itself. No event can inject an ALLOW.
  * A pinch only counts on the current, unexpired, ALLOWed proposal, held long enough.
  * WARDEN runs again at the moment of confirmation, with the latest facts.
  * Idle time or a restart returns the session to LOCKED and clears confirmations.
  * Every accepted or refused event is written to the ledger.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum
from typing import Callable, Optional, Union

from pydantic import Field

from .actions import ActionRegistry
from .ledger import Ledger
from .safety_guard import Policy, evaluate
from .schemas import (Confirmation, Decision, EvidenceBundle, Intent, PolicyDecision,
                      Proposal, Strict, Target, new_id, utcnow)


class State(str, Enum):
    LOCKED = "LOCKED"
    TARGET_PENDING = "TARGET_PENDING"
    TARGET_CONFIRMED = "TARGET_CONFIRMED"
    INTENT_CAPTURED = "INTENT_CAPTURED"
    EVIDENCE_GATHERING = "EVIDENCE_GATHERING"
    PROPOSAL_READY = "PROPOSAL_READY"
    SAFETY_BLOCKED = "SAFETY_BLOCKED"
    AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"
    CONFIRMED = "CONFIRMED"
    LOGGED = "LOGGED"
    ESCALATED = "ESCALATED"
    COMPLETE = "COMPLETE"


# ---- typed events ----
class Unlock(Strict):
    presenter_id: str

class Lock(Strict):
    reason: str = "manual"

class TargetSeen(Strict):
    target: Target

class TargetConfirmedByTechnician(Strict):
    target_id: str

class IntentParsed(Strict):
    intent: Intent

class GatherEvidence(Strict):
    pass

class EvidenceReady(Strict):
    bundle: EvidenceBundle

class ProposalMade(Strict):
    proposal: Proposal

class FactsUpdated(Strict):
    facts: dict[str, Union[bool, float, int]] = Field(default_factory=dict)

class Pinch(Strict):
    confirmation: Confirmation

class NextStep(Strict):
    pass

class ObservationRecorded(Strict):
    """The technician's yes/no answer to a question FOREMAN asked (iteration 2, P0-E). Evidence, not approval."""
    question_id: str
    question: str
    answer: bool

class Escalate(Strict):
    reason: str

class Complete(Strict):
    pass

class Skip(Strict):
    reason: str = "presenter skipped"


EVENTS = (Unlock, Lock, TargetSeen, TargetConfirmedByTechnician, IntentParsed, GatherEvidence,
          EvidenceReady, ProposalMade, FactsUpdated, Pinch, NextStep, Escalate, Complete, Skip, ObservationRecorded)


class Refused(Exception):
    pass


class Session:
    TARGET_MIN = 0.85       # below this, the technician must confirm the target
    INTENT_MIN = 0.60       # below this, ECHO asks again
    PINCH_HOLD_MS = 600     # a pinch shorter than this is not a confirmation
    STALE_AFTER = timedelta(seconds=90)
    IDLE_LOCK = timedelta(seconds=120)

    def __init__(self, ledger: Ledger, policy: Policy, session_id: Optional[str] = None,
                 clock: Callable[[], datetime] = utcnow, actions: Optional[ActionRegistry] = None):
        self.ledger, self.policy, self.clock = ledger, policy, clock
        self.id = session_id or new_id("ses")
        self.actions = actions
        self.state = State.LOCKED
        self._reset()
        self.last_activity = clock()

    TERMINAL = {"LOCKED", "COMPLETE", "ESCALATED"}

    @classmethod
    def restore(cls, ledger: Ledger, policy: Policy, session_id: str, **kw) -> "Session":
        """After a crash or restart: always LOCKED, nothing carried over but the log.

        A restart is a CANCELLATION of whatever was in progress (iteration 2, ST-12): no pending question,
        proposal or confirmation survives, and the log says exactly what was cancelled, so nobody later
        mistakes an unfinished job for a finished one. Recovery means starting a new job."""
        s = cls(ledger, policy, session_id=session_id, **kw)
        last_state, pending = None, None
        for e in ledger.events(session_id):
            if not e["accepted"]:
                continue
            st = e["payload"].get("state")
            if st:
                last_state = st
            if e["kind"] == "proposal_ready":
                pending = e["payload"].get("proposal", {}).get("step", {}).get("text")
            if e["kind"] in ("confirmed", "skipped", "next_step", "locked", "auto_locked"):
                pending = None
        if last_state and last_state not in cls.TERMINAL:
            ledger.append(s.id, "cancelled_by_restart", {"state": s.state.value, "was": last_state,
                                                         "pending_step": pending})
        ledger.append(s.id, "restored", {"state": s.state.value})
        return s

    def _reset(self) -> None:
        self.target: Optional[Target] = None
        self.pending_target: Optional[Target] = None
        self.intent: Optional[Intent] = None
        self.bundle: Optional[EvidenceBundle] = None
        self.proposal: Optional[Proposal] = None
        self.decision: Optional[PolicyDecision] = None
        self.facts: dict = {}
        self.proposal_at: Optional[datetime] = None

    # ---- entry point ----
    def handle(self, event) -> State:
        name = type(event).__name__
        if not isinstance(event, EVENTS):
            self.ledger.append(self.id, "untyped_input", {"reason": "not a typed event", "got": name,
                                                          "state": self.state.value}, accepted=False)
            raise Refused(f"Refused untyped input ({name}). Nothing moves on free text.")
        self._idle_check()
        try:
            new_state = getattr(self, f"_on_{name}")(event)
        except Refused as r:
            self.ledger.append(self.id, name, {"reason": str(r), "state": self.state.value}, accepted=False)
            raise
        self.last_activity = self.clock()
        return new_state

    def _go(self, state: State, kind: str, payload: Optional[dict] = None) -> State:
        self.state = state
        self.ledger.append(self.id, kind, {"state": state.value, **(payload or {})})
        return state

    def _need(self, *states: State) -> None:
        if self.state not in states:
            raise Refused(f"Not allowed in {self.state.value}")

    def _idle_check(self) -> None:
        if self.state != State.LOCKED and self.clock() - self.last_activity > self.IDLE_LOCK:
            self._reset()
            self._go(State.LOCKED, "auto_locked", {"reason": "idle"})

    # ---- handlers ----
    def _on_Lock(self, e: Lock) -> State:
        self._reset()
        return self._go(State.LOCKED, "locked", {"reason": e.reason})

    def _on_Unlock(self, e: Unlock) -> State:
        self._need(State.LOCKED)
        return self._go(State.TARGET_PENDING, "unlocked", {"presenter_id": e.presenter_id})

    def _on_TargetSeen(self, e: TargetSeen) -> State:
        self._need(State.TARGET_PENDING)
        t = e.target
        if t.source == "asset_tag" and t.confidence >= self.TARGET_MIN:
            self.target = t
            return self._go(State.TARGET_CONFIRMED, "target_confirmed", {"target": t})
        self.pending_target = t
        return self._go(State.TARGET_PENDING, "target_needs_confirmation", {"target": t})

    def _on_TargetConfirmedByTechnician(self, e: TargetConfirmedByTechnician) -> State:
        self._need(State.TARGET_PENDING)
        if not self.pending_target or self.pending_target.id != e.target_id:
            raise Refused("No matching target waiting for confirmation")
        self.target = self.pending_target.model_copy(update={"source": "technician"})
        return self._go(State.TARGET_CONFIRMED, "target_confirmed", {"target": self.target})

    def _on_IntentParsed(self, e: IntentParsed) -> State:
        self._need(State.TARGET_CONFIRMED)
        i = e.intent
        if i.asset_id != self.target.asset_id:
            raise Refused(f"Intent is about {i.asset_id}, but the confirmed target is {self.target.asset_id}")
        if i.confidence < self.INTENT_MIN:
            raise Refused("Unclear voice: ask the technician to repeat")
        self.intent = i
        return self._go(State.INTENT_CAPTURED, "intent_captured", {"intent": i})

    def _on_GatherEvidence(self, e: GatherEvidence) -> State:
        self._need(State.INTENT_CAPTURED)
        return self._go(State.EVIDENCE_GATHERING, "evidence_gathering")

    def _on_EvidenceReady(self, e: EvidenceReady) -> State:
        self._need(State.EVIDENCE_GATHERING)
        if e.bundle.asset_id != self.target.asset_id:
            raise Refused("Evidence is for a different machine")
        self.bundle = e.bundle
        return self._go(State.EVIDENCE_GATHERING, "evidence_ready", {
            "bundle_id": e.bundle.id,
            "sources": [x.ref for x in e.bundle.items],
            "limitations": [l.kind.value for l in e.bundle.limitations],
            "conflicts": [c.detail for c in e.bundle.conflicts]})

    def _on_ObservationRecorded(self, e: ObservationRecorded) -> State:
        self._need(State.EVIDENCE_GATHERING)
        if self.bundle is not None:
            raise Refused("Evidence is already settled for this job; start a new job to change it")
        return self._go(State.EVIDENCE_GATHERING, "observation", {"question_id": e.question_id,
                                                                  "question": e.question, "answer": e.answer})

    def _on_ProposalMade(self, e: ProposalMade) -> State:
        self._need(State.EVIDENCE_GATHERING)
        p = e.proposal
        if not self.bundle or p.bundle_id != self.bundle.id:
            raise Refused("Proposal does not match the current evidence bundle")
        unknown = set(p.step.evidence_ids) - self.bundle.ids()
        if unknown:
            raise Refused(f"Proposal cites evidence that isn't in the bundle: {sorted(unknown)}")
        hidden = {c.id for c in self.bundle.conflicts} - set(p.surfaced_conflicts)
        if hidden:
            raise Refused("Proposal hides a disagreement between sources; surface it first")
        self.proposal, self.proposal_at = p, self.clock()
        self._go(State.PROPOSAL_READY, "proposal_ready", {"proposal": p})
        return self._run_warden()

    def _run_warden(self, kind: str = "safety_decision") -> State:
        self.decision = evaluate(self.proposal, self.facts, self.policy)
        payload = self.decision.model_dump(mode="json")
        if self.decision.decision == Decision.ALLOW:
            return self._go(State.AWAITING_CONFIRMATION, kind, payload)
        return self._go(State.SAFETY_BLOCKED, kind, payload)

    def _on_FactsUpdated(self, e: FactsUpdated) -> State:
        if self.state == State.LOCKED:
            raise Refused("Locked")
        self.facts.update(e.facts)
        self.ledger.append(self.id, "facts_updated", {"facts": e.facts, "state": self.state.value})
        if self.state in (State.SAFETY_BLOCKED, State.AWAITING_CONFIRMATION):
            return self._run_warden()
        return self.state

    def _on_Pinch(self, e: Pinch) -> State:
        if self.state != State.AWAITING_CONFIRMATION:
            raise Refused(f"No step is waiting for confirmation ({self.state.value})")
        c = e.confirmation
        if c.proposal_id != self.proposal.id:
            raise Refused("Pinch is for a different or old proposal")
        if c.hold_ms < self.PINCH_HOLD_MS:
            raise Refused(f"Pinch held {c.hold_ms} ms; needs {self.PINCH_HOLD_MS} ms")
        if self.clock() - self.proposal_at > self.STALE_AFTER:
            raise Refused("Proposal is stale; re-check before confirming")
        if self._run_warden("safety_recheck") != State.AWAITING_CONFIRMATION:   # facts may have changed
            raise Refused("WARDEN no longer allows this step")
        self._go(State.CONFIRMED, "confirmed_pending_log", {"proposal_id": self.proposal.id})
        ran = self.actions.run(self.proposal.step) if self.actions else False
        return self._go(State.LOGGED, "confirmed", {
            "proposal_id": self.proposal.id, "step_text": self.proposal.step.text,
            "action_id": self.proposal.step.action_id, "action_ran": ran,
            "sources": [x.ref for x in self.bundle.items if x.id in self.proposal.step.evidence_ids],
            "policy": f"{self.decision.policy_suite} v{self.decision.policy_version}",
            "hold_ms": c.hold_ms})

    def _on_NextStep(self, e: NextStep) -> State:
        self._need(State.LOGGED)
        self.proposal = self.decision = self.proposal_at = None
        return self._go(State.EVIDENCE_GATHERING, "next_step")

    def _on_Escalate(self, e: Escalate) -> State:
        if self.state in (State.LOCKED, State.COMPLETE, State.ESCALATED):
            raise Refused(f"Cannot escalate from {self.state.value}")
        return self._go(State.ESCALATED, "escalated", {
            "reason": e.reason,
            "asset_id": self.target.asset_id if self.target else None,
            "sources": [x.ref for x in self.bundle.items] if self.bundle else [],
            "limitations": [l.detail for l in self.bundle.limitations] if self.bundle else [],
            "last_decision": self.decision.model_dump(mode="json") if self.decision else None})

    def _on_Skip(self, e: Skip) -> State:
        """Skipping is allowed and logged. It never confirms anything."""
        self._need(State.AWAITING_CONFIRMATION, State.SAFETY_BLOCKED)
        skipped = self.proposal
        self.proposal = self.decision = self.proposal_at = None
        return self._go(State.EVIDENCE_GATHERING, "skipped",
                        {"reason": e.reason, "step_text": skipped.step.text, "proposal_id": skipped.id})

    def _on_Complete(self, e: Complete) -> State:
        self._need(State.LOGGED)
        return self._go(State.COMPLETE, "complete")
