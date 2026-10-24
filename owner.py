"""OWNER: take input only from the hand that wears the enrolled ring.

Several hands can be in view (people behind the operator, a colleague pointing). JAMES tracks up
to config.MAX_HANDS of them and lets exactly one drive the cursor, pinch and gestures: the hand
with the ring on its ring finger.

How it works (no images are stored, only a few colour numbers):
  * Enrol (press K): hold the ring hand up, fingers spread, for 3 s. JAMES samples the colour of
    the ring band on the ring finger (landmarks 13 -> 14, the first finger bone) and of the bare
    skin just above it on the same finger, in CIE Lab. It stores the ring colour and the
    ring-minus-skin contrast, which is what separates "a ring" from "a finger" under changing light.
  * Find: each frame, every hand is scored against that signature. A hand must match in at least
    5 of 8 consecutive frames before it becomes the owner (one lucky frame is not enough).
  * Follow: once found, the owner hand is followed by position, so it keeps control while the ring
    is hidden (a fist, the V sign, the palm turned). If it leaves view for LOST_MS, JAMES looks again.

This is a visual filter that keeps background hands out. It is NOT security authentication: a
similar ring on another hand can pass. Use the lock (U) for anything that must be protected.

OWNER KEY (KeyFilter, config.OWNER_MODE = "key", the default since 2026-09-26): the ring hid itself in
the V sign and a fist. Instead, a printed ArUco marker (DICT_4X4_50, id config.OWNER_KEY_ID) worn on the
watch strap or the back of the hand is the key. The hand whose wrist/forearm/back-of-hand line is
nearest the key, in 3 of 5 frames, becomes the owner, and is then followed by position like the ring
hand (so palm-facing gestures that hide the key keep working). If the key shows up on a different hand,
control moves to that hand. Same caveat: anyone holding a copy of the key passes. Not security."""
from __future__ import annotations

import json
import math
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

WRIST, MID_MCP, MID_PIP, RING_MCP, RING_PIP, RING_TIP = 0, 9, 10, 13, 14, 16
L_WEIGHT = 0.5              # lightness changes most with lighting; colour (a, b) counts more
MATCH_SCORE = 0.6
VOTES_NEEDED, VOTE_WINDOW = 5, 8
LOST_MS = 1500
ENROLL_MS = 3000
MIN_SAMPLES = 12


def _palm(pts) -> float:
    return math.dist(pts[WRIST], pts[MID_MCP]) or 1.0


def _quad(p0, p1, t0, t1, half_w):
    p0, p1 = np.asarray(p0, float), np.asarray(p1, float)
    d = p1 - p0
    n = np.linalg.norm(d)
    if n < 1e-6:
        return None
    u = d / n
    v = np.array([-u[1], u[0]])
    a, b = p0 + d * t0, p0 + d * t1
    return np.array([a + v * half_w, b + v * half_w, b - v * half_w, a - v * half_w], np.float32)


def patch_lab(img: np.ndarray, quad) -> tuple[np.ndarray | None, int]:
    """Median CIE Lab colour inside a quad (L 0..100, a/b centred on 0), and the pixel count."""
    if quad is None:
        return None, 0
    H, W = img.shape[:2]
    x0, y0 = np.floor(quad.min(0)).astype(int)
    x1, y1 = np.ceil(quad.max(0)).astype(int) + 1
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(W, x1), min(H, y1)
    if x1 - x0 < 2 or y1 - y0 < 2:
        return None, 0
    roi = img[y0:y1, x0:x1]
    mask = np.zeros(roi.shape[:2], np.uint8)
    cv2.fillConvexPoly(mask, np.round(quad - [x0, y0]).astype(np.int32), 255)
    lab = cv2.cvtColor(roi, cv2.COLOR_BGR2LAB)[mask > 0].astype(np.float32)
    if len(lab) == 0:
        return None, 0
    med = np.median(lab, axis=0)
    return np.array([med[0] * 100 / 255, med[1] - 128, med[2] - 128], np.float32), len(lab)


def ring_visible(pts) -> bool:
    """The ring finger is roughly straight, so its first bone (where a ring sits) faces the camera."""
    return math.dist(pts[WRIST], pts[RING_TIP]) > math.dist(pts[WRIST], pts[RING_PIP]) * 1.1


def features(img, pts) -> dict | None:
    """Ring-band colour, bare-skin colour on the same finger, and their difference."""
    palm = _palm(pts)
    hw = max(2.0, 0.07 * palm)
    ring, n1 = patch_lab(img, _quad(pts[RING_MCP], pts[RING_PIP], 0.22, 0.5, hw))
    skin, n2 = patch_lab(img, _quad(pts[RING_MCP], pts[RING_PIP], 0.66, 0.92, hw))
    mid, n3 = patch_lab(img, _quad(pts[MID_MCP], pts[MID_PIP], 0.22, 0.5, hw))       # same spot, next finger
    if ring is None or n1 < 8 or (n2 < 8 and n3 < 8):
        return None
    refs = [s for s, n in ((skin, n2), (mid, n3)) if s is not None and n >= 8]
    skin_ref = np.median(np.stack(refs), axis=0)
    return {"ring": ring, "skin": skin_ref, "rel": ring - skin_ref}


def _w(v):
    v = np.asarray(v, np.float32).copy()
    v[0] *= L_WEIGHT
    return v


def score(feat: dict | None, sig: dict) -> float:
    """0..1: how much this finger looks like the enrolled ring finger."""
    if feat is None:
        return 0.0
    rel_r, rel_e = _w(feat["rel"]), _w(sig["rel"])
    mr, me = float(np.linalg.norm(rel_r)), float(np.linalg.norm(rel_e))
    if me < 1e-3 or mr < 1e-3:
        return 0.0
    cos = max(0.0, float(rel_r @ rel_e) / (mr * me))
    ratio = min(mr, me) / max(mr, me)
    d_abs = float(np.linalg.norm(_w(feat["ring"]) - _w(sig["ring"])))
    s = 0.4 * cos + 0.3 * ratio + 0.3 * max(0.0, 1 - d_abs / 30)
    if mr < max(5.0, 0.45 * me):              # no real contrast with the skin: that is a bare finger
        s *= 0.3
    return round(s, 3)


def signature(samples: list[dict], handed: list[str]) -> tuple[dict | None, str]:
    """Combine enrolment samples. Returns (signature, message)."""
    if len(samples) < MIN_SAMPLES:
        return None, f"Only {len(samples)} clear views of the ring finger. Hold the ring hand up, fingers spread, and try again."
    ring = np.median(np.stack([s["ring"] for s in samples]), axis=0)
    rel = np.median(np.stack([s["rel"] for s in samples]), axis=0)
    spread = float(np.median([np.linalg.norm(_w(s["rel"] - rel)) for s in samples]))
    contrast = float(np.linalg.norm(_w(rel)))
    if contrast < 5:
        return None, ("The ring looks too much like the skin in this light. Try brighter light, or turn the "
                      "hand so the band catches the light, then enrol again.")
    side = max(set(handed), key=handed.count) if handed else ""
    sig = {"ring": [round(float(x), 2) for x in ring], "rel": [round(float(x), 2) for x in rel],
           "spread": round(spread, 2), "contrast": round(contrast, 2), "hand": side, "samples": len(samples),
           "enrolled": time.strftime("%Y-%m-%d %H:%M"), "version": 1}
    return sig, f"Ring enrolled from {len(samples)} views (contrast {contrast:.0f}). Only this hand controls JAMES now."


class Enroller:
    def __init__(self, ts_ms: int):
        self.t0 = ts_ms
        self.samples: list[dict] = []
        self.handed: list[str] = []

    def add(self, img, pts, handed: str = ""):
        if pts is None or not ring_visible(pts):
            return
        f = features(img, pts)
        if f is not None:
            self.samples.append(f)
            if handed:
                self.handed.append(handed)

    def progress(self, ts_ms: int) -> float:
        return min(1.0, (ts_ms - self.t0) / ENROLL_MS)

    def done(self, ts_ms: int) -> bool:
        return ts_ms - self.t0 >= ENROLL_MS

    def finish(self):
        return signature(self.samples, self.handed)


@dataclass
class OwnerFilter:
    kind = "ring"
    sig: dict | None = None
    lock: dict | None = None                       # {"wrist", "palm", "seen"} of the followed owner hand
    cand: dict | None = None                       # {"wrist", "palm", "votes": deque}
    recheck: deque = field(default_factory=lambda: deque(maxlen=15))

    @property
    def enrolled(self) -> bool:
        return self.sig is not None

    @classmethod
    def load(cls, path: Path) -> "OwnerFilter":
        try:
            sig = json.loads(Path(path).read_text())
            sig["ring"], sig["rel"] = np.array(sig["ring"], np.float32), np.array(sig["rel"], np.float32)
            return cls(sig)
        except (OSError, ValueError, KeyError):
            return cls(None)

    def save(self, path: Path, sig: dict):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(sig, indent=2))
        self.sig = dict(sig, ring=np.array(sig["ring"], np.float32), rel=np.array(sig["rel"], np.float32))
        self.lock = self.cand = None
        self.recheck.clear()

    def forget(self, path: Path):
        try:
            Path(path).unlink()
        except OSError:
            pass
        self.sig = self.lock = self.cand = None

    @staticmethod
    def largest(hands) -> int | None:
        if not hands:
            return None
        areas = [np.ptp(np.array(h)[:, 0]) * np.ptp(np.array(h)[:, 1]) for h in hands]
        return int(np.argmax(areas))

    def _near(self, ref, hands) -> int | None:
        best, bd = None, 1e9
        for i, h in enumerate(hands):
            d = math.dist(h[WRIST], ref["wrist"])
            if d < bd:
                best, bd = i, d
        return best if best is not None and bd < 1.3 * ref["palm"] + 20 else None

    def select(self, img, hands: list, ts_ms: int, keys=()) -> tuple[int | None, str, float]:
        """Which hand drives input: (index or None, status, score). status: off | owner | searching.
        keys is ignored here (it is for KeyFilter, so Lens can call either one the same way)."""
        if not self.enrolled:
            return self.largest(hands), "off", 0.0
        if self.lock is not None:
            i = self._near(self.lock, hands)
            if i is not None:
                h = hands[i]
                self.lock.update(wrist=h[WRIST], palm=_palm(h), seen=ts_ms)
                s = 0.0
                if ring_visible(h):                          # re-check while the ring shows: a swapped hand drops out
                    s = score(features(img, h), self.sig)
                    self.recheck.append(s >= MATCH_SCORE * 0.8)
                    if len(self.recheck) == self.recheck.maxlen and sum(self.recheck) < 3:
                        self.lock = None
                        self.recheck.clear()
                        return None, "searching", s
                return i, "owner", s
            if ts_ms - self.lock["seen"] < LOST_MS:
                return None, "owner", 0.0                    # briefly out of view: nobody else takes over
            self.lock = None
            self.recheck.clear()
        scores = [score(features(img, h), self.sig) if ring_visible(h) else 0.0 for h in hands]
        best = int(np.argmax(scores)) if scores else None
        if best is None:
            self.cand = None
            return None, "searching", 0.0
        h, s = hands[best], scores[best]
        if self.cand is None or math.dist(h[WRIST], self.cand["wrist"]) > 1.3 * self.cand["palm"] + 20:
            self.cand = {"wrist": h[WRIST], "palm": _palm(h), "votes": deque(maxlen=VOTE_WINDOW)}
        self.cand.update(wrist=h[WRIST], palm=_palm(h))
        self.cand["votes"].append(s >= MATCH_SCORE)
        if sum(self.cand["votes"]) >= VOTES_NEEDED:
            self.lock = {"wrist": h[WRIST], "palm": _palm(h), "seen": ts_ms}
            self.cand = None
            return best, "owner", s
        return None, "searching", s


# ---------------- owner key: a printed ArUco marker on the watch strap or the back of the hand ----------------
KEY_VOTES, KEY_WINDOW = 3, 5          # the key must sit on the same hand in 3 of 5 frames
KEY_REACH = 0.7                       # max distance from the hand's forearm-to-knuckle line, in palm lengths
KEY_FOREARM = 0.9                     # how far down the forearm (in palm lengths) a watch-strap key may sit
KEY_LOST_MS = 4000                    # key mode: the owner hand may leave view this long and still be the owner
                                      # (log 01:07:43: lost after 1.5 s while reaching for Spotify, X pressed at 01:07:55)


def key_distance(pts, center) -> float:
    """How far a key is from a hand, in palm lengths: the smaller of (a) the distance to the line that runs from
    KEY_FOREARM palm lengths down the forearm (watch, strap) up to the middle knuckle (back of the hand) and
    (b) the distance to the nearest of the 21 hand points (a key held between the fingers)."""
    w, m, c = (np.asarray(p, float) for p in (pts[WRIST], pts[MID_MCP], center))
    v = m - w
    palm = float(np.linalg.norm(v)) or 1.0
    a = w - v * KEY_FOREARM
    ab = m - a
    t = float(np.clip((c - a) @ ab / (ab @ ab + 1e-9), 0.0, 1.0))
    line = float(np.linalg.norm(c - (a + t * ab)))
    held = min(float(np.linalg.norm(c - np.asarray(q, float))) for q in pts)    # held in the fingers
    return min(line, held) / palm


def key_hand(hands, keys) -> int | None:
    """Index of the hand wearing the key this frame (nearest within KEY_REACH), or None."""
    best, bd = None, KEY_REACH
    for c in keys:
        for i, h in enumerate(hands):
            d = key_distance(h, c)
            if d < bd:
                best, bd = i, d
    return best


@dataclass
class KeyFilter:
    key_id: int = 49
    lock: dict | None = None                       # {"wrist", "palm", "seen"} of the followed owner hand
    cand: dict | None = None                       # {"wrist", "palm", "votes": deque} of a hand showing the key
    kind = "key"

    @property
    def enrolled(self) -> bool:                    # the key IS the enrolment: nothing to learn
        return True

    def forget(self, path=None):
        self.lock = self.cand = None

    def _vote(self, h, hit: bool) -> bool:
        if self.cand is None or math.dist(h[WRIST], self.cand["wrist"]) > 1.3 * self.cand["palm"] + 20:
            self.cand = {"wrist": h[WRIST], "palm": _palm(h), "votes": deque(maxlen=KEY_WINDOW)}
        self.cand.update(wrist=h[WRIST], palm=_palm(h))
        self.cand["votes"].append(hit)
        return sum(self.cand["votes"]) >= KEY_VOTES

    epoch = 0                                  # +1 each time a hand takes control (iteration 2, P1-B / ST-14)

    def _take(self, h, ts_ms):
        self.lock = {"wrist": h[WRIST], "palm": _palm(h), "seen": ts_ms}
        self.cand = None
        self.epoch += 1

    def select(self, img, hands: list, ts_ms: int, keys=()) -> tuple[int | None, str, float]:
        """keys: centres (raw frame pixels) of the owner key seen this frame. Returns (index, status, score)."""
        k = key_hand(hands, keys) if keys else None
        if self.lock is not None:
            i = OwnerFilter._near(None, self.lock, hands)
            if k is not None and k != i:                         # the key is on another hand: hand over after votes
                if self._vote(hands[k], True):
                    self._take(hands[k], ts_ms)
                    return k, "owner", 1.0
            elif self.cand is not None:
                self.cand["votes"].append(False)
            if i is not None:
                h = hands[i]
                self.lock.update(wrist=h[WRIST], palm=_palm(h), seen=ts_ms)
                return i, "owner", 1.0 if k == i else 0.0
            if ts_ms - self.lock["seen"] < KEY_LOST_MS:
                return None, "owner", 0.0                        # briefly out of view: nobody else takes over
            self.lock = None
        if k is None:
            if self.cand is not None:
                self.cand["votes"].append(False)
            return None, "searching", 0.0
        if self._vote(hands[k], True):
            self._take(hands[k], ts_ms)
            return k, "owner", 1.0
        return None, "searching", 1.0
