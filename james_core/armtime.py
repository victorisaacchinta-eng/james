"""ARM TIME: a gentle guard against tired arms ("gorilla arm") when driving JAMES by hand.

A heuristic, not a validated fatigue model. It counts arm load while a hand drives input, weighted by how
high the wrist sits in the frame, and lets the load recover while the hand rests (out of view). When the load
crosses ARM_NUDGE_S, JAMES suggests voice once; a real rest re-arms the nudge.

Why: people using mid-air gestures report tired arms within minutes, and fatigue research (Consumed
Endurance, NICER) recommends breaks and switching from gesture to speech as effort builds. Sources are in
docs/ITERATIONS.md, iteration 10. Weights are starting values to tune on the Mac, not measured constants.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

NUDGE_S = 45.0          # load (weighted seconds) that triggers the voice suggestion
RECOVER_RATE = 2.0      # load drains this many seconds per second of rest
REST_S = 3.0            # a rest this long (no hand) re-arms the nudge once the load is below half
HIGH, MID = 0.40, 0.70  # wrist above HIGH of the frame height counts 1.5x, above MID 1.0x, lower 0.5x
MAX_DT = 0.5            # longer gaps between frames (a stall) never count as load
LOAD_CAP = 1.5          # load never exceeds 1.5x NUDGE_S, so recovery after an ignored nudge stays bounded


def weight(wrist_y: float, frame_h: float) -> float:
    f = wrist_y / max(1.0, frame_h)
    return 1.5 if f < HIGH else 1.0 if f < MID else 0.5


@dataclass
class ArmReport:
    load: float = 0.0
    nudge: bool = False         # True on the one frame the suggestion should be shown
    up_s: float = 0.0           # total seconds a hand drove input this session
    longest_s: float = 0.0      # longest continuous stretch
    nudges: int = 0


class ArmTime:
    def __init__(self, nudge_s: float = NUDGE_S, recover_rate: float = RECOVER_RATE, rest_s: float = REST_S):
        self.nudge_s, self.recover_rate, self.rest_s = nudge_s, recover_rate, rest_s
        self.load = 0.0
        self.up_s = 0.0
        self.longest_s = 0.0
        self.nudges = 0
        self._stretch = 0.0
        self._armed = True
        self._t: Optional[float] = None
        self._rest_since: Optional[float] = None

    def update(self, t: float, wrist_y: Optional[float], frame_h: float, quiet: bool = False) -> ArmReport:
        """wrist_y: the driving hand's wrist y in pixels, or None when no hand drives. quiet: True while voice is
        already in use, or while ATLAS or CANTUS owns the window: load still counts, no nudge there (a game or a
        3D view is not the moment). A nudge that comes due during them shows once you are back."""
        dt = 0.0 if self._t is None else min(MAX_DT, max(0.0, t - self._t))
        self._t = t
        rep = ArmReport()
        if wrist_y is not None:
            self._rest_since = None
            self.load = min(self.nudge_s * LOAD_CAP, self.load + dt * weight(wrist_y, frame_h))
            self.up_s += dt
            self._stretch += dt
            self.longest_s = max(self.longest_s, self._stretch)
            if self._armed and not quiet and self.load >= self.nudge_s:
                self._armed, rep.nudge = False, True
                self.nudges += 1
        else:
            self._stretch = 0.0
            self._rest_since = self._rest_since if self._rest_since is not None else t
            self.load = max(0.0, self.load - dt * self.recover_rate)
            if t - self._rest_since >= self.rest_s and self.load < self.nudge_s / 2:
                self._armed = True
        rep.load, rep.up_s, rep.longest_s, rep.nudges = round(self.load, 2), round(self.up_s, 1), \
            round(self.longest_s, 1), self.nudges
        return rep

    def summary(self) -> str:
        return f"ARM up={self.up_s:.0f}s longest={self.longest_s:.0f}s nudges={self.nudges}"
