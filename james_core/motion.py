"""Hand motion helpers for LENS: smoothing, the pre-pinch aim point, and swipes.

Three problems a webcam hand tracker has, and the fix for each:

1. Jitter. Raw MediaPipe landmarks shake by a few pixels even when the hand is still, so the cursor
   trembles and the pinch ratio flickers near its threshold. `OneEuro` is the 1-euro filter
   (Casiez, Roussel, Vogel, CHI 2012): heavy smoothing when the hand is slow, almost none when it
   is fast, so a still hand is steady and a moving hand does not lag.
2. Pinch drift. Closing thumb and index pulls the fingertip a few pixels away from where the user
   was aiming. `Trail` remembers where the fingertip was just before the pinch started, so a
   pinch selects what the user pointed at, not what the finger slid onto.
3. Fast motion. At 10 to 15 FPS a quick flick moves 100+ px between two frames. `Swipe` looks at
   the path over a short window, not at one frame, so a deliberate swipe is caught and a slow
   drift is not.

No model, no network: plain NumPy, deterministic, unit-tested in tests/test_motion.py."""
from __future__ import annotations

import math
from collections import deque
from typing import Optional

import numpy as np


class OneEuro:
    """1-euro filter over an array of points (any shape ending in x, y). Time in seconds.

    min_cutoff: smoothing of a still hand (Hz; lower = steadier, more lag at rest).
    beta: how fast smoothing falls away with speed (higher = less lag on fast moves).
    """

    def __init__(self, min_cutoff: float = 1.0, beta: float = 0.01, d_cutoff: float = 1.0):
        self.min_cutoff, self.beta, self.d_cutoff = min_cutoff, beta, d_cutoff
        self.x: Optional[np.ndarray] = None
        self.dx: Optional[np.ndarray] = None
        self.t: Optional[float] = None

    @staticmethod
    def _alpha(cutoff, dt: float):
        tau = 1.0 / (2 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def reset(self):
        self.x = self.dx = self.t = None

    def __call__(self, x, t: float) -> np.ndarray:
        x = np.asarray(x, dtype=float)
        if self.x is None or self.x.shape != x.shape:
            self.x, self.dx, self.t = x.copy(), np.zeros_like(x), t
            return x.copy()
        dt = max(t - self.t, 1e-3)
        self.t = t
        a_d = self._alpha(self.d_cutoff, dt)
        self.dx = a_d * ((x - self.x) / dt) + (1 - a_d) * self.dx
        speed = np.linalg.norm(self.dx, axis=-1, keepdims=True)       # px/s per point
        a = self._alpha(self.min_cutoff + self.beta * speed, dt)
        self.x = a * x + (1 - a) * self.x
        return self.x.copy()

    @property
    def speed(self) -> np.ndarray:
        """Filtered speed of each point, px/s (zeros before the second sample)."""
        if self.dx is None:
            return np.zeros(0)
        return np.linalg.norm(self.dx, axis=-1)


class HandSmoother:
    """One OneEuro per tracked hand. MediaPipe does not keep hand order stable between frames, so each
    new hand is matched to the track whose wrist is nearest; a hand that jumps further than `max_jump`
    px, or a track unseen for `forget_s`, starts fresh (no smoothing across two different hands)."""

    def __init__(self, min_cutoff=1.0, beta=0.01, max_jump=180.0, forget_s=0.3):
        self.min_cutoff, self.beta, self.max_jump, self.forget_s = min_cutoff, beta, max_jump, forget_s
        self.tracks: list[dict] = []          # {"f": OneEuro, "wrist": (x, y), "seen": t}

    def __call__(self, hands: list, t: float) -> list:
        self.tracks = [k for k in self.tracks if t - k["seen"] <= self.forget_s]
        free = list(range(len(self.tracks)))
        out = []
        for pts in hands:
            arr = np.asarray(pts, dtype=float)
            best, best_d = None, self.max_jump
            for i in free:
                d = math.dist(self.tracks[i]["wrist"], arr[0])
                if d < best_d:
                    best, best_d = i, d
            if best is None:
                self.tracks.append({"f": OneEuro(self.min_cutoff, self.beta), "wrist": tuple(arr[0]), "seen": t})
                best = len(self.tracks) - 1
            else:
                free.remove(best)
            k = self.tracks[best]
            sm = k["f"](arr, t)
            k["wrist"], k["seen"] = tuple(arr[0]), t
            out.append([(int(round(p[0])), int(round(p[1]))) for p in sm])
        return out


class Trail:
    """Recent fingertip positions with timestamps, for the pre-pinch aim and for swipes."""

    def __init__(self, keep_s: float = 0.6, max_jump: float = 220.0):
        self.keep_s, self.max_jump = keep_s, max_jump
        self.pts: deque = deque()

    def push(self, t: float, p) -> None:
        if p is None:
            self.pts.clear()
            return
        if self.pts and math.dist(self.pts[-1][1], p) > self.max_jump:
            self.pts.clear()                  # a different hand took over: never mix two hands' paths
        self.pts.append((t, (float(p[0]), float(p[1]))))
        while self.pts and t - self.pts[0][0] > self.keep_s:
            self.pts.popleft()

    def at(self, t: float) -> Optional[tuple]:
        """Position at time t (the newest sample not after t), or the oldest kept sample."""
        if not self.pts:
            return None
        best = self.pts[0][1]
        for ts, p in self.pts:
            if ts <= t:
                best = p
            else:
                break
        return best

    def window(self, since: float) -> list:
        return [(ts, p) for ts, p in self.pts if ts >= since]


class Swipe:
    """A swipe is a fast, mostly straight move of at least `min_palms` palm widths inside `window_s`.
    Returns 'left' | 'right' | 'up' | 'down' once per swipe (raw camera coordinates), then waits
    `cooldown_s` so the return move of the hand is not read as a second swipe."""

    def __init__(self, window_s=0.28, min_palms=1.6, straightness=0.55, cooldown_s=0.7):
        self.window_s, self.min_palms, self.straightness, self.cooldown_s = window_s, min_palms, straightness, cooldown_s
        self.until = -1.0

    def __call__(self, trail: Trail, t: float, palm: float) -> Optional[str]:
        if t < self.until or palm <= 0:
            return None
        path = trail.window(t - self.window_s)
        if len(path) < 3:
            return None
        (_, p0), (_, p1) = path[0], path[-1]
        dx, dy = p1[0] - p0[0], p1[1] - p0[1]
        main, cross = (abs(dx), abs(dy)) if abs(dx) >= abs(dy) else (abs(dy), abs(dx))
        if main < self.min_palms * palm or cross > self.straightness * main:
            return None
        self.until = t + self.cooldown_s
        trail.pts.clear()
        if abs(dx) >= abs(dy):
            return "right" if dx > 0 else "left"
        return "down" if dy > 0 else "up"


def point_segment_dist(q, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Distance from point q to each segment a[i]-b[i] (arrays of shape (N, 2))."""
    q = np.asarray(q, dtype=float)
    ab = b - a
    denom = np.einsum("ij,ij->i", ab, ab)
    denom[denom == 0] = 1.0
    s = np.clip(np.einsum("ij,ij->i", q - a, ab) / denom, 0.0, 1.0)
    closest = a + ab * s[:, None]
    return np.linalg.norm(closest - q, axis=1)


def swept_hit(p0, p1, center, radius: float) -> bool:
    """Did the fingertip pass within `radius` of `center` anywhere on its way from p0 to p1? Catches a
    fast flick that jumps over a small target between two frames (a per-frame test would miss it)."""
    if p0 is None:
        p0 = p1
    a = np.asarray([p0], dtype=float)
    b = np.asarray([p1], dtype=float)
    return bool(point_segment_dist(center, a, b)[0] <= radius)
