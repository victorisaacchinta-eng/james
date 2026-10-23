"""ATLAS: exploded views you drive with your hands.

Two homes:
- app.py (maintenance, key X): the targeted machine from the industry pack, wired to the live job and WARDEN.
- atlas_app.py (the ATLAS app in james.py's launcher, or `python atlas_app.py`): a library of real,
  openly licensed models (NASA rovers and spacecraft, a car, a watch...), no job and no WARDEN.


JAMES already tells the technician WHAT to do, one sourced step at a time. ATLAS shows WHERE:
the pack's model of the targeted machine, drawn as a line schematic in the camera's free space,
with the part the current step touches lit up.

    amber  the part the current step touches (WARDEN allows the step)
    red    the part the current step touches, and WARDEN is blocking that step
    cyan   the part you picked; its card shows the rules that guard it (a WARDEN preview)

Gestures while ATLAS is open (it owns the hand, so a pinch here can NEVER confirm a step). The hands are read
by atlas_gestures.HandEngine, one gesture per command, continuously:
    open slowly          explode follows how open the hand is      close your hand   it pulls back together
    move or twist        turn, tilt and roll                        point             highlight a part
    pinch a part         pull it out and drag it (it springs back)  two hands         apart or together: zoom
    peace, held          next model (library app)

Everything that decides a colour is deterministic: the step's WARDEN hazard tags and the part's own
words (james_core.model3d.parts_for_step), and james_core.safety_guard.evaluate for the preview.
No model call, no network. Drawn in JSIL: thin lines, no glow, no particles.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

import cv2
import numpy as np

import config
import jsil as J
from james_core import model3d
from james_core.motion import point_segment_dist
from atlas_gestures import HandEngine, draw_strip, GESTURES
from james_core.safety_guard import evaluate
from james_core.schemas import Decision, Proposal, ProposedStep

STAGGER = 0.3            # parts nearest the core move first; the outermost start this late (0..1 of the travel)
HIT_PX = 22              # pointing or pinching reaches a part whose nearest line is within this many pixels
ZOOM_MIN, ZOOM_MAX = 0.4, 6.0
BASE_YAW, BASE_PITCH = -34.0, 16.0

# Part colours in the ATLAS app (library models only). In the maintenance app colour means safety (amber = this
# step, red = WARDEN block, cyan = picked), so parts there stay grey. Each family has its own hue; a part is put in a
# family by the words in its id and label. Hues are spaced round the wheel and kept a little muted, so the camera
# behind stays readable and no colour shouts.
FAMILIES = (   # (key, name, hex, words)
    ("wheel", "Wheels and tyres", "#F0A53C", ("wheel", "tyre", "tire", "rim")),
    ("optic", "Glass and optics", "#62CFE6", ("glass", "mirror", "lens", "crystal", "window", "visor")),
    ("power", "Power and drive", "#EC7360", ("motor", "engine", "power", "rtg", "battery", "solar", "axle", "shaft",
                                             "coupling", "drive", "thruster", "impeller", "rotor")),
    ("sense", "Sensors and signal", "#73CF8A", ("cam", "camera", "radar", "antenna", "sensor", "light", "wiring",
                                                 "harness", "terminal", "hga", "uhf", "rimfax", "dial", "hands")),
    ("mech", "Mechanisms", "#E284B6", ("arm", "turret", "suspension", "rocker", "bogie", "hinge", "seal", "bearing",
                                       "valve", "flap", "pusher", "button", "clasp", "buckle", "sampl", "mast",
                                       "fitting", "metal", "strainer", "boom")),
    ("soft", "Interior and soft parts", "#CDB284", ("seat", "steering", "dash", "cockpit", "cabin", "leather", "strap",
                                                    "band", "hose", "mask", "sunshield", "floor")),
    ("body", "Body and structure", "#93B6CC", ("body", "chassis", "shell", "frame", "fuselage", "structure", "cage",
                                               "case", "bezel", "base", "casing", "hood", "door", "cover", "leg",
                                               "back", "bus", "panel", "nozzle")),
)
FAMILY = {k: (name, J.hex_bgr(h)) for k, name, h, _ in FAMILIES}


def part_family(pid: str, label: str, index: int = 0) -> str:
    """The colour family of a part, from the words in its id and label; parts nobody named cycle the families."""
    text = f"{pid} {label}".lower().replace("_", " ")
    for key, _, _, words in FAMILIES:
        if any(w in text for w in words):
            return key
    return FAMILIES[index % len(FAMILIES)][0]


def mix(c, d, t: float) -> tuple:
    return tuple(int(round(a + (b - a) * t)) for a, b in zip(c, d))


FACT_WORDS = {"loto_confirmed": "lockout/tagout", "zero_energy_verified": "zero-energy check",
              "ppe_gloves": "gloves", "ppe_eye": "eye protection", "casing_temp_c": "casing temperature"}


@dataclass
class StepContext:
    """What the job is doing right now, as ATLAS needs it. Built by app.py from the session."""
    text: str = ""
    touches: tuple = ()
    index: int = 0
    total: int = 0
    blocked: bool = False
    rule_ids: tuple = ()
    facts: dict = field(default_factory=dict)


class Spring:
    """Critically damped spring: moves fast, settles without overshoot. Motion that reads as physical."""

    def __init__(self, x: float, omega: float):
        self.x, self.v, self.target, self.omega = float(x), 0.0, float(x), omega

    def step(self, dt: float):
        n = max(1, math.ceil(dt / 0.02))                      # small sub-steps keep it stable at low FPS
        h = dt / n
        for _ in range(n):
            a = self.omega ** 2 * (self.target - self.x) - 2 * self.omega * self.v
            self.v += a * h
            self.x += self.v * h

    def snap(self):
        self.x, self.v = self.target, 0.0

    @property
    def settled(self) -> bool:
        return abs(self.target - self.x) < 1e-3 and abs(self.v) < 1e-3


def _smoothstep(e):
    e = np.clip(e, 0.0, 1.0)
    return e * e * (3 - 2 * e)


class Atlas:
    ZOOM_MIN, ZOOM_MAX = ZOOM_MIN, ZOOM_MAX

    def __init__(self, model, policy=None, reduced_motion: bool = False, idle_sway: bool = True):
        """model: a james_core.model3d.Model (pack schematic) or an atlas_library.LibModel (lines included).
        policy: the WARDEN policy in the maintenance app; None in the library app (no job, no rules)."""
        self.model, self.policy = model, policy
        self.library = policy is None
        self.reduced, self.idle_sway = reduced_motion, idle_sway
        self.parts = {p.id: p for p in model.parts}
        given = getattr(model, "segs", None)
        self.segs = {p.id: (np.asarray(given[p.id], np.float32) if given else model3d.part_segments(p))
                     for p in model.parts}
        w = getattr(model, "weights", None) or {}
        self.weights = {k: np.asarray(w[k], np.float32) if k in w else np.ones(len(v), np.float32)
                        for k, v in self.segs.items()}
        self.off = {p.id: np.asarray(p.explode, np.float32) for p in model.parts}
        self.family = {p.id: part_family(p.id, p.label, i) for i, p in enumerate(model.parts)}
        self.tint = {k: FAMILY[f][1] for k, f in self.family.items()}
        closed = np.concatenate([s.reshape(-1, 3) for s in self.segs.values()])
        opened = np.concatenate([(s + self.off[k]).reshape(-1, 3) for k, s in self.segs.items()])
        both = np.concatenate([closed, opened])
        self.centre = ((both.min(0) + both.max(0)) / 2).astype(np.float32)
        self.r_closed = float(np.linalg.norm(closed - self.centre, axis=1).max())
        self.r_open = float(np.linalg.norm(opened - self.centre, axis=1).max())
        order = sorted(self.parts, key=lambda k: float(np.linalg.norm(self.off[k])))
        n = max(1, len(order) - 1)
        self.delay = {k: STAGGER * i / n for i, k in enumerate(order)}
        self.explode = Spring(0.0, 7.5)
        self._fit = None
        self._dt = 0.0
        self.yaw, self.pitch, self.roll = Spring(BASE_YAW, 13.0), Spring(BASE_PITCH, 13.0), Spring(0.0, 13.0)
        self.active = False
        self.asset_id = ""
        self.selected: str | None = None
        self.hover: str | None = None
        self.ctx = StepContext()
        self.proj: dict = {}
        self.opened_at = 0.0
        self._events: list = []
        self.requests: list = []           # "next": the hands asked for the next model (the app decides)
        self._t = None
        self._hand_t = 0.0
        self._sway = 0.0
        self.zoom = Spring(1.0, 12.0)
        self.pan_x, self.pan_y = Spring(0.0, 12.0), Spring(0.0, 12.0)
        self.grab_id: str | None = None    # the part a pinch is dragging (or springing back), screen offset below
        self.grab_x, self.grab_y = Spring(0.0, 22.0), Spring(0.0, 22.0)
        self._hold_until_release = False   # a pinch carried in from whatever opened ATLAS must end first
        self.hands = HandEngine()          # the ATLAS app shares one between models
        self.backdrop = 0.24 if policy is not None else 0.5   # camera brightness behind the drawing: the job app
        #                                  keeps it faint (the task card matters); the ATLAS app shows you, like a mirror
        self.legend_outside = False        # the app draws the legend and the part card itself (wide window)

    # open / close
    def open(self, asset_id: str, now: float | None = None):
        now = time.monotonic() if now is None else now
        self.active, self.asset_id, self.opened_at = True, asset_id, now
        self.selected = self.hover = None
        self.proj, self._rect = {}, (0, 0, 0, 0)          # never hit-test against the last open's drawing
        self._hold_until_release = True        # a pinch still held from whatever opened ATLAS must end first
        self.grab_id, self.requests = None, []
        for sp in (self.grab_x, self.grab_y):
            sp.x = sp.v = sp.target = 0.0
        self.hands.rebind()
        self.explode.x, self.explode.v, self.explode.target = 0.0, 0.0, 1.0   # open = assemble -> explode
        self.reset_view()
        self._t = now
        self._fit = None
        if self.reduced:
            self.settle()

    def close(self):
        self.active, self.hover = False, None
        self.hands.rebind()

    def settle(self):
        for s in self._springs():
            s.snap()
        self._fit = None

    def _springs(self):
        return (self.explode, self.yaw, self.pitch, self.roll, self.zoom, self.pan_x, self.pan_y,
                self.grab_x, self.grab_y)

    # direct controls (keys and buttons call these; gestures end up here too)
    def reset_view(self):
        self.hands.hist.clear()                          # a key or button wins over any undo
        self.yaw.target, self.pitch.target, self.roll.target = BASE_YAW, BASE_PITCH, 0.0
        self.zoom.target, self.pan_x.target, self.pan_y.target = 1.0, 0.0, 0.0

    def turn(self, dyaw: float = 0.0, dpitch: float = 0.0):
        self.hands.hist.clear()                          # a key or button wins over any undo
        self.yaw.target += dyaw
        self.pitch.target = float(np.clip(self.pitch.target + dpitch, -75, 85))

    def zoom_by(self, f: float):
        self.hands.hist.clear()                          # a key or button wins over any undo
        self.zoom.target = float(np.clip(self.zoom.target * f, ZOOM_MIN, ZOOM_MAX))

    def toggle_explode(self):
        self.hands.hist.clear()                          # a key or button wins over any undo
        self.explode.target = 0.0 if self.explode.target >= 0.5 else 1.0

    def select_next(self, step: int = 1, now: float | None = None):
        ids = list(self.parts)
        i = ids.index(self.selected) if self.selected in ids else (-1 if step > 0 else 0)
        self.selected = None
        self.select(ids[(i + step) % len(ids)], now)

    def pop_requests(self) -> list:
        rq, self.requests = self.requests, []
        return rq

    def pop_events(self) -> list:
        ev, self._events = self._events, []
        return ev

    def select(self, part_id: str | None, now: float | None = None):
        now = time.monotonic() if now is None else now
        self.selected = None if part_id == self.selected else part_id
        if self.selected:
            p = self.parts[self.selected]
            self._events.append(("atlas_part_selected", {
                "asset_id": self.asset_id, "part": p.id, "hazard": getattr(p, "hazard", None),
                "ms_since_open": int((now - self.opened_at) * 1000),
                "in_current_step": p.id in self.linked()}))

    # what is lit
    def linked(self) -> list[str]:
        if self.library or (not self.ctx.text and not self.ctx.touches):
            return []
        return model3d.parts_for_step(self.model, self.ctx.text, self.ctx.touches)

    def rules_for(self, hazard: str | None) -> list[str]:
        if not hazard:
            return []
        return [r["id"] for r in self.policy.rules if hazard in (r.get("when_touches_any") or ())]

    def preview(self, part_id: str) -> tuple[str, str, tuple]:
        """WARDEN preview for work on this part with the job's current facts: (status, text, colour).
        A preview only: it is never logged as a decision, and every real step is still checked by WARDEN."""
        p = self.parts[part_id]
        if self.library:
            return "none", "", J.TEXT_2
        if not p.hazard:
            return "none", "No WARDEN rule for this part", J.TEXT_2
        step = ProposedStep(action_id="SHOW_STEP", text=f"ATLAS preview: {p.label}",
                            evidence_ids=("atlas:preview",), touches=(p.hazard,))
        d = evaluate(Proposal(bundle_id="atlas-preview", step=step, step_index=1, step_total=1),
                     self.ctx.facts or {}, self.policy)
        if d.decision == Decision.ALLOW:
            rules = self.rules_for(p.hazard)
            return "allow", ("Clear: " + ", ".join(rules) + " met") if rules else "Clear", J.SUCCESS
        need = [FACT_WORDS.get(f, f.replace("_", " ")) for f in d.missing]
        txt = "Gated by " + ", ".join(d.rule_ids) + (". Needs " + ", ".join(need) if need else "")
        return "gate", txt, J.ERROR

    # input
    def update(self, r, now: float):
        """Read one frame of LENS (already in screen coordinates): r.hand is the driving hand's 21 points,
        r.others the other hands in view. atlas_gestures.HandEngine turns them into commands."""
        if not self.active or r is None:
            return
        self.hands.update(self, r, now)
        if self.reduced:
            self.settle()

    def tick(self, now: float):
        dt = 0.0 if self._t is None else min(0.1, max(0.0, now - self._t))
        self._t, self._dt = now, dt
        if self.reduced:
            self.settle()
            self._sway = 0.0
            return
        for s in self._springs():
            s.step(dt)
        if self.grab_id and not self.hands.grab and abs(self.grab_x.x) < 0.5 and abs(self.grab_y.x) < 0.5:
            self.grab_id = None                                   # sprang back home
        idle = self.idle_sway and now - self._hand_t > 2.5 and not self.hands.busy
        target = 1.0 if idle else 0.0
        self._sway += (target - self._sway) * min(1.0, dt * 1.5)
        self._phase = getattr(self, "_phase", 0.0) + dt * 0.45 * self._sway

    # geometry
    def _sway_deg(self) -> float:
        """Idle sway in degrees, in 0.75 degree steps: a swaying view redraws about 5 times a second."""
        return round(9.0 * self._sway * math.sin(getattr(self, "_phase", 0.0)) / 0.75) * 0.75

    def _rotation(self) -> np.ndarray:
        yaw = math.radians(self.yaw.x + self._sway_deg())
        pitch, roll = math.radians(self.pitch.x), math.radians(self.roll.x)
        cyw, syw, cp, sp = math.cos(yaw), math.sin(yaw), math.cos(pitch), math.sin(pitch)
        cr, sr = math.cos(roll), math.sin(roll)
        return np.array([[cr, -sr, 0], [sr, cr, 0], [0, 0, 1]], np.float32) @ \
            np.array([[1, 0, 0], [0, cp, -sp], [0, sp, cp]], np.float32) @ \
            np.array([[cyw, 0, syw], [0, 1, 0], [-syw, 0, cyw]], np.float32)

    def _packed(self):
        """All lines in one array, so a frame is one matrix multiply instead of one per part."""
        if getattr(self, "_pack", None) is None:
            ids = list(self.segs)
            sizes = [len(self.segs[k]) for k in ids]
            starts = np.cumsum([0] + sizes)
            allsegs = np.concatenate([self.segs[k] for k in ids]).astype(np.float32) - self.centre
            offs = np.concatenate([np.repeat(self.off[k][None], n, 0) for k, n in zip(ids, sizes)])
            delay = np.array([self.delay[k] for k in ids], np.float32)
            self._pack = (ids, starts, allsegs, offs[:, None, :], delay, np.repeat(np.arange(len(ids)), sizes))
            # explode guides: each part's centre, and its explode offset
            self._cent = np.array([self.segs[k].reshape(-1, 3).mean(0) - self.centre for k in ids], np.float32)
            self._offp = np.array([self.off[k] for k in ids], np.float32)
            # turntable: a ring under the assembled model, with ticks every 15 degrees (cardinal ones longer)
            closed = allsegs.reshape(-1, 3)
            floor = float(closed[:, 1].min()) - 0.02 * self.r_closed
            rad = 0.62 * float(np.linalg.norm(closed[:, [0, 2]], axis=1).max())
            ang = np.radians(np.arange(0, 360, 5))
            ring = np.stack([rad * np.cos(ang), np.full_like(ang, floor), rad * np.sin(ang)], 1)
            tick_a = np.radians(np.arange(0, 360, 15))
            inner = np.where(np.arange(len(tick_a)) % 6 == 0, 0.86, 0.94)[:, None]
            t0 = np.stack([rad * np.cos(tick_a), np.full_like(tick_a, floor), rad * np.sin(tick_a)], 1)
            t1 = t0 * np.array([1, 0, 1]) * inner + np.array([0, floor, 0])
            self._ring = (ring.astype(np.float32), t0.astype(np.float32), t1.astype(np.float32))
        return self._pack

    def project(self, area):
        """Project every part to fit `area` (x, y, w, h). Fills self.proj:
        id -> [a2d (N, 2), b2d (N, 2), depth 0 far..1 near per segment, top point of the part]."""
        ids, starts, allsegs, offs, delay, owner = self._packed()
        rot = self._rotation()
        D = self.r_open * 3.6
        e = float(np.clip(self.explode.x, 0, 1.05))
        ei = _smoothstep((e - delay) / (1 - STAGGER)).astype(np.float32)        # per part
        pts = (allsegs + offs * ei[owner][:, None, None]) @ rot.T                # (M, 2, 3)
        s = D / (D - pts[..., 2])
        raw = np.empty(pts.shape[:2] + (2,), np.float32)
        raw[..., 0] = pts[..., 0] * s
        raw[..., 1] = -pts[..., 1] * s
        z = pts[..., 2].mean(axis=1)
        lo, hi = raw.reshape(-1, 2).min(0), raw.reshape(-1, 2).max(0)
        x, y, w, h = area
        k_target = 0.94 * min(w / max(hi[0] - lo[0], 1e-3), h / max(hi[1] - lo[1], 1e-3))
        mid = (lo + hi) / 2
        # the "camera" eases to the new fit, so turning or exploding never makes the drawing jump
        if not getattr(self, "_fit", None) or self.reduced:
            self._fit = [Spring(k_target, 8.0), Spring(mid[0], 8.0), Spring(mid[1], 8.0)]
        swaying = self._sway > 0.05 and not self.hands.busy
        for sp, tgt in zip(self._fit, (k_target, mid[0], mid[1])):
            if not swaying:                       # the idle sway never refits the camera: no extra redraws
                sp.target = tgt
            sp.step(getattr(self, "_dt", 0.0)) if not self.reduced else sp.snap()
        k, mx, my = (sp.x for sp in self._fit)
        k *= self.zoom.x
        cx, cy = x + w / 2 + self.pan_x.x, y + h / 2 + self.pan_y.x
        scr = np.empty_like(raw)
        scr[..., 0] = cx + (raw[..., 0] - mx) * k
        scr[..., 1] = cy + (raw[..., 1] - my) * k
        if self.grab_id in ids:                   # the pinched part follows the pinch, then springs back
            gi = ids.index(self.grab_id)
            scr[starts[gi]:starts[gi + 1], :, 0] += self.grab_x.x
            scr[starts[gi]:starts[gi + 1], :, 1] += self.grab_y.x
        def to_screen(p3):
            q = p3 @ rot.T
            sc = D / (D - q[..., 2])
            return np.stack([cx + (q[..., 0] * sc - mx) * k, cy + (-q[..., 1] * sc - my) * k], -1)
        cent_now = to_screen(self._cent + self._offp * ei[:, None])
        if self.grab_id in ids:
            cent_now[ids.index(self.grab_id)] += (self.grab_x.x, self.grab_y.x)
        self._guides = (to_screen(self._cent), cent_now)
        ring, t0, t1 = self._ring
        self._ring_scr = (to_screen(ring), to_screen(t0), to_screen(t1))
        zlo, zhi = float(z.min()), float(z.max())
        zn = (z - zlo) / ((zhi - zlo) or 1.0)
        out = {}
        for i, pid in enumerate(ids):
            a, b = starts[i], starts[i + 1]
            part = scr[a:b]
            flat = part.reshape(-1, 2)
            top = flat[int(np.argmin(flat[:, 1]))] if len(flat) else np.array([cx, cy])
            out[pid] = [part[:, 0], part[:, 1], zn[a:b], top, self.weights[pid]]
        self.proj = out
        self._rect = getattr(self, "_box", area)
        return out

    def hit(self, q) -> str | None:
        if q is None or not self.proj:
            return None
        x, y, w, h = getattr(self, "_rect", (0, 0, 0, 0))
        if not (x <= q[0] <= x + w and y <= q[1] <= y + h):
            return None
        best, best_key = None, None
        for pid, (a, b, z, *_) in self.proj.items():
            d = point_segment_dist(q, a, b)
            i = int(np.argmin(d))
            if d[i] <= HIT_PX:
                key = (round(float(d[i]) / 4), -float(z[i]))                # nearest line, then nearest to you
                if best_key is None or key < best_key:
                    best, best_key = pid, key
        return best

    # drawing
    def role(self, pid: str, linked: list) -> str:
        if pid in linked and self.ctx.blocked:
            return "blocked"                       # safety colour wins over selection; selection keeps its brackets
        if pid == self.selected:
            return "selected"
        if pid in linked:
            return "step"
        if pid == self.hover:
            return "hover"
        return "base"

    COLORS = {"selected": J.CYAN, "step": J.CONFIRM, "blocked": J.ERROR, "hover": J.CYAN_SOFT, "base": J.TEXT_2}

    def draw(self, img, rect, now: float | None = None):
        if not self.active:
            return img
        self.tick(time.monotonic() if now is None else now)
        x, y, w, h = (int(v) for v in rect)
        H, W = img.shape[:2]
        x0, y0, x1, y1 = max(0, x), max(0, y), min(W, x + w), min(H, y + h)
        box = (x0, y0, x1 - x0, y1 - y0)
        if x1 - x0 < 40 or y1 - y0 < 40:
            self.proj = {}
            return img
        roi = img[y0:y1, x0:x1]
        cv2.convertScaleAbs(roi, dst=roi, alpha=self.backdrop, beta=4)  # the camera stays visible behind
        J.brackets(img, x0, y0, box[2] - 1, box[3] - 1, J.MUTED, L=18, thick=1)

        linked = self.linked()
        card = self._card_rows(linked, box)
        head, band, foot = 64, 62, 30
        bottom = (card["y"] - 14) if card else (y1 - foot)
        area = (x0 + 28, y0 + head + band, box[2] - 56, bottom - (y0 + head + band))
        if card and self.library and box[2] - card["w"] > 520:   # wide panel: the drawing sits beside the card
            left = card["x"] + card["w"] + 16
            area = (left, y0 + head + band, x0 + box[2] - 28 - left, (y1 - foot) - (y0 + head + band))
        if card and area[3] < 120:                         # small window: the drawing matters more than the card
            card = None
            area = (x0 + 28, area[1], box[2] - 56, (y1 - foot) - area[1])
        if area[2] < 80 or area[3] < 80:
            self.proj = {}
            J.text(img, "ATLAS needs a bigger window", x0 + 16, y0 + 30, 13, J.TEXT_2)
            return img
        self._box = box
        view = (box, area, round(float(self.explode.x), 4), round(self.yaw.x, 3), round(self.pitch.x, 3),
                round(self.roll.x, 3), self.grab_id, round(self.grab_x.x, 1), round(self.grab_y.x, 1),
                round(self.zoom.x, 4), round(self.pan_x.x, 1), round(self.pan_y.x, 1),
                self._sway_deg(),
                tuple(round(sp.x, 3) for sp in (self._fit or ())))
        settled = self._fit is not None and all(sp.settled for sp in self._fit)
        if view != getattr(self, "_view_key", None) or not settled or not self.proj:
            proj = self.project(area)                 # only when something moved: a still view costs no maths
            self._view_key = view
        else:
            proj = self.proj
        roles = tuple(self.role(k, linked) for k in proj)
        moving = view != getattr(self, "_last_view", None)
        self._last_view = view
        # motion detail: if a full drawing costs more than the frame budget on this machine, draw every 2nd or 3rd
        # line while the view moves; the full drawing comes back the moment it is still. A fast Mac never needs it.
        budget = getattr(config, "ATLAS_FRAME_BUDGET_MS", 14.0)
        ema = getattr(self, "_full_ms", 0.0)
        stride = 1 if not moving or ema <= budget else min(3, int(math.ceil(ema / budget)))
        layer_key = (view, roles, bool(self.selected or linked or self.hover), stride)
        if layer_key != getattr(self, "_layer_key", None) or getattr(self, "_layer", None) is None \
                or self._layer.shape != roi.shape:
            t0 = time.perf_counter()
            self._layer = self._draw_lines(np.zeros_like(roi), proj, linked, (x0, y0), stride)
            ms = (time.perf_counter() - t0) * 1000 * stride            # what a full drawing would have cost
            self._full_ms = ms if ema == 0 else ema + (ms - ema) * 0.2
            self._layer_key = layer_key
        self.stride = stride
        cv2.max(roi, self._layer, dst=roi)            # lines are drawn into the panel only: zoom never spills
        self._after_lines(img, proj, linked, box, card, head, band, foot, x0, y0, y1)
        return img

    def colour(self, pid: str, role: str) -> tuple:
        """Line colour of a part. Library: its family hue (lighter when pointed at or picked). Maintenance: the
        safety colours only."""
        if not self.library:
            return self.COLORS[role]
        t = self.tint[pid]
        return {"selected": mix(t, J.TEXT, 0.35), "hover": mix(t, J.TEXT, 0.5)}.get(role, t)

    def _draw_lines(self, layer, proj, linked, origin, stride: int = 1, ground: bool = True):
        x0, y0 = origin
        focus = bool(self.selected or linked or (self.library and self.hover))   # what you look at: the rest dims
        if ground:
            self._draw_ground(layer, proj, origin)
        order = sorted(proj, key=lambda k: ("base", "hover", "step", "blocked", "selected").index(self.role(k, linked)))
        for pid in order:
            a, b, z, _, wt = proj[pid]
            role = self.role(pid, linked)
            col = np.array(self.colour(pid, role), np.float32)
            thick = 2 if role in ("selected", "step", "blocked") + (("hover",) if self.library else ()) \
                and len(a) < 900 else 1                                        # dense parts stay thin
            shade = (0.6, 0.8, 1.0) if role in ("base", "hover") else (0.7, 0.86, 1.0)
            if self.backdrop > 0.3 and role == "base":           # a brighter camera behind: brighter lines
                shade = (0.72, 0.88, 1.05)
            if focus and role == "base":
                shade = tuple(v * (0.72 if self.library else 0.78) for v in shade)
            if stride > 1 and len(a) > 400:                       # moving on a slow machine: fewer lines until still
                a, b, z, wt = a[::stride], b[::stride], z[::stride], wt[::stride]
            bucket = np.minimum((z * 3).astype(int), 2) + 3 * (wt < 0.99)     # 3 depths x (feature edge, wire)
            for bi in range(6):
                m = bucket == bi
                if not m.any():
                    continue
                lines = ((np.stack([a[m], b[m]], axis=1) - (x0, y0)) * 16)
                lines = np.clip(lines, -2 ** 20, 2 ** 20).astype(np.int32)
                c = tuple(int(min(255, v)) for v in col * shade[bi % 3] * (1.0 if bi < 3 else 0.5))
                cv2.polylines(layer, lines, False, c, 1 if bi >= 3 else thick, cv2.LINE_AA, shift=4)
        return layer

    def _draw_ground(self, layer, proj, origin):
        """The turntable under the model (it turns and tilts with it) and, while exploded, a dashed guide from
        where each part sits assembled to where it is now."""
        x0, y0 = origin
        o = np.array([x0, y0], np.float32)
        rs = getattr(self, "_ring_scr", None)
        if rs is not None:
            ring, t0, t1 = rs
            base = J.MUTED
            pts = ((ring - o) * 16).astype(np.int32)
            cv2.polylines(layer, [pts], True, mix(base, (0, 0, 0), 0.25), 1, cv2.LINE_AA, shift=4)
            for i, (p, q) in enumerate(zip(t0 - o, t1 - o)):
                cv2.line(layer, tuple(int(v) for v in p), tuple(int(v) for v in q),
                         base if i % 6 == 0 else mix(base, (0, 0, 0), 0.35), 1, cv2.LINE_AA)
        g = getattr(self, "_guides", None)
        if g is None or float(self.explode.x) < 0.12:
            return
        ids = list(proj)
        for pid, p, q in zip(ids, g[0] - o, g[1] - o):
            d = float(np.hypot(*(q - p)))
            if d < 18:
                continue
            col = mix(self.colour(pid, "base") if self.library else J.MUTED, (0, 0, 0), 0.5)
            n = int(d // 7)
            for i in range(0, n, 2):                             # dashes 7 px long, 7 px apart
                u, v = p + (q - p) * (i / n), p + (q - p) * (min(n, i + 1) / n)
                cv2.line(layer, (int(u[0]), int(u[1])), (int(v[0]), int(v[1])), col, 1, cv2.LINE_AA)
            cv2.circle(layer, (int(p[0]), int(p[1])), 2, col, -1, cv2.LINE_AA)

    def _after_lines(self, img, proj, linked, box, card, head, band, foot, x0, y0, y1):
        if self.selected in proj:                  # selection brackets, so a picked part is visible in any colour
            a, b = proj[self.selected][:2]
            pts = np.concatenate([a, b])
            (sx0, sy0), (sx1, sy1) = pts.min(0) - 8, pts.max(0) + 8
            J.brackets(img, int(sx0), int(sy0), int(sx1 - sx0), int(sy1 - sy0), J.CYAN, L=8, thick=1)
        self._labels(img, (x0 + 16, y0 + head, box[2] - 32, band), linked)
        self._chrome(img, box)
        if card:
            self._card_draw(img, card)
        elif not (self.library and self.legend_outside):
            msg = ("Point at a part to light it up, pinch it to pull it out." if self.library else
                   "This step has no physical part. Pinch a part to see what guards it."
                   if self.ctx.text else "Pinch a part to see what it does and which rules guard it.")
            J.text(img, J.fit(msg, box[2] - 32, 13), x0 + 16, y1 - foot - 16, 13, J.TEXT_2)

    def _labels(self, img, band, linked):
        """Labels in a band above the drawing, staggered over three rows. Sorted left to right like their
        parts, so leaders do not cross, and a far-row leader passes between near-row labels."""
        bx, by, bw, bh = band
        e = float(np.clip(self.explode.x, 0, 1))
        items = []
        for pid, (_, _, _, top, *_) in self.proj.items():
            role = self.role(pid, linked)
            if (e < 0.55 or self.zoom.x > 1.3) and role == "base":    # zoomed in: only what you are looking at
                continue
            items.append((float(top[0]), pid, role, top))
        items.sort()
        rows = 3 if len(items) > 6 else (2 if len(items) > 3 else 1)
        row_y = [by + bh - 6 - 19 * r for r in range(rows)]            # row 0 sits nearest the drawing
        for r in range(rows):
            mine = [it for i, it in enumerate(items) if i % rows == r]
            placed = []
            for ax, pid, role, top in mine:
                strong = role != "base"
                size, kind = (12.5, "semi") if strong else (11.5, "sans")
                tw = J.measure(self.parts[pid].label, size, kind)
                lx = min(max(ax - tw / 2, bx), bx + bw - tw)
                if placed and lx < placed[-1][0] + placed[-1][1] + 14:
                    lx = placed[-1][0] + placed[-1][1] + 14
                placed.append([lx, tw, pid, role, top, size, kind])
            over = (placed[-1][0] + placed[-1][1]) - (bx + bw) if placed else 0
            if over > 0:                                                # slide the row back inside the band
                for i in range(len(placed) - 1, -1, -1):
                    limit = (bx + bw) if i == len(placed) - 1 else placed[i + 1][0] - 14
                    placed[i][0] = min(placed[i][0], limit - placed[i][1])
            for lx, tw, pid, role, top, size, kind in placed:
                strong = role != "base"
                col = self.colour(pid, role) if strong else J.TEXT_2
                lc = col if strong else (mix(self.tint[pid], (0, 0, 0), 0.35) if self.library else J.MUTED)
                ty = row_y[r]
                start = (int(lx + tw / 2), int(ty + 5))
                end = (int(top[0]), int(top[1]) - 3)
                if end[1] > start[1]:
                    cv2.line(img, start, end, lc, 1, cv2.LINE_AA)
                cv2.circle(img, end, 2, lc, -1, cv2.LINE_AA)
                J.text(img, self.parts[pid].label, lx, ty, size, col, kind)

    def _chrome(self, img, box):
        x, y, w, h = box
        e = float(np.clip(self.explode.x, 0, 1))
        state = "EXPLODED" if self.explode.target >= 0.5 else "ASSEMBLED"
        zoom = f"   ·   ZOOM {self.zoom.target:.1f}x" if abs(self.zoom.target - 1) > 0.05 else ""
        if self.library and self.legend_outside:          # the app's sidebar has the title, legend and card
            J.text(img, f"{state} {int(round(e * 100))}%   ·   {len(self.parts)} PARTS{zoom}", x + 16, y + 22, 10,
                   J.TEXT_2, "mono")
            credit = getattr(self.model, "credit", "")
            for i, ln in enumerate(J.wrap(credit, int(w * 0.6), 10.5)[:3]):           # never cut: CC BY needs it all
                J.text(img, ln, x + w - 16, y + 22 + 13 * i, 10.5, J.MUTED, anchor="r")
            self._family_key(img, x + w - 16, y + h - 12)
            return
        rows = GESTURES if self.library else [g for g in GESTURES if g[0] != "peace"]
        if self.library:
            J.label(img, getattr(self.model, "kind", "") or "Atlas", x + 16, y + 22, J.CYAN)
            J.text(img, self.model.label, x + 16, y + 45, 16, J.TEXT, "semi")
            J.text(img, f"{state} {int(round(e * 100))}%   ·   {len(self.parts)} PARTS{zoom}", x + w - 16, y + 22, 10,
                   J.TEXT_2, "mono", anchor="r")
            credit = getattr(self.model, "credit", "")
            for i, ln in enumerate(J.wrap(credit, int(w * 0.55), 10.5)[:3]):          # never cut: CC BY needs it all
                J.text(img, ln, x + w - 16, y + 40 + 13 * i, 10.5, J.MUTED, anchor="r")
            draw_strip(img, x + 16, y + h - 8, w - 32, self.hands.active, rows)
            return
        J.label(img, "Atlas", x + 16, y + 22, J.CYAN)
        J.text(img, f"{self.asset_id}  ·  {self.model.label}", x + 16, y + 45, 16, J.TEXT, "semi")
        J.text(img, f"{state} {int(round(e * 100))}%{zoom}   ·   SCHEMATIC, NOT OEM CAD", x + w - 16, y + 22, 10,
               J.TEXT_2, "mono", anchor="r")
        lx = x + w - 16
        for name, col in (("Selected", J.CYAN), ("WARDEN block", J.ERROR), ("This step", J.CONFIRM)):
            tw = J.text(img, name, lx, y + 44, 11, J.TEXT_2, anchor="r")
            cv2.circle(img, (int(lx - tw - 9), y + 40), 3, col, -1, cv2.LINE_AA)
            lx -= tw + 30
        draw_strip(img, x + 16, y + h - 8, w - 32, self.hands.active, rows)

    def _family_key(self, img, right, base):
        """The colour key: one dot and name per colour family this model uses, right-aligned on one line."""
        used = [k for k, *_ in FAMILIES if k in self.family.values()]
        lx = right
        for k in reversed(used):
            name, col = FAMILY[k]
            tw = J.text(img, name, lx, base, 10.5, J.TEXT_2, anchor="r")
            cv2.circle(img, (int(lx - tw - 8), base - 4), 3, col, -1, cv2.LINE_AA)
            lx -= tw + 26

    CARD_H = {"kicker": 20, "title": 24, "body": 18, "gap": 8, "kv": 18, "foot": 20}

    def _card_rows(self, linked, box):
        pid, kicker = None, ""
        if self.legend_outside and self.library:
            return None
        if self.hover and self.hands.pose == "point" and self.hover != self.selected:
            pid, kicker = self.hover, "Pointing at"
        elif self.selected:
            pid, kicker = self.selected, "Selected part"
        elif linked:
            pid = linked[0]
            kicker = "This step" + (f" · step {self.ctx.index} of {self.ctx.total}" if self.ctx.total else "")
        if pid is None:
            return None
        x, y, w, h = box
        cw = min(360, int(w * 0.46))
        p = self.parts[pid]
        accent = self.COLORS[self.role(pid, linked)]
        _, wtxt, wcol = self.preview(pid)
        in_step = pid in linked
        if in_step and self.ctx.blocked:                   # the step's real WARDEN decision outranks a preview
            wtxt, wcol = "This step is blocked by " + (", ".join(self.ctx.rule_ids) or "WARDEN"), J.ERROR
        rows = [("kicker", kicker.upper(), accent), ("title", p.label, J.TEXT)]
        rows += [("body", ln, J.TEXT_2) for ln in J.wrap(p.role, cw - 32, 13)]
        if self.library:
            ids = list(self.parts)
            rows.append(("foot", f"Part {ids.index(pid) + 1} of {len(ids)}  ·  pinch empty space to clear", J.MUTED))
            ch = 14 + sum(self.CARD_H[r[0]] for r in rows) + 6
            return {"rows": rows, "x": x + 16, "y": y + h - 30 - ch, "w": cw, "h": ch, "accent": accent}
        rows.append(("gap",))
        rows += [("kv", "WARDEN" if i == 0 else "", ln, wcol) for i, ln in enumerate(J.wrap(wtxt, cw - 32 - 84, 12.5))]
        step_txt = (f"In step {self.ctx.index} of {self.ctx.total}" if self.ctx.total else "In the current step") \
            if in_step else ("Not in the current step" if self.ctx.text else "No step on screen")
        rows.append(("kv", "STEP", step_txt, (J.ERROR if self.ctx.blocked else J.CONFIRM) if in_step else J.TEXT_2))
        if p.hazard:
            rows.append(("kv", "TAG", p.hazard, J.TEXT_2))
        rows.append(("foot", "Preview only. WARDEN checks every real step.", J.MUTED))
        ch = 14 + sum(self.CARD_H[r[0]] for r in rows) + 6
        return {"rows": rows, "x": x + 16, "y": y + h - 30 - ch, "w": cw, "h": ch, "accent": accent}

    def _card_draw(self, img, card):
        cx, cy, cw, ch = card["x"], card["y"], card["w"], card["h"]
        J.panel(img, cx, cy, cw, ch, 0.92, accent=card["accent"])
        yy = cy + 14
        for r in card["rows"]:
            k = r[0]
            if k == "kicker":
                J.label(img, r[1], cx + 16, yy + 10, r[2])
            elif k == "title":
                J.text(img, r[1], cx + 16, yy + 18, 17, r[2], "semi")
            elif k == "body":
                J.text(img, r[1], cx + 16, yy + 13, 13, r[2])
            elif k == "kv":
                if r[1]:
                    J.text(img, r[1], cx + 16, yy + 13, 9.5, J.MUTED, "semi", track=0.8)
                J.text(img, r[2], cx + 100, yy + 13, 12.5, r[3])
            elif k == "foot":
                J.text(img, r[1], cx + 16, yy + 15, 10, r[2], "mono")
            yy += self.CARD_H[k]
