from datetime import datetime, timedelta, timezone

from james_core.evidence import build_bundle
from james_core.ledger import Ledger
from james_core.safety_guard import load_policy
from james_core.schemas import (AgentId, Confirmation, Evidence, EvidenceKind, Intent,
                                Proposal, ProposedStep, Target)
from james_core.session import (EvidenceReady, GatherEvidence, IntentParsed, ProposalMade,
                                Session, TargetSeen, Unlock)


class Clock:
    def __init__(self):
        self.t = datetime(2026, 9, 26, 2, 0, tzinfo=timezone.utc)
    def __call__(self):
        return self.t
    def advance(self, s):
        self.t += timedelta(seconds=s)


def manual(asset="P-3", **kw):
    return Evidence(agent=AgentId.PAGE, kind=EvidenceKind.MANUAL, asset_id=asset,
                    claim="Bearing check procedure", ref="manual:pump-oem.pdf#p42", confidence=0.9, **kw)

def sensor(asset="P-3", **kw):
    return Evidence(agent=AgentId.PULSE, kind=EvidenceKind.SENSOR, asset_id=asset,
                    claim="vibration RMS 7.1 mm/s, last 2 h, baseline 2.8",
                    ref="log:p3_vibration.csv[00:00..02:00]", confidence=0.8, **kw)

def memory(asset="P-3", **kw):
    return Evidence(agent=AgentId.LEDGER, kind=EvidenceKind.MEMORY, asset_id=asset,
                    claim="Bearing replaced after similar vibration", ref="job:018", confidence=0.7, **kw)


def session_at_evidence(clock=None, items=None):
    clock = clock or Clock()
    s = Session(Ledger(), load_policy(), clock=clock)
    s.handle(Unlock(presenter_id="owner"))
    s.handle(TargetSeen(target=Target(asset_id="P-3", source="asset_tag", confidence=0.97)))
    s.handle(IntentParsed(intent=Intent(asset_id="P-3", request="diagnose", symptoms=("vibration",),
                                        transcript="Pump 3 is vibrating more than usual", confidence=0.9)))
    s.handle(GatherEvidence())
    b = build_bundle("P-3", items if items is not None else [manual(), sensor(), memory()])
    s.handle(EvidenceReady(bundle=b))
    return s, b, clock


def propose(s, b, text="Remove the coupling guard", touches=("coupling_guard",), action="SHOW_STEP",
            cite=True, surfaced=()):
    step = ProposedStep(action_id=action, text=text, touches=touches,
                        evidence_ids=tuple(e.id for e in b.items) if cite else ())
    p = Proposal(bundle_id=b.id, step=step, step_index=2, step_total=5, surfaced_conflicts=surfaced)
    return s.handle(ProposalMade(proposal=p)), p


def pinch(p, hold=800):
    return Confirmation(proposal_id=p.id, gesture="pinch_hold", hold_ms=hold)
