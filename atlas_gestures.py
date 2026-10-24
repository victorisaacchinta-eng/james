"""ATLAS hands: one gesture, one command, read continuously (the HoloHand way).

    Open slowly       explode follows how open your hand is (0..100%)
    Close your hand   the same control at 0: it pulls back together
    Move or twist     an open (or closed) hand moving turns and tilts the model; twisting the hand rolls it
    Point             index finger only: the part under the fingertip lights up and its card shows
    Pinch a part      pull it out and drag it; let go and it springs back into place (and stays picked)
    Two hands         apart or together: zoom
    Peace, held       next model (library app only)

Why it feels steady:
- poses are geometry ratios with hysteresis per finger, and a pose must be seen for 50 to 150 ms before it counts
  (james_core.handpose.PoseTracker); one noisy frame changes nothing;
- a fist is never a pinch (in a fist the thumb also touches the index: the index must be half out);
- explode and rotation follow the hand only while it is a palm; when it turns into a point, pinch or V, whatever
  it did while changing shape is undone, and after one of those ends they wait a moment before following again;
- movement has a dead zone (tracking jitter turns nothing), single-frame jumps are ignored, and a hand that has
  just appeared does not turn anything for a quarter of a second;
- the second hand must stay in view a moment before two-hand zoom starts, and the zoom has a dead zone.

Every threshold is in config.py (ATLAS_*), with defaults here.
"""
from __future__ import annotations

import math
from collections import deque
from functools import lru_cache

import cv2
import numpy as np

import config
import jsil as J
from james_core import handpose as HP

GESTURES = [   # (id, title, what it does): the legend, in this order
    ("open", "Open slowly", "explode it, see inside"),
    ("fist", "Close your hand", "pull it back together"),
    ("move", "Move or twist", "turn, tilt and roll it"),
    ("point", "Point", "highlight a part"),
    ("pinch", "Pinch a part", "pull it out, drag it"),
    ("two", "Two hands", "apart or together to zoom"),
    ("peace", "Peace, hold", "jump to the next model"),
]


def cfg(name, default):
    return getattr(config, name, default)


def soft_dead(v: float, dz: float) -> float:
    """Zero inside the dead zone, then linear from its edge (no jump at the edge)."""
    return 0.0 if abs(v) <= dz else v - math.copysign(dz, v)


class HandEngine:
    """Reads LENS frames and drives one Atlas. One engine per window: switching models keeps it."""

    def __init__(self):
        self.poses = HP.PoseTracker()
        self.reset()

    def reset(self):
        self.poses.reset()
        self.pose = self.prev = None
        self.open_s = None                 # smoothed openness 0..1
        self.hist: deque = deque()         # (t, explode, yaw, pitch, roll) targets while the hand was a palm
        self.last_c = self.last_ang = None
        self.hand_since = None
        self.last_hand_t = -9.0
        self.quiet_until = 0.0
        self.rotating_until = 0.0
        self.grab = None                   # {"pid", "start"} while a pinched part is being dragged
        self.press = None                  # a pinch on empty space: a quick one clears the pick
        self.other_since = None
        self.other_last = -9.0
        self.other_c = None
        self.two = None                    # {"d0", "z0", "hc"} during two-hand zoom
        self.peace_latched = False
        self.last_switch = -9.0
        self.progress = 0.0                # peace hold 0..1
        self.active = None                 # legend row id now, or None
        self.centre = None                 # palm centre, for the ring
        self.size = 0.0
        self.hover_cand = (None, 0.0)

    def rebind(self):
        """A different model is now on screen: keep the pose, forget anchors and drags."""
        self.grab = self.press = self.two = None
        self.last_c = self.last_ang = None
        self.hist.clear()
        self.quiet_until = 0.0

    def skip(self):
        """The window used this frame (a shelf tile): nothing here may jump next frame."""
        self.last_c = self.last_ang = None
        self.hist.clear()

    @property
    def busy(self) -> bool:
        return self.grab is not None or self.two is not None

    # ------------------------------------------------------------------
    def update(self, a, r, now: float):
        hand = getattr(r, "hand", None)
        hand = hand if HP.valid(hand) else None
        tip = getattr(r, "fingertip", None)
        if tip is None and hand is None:
            a._hold_until_release = False                        # no hand, so no pinch is being carried in
            if (self.grab or self.two) and now - self.last_hand_t <= cfg("ATLAS_LOST_GRACE_S", 0.25):
                return                                           # a one-frame dropout is not a release
            self._lost(a)
            return
        self.last_hand_t = a._hand_t = now
        if self.hand_since is None:
            self.hand_since = now
        pinching = bool(getattr(r, "pinching", False))
        if a._hold_until_release:                                # a pinch carried in from the launcher
            if pinching:
                pinching = False
            else:
                a._hold_until_release = False
        pose = self.pose = self.poses.update(hand, pinching, now)
        if hand is not None:
            self.centre, self.size = HP.palm_centre(hand), HP.palm_size(hand)
            o = HP.openness(hand, cfg("ATLAS_FIST_RATIO", HP.FIST_RATIO), cfg("ATLAS_OPEN_RATIO", HP.OPEN_RATIO))
            self.open_s = o if self.open_s is None else self.open_s + (o - self.open_s) * 0.45
        else:
            self.centre, self.size = tip, 0.0
        hc = HP.palm_centre(hand) if hand is not None else tip

        if self._two_hands(a, r, hc, now):
            self.prev, self.active, self.progress = pose, "two", 0.0
            a.hover = None
            return

        entering = pose != self.prev
        if entering and pose in ("point", "pinch", "peace") and self.prev == "palm":
            self._undo_since(a, self.poses.changed_at)
        if entering and pose == "palm" and self.prev in ("point", "pinch", "peace"):
            self.quiet_until = now + cfg("ATLAS_SETTLE_S", 0.3)
            self.last_c = self.last_ang = None

        # pinch: grab a part and drag it; release springs it back
        if pose == "pinch":
            q = getattr(r, "pinch_point", None) or tip
            if entering or (self.grab is None and self.press is None):
                pid = a.hover if (self.prev == "point" and a.hover) else a.hit(q)
                if pid:
                    self.grab = {"pid": pid, "start": tip, "t": now}
                    if a.grab_id != pid:                       # another part still springing back: let it land
                        for sp in (a.grab_x, a.grab_y):
                            sp.x = sp.v = sp.target = 0.0
                    a.grab_id = pid
                    a.grab_x.omega = a.grab_y.omega = 22.0
                    if a.selected != pid:
                        a.select(pid, now)
                else:
                    self.press = {"t": now, "start": tip}
            elif self.grab is not None and tip is not None:
                a.grab_x.target = tip[0] - self.grab["start"][0]
                a.grab_y.target = tip[1] - self.grab["start"][1]
        elif self.prev == "pinch":
            self._release(a, tip, now)

        # point: highlight the part under the fingertip (a moment of agreement before it changes)
        if pose == "point":
            h = a.hit(tip)
            if hand is None or h == a.hover:
                a.hover, self.hover_cand = h, (h, now)
            else:
                cand, t0 = self.hover_cand
                if cand != h:
                    self.hover_cand = (h, now)
                elif now - t0 >= (0.06 if h else 0.15):
                    a.hover = h
        elif self.grab is None:
            a.hover = None

        # peace: hold to go to the next model
        self.progress = 0.0
        if pose == "peace" and a.library:
            hold = cfg("ATLAS_PEACE_HOLD_S", 0.5)
            if not self.peace_latched and now - self.last_switch >= 1.0:
                self.progress = min(1.0, (now - self.poses.since) / hold)
                if self.progress >= 1.0:
                    a.requests.append("next")
                    self.peace_latched, self.last_switch, self.progress = True, now, 0.0
        else:
            self.peace_latched = False

        # palm: openness drives explode, movement and twist drive the view
        pending = self.poses.pending not in (None, "palm")
        warm = now - self.hand_since >= cfg("ATLAS_WARMUP_S", 0.25)   # a hand coming into view changes nothing yet
        if pose == "palm" and hand is not None and now >= self.quiet_until and not pending and warm:
            lo, hi = cfg("ATLAS_EXPLODE_FROM", 0.12), cfg("ATLAS_EXPLODE_TO", 0.85)
            e = min(1.0, max(0.0, (self.open_s - lo) / (hi - lo)))
            e = round(e * 50) / 50
            if abs(e - a.explode.target) >= 0.02 or e in (0.0, 1.0):
                a.explode.target = e
            self._turn(a, hand, now)
            self.hist.append((now, a.explode.target, a.yaw.target, a.pitch.target, a.roll.target))
            while self.hist and now - self.hist[0][0] > 1.0:
                self.hist.popleft()
        elif pose == "palm" and hand is not None:
            self.last_c = None                                   # start afresh (and at rest) when it resumes

        self.prev = pose
        self.active = self._legend_row(pose, now)

    # ------------------------------------------------------------------
    def _turn(self, a, hand, now):
        """Turn, tilt and roll from the palm's movement and twist. A clutch per axis: the hand must first leave
        a small circle around where it rested (so tracking jitter turns nothing); then it tracks 1:1 until it
        rests again for a moment."""
        c, ang = HP.palm_centre(hand), HP.hand_angle(hand)
        if self.last_ang is not None:                       # a light filter on the twist angle
            ang = self.last_ang + ((ang - self.last_ang + 180) % 360 - 180) * 0.5
        size = HP.palm_size(hand)
        if self.last_c is None or now - self.hand_since < cfg("ATLAS_WARMUP_S", 0.25):
            self.last_c, self.last_ang = c, ang
            self.rest_c, self.rest_ang, self.move_on, self.twist_on = c, ang, False, False
            self.cand_c, self.cand_ang, self.still_since = c, ang, now
            return
        dx, dy = c[0] - self.last_c[0], c[1] - self.last_c[1]
        da = (ang - self.last_ang + 180) % 360 - 180
        if math.hypot(dx, dy) > cfg("ATLAS_JUMP_PX", 90) or abs(da) > 40:      # a tracking glitch: skip it
            self.last_c, self.last_ang = c, ang
            return
        start_px = max(cfg("ATLAS_START_PX", 6.0), 0.06 * size)
        if not self.move_on and math.dist(c, self.rest_c) > start_px:
            self.move_on = True
        if not self.twist_on and abs((ang - self.rest_ang + 180) % 360 - 180) > cfg("ATLAS_START_DEG", 4.0):
            self.twist_on = True
        g = cfg("ATLAS_TURN_DEG_PER_PX", 0.28)
        if self.move_on and (dx or dy):
            a.yaw.target += dx * g
            a.pitch.target = float(np.clip(a.pitch.target + dy * g * 0.8, -75, 85))
        if self.twist_on and da:
            a.roll.target -= da * cfg("ATLAS_TWIST_GAIN", 1.0)
        if self.move_on or self.twist_on:
            self.rotating_until = now + 0.25
        # resting: the hand stays inside a small circle (and angle band) for 0.2 s, whatever the jitter inside it
        if math.dist(c, self.cand_c) > start_px / 2 or \
                abs((ang - self.cand_ang + 180) % 360 - 180) > cfg("ATLAS_START_DEG", 4.0) / 2:
            self.cand_c, self.cand_ang, self.still_since = c, ang, now
        elif now - self.still_since >= 0.2:
            self.rest_c, self.rest_ang, self.move_on, self.twist_on = c, ang, False, False
            self.rotating_until = min(self.rotating_until, now)
        self.last_c, self.last_ang = c, ang

    def _undo_since(self, a, t0):
        """Put back the explode and view the hand had just before it started changing into this pose."""
        keep = None
        for h in self.hist:
            if h[0] <= t0 - cfg("ATLAS_UNDO_S", 0.25):
                keep = h
        if keep is not None:
            _, a.explode.target, a.yaw.target, a.pitch.target, a.roll.target = keep

    def _release(self, a, tip, now):
        if self.grab is not None:
            a.grab_x.omega = a.grab_y.omega = 9.0
            a.grab_x.target = a.grab_y.target = 0.0                # back into its place, still picked
            self.grab = None
        elif self.press is not None:
            moved = tip is not None and self.press["start"] is not None and \
                math.dist(tip, self.press["start"]) > cfg("ATLAS_TAP_MOVE_PX", 24)
            if not moved and now - self.press["t"] <= 0.8 and a.selected:
                a.selected = None                                  # a quick pinch on empty space clears the pick
            self.press = None

    def _lost(self, a):
        if self.grab is not None:
            self._release(a, None, 0.0)
        self.press = self.two = None
        self.poses.reset()
        self.pose = self.prev = None
        self.hand_since = None
        self.last_c = self.last_ang = None
        self.other_since = None
        self.open_s = None
        self.active, self.progress, self.centre = None, 0.0, None
        self.peace_latched = False
        self.hist.clear()
        a.hover = None

    def _two_hands(self, a, r, hc, now) -> bool:
        """Two-hand zoom. Both hands must be open palms (a resting hand, a bystander's hand or a fist does not
        start it), the second must stay a moment, and the zoom only changes while BOTH hands are seen."""
        hand = getattr(r, "hand", None)
        others = [h for h in (getattr(r, "others", None) or []) if HP.valid(h)]
        other = max(others, key=HP.palm_size) if others else None
        open_other = other is not None and HP.openness(other) >= 0.5
        driving_ok = self.pose == "palm" and (self.open_s or 0) >= 0.5 and HP.valid(hand)
        if open_other:
            self.other_c, self.other_last = HP.palm_centre(other), now
            if self.other_since is None:
                self.other_since = now
        elif self.other_since is not None and now - self.other_last > cfg("ATLAS_TWO_GRACE_S", 0.25):
            self.other_since = None
        if self.other_since is None or self.grab is not None or hc is None or self.pose != "palm":
            if self.two is not None:                               # ended: the remaining hand starts afresh
                self.two = None
                self.quiet_until = now + cfg("ATLAS_SETTLE_S", 0.3)
                self.last_c = self.last_ang = None
                self.hist.clear()
            return False
        if self.two is None and (not driving_ok or now - self.other_since < cfg("ATLAS_TWO_ENTER_S", 0.2)):
            return False
        if not open_other:                                         # the other hand missed a frame: hold, no change
            return self.two is not None
        d = math.dist(hc, self.other_c)
        floor = 0.5 * HP.palm_size(hand) if HP.valid(hand) else 40.0
        t = self.two
        if t is None or math.dist(hc, t["hc"]) > cfg("ATLAS_JUMP_PX", 90) * 1.4:   # start, or the hands swapped
            if d < floor:
                return self.two is not None
            self.two = {"d0": d, "z0": a.zoom.target, "hc": hc}
            self.press = None
            return True
        ratio = 1.0 + soft_dead(max(d, 1.0) / t["d0"] - 1.0, cfg("ATLAS_ZOOM_DEAD", 0.04))
        a.zoom.target = float(np.clip(t["z0"] * ratio, a.ZOOM_MIN, a.ZOOM_MAX))
        t["hc"] = hc
        return True

    def _legend_row(self, pose, now):
        if pose in ("point", "pinch"):
            return pose
        if pose == "peace":
            return "peace"
        if pose == "palm":
            if now < self.rotating_until:
                return "move"
            return "fist" if (self.open_s or 0) < 0.3 else "open"
        return None

    # ------------------------------------------------------------------
    def draw_ring(self, img, a, other=None):
        """The ring around the driving hand: its size follows how open the hand is, the amber arc is the
        explode level, a cyan arc fills while a peace sign is held. During two-hand zoom: a dashed line
        between the hands and the zoom."""
        if self.centre is None or self.size <= 0:
            return
        cx, cy = (int(v) for v in self.centre)
        if self.two is not None and self.other_c is not None:
            ox, oy = (int(v) for v in self.other_c)
            n = max(2, int(math.dist((cx, cy), (ox, oy)) // 14))
            for i in range(0, n, 2):
                p0 = (int(cx + (ox - cx) * i / n), int(cy + (oy - cy) * i / n))
                p1 = (int(cx + (ox - cx) * (i + 1) / n), int(cy + (oy - cy) * (i + 1) / n))
                cv2.line(img, p0, p1, J.CONFIRM, 1, cv2.LINE_AA)
            J.text(img, f"ZOOM {a.zoom.target:.2f}x", (cx + ox) // 2, (cy + oy) // 2 - 10, 11, J.CONFIRM, "mono",
                   anchor="c")
            return
        o = self.open_s or 0.0
        rad = int(self.size * (0.85 + 0.85 * o))
        cv2.circle(img, (cx, cy), rad, J.TEXT_2, 1, cv2.LINE_AA)
        ex = float(np.clip(a.explode.x, 0, 1))
        if ex > 0.01:
            cv2.ellipse(img, (cx, cy), (rad, rad), -90, 0, int(360 * ex), J.CONFIRM, 2, cv2.LINE_AA)
        if self.progress > 0:
            cv2.ellipse(img, (cx, cy), (rad + 6, rad + 6), -90, 0, int(360 * self.progress), J.CYAN, 2, cv2.LINE_AA)
        words = {"open": f"OPEN {int(round(o * 100))}%", "fist": "CLOSED", "move": "TURN", "point": "POINT",
                 "pinch": "PINCH" + (" · DRAG" if self.grab else ""), "peace": "PEACE"}
        if self.active in words:
            H, W = img.shape[:2]
            lx = min(cx + int(rad * 0.72) + 6, W - 110)
            ly = max(cy - int(rad * 0.72), 70)
            J.text(img, words[self.active], lx, ly, 10.5, J.CONFIRM if self.active in ("pinch", "peace") else J.TEXT,
                   "mono")


# ---------------- the legend ----------------

@lru_cache(maxsize=256)
def _fit_hand(pose, angle, box):
    """A clean hand in `pose`, scaled to fill box (x, y, w, h), centred."""
    pts = np.array(HP.synth(pose, (0, 0), 100, angle), float)
    lo, hi = pts.min(0), pts.max(0)
    bx, by, bw, bh = box
    k = min(bw / max(hi[0] - lo[0], 1), bh / max(hi[1] - lo[1], 1))
    off = np.array([bx + (bw - (hi[0] - lo[0]) * k) / 2, by + (bh - (hi[1] - lo[1]) * k) / 2])
    return tuple(tuple(int(round(v)) for v in (p - lo) * k + off) for p in pts)


def _draw_hand(img, pts, color):
    for i, j in J.HAND_LINKS:
        cv2.line(img, pts[i], pts[j], color, 1, cv2.LINE_AA)
    for i in (4, 8, 12, 16, 20):
        cv2.circle(img, pts[i], 1, color, -1, cv2.LINE_AA)


def glyph(img, kind: str, x: int, y: int, s: int, color):
    """A small hand drawing for a legend row, in a box of side s at (x, y)."""
    x, y, s = int(x), int(y), int(s)
    if kind == "two":
        for bx, ang in ((x, -10), (x + int(s * 0.56), 10)):
            _draw_hand(img, _fit_hand("open", ang, (bx, y + int(s * 0.22), int(s * 0.44), int(s * 0.78))), color)
        m, yy = x + s // 2, y + int(s * 0.1)
        cv2.line(img, (m - int(s * 0.3), yy), (m + int(s * 0.3), yy), color, 1, cv2.LINE_AA)
        for d in (-1, 1):
            tipx = m + d * int(s * 0.3)
            cv2.line(img, (tipx, yy), (tipx - d * 3, yy - 3), color, 1, cv2.LINE_AA)
            cv2.line(img, (tipx, yy), (tipx - d * 3, yy + 3), color, 1, cv2.LINE_AA)
        return
    pose = {"open": "open", "fist": "fist", "move": "open", "point": "point", "pinch": "pinch",
            "peace": "peace"}[kind]
    inset = 0.18 if kind in ("fist", "move") else 0.04
    box = (x + int(s * inset), y + int(s * inset), int(s * (1 - 2 * inset)), int(s * (1 - 2 * inset)))
    pts = _fit_hand(pose, 20 if kind == "move" else 0, box)
    _draw_hand(img, pts, color)
    if kind == "pinch":                                      # where thumb and index meet
        cv2.circle(img, pts[8], max(2, s // 9), color, 1, cv2.LINE_AA)
    if kind == "move":                                       # turning arrow around the hand
        c, r = (x + s // 2, y + s // 2), int(s * 0.48)
        cv2.ellipse(img, c, (r, r), 0, 205, 290, color, 1, cv2.LINE_AA)
        end = (int(c[0] + r * math.cos(math.radians(290))), int(c[1] + r * math.sin(math.radians(290))))
        cv2.line(img, end, (end[0] - 4, end[1] - 1), color, 1, cv2.LINE_AA)
        cv2.line(img, end, (end[0] - 1, end[1] + 4), color, 1, cv2.LINE_AA)


def draw_strip(img, x: int, y: int, w: int, active: str | None, rows=None):
    """One line of the legend: glyph + short title per gesture, the one in use in amber. For narrow screens and
    the maintenance app."""
    rows = rows or GESTURES
    short = {"open": "Open", "fist": "Close", "move": "Move/twist", "point": "Point", "pinch": "Pinch part",
             "two": "Two hands", "peace": "Peace"}
    item_w = [22 + J.measure(short[k], 10.5) + 14 for k, *_ in rows]
    scale = min(1.0, w / max(1.0, sum(item_w)))
    xx = x
    for (k, *_), iw in zip(rows, item_w):
        on = k == active
        col = J.CONFIRM if on else J.MUTED
        glyph(img, k, int(xx), y - 17, 20, col)
        if scale > 0.75:
            J.text(img, short[k], xx + 22, y - 3, 10.5, J.TEXT if on else J.TEXT_2, "semi" if on else "sans")
        xx += iw * scale if scale > 0.75 else 26
