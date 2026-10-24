"""ATLAS, the app: explore real machines with your hands.

    python atlas_app.py                 # camera: pinch, two-hand zoom, explode (Esc or Q quits)
    python atlas_app.py --model jwst    # start on one model
    python atlas_app.py --demo          # no camera: saves a picture of every model to data/atlas_frames/

Also in james.py: open your palm for the launcher and pinch the ATLAS tile, or say "open atlas".

The shelf holds the ATLAS library (atlas_library/: NASA's Perseverance, Ingenuity and Webb, a concept car,
a chronograph and a flight helmet, all openly licensed and credited on screen) plus the industry pack's pump.

Hands (the legend on the left lights up the one it sees):
        open slowly = explode  ·  close your hand = assemble  ·  move or twist = turn, tilt, roll
        point = highlight a part  ·  pinch a part = pull it out and drag it  ·  two hands = zoom
        peace sign, held = next model  ·  quick pinch on a shelf tile = that model, on Close = leave ATLAS
Keys:   A D turn  ·  W S tilt  ·  + - zoom  ·  E explode/assemble  ·  N B next/previous model  ·  Tab next part
        R reset the view  ·  X owner key on/off (key mode)  ·  Esc close
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import cv2
import numpy as np

import config
import jsil as J
from atlas import FAMILY, Atlas
from atlas_gestures import GESTURES, HandEngine, glyph
from james_core import handpose as HP
from james_core.atlas_library import load_library, from_pack

SHELF_H = 92
SIDE_W = 272             # the sidebar (title, tracking, explode, gesture legend, part card) from this window width
SIDE_FROM_W = 960
TILE_W, TILE_H = 132, 70
HINTS = ("Esc close | A D turn | W S tilt | + - zoom | E explode | N B model | Tab next part | R reset | "
         "X owner key on/off | Q quit")


def library_models(pack=None) -> tuple[list, list[str]]:
    models, problems = load_library()
    pack = config.PACK if pack is None else pack
    try:
        pump = from_pack(pack)
        if pump is not None:
            models.append(pump)
    except Exception as e:  # noqa: BLE001  the pack is the maintenance app's business; the shelf goes on
        problems.append(f"pack model: {e}")
    return models, problems


class AtlasViewer:
    """The ATLAS app screen: a shelf of models, one Atlas at a time, hands and keys. No job, no WARDEN,
    nothing it does leaves this window."""

    def __init__(self, models=None, problems=(), reduced_motion=None, idle_sway=None):
        if models is None:
            models, problems = library_models()
        if not models:
            raise RuntimeError("ATLAS has no models to show: " + "; ".join(problems))
        self.models, self.problems = list(models), list(problems)
        self.reduced = getattr(config, "REDUCED_MOTION", False) if reduced_motion is None else reduced_motion
        self.sway = getattr(config, "ATLAS_IDLE_SWAY", True) if idle_sway is None else idle_sway
        self.i = 0
        self.atlas: Atlas | None = None
        self._atl: dict = {}
        self.active = False
        self.header = J.Header()
        self.buttons: list = []                  # (key, rect)
        self.hover_ui = None
        self._ui_press = None
        self._was_pinch = False
        self._thumbs: dict = {}
        self.opened_at = 0.0
        self.msg = None                          # (text, until)
        self.hands = HandEngine()                # one for the window: switching models keeps the hand's state
        self.side = None                         # sidebar rect, when the window is wide enough
        self.n_hands = 0
        self.track = "off"
        self._cache: dict = {}
        self.fps = 0.0
        self._last_render = None

    # ---------- open / switch / close ----------
    def open(self, now: float | None = None, model_id: str | None = None):
        now = time.monotonic() if now is None else now
        if model_id is not None:
            ids = [m.id for m in self.models]
            if model_id not in ids:
                raise KeyError(f"no model '{model_id}'. Have: {', '.join(ids)}")
            self.i = ids.index(model_id)
        self.active, self.opened_at = True, now
        self._was_pinch, self._ui_press = True, None      # a pinch carried in from the launcher must end first
        self.show(self.i, now)
        if self.problems:
            self.flash("Left out: " + "; ".join(self.problems), now, 8)

    def close(self):
        self.active = False
        if self.atlas is not None:
            self.atlas.close()

    def show(self, i: int, now: float | None = None):
        now = time.monotonic() if now is None else now
        self.i = i % len(self.models)
        m = self.models[self.i]
        if self.atlas is not None and self.atlas.model is not m:     # keep one drawing layer, not one per model
            self.atlas._layer = self.atlas._pack = None
        a = self._atl.get(m.id)
        if a is None:
            a = self._atl[m.id] = Atlas(m, None, reduced_motion=self.reduced, idle_sway=self.sway)
        a.hands = self.hands
        a.open(m.label, now)
        self.atlas = a

    def step_model(self, d: int, now: float | None = None):
        self.show(self.i + d, now)

    def flash(self, text: str, now: float | None = None, secs: float = 3.0):
        self.msg = (text, (time.monotonic() if now is None else now) + secs)

    # ---------- layout ----------
    def layout(self, W: int, H: int):
        shelf_y = H - 30 - J.S3 - SHELF_H
        top = 52 + J.S3
        if W >= SIDE_FROM_W:
            self.side = (J.S4, top, SIDE_W, shelf_y - J.S3 - top)
            x = J.S4 + SIDE_W + J.S3
            rect = (x, top, W - J.S4 - x, shelf_y - J.S3 - top)
        else:
            self.side = None
            rect = (J.S4, top, W - 2 * J.S4, shelf_y - J.S3 - top)
        if self.atlas is not None:
            self.atlas.legend_outside = self.side is not None
        n = len(self.models)
        gap = J.S2
        side = 2 * (96 + gap)                                   # Reset and Close
        room = W - 2 * J.S4 - side
        tw = int(min(TILE_W, (room - gap * (n - 1)) / max(1, n)))
        x = J.S4
        self.buttons = []
        for k in range(n):
            self.buttons.append((f"model:{k}", (x, shelf_y + 8, tw, TILE_H)))
            x += tw + gap
        bx = W - J.S4 - 2 * 96 - gap
        self.buttons.append(("reset", (bx, shelf_y + 8, 96, TILE_H)))
        self.buttons.append(("close", (bx + 96 + gap, shelf_y + 8, 96, TILE_H)))
        return rect, shelf_y

    def ui_hit(self, p):
        if p is None:
            return None
        for key, (x, y, w, h) in self.buttons:
            if x - 4 <= p[0] <= x + w + 4 and y - 4 <= p[1] <= y + h + 4:
                return key
        return None

    def activate(self, key: str, now: float | None = None):
        if key.startswith("model:"):
            self.show(int(key[6:]), now)
        elif key == "reset":
            self.hands.hist.clear()
            self.atlas.reset_view()
            self.atlas.explode.target = 1.0
        elif key == "close":
            self.close()

    # ---------- input ----------
    def update(self, view, now: float):
        if not self.active or view is None:
            return
        tip = getattr(view, "fingertip", None)
        pinching = bool(getattr(view, "pinching", False)) and tip is not None
        aim = getattr(view, "pinch_point", None) or tip
        hand = getattr(view, "hand", None)                       # a pinch that began in a fist is not a press
        self._fist_pinch = pinching and (getattr(self, "_fist_pinch", False) or
                                         (HP.valid(hand) and HP.ratios(hand)[0] <= HP.PINCH_MIN_INDEX))
        if pinching and not self._was_pinch and not self._fist_pinch:
            key = self.ui_hit(aim)
            if key:
                self._ui_press = key                             # the shelf owns this pinch until release
        self.n_hands = (1 if getattr(view, "hand", None) or tip is not None else 0) + \
            len(getattr(view, "others", None) or [])
        self.track = ("on" if self.n_hands else
                      "key" if getattr(view, "owner", "") == "searching" and getattr(view, "others", None) else "off")
        if self._ui_press:
            if not pinching:                                     # a tap on a tile: act on release
                if self.ui_hit(aim) == self._ui_press or self.ui_hit(tip) == self._ui_press:
                    self.activate(self._ui_press, now)
                self._ui_press = None
            self._was_pinch = pinching
            self.hover_ui = self._ui_press
            self.hands.skip()
            return
        self._was_pinch = pinching
        self.hover_ui = self.ui_hit(tip)
        if self.hover_ui and not pinching and self.hands.grab is None:
            self.atlas.hover = None
            self.hands.skip()
            return
        self.atlas.update(view, now)
        for req in self.atlas.pop_requests():
            if req == "next":
                self.step_model(1, now)
                self.flash(self.models[self.i].label, now, 1.5)

    def key(self, c: str, now: float | None = None) -> bool:
        """One key press (lower-case character, or 'tab'). True if ATLAS used it."""
        a = self.atlas
        if not self.active or a is None:
            return False
        acts = {"a": lambda: a.turn(-20), "d": lambda: a.turn(20), "w": lambda: a.turn(0, -10),
                "s": lambda: a.turn(0, 10), "=": lambda: a.zoom_by(1.25), "+": lambda: a.zoom_by(1.25),
                "-": lambda: a.zoom_by(0.8), "e": a.toggle_explode, "n": lambda: self.step_model(1, now),
                "]": lambda: self.step_model(1, now), "b": lambda: self.step_model(-1, now),
                "[": lambda: self.step_model(-1, now), "r": lambda: self.activate("reset", now),
                "tab": lambda: a.select_next(1, now), "\t": lambda: a.select_next(1, now)}
        if c in acts:
            acts[c]()
            return True
        return False

    # ---------- drawing ----------
    def thumb(self, m) -> np.ndarray:
        if m.id not in self._thumbs:
            a = Atlas(m, None, reduced_motion=True, idle_sway=False)
            a.open(m.label, 0.0)
            a.explode.target = 0.0
            a.settle()
            img = np.zeros((TILE_H - 22, TILE_W - 8, 3), np.uint8)
            a._box = (0, 0, img.shape[1], img.shape[0])
            proj = a.project((4, 4, img.shape[1] - 8, img.shape[0] - 8))
            a._draw_lines(img, proj, [], (0, 0), ground=False)
            self._thumbs[m.id] = img
        return self._thumbs[m.id]

    def render(self, frame, view=None, now: float | None = None):
        now = time.monotonic() if now is None else now
        if self._last_render is not None and now > self._last_render:
            f = 1.0 / (now - self._last_render)
            self.fps = f if self.fps == 0 else self.fps + (f - self.fps) * 0.1
        self._last_render = now
        H, W = frame.shape[:2]
        cv2.convertScaleAbs(frame, dst=frame, alpha=0.62)
        rect, shelf_y = self.layout(W, H)
        if self.atlas is not None and rect[2] > 200 and rect[3] > 160:
            self.atlas.draw(frame, rect, now)
        # header
        if self.side is not None:
            self.draw_side(frame, now)
        x = self.header.draw(frame)
        x = J.pill(frame, "Atlas", x, 31, J.CYAN)
        fps = [(None, f"{self.fps:.0f} FPS")] if self.fps > 0 else []
        J.status_right(frame, [(J.SUCCESS, "OFFLINE"), (None, f"{len(self.models)} MODELS")] + fps +
                       [(None, time.strftime("%H:%M"))], y=25)
        J.text(frame, "Open models, credited on screen  ·  atlas_library/NOTICE.md", W - J.S4, 43, 10, J.MUTED,
               "mono", anchor="r")
        self.draw_shelf(frame, shelf_y)
        self.draw_hands(frame, view)
        if self.msg and now < self.msg[1]:
            J.toast(frame, self.msg[0], J.CONFIRM, y_bottom=shelf_y - J.S3)
        J.hints(frame, HINTS, True)
        return frame

    def _cached(self, img, name, key, rect, paint):
        """Paint a panel only when what it shows changes; every other frame is one blend. The sidebar and the
        shelf hold about 60 pieces of text: drawing them every frame was most of the app's own cost."""
        x, y, w, h = rect
        H, W = img.shape[:2]
        x0, y0, x1, y1 = max(0, x), max(0, y), min(W, x + w), min(H, y + h)
        if x1 <= x0 or y1 <= y0:
            return
        c = self._cache.get(name)
        if c is None or c[0] != key or c[1].shape[:2] != (y1 - y0, x1 - x0):
            full = np.zeros((y1, x1, 3), np.uint8)
            paint(full)
            c = self._cache[name] = (key, full[y0:y1, x0:x1].copy())
        roi = img[y0:y1, x0:x1]
        cv2.addWeighted(roi, 0.1, c[1], 1.0, 0, dst=roi)

    def draw_side(self, img, now):
        a, e = self.atlas, self.hands
        if a is None:
            return
        pid = a.hover if (a.hover and e.pose == "point") else a.selected
        key = (a.model.id, self.track, self.n_hands, e.active, int(round(float(np.clip(a.explode.x, 0, 1)) * 100)),
               round(a.zoom.target, 1), pid, e.pose == "point", int(e.progress * 30), self.side)
        self._cached(img, "side", key, self.side, lambda c: self._paint_side(c, now))

    def _paint_side(self, img, now):
        """Left sidebar: the model, tracking, explode level, the gesture legend (the gesture the hands are
        making right now is lit amber) and the part card."""
        x, y, w, h = self.side
        a, e = self.atlas, self.hands
        if a is None or h < 200:
            return
        J.panel(img, x, y, w, h, 0.9)
        pad = 14
        cx, iw, right = x + pad, w - 2 * pad, x + w - pad
        m = a.model
        yy = y + 22
        J.label(img, getattr(m, "kind", "") or "Atlas", cx, yy, J.CYAN)
        J.text(img, f"{len(a.parts)} PARTS", right, yy, 9.5, J.MUTED, "mono", anchor="r")
        for ln in J.wrap(m.label, iw, 17, "semi")[:2]:
            yy += 23
            J.text(img, ln, cx, yy, 17, J.TEXT, "semi")
        # tracking and hands
        yy += 12
        tw = (iw - 8) // 2
        track = {"on": ("Hand on", J.SUCCESS), "key": ("Show your key", J.CONFIRM), "off": ("No hand", J.TEXT_2)}
        word, col = track[self.track]
        for i, (k, v, c) in enumerate((("TRACKING", word, col), ("HANDS", str(self.n_hands), J.TEXT))):
            bx = cx + i * (tw + 8)
            J.panel(img, bx, yy, tw, 40, 0.95, J.SURFACE_2, highlight=False)
            J.text(img, k, bx + 10, yy + 15, 9, J.MUTED, "semi", track=0.8)
            J.text(img, v, bx + 10, yy + 32, 12.5, c, "semi")
        yy += 40 + 22
        # explode level
        ex = float(np.clip(a.explode.x, 0, 1))
        J.text(img, "EXPLODE", cx, yy, 9, J.MUTED, "semi", track=0.8)
        zoom = f"ZOOM {a.zoom.target:.1f}x   " if abs(a.zoom.target - 1) > 0.05 else ""
        J.text(img, f"{zoom}{int(round(ex * 100))}%", right, yy, 10, J.TEXT_2, "mono", anchor="r")
        yy += 9
        cv2.line(img, (cx, yy), (right, yy), J.RULE, 2)
        if ex > 0.005:
            cv2.line(img, (cx, yy), (cx + int(iw * ex), yy), J.CONFIRM, 2)
        cv2.circle(img, (cx + int(iw * ex), yy), 4, J.CONFIRM, -1, cv2.LINE_AA)
        yy += 24
        # the legend
        J.text(img, "GESTURES", cx, yy, 9, J.MUTED, "semi", track=0.8)
        J.text(img, "lit = what I see", right, yy, 9.5, J.MUTED, anchor="r")
        yy += 8
        card_h = 96
        rows_h = (y + h - pad - card_h - 8) - yy
        rh = int(min(42, max(24, rows_h / len(GESTURES))))
        sub = rh >= 33
        for k, title, what in GESTURES:
            on = k == e.active
            if on:
                J.panel(img, cx - 4, yy + 1, iw + 8, rh - 2, 0.95, J.SURFACE_2, border=J.CONFIRM, highlight=False)
            g = min(rh - 6, 30)
            glyph(img, k, cx + 2, yy + (rh - g) // 2, g, J.CONFIRM if on else J.TEXT_2)
            tx = cx + g + 14
            if sub:
                J.text(img, title, tx, yy + rh // 2 - 2, 12.5, J.TEXT if on else J.TEXT_2, "semi")
                J.text(img, what, tx, yy + rh // 2 + 13, 10.5, J.TEXT_2 if on else J.MUTED)
            else:
                J.text(img, title, tx, yy + rh // 2 + 5, 12, J.TEXT if on else J.TEXT_2, "semi")
                J.text(img, what, right, yy + rh // 2 + 5, 10, J.MUTED, anchor="r")
            if k == "peace" and e.progress > 0:
                cv2.line(img, (cx - 3, yy + rh - 3), (cx - 3 + int((iw + 6) * e.progress), yy + rh - 3), J.CYAN, 2)
            yy += rh
        # the part card
        cy = y + h - pad - card_h
        pid, kicker = None, ""
        if a.hover and e.pose == "point":
            pid, kicker = a.hover, "Pointing at"
        elif a.selected:
            pid, kicker = a.selected, "Picked"
        J.panel(img, cx - 4, cy, iw + 8, card_h, 0.95, J.SURFACE_2, highlight=False,
                accent=(a.tint[pid] if pid else None))
        if pid:
            p = a.parts[pid]
            ids = list(a.parts)
            fam_name, fam_col = FAMILY[a.family[pid]]
            J.label(img, kicker, cx + 6, cy + 18, J.CYAN, 9.5)
            kw = J.measure(kicker.upper(), 9, "semi", 1.2)
            cv2.circle(img, (int(cx + 6 + kw + 12), cy + 14), 3, fam_col, -1, cv2.LINE_AA)
            J.text(img, fam_name, cx + 6 + kw + 20, cy + 18, 9.5, J.TEXT_2)
            J.text(img, f"{ids.index(pid) + 1} / {len(ids)}", right - 2, cy + 18, 9.5, J.MUTED, "mono", anchor="r")
            J.text(img, J.fit(p.label, iw - 12, 14, "semi"), cx + 6, cy + 38, 14, J.TEXT, "semi")
            for i, ln in enumerate(J.wrap(p.role, iw - 12, 11.5)[:3]):
                J.text(img, ln, cx + 6, cy + 56 + 15 * i, 11.5, J.TEXT_2)
        else:
            J.text(img, "No part picked", cx + 6, cy + 24, 13, J.TEXT_2, "semi")
            for i, ln in enumerate(J.wrap("Point at a part to light it up. Pinch it to pull it out and read "
                                          "what it does.", iw - 12, 11.5)[:3]):
                J.text(img, ln, cx + 6, cy + 44 + 15 * i, 11.5, J.MUTED)

    def draw_shelf(self, img, shelf_y):
        key = (self.i, self.hover_ui, tuple(self.buttons), img.shape[1], len(self.models))
        self._cached(img, "shelf", key, (0, shelf_y, img.shape[1], SHELF_H), lambda c: self._paint_shelf(c, shelf_y))

    def _paint_shelf(self, img, shelf_y):
        W = img.shape[1]
        J.panel(img, 0, shelf_y, W, SHELF_H, 0.9, J.BLACK, border=None, highlight=False)
        for key, (x, y, w, h) in self.buttons:
            current = key == f"model:{self.i}"
            hover = key == self.hover_ui
            J.panel(img, x, y, w, h, 0.95, J.SURFACE_2 if (hover or current) else J.SURFACE,
                    border=J.CYAN if hover else (J.TEAL if current else J.RULE))
            if key.startswith("model:"):
                m = self.models[int(key[6:])]
                th = self.thumb(m)
                tw = min(th.shape[1], w - 8)
                tile = img[y + 4:y + 4 + th.shape[0], x + 4:x + 4 + tw]
                cv2.max(tile, th[:, :tw], dst=tile)
                J.text(img, J.fit(m.label, w - 12, 11), x + 6, y + h - 7, 11, J.TEXT if current else J.TEXT_2,
                       "semi" if current else "sans")
            else:
                label = {"reset": "Reset view", "close": "Close"}[key]
                cx, cy = x + w // 2, y + h // 2 - 8
                col = J.CYAN if hover else J.TEXT_2
                if key == "close":
                    cv2.line(img, (cx - 9, cy - 9), (cx + 9, cy + 9), col, 2, cv2.LINE_AA)
                    cv2.line(img, (cx + 9, cy - 9), (cx - 9, cy + 9), col, 2, cv2.LINE_AA)
                else:
                    cv2.ellipse(img, (cx, cy), (11, 11), 0, 40, 330, col, 2, cv2.LINE_AA)
                    cv2.line(img, (cx + 8, cy - 9), (cx + 13, cy - 3), col, 2, cv2.LINE_AA)
                J.text(img, label, cx, y + h - 9, 11.5, col, anchor="c")

    def draw_hands(self, img, view):
        if view is None:
            return
        for oh in getattr(view, "others", None) or []:
            J.skeleton(img, oh, J.HAND_LINKS, J.MUTED)
        if getattr(view, "hand", None):
            J.skeleton(img, view.hand, J.HAND_LINKS, J.CYAN_SOFT)
        if self.atlas is not None:
            self.hands.draw_ring(img, self.atlas)
        tip = getattr(view, "fingertip", None)
        if tip is not None and self.hands.pose in ("point", "pinch", None):
            hovering = (self.atlas is not None and self.atlas.hover) or self.hover_ui
            st = "TARGETED" if hovering else ("TARGETABLE" if getattr(view, "pinching", False) else "TRACKING")
            J.cursor(img, tip, st, 0.0)


# ---------------- standalone ----------------
def demo(out_dir: Path, models=None) -> list[Path]:
    """No camera: a picture of every model (exploded, with a part picked) and one zoomed view. For checking a
    build and for screenshots."""
    out_dir.mkdir(parents=True, exist_ok=True)
    v = AtlasViewer(models, reduced_motion=True, idle_sway=False)
    paths = []
    bg = np.full((config.FRAME_H, config.FRAME_W, 3), (70, 74, 78), np.uint8)
    v.open(0.0)
    for k, m in enumerate(v.models):
        v.show(k, 0.0)
        v.atlas.settle()
        v.render(bg.copy(), None, 1.0)                       # first draw sets up the projection
        v.atlas.select(m.parts[0].id, 1.0)
        v.hands.active = "pinch"                             # the legend as it looks while a part is pinched
        p = out_dir / f"{k + 1:02d}_{m.id}.png"
        cv2.imwrite(str(p), v.render(bg.copy(), None, 1.0))
        paths.append(p)
    v.show(0, 0.0)
    v.atlas.zoom_by(2.0)
    v.atlas.turn(60, 10)
    v.atlas.settle()
    p = out_dir / f"{len(v.models) + 1:02d}_zoomed.png"
    cv2.imwrite(str(p), v.render(bg.copy(), None, 2.0))
    paths.append(p)
    return paths


def run_camera(model_id: str | None = None):
    from agents.lens import Lens
    from app import mirror_result
    viewer = AtlasViewer()
    viewer.open(model_id=model_id)
    cap = cv2.VideoCapture(config.CAMERA_INDEX)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, config.FRAME_W)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, config.FRAME_H)
    if not cap.isOpened():
        sys.exit("Camera not available. Allow camera access for Terminal in System Settings > Privacy & Security.")
    lens = Lens()
    t0 = time.monotonic()
    cv2.namedWindow("JAMES ATLAS", cv2.WINDOW_NORMAL)
    # heat: never draw faster than ATLAS_MAX_FPS, and with no hand in view for a few seconds, look for one on
    # every 3rd frame only (hand tracking is the biggest cost; a hand coming back is still found within 0.1 s)
    min_dt = 1.0 / max(5, getattr(config, "ATLAS_MAX_FPS", 30))
    idle_after = getattr(config, "ATLAS_IDLE_AFTER_S", 3.0)
    from agents.lens import LensResult
    last_hand, n = time.monotonic(), 0
    try:
        while viewer.active:
            loop_t = time.monotonic()
            ok, frame = cap.read()
            if not ok:
                break
            n += 1
            if loop_t - last_hand > idle_after and n % 3:
                r = LensResult()
            else:
                r = lens.process(frame, int((time.monotonic() - t0) * 1000))
                if r.hand or r.fingertip or r.others:
                    last_hand = loop_t
            if config.MIRROR:
                frame, r = cv2.flip(frame, 1), mirror_result(r, frame.shape[1])
            now = time.monotonic()
            viewer.update(r, now)
            cv2.imshow("JAMES ATLAS", viewer.render(frame, r, now))
            spare = min_dt - (time.monotonic() - loop_t)
            k = cv2.waitKey(max(1, int(spare * 1000))) & 0xFF
            if k == 255:
                continue
            if k in (27,) or chr(k).lower() == "q":
                break
            if chr(k).lower() == "x" and getattr(lens.owner, "kind", "") == "key":   # same as james.py
                off = lens.toggle_pause()
                viewer.flash("Owner key off: any hand drives ATLAS. X turns it back on." if off else
                             "Owner key on: only the hand wearing the key drives ATLAS.", now, 4)
                continue
            viewer.key("tab" if k == 9 else chr(k).lower(), now)
    finally:
        lens.close()
        cap.release()
        cv2.destroyAllWindows()


def main(argv=None):
    ap = argparse.ArgumentParser(description="ATLAS: explore real machines with your hands")
    ap.add_argument("--demo", action="store_true", help="no camera: save a picture of every model")
    ap.add_argument("--model", help="start on this model id (see --list)")
    ap.add_argument("--list", action="store_true", help="list the models and their credits")
    a = ap.parse_args(argv)
    if a.list:
        models, problems = library_models()
        for m in models:
            print(f"{m.id:38s} {len(m.parts):2d} parts  {m.label}  ·  {m.licence}  ·  {m.credit}")
        for p in problems:
            print("LEFT OUT:", p)
        return
    if a.demo:
        for p in demo(config.DATA / "atlas_frames"):
            print(p.relative_to(config.ROOT))
        return
    run_camera(a.model)


if __name__ == "__main__":
    main()
