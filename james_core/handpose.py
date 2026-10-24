"""Hand poses from 21 MediaPipe points: how open the hand is, and which pose it is making.

Every measure is a ratio of distances inside the hand, so it does not change with how far the hand is from the
camera. The finger measure is fingertip-to-wrist over knuckle-to-wrist: about 1.9 for a straight finger, about
0.8 to 1.0 for a finger folded into a fist, and in between while it bends.

    openness(pts)        0.0 fist .. 1.0 open hand (the mean over the four fingers)
    PoseTracker          palm | point | pinch | peace, with hysteresis per finger and a short confirm time, so
                         a pose has to be seen for a moment before it counts and one noisy frame changes nothing
    synth(pose, ...)     a clean 21-point hand in any pose: the legend draws these, and the tests feed them in

No model call and no learning: geometry only, so it behaves the same on every machine.
"""
from __future__ import annotations

import math

import numpy as np

WRIST = 0
FINGERS = ((5, 6, 8), (9, 10, 12), (13, 14, 16), (17, 18, 20))     # (knuckle, middle joint, tip): index..pinky

FIST_RATIO = 1.05        # tip/knuckle distance ratio at or below this: the finger is fully folded (0)
OPEN_RATIO = 1.75        # at or above this: fully straight (1)
UP_ON, UP_OFF = 1.50, 1.38   # a finger counts as "up" above UP_ON and stays up until it drops below UP_OFF
DOWN = 1.30              # a finger counts as "down" (folded) below this
PINCH_MIN_INDEX = 1.12   # a pinch needs the index finger half out: in a fist the thumb also touches the index
PEACE_SPREAD = 0.22      # index and middle tips at least this far apart (x palm size) for a V
CONFIRM_S = {"palm": 0.10, "point": 0.12, "pinch": 0.05, "peace": 0.15}


def valid(pts) -> bool:
    return pts is not None and len(pts) >= 21


def palm_size(pts) -> float:
    return math.dist(pts[0], pts[9]) or 1.0


def palm_centre(pts) -> tuple[float, float]:
    """Wrist and the four knuckles: it hardly moves when the fingers open or close."""
    xs = [pts[i][0] for i in (0, 5, 9, 13, 17)]
    ys = [pts[i][1] for i in (0, 5, 9, 13, 17)]
    return sum(xs) / 5.0, sum(ys) / 5.0


def hand_angle(pts) -> float:
    """Direction wrist -> the middle of the four knuckles, degrees in image coordinates (an upright hand is about
    -90). Four knuckles, not one, so a single noisy point barely moves it."""
    kx = sum(pts[i][0] for i in (5, 9, 13, 17)) / 4.0
    ky = sum(pts[i][1] for i in (5, 9, 13, 17)) / 4.0
    return math.degrees(math.atan2(ky - pts[0][1], kx - pts[0][0]))


def ratios(pts) -> list[float]:
    w = pts[WRIST]
    return [math.dist(w, pts[t]) / max(math.dist(w, pts[m]), 1e-6) for m, _, t in FINGERS]


def openness(pts, fist: float = FIST_RATIO, open_: float = OPEN_RATIO) -> float:
    r = ratios(pts)
    return float(np.mean([min(1.0, max(0.0, (v - fist) / (open_ - fist))) for v in r]))


class PoseTracker:
    """palm (open, half open or fist), point, pinch or peace for one hand, frame by frame.

    A new pose must be seen for CONFIRM_S before it replaces the current one. `changed_at` is when the winning
    pose was first seen, so a caller can undo what the hand did while it was changing shape."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.pose: str | None = None
        self.since = 0.0                  # when the current pose was confirmed
        self.changed_at = 0.0             # when the current pose was first seen (before its confirm time)
        self._cand, self._cand_t = None, 0.0
        self._up = [False] * 4
        self._carried = False             # a pinch that began in a fist: ignored until it ends
        self.raw: str | None = None

    def classify(self, pts, pinching: bool) -> str:
        r = ratios(pts)
        for i, v in enumerate(r):
            self._up[i] = v > (UP_OFF if self._up[i] else UP_ON)
        down = [v < DOWN for v in r]
        up = self._up
        # opening a fist: LENS still says "pinch" (its release needs a wider gap) while the index is already out.
        # A pinch that began with the index folded is not a pinch until it is released.
        self._carried = pinching and (self._carried or r[0] <= PINCH_MIN_INDEX)
        if pinching and not self._carried:
            return "pinch"
        if up[0] and up[1] and down[2] and down[3] and math.dist(pts[8], pts[12]) > PEACE_SPREAD * palm_size(pts):
            return "peace"
        if up[0] and not up[1] and down[1] and down[2] and down[3]:
            return "point"
        return "palm"

    @property
    def pending(self) -> str | None:
        """A different pose seen but not yet confirmed (None if there is none)."""
        return self._cand

    def update(self, pts, pinching: bool, now: float) -> str | None:
        """pts: 21 points or None. Without landmarks (a fingertip-only source) the pose is pinch or point,
        straight away: there is nothing to confirm."""
        if not valid(pts):
            raw = "pinch" if pinching else "point"
            if raw != self.pose:
                self.pose, self.since, self.changed_at = raw, now, now
            self.raw, self._cand = raw, None
            return self.pose
        raw = self.raw = self.classify(pts, pinching)
        if raw == self.pose:
            self._cand = None
            return self.pose
        if raw != self._cand:
            self._cand, self._cand_t = raw, now
        if self.pose is None or now - self._cand_t >= CONFIRM_S[raw] - 1e-9:
            self.pose, self.since, self.changed_at, self._cand = raw, now, self._cand_t, None
        return self.pose


# ---------------- clean hands, for the legend and the tests ----------------

# finger: knuckle position (x, y) in palm units (wrist at 0, middle knuckle at (0, -1)), lean in degrees,
# segment lengths
_HAND = (((-0.30, -0.93), -8, (0.42, 0.25, 0.20)),
         ((-0.05, -1.00), 0, (0.47, 0.29, 0.20)),
         ((0.18, -0.95), 7, (0.43, 0.27, 0.19)),
         ((0.38, -0.83), 15, (0.33, 0.20, 0.17)))

POSES = {   # curl per finger (0 straight .. 1 folded), spread, thumb: out | across | pinch
    "open": ((0, 0, 0, 0), 1.35, "out"),
    "half": ((0.5, 0.5, 0.5, 0.5), 1.0, "out"),
    "fist": ((1, 1, 1, 1), 0.8, "across"),
    "point": ((0, 1, 1, 1), 0.8, "across"),
    "peace": ((0, 0, 1, 1), 2.2, "across"),
    "pinch": ((0.45, 0.55, 0.65, 0.75), 1.0, "pinch"),
}


def _finger(knuckle, lean, lengths, curl, spread):
    """A finger bending towards the camera, seen from the front: the bent parts get shorter on screen."""
    pts = [np.array(knuckle, float)]
    d = math.radians(lean * spread)
    up = np.array([math.sin(d), -math.cos(d)])
    phi = 0.0
    for L, k in zip(lengths, (85, 100, 70)):
        phi += math.radians(k * curl)
        pts.append(pts[-1] + up * L * math.cos(phi))
    return pts


def synth(pose: str = "open", wrist=(0.0, 0.0), size: float = 1.0, angle: float = 0.0) -> list[tuple[int, int]]:
    """21 points of a right hand, palm to the camera, fingers up, in pose `pose` (see POSES), rotated by `angle`
    degrees (clockwise on screen), with the wrist at `wrist` and the wrist-to-middle-knuckle length `size`."""
    curls, spread, thumb = POSES[pose]
    pts = [np.zeros(2)] * 21
    pts[0] = np.array([0.0, 0.0])
    for (knuckle, lean, lengths), c, (m, p, t) in zip(_HAND, curls, FINGERS):
        f = _finger(knuckle, lean, lengths, c, spread)
        pts[m], pts[p], pts[p + 1], pts[t] = f
    pts[1] = np.array([-0.25, -0.22])
    pts[2] = np.array([-0.45, -0.45])
    if thumb == "out":
        pts[3], pts[4] = np.array([-0.62, -0.64]), np.array([-0.76, -0.82])
    elif thumb == "across":
        pts[3], pts[4] = np.array([-0.38, -0.66]), np.array([-0.12, -0.70])
    else:                                                 # pinch: the thumb tip meets the index tip
        tip = pts[8]
        pts[4] = tip + np.array([-0.04, 0.03])
        pts[3] = (pts[2] + pts[4]) / 2 + np.array([-0.08, 0.0])
    a = math.radians(angle)
    rot = np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]])
    out = []
    for p in pts:
        q = rot @ p * size + np.asarray(wrist, float)
        out.append((int(round(q[0])), int(round(q[1]))))
    return out
