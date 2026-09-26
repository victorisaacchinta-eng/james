"""JSIL v1.0: the JAMES Spatial Interface Language, as code.

One module both HUDs use: colour tokens, spacing, type, surfaces, the JAMES mark, the spatial
cursor, target brackets, toasts, and real frame telemetry.

Renderer (JSIL sections 40 to 43): the camera frame stays a NumPy/OpenCV image.
- Text is rasterised once by PIL into an alpha mask and cached; each frame only alpha-blends it.
- Geometry (cursor, brackets, skeleton, rings) is drawn with cv2, which is cheap.
- Static chrome (the header) is built once per window size and composited each frame.
No blur, no glow, no particles. Glass comes from layering and contrast.
"""
from __future__ import annotations

import math
import os
import resource
import sys
import time
from collections import deque
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
BRANDING = ROOT / "assets" / "branding"


# ---------- colour tokens (BGR for OpenCV) ----------
def hex_bgr(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[4:6], 16), int(h[2:4], 16), int(h[0:2], 16)


BLACK = hex_bgr("#080D0F")
SURFACE = hex_bgr("#0D1417")
SURFACE_2 = hex_bgr("#111B1E")
RULE = hex_bgr("#1F2D31")           # thin borders
HILITE = hex_bgr("#2A3C40")         # inner top highlight
CYAN = hex_bgr("#35E0D0")           # active interaction and system intelligence only
TEAL = hex_bgr("#2AB8B0")
CYAN_SOFT = hex_bgr("#68E6E0")
TEXT = hex_bgr("#EAF4F4")
TEXT_2 = hex_bgr("#93A6A8")
MUTED = hex_bgr("#5D7073")
SUCCESS = hex_bgr("#4CC38A")        # restrained green
CONFIRM = hex_bgr("#F2B544")        # amber
ERROR = hex_bgr("#E5534B")          # restrained red; also WARDEN blocks
LOCKED = hex_bgr("#6E7F91")         # muted blue-grey

SPACE = (4, 8, 12, 16, 24, 32, 48, 64)
S1, S2, S3, S4, S6, S8 = 4, 8, 12, 16, 24, 32

# ---------- type ----------
_SANS = ["/System/Library/Fonts/SFNS.ttf", "/Library/Fonts/Inter-Regular.ttf",
         os.path.expanduser("~/Library/Fonts/Inter-Regular.ttf"), "/usr/share/fonts/opentype/inter/Inter-Regular.otf",
         "/System/Library/Fonts/Helvetica.ttc", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
_SANS_SEMI = ["/Library/Fonts/Inter-SemiBold.ttf", os.path.expanduser("~/Library/Fonts/Inter-SemiBold.ttf"),
              "/usr/share/fonts/opentype/inter/Inter-SemiBold.otf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
_MONO = ["/System/Library/Fonts/SFNSMono.ttf", "/System/Library/Fonts/Menlo.ttc",
         "/usr/share/fonts/truetype/jetbrains-mono/JetBrainsMono-Regular.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"]


def _first(paths):
    return next((p for p in paths if os.path.exists(p)), None)


@lru_cache(maxsize=None)
def font(size: int, kind: str = "sans") -> ImageFont.FreeTypeFont:
    """kind: sans | semi | mono. SF Pro on macOS (variable font: weight set by name), Inter elsewhere."""
    size = max(6, int(size))
    if kind == "mono":
        p = _first(_MONO)
        return ImageFont.truetype(p, size) if p else ImageFont.load_default(size)
    if kind == "semi":
        sf = "/System/Library/Fonts/SFNS.ttf"
        if os.path.exists(sf):
            f = ImageFont.truetype(sf, size)
            try:
                names = f.get_variation_names()
                pick = next((n for n in names if n.lower() in (b"semibold", b"medium")), None)
                if pick:
                    f.set_variation_by_name(pick)
            except Exception:  # noqa: BLE001  non-variable build: stays regular
                pass
            return f
        p = _first(_SANS_SEMI) or _first(_SANS)
        return ImageFont.truetype(p, size) if p else ImageFont.load_default(size)
    p = _first(_SANS)
    return ImageFont.truetype(p, size) if p else ImageFont.load_default(size)


_MASKS: dict = {}


def _mask(s: str, size: int, kind: str, track: float) -> tuple[np.ndarray, int]:
    """Alpha mask for a string, and its ascent (baseline offset). Cached."""
    key = (s, size, kind, track)
    m = _MASKS.get(key)
    if m is not None:
        return m
    if len(_MASKS) > 1500:
        _MASKS.clear()
    f = font(size, kind)
    asc, desc = f.getmetrics()
    w = int(math.ceil(measure(s, size, kind, track))) + 2
    img = Image.new("L", (max(1, w), asc + desc + 2), 0)
    d = ImageDraw.Draw(img)
    if track:
        x = 0.0
        for ch in s:
            d.text((x, asc), ch, font=f, fill=255, anchor="ls")
            x += f.getlength(ch) + track
    else:
        d.text((0, asc), s, font=f, fill=255, anchor="ls")
    m = (np.asarray(img, dtype=np.float32) / 255.0, asc)
    _MASKS[key] = m
    return m


@lru_cache(maxsize=4096)
def measure(s: str, size: int, kind: str = "sans", track: float = 0.0) -> float:
    f = font(size, kind)
    return f.getlength(s) + (track * max(0, len(s) - 1) if track else 0)


def _blend(img, x, y, alpha, color):
    H, W = img.shape[:2]
    h, w = alpha.shape
    x0, y0, x1, y1 = max(0, x), max(0, y), min(W, x + w), min(H, y + h)
    if x0 >= x1 or y0 >= y1:
        return
    a = alpha[y0 - y:y1 - y, x0 - x:x1 - x][..., None]
    roi = img[y0:y1, x0:x1].astype(np.float32)
    img[y0:y1, x0:x1] = (roi + (np.asarray(color, np.float32) - roi) * a).astype(np.uint8)


def _composite(img, x, y, col_u8, a, inv):
    """img region = col * a + img * (1 - a), with OpenCV's native blend (no float temporaries)."""
    H, W = img.shape[:2]
    h, w = a.shape
    x0, y0, x1, y1 = max(0, x), max(0, y), min(W, x + w), min(H, y + h)
    if x0 >= x1 or y0 >= y1:
        return
    sl = (slice(y0 - y, y1 - y), slice(x0 - x, x1 - x))
    c, aa, ii = col_u8[sl], a[sl], inv[sl]
    if not c.flags.c_contiguous:
        c, aa, ii = np.ascontiguousarray(c), np.ascontiguousarray(aa), np.ascontiguousarray(ii)
    img[y0:y1, x0:x1] = cv2.blendLinear(c, np.ascontiguousarray(img[y0:y1, x0:x1]), aa, ii)


def text(img, s, x, y, size=15, color=TEXT, kind="sans", anchor="l", opacity=1.0, track=0.0) -> int:
    """Draw `s` with its baseline at y. anchor: l | c | r. Returns the drawn width."""
    s = str(s)
    if not s:
        return 0
    mask, asc = _mask(s, int(size), kind, track)
    w = mask.shape[1]
    if anchor == "c":
        x -= w // 2
    elif anchor == "r":
        x -= w
    _blend(img, int(x), int(y - asc), mask * opacity if opacity < 1 else mask, color)
    return w


def label(img, s, x, y, color=TEXT_2, size=10.5, anchor="l"):
    """Small uppercase section label with tracking (JSIL section 08)."""
    return text(img, s.upper(), x, y, size, color, "semi", anchor, track=1.2)


def wrap(s: str, width: int, size=15, kind="sans") -> list[str]:
    words, lines, cur = str(s).split(), [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if measure(trial, int(size), kind) > width and cur:
            lines.append(cur)
            cur = w
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines


def fit(s: str, width: int, size=15, kind="sans") -> str:
    """One line that never overflows: trims with an ellipsis instead of clipping."""
    s = str(s)
    if measure(s, int(size), kind) <= width:
        return s
    while s and measure(s + "...", int(size), kind) > width:
        s = s[:-1]
    return s.rstrip() + "..."


# ---------- surfaces ----------
def panel(img, x, y, w, h, alpha=0.84, fill=SURFACE, border=RULE, accent=None, highlight=True):
    """Dark translucent instrument surface: fill, 1 px border, faint inner top highlight,
    optional 2 px state accent on the left edge. Near-square corners by design."""
    H, W = img.shape[:2]
    x0, y0, x1, y1 = max(0, int(x)), max(0, int(y)), min(W, int(x + w)), min(H, int(y + h))
    if x0 >= x1 or y0 >= y1:
        return
    roi = img[y0:y1, x0:x1]
    dim = cv2.addWeighted(roi, 1 - alpha, roi, 0, 0)
    img[y0:y1, x0:x1] = cv2.add(dim, (fill[0] * alpha, fill[1] * alpha, fill[2] * alpha, 0))
    if border is not None:
        cv2.rectangle(img, (x0, y0), (x1 - 1, y1 - 1), border, 1)
    if highlight and y1 - y0 > 6:
        cv2.line(img, (x0 + 1, y0 + 1), (x1 - 2, y0 + 1), HILITE, 1)
    if accent is not None:
        cv2.rectangle(img, (x0, y0), (x0 + 1, y1 - 1), accent, -1)


def pill(img, s, x, y, color=TEXT_2, filled=False, size=10.5) -> int:
    """State pill: dot + uppercase label. Baseline at y. Returns the right edge."""
    s = s.upper()
    tw = int(measure(s, int(size), "semi", 1.0))
    w, h = tw + 26, int(size) + 11
    top = y - int(size) - 4
    if filled:
        cv2.rectangle(img, (x, top), (x + w, top + h), color, -1)
        fg = BLACK
    else:
        cv2.rectangle(img, (x, top), (x + w, top + h), color, 1)
        fg = color
    cv2.circle(img, (x + 10, top + h // 2), 3, fg, -1, cv2.LINE_AA)
    text(img, s, x + 18, y, size, fg, "semi", track=1.0)
    return x + w


def switch(img, x, y_mid, on: bool, label: str = "VOICE", state: str = "normal", progress: float = 0.0) -> tuple:
    """A small on/off switch: LABEL [track + knob]. state: normal | targeted | confirming (pinch filling).
    Returns the hit rect (x, y, w, h), a little larger than what is drawn so a fingertip can find it."""
    tw = int(measure(label, 10, "semi", 1.0))
    text(img, label, x, y_mid + 4, 10, TEXT if on else MUTED, "semi", track=1.0)
    tx, W, H = x + tw + 10, 34, 18
    r = H // 2
    top, left, right = y_mid - r, tx + r, tx + W - r
    col = CYAN if on else RULE
    if on:
        cv2.rectangle(img, (left, top), (right, top + H), col, -1)
        cv2.circle(img, (left, y_mid), r, col, -1, cv2.LINE_AA)
        cv2.circle(img, (right, y_mid), r, col, -1, cv2.LINE_AA)
    else:
        for (ax, ay) in ((left, y_mid), (right, y_mid)):
            cv2.circle(img, (ax, ay), r, MUTED, 1, cv2.LINE_AA)
        cv2.rectangle(img, (left, top + 1), (right, top + H - 1), BLACK, -1)
        cv2.line(img, (left, top), (right, top), MUTED, 1, cv2.LINE_AA)
        cv2.line(img, (left, top + H), (right, top + H), MUTED, 1, cv2.LINE_AA)
    kx = right if on else left
    cv2.circle(img, (kx, y_mid), r - 3, BLACK if on else TEXT_2, -1, cv2.LINE_AA)
    rect = (x - 6, y_mid - 15, tx + W - x + 12, 30)
    if state in ("targeted", "confirming"):
        rx, ry, rw, rh = rect
        cv2.rectangle(img, (rx, ry), (rx + rw, ry + rh), CYAN if state == "targeted" else CONFIRM, 1, cv2.LINE_AA)
        if state == "confirming":
            cv2.line(img, (rx, ry + rh + 2), (rx + int(rw * min(1.0, progress)), ry + rh + 2), CONFIRM, 2)
    return rect


def dot(img, x, y, color, r=3):
    cv2.circle(img, (int(x), int(y)), r, color, -1, cv2.LINE_AA)


# ---------- the JAMES mark ----------
@lru_cache(maxsize=16)
def mark(size: int) -> np.ndarray:
    """The emblem as crisp flat geometry (BGRA), for runtime sizes. The raster logo in
    assets/branding is for splash, about and presentation use."""
    ss = 4
    S = size * ss
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)

    def P(pts):
        return [(px / 100 * S, (py / 100 * 0.92 + 0.04) * S) for px, py in pts]

    cy, cs, tl = (53, 214, 208), (42, 184, 176), (104, 230, 224)            # RGB
    d.polygon(P([(50, 0), (1, 97), (13, 97), (50, 22)]), fill=cy + (255,))    # left leg
    d.polygon(P([(50, 0), (99, 97), (87, 97), (50, 22)]), fill=cs + (255,))   # right leg
    d.polygon(P([(15, 88), (34, 57), (44, 61), (27, 88)]), fill=cs + (235,))  # lower facets
    d.polygon(P([(85, 88), (66, 57), (56, 61), (73, 88)]), fill=cy + (235,))
    c = (50, 50)                                                               # four-point core
    d.polygon(P([(c[0], c[1] - 20), (c[0] + 3, c[1]), (c[0], c[1] + 26), (c[0] - 3, c[1])]), fill=(234, 244, 244, 255))
    d.polygon(P([(c[0] - 22, c[1]), (c[0], c[1] - 2.6), (c[0] + 22, c[1]), (c[0], c[1] + 2.6)]), fill=tl + (255,))
    im = im.resize((size, size), Image.LANCZOS)
    a = np.asarray(im).astype(np.float32)
    return np.dstack([a[..., 2], a[..., 1], a[..., 0], a[..., 3]])            # BGRA


def blit_rgba(img, bgra: np.ndarray, x: int, y: int, opacity=1.0):
    alpha = bgra[..., 3] / 255.0 * opacity
    H, W = img.shape[:2]
    h, w = alpha.shape
    x0, y0, x1, y1 = max(0, x), max(0, y), min(W, x + w), min(H, y + h)
    if x0 >= x1 or y0 >= y1:
        return
    a = alpha[y0 - y:y1 - y, x0 - x:x1 - x][..., None]
    src = bgra[y0 - y:y1 - y, x0 - x:x1 - x, :3]
    roi = img[y0:y1, x0:x1].astype(np.float32)
    img[y0:y1, x0:x1] = (roi * (1 - a) + src * a).astype(np.uint8)


@lru_cache(maxsize=4)
def logo(height: int) -> np.ndarray | None:
    """Full raster logo (emblem + JAMES + expansion) for the splash. None if the asset is missing."""
    p = BRANDING / "james_full_logo.png"
    if not p.exists():
        return None
    im = Image.open(p).convert("RGBA")
    im = im.resize((int(im.width * height / im.height), height), Image.LANCZOS)
    a = np.asarray(im).astype(np.float32)
    return np.dstack([a[..., 2], a[..., 1], a[..., 0], a[..., 3]])


def splash(img, t_since_start: float, hold=1.1, fade=0.5) -> bool:
    """Brand splash at start. Returns False once finished (then costs nothing)."""
    if t_since_start > hold + fade:
        return False
    k = 1.0 if t_since_start < hold else 1 - (t_since_start - hold) / fade
    H, W = img.shape[:2]
    roi = img
    img[:] = cv2.addWeighted(roi, 1 - 0.92 * k, np.full_like(roi, BLACK), 0.92 * k, 0)
    lg = logo(int(H * 0.42))
    if lg is not None:
        blit_rgba(img, lg, (W - lg.shape[1]) // 2, (H - lg.shape[0]) // 2 - 10, k)
    return True


# ---------- header (static chrome cached per window size) ----------
class Header:
    """Top-left: [mark] JAMES + expansion. The chrome is rendered once per size and composited."""
    H = 52

    def __init__(self):
        self._key = None
        self._layer = None       # (color float32 HxWx3, alpha float32 HxW)
        self.end_x = 360

    def _build(self, W, subtitle):
        col = np.zeros((self.H, W, 3), np.float32)
        al = np.zeros((self.H, W), np.float32)
        m = mark(26)
        col[13:39, 16:42] = m[..., :3]
        al[13:39, 16:42] = m[..., 3] / 255.0
        for s, x, y, size, color, kind, track in (("JAMES", 50, 27, 17, TEXT, "semi", 3.0),
                                                   ("JOINT AUTONOMOUS MULTIMODAL EXECUTION SYSTEM", 51, 42, 9, TEXT_2, "sans", 1.2)):
            msk, asc = _mask(s, size, kind, track)
            h, w = msk.shape
            y0 = y - asc
            w = min(w, W - x)
            region = al[y0:y0 + h, x:x + w]
            mm = msk[:region.shape[0], :region.shape[1]]
            col[y0:y0 + h, x:x + w] = col[y0:y0 + h, x:x + w] * (1 - mm[..., None]) + np.asarray(color, np.float32) * mm[..., None]
            al[y0:y0 + h, x:x + w] = np.maximum(region, mm)
        self._layer = (np.clip(col, 0, 255).astype(np.uint8), al.astype(np.float32), (1 - al).astype(np.float32))
        self._key = (W, subtitle)
        self.end_x = 51 + int(measure("JOINT AUTONOMOUS MULTIMODAL EXECUTION SYSTEM", 9, "sans", 1.2)) + S6

    def draw(self, img, subtitle=""):
        H, W = img.shape[:2]
        if self._key != (W, subtitle):
            self._build(W, subtitle)
        panel(img, 0, 0, W, self.H, 0.86, BLACK, border=None, highlight=False)
        cv2.line(img, (0, self.H - 1), (W, self.H - 1), RULE, 1)
        col, al, inv = self._layer
        _composite(img, 0, 0, col, al, inv)
        return self.end_x   # x where the state pill can start


def status_right(img, items, y=31, right=None):
    """Compact global status, right-aligned: [(color, text), ...]. Only real values belong here."""
    x = (right or img.shape[1]) - S4
    for color, s in reversed(items):
        w = int(measure(s, 11, "mono"))
        text(img, s, x, y, 11, TEXT_2 if color is None else color, "mono", anchor="r")
        x -= w
        if color is not None and color not in (TEXT, TEXT_2, MUTED):
            dot(img, x - 11, y - 4, color)
            x -= 20
        x -= S4
    return x


# ---------- spatial cursor and target geometry (cv2: JSIL section 42) ----------
CURSOR_COLORS = {"TRACKING": TEXT_2, "TARGETABLE": CYAN_SOFT, "TARGETED": CYAN, "CONFIRMING": CONFIRM,
                 "SELECTED": SUCCESS, "ERROR": ERROR, "LOCKED": LOCKED}


def cursor(img, p, state="TRACKING", progress=0.0):
    """Spatial intent reticle: four ticks around a gap, a core dot, and a progress arc when confirming."""
    if p is None or state == "IDLE":
        return
    x, y = int(p[0]), int(p[1])
    col = CURSOR_COLORS.get(state, TEXT_2)
    r = 9 if state in ("TRACKING", "LOCKED") else 12
    L = 5
    for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0)):
        cv2.line(img, (x + dx * r, y + dy * r), (x + dx * (r + L), y + dy * (r + L)), col, 2 if state != "LOCKED" else 1, cv2.LINE_AA)
    if state != "LOCKED":
        cv2.circle(img, (x, y), 2 if state == "TRACKING" else 3, col, -1, cv2.LINE_AA)
    if state in ("TARGETED", "CONFIRMING", "SELECTED"):
        cv2.circle(img, (x, y), r + L + 5, col if state != "TARGETED" else TEAL, 1, cv2.LINE_AA)
    if state == "CONFIRMING" and progress > 0:
        cv2.ellipse(img, (x, y), (r + L + 5, r + L + 5), -90, 0, int(360 * min(1.0, progress)), CONFIRM, 3, cv2.LINE_AA)


def brackets(img, x, y, w, h, color=CYAN, L=None, thick=2):
    L = L or max(10, min(w, h) // 6)
    for px, py, dx, dy in ((x, y, 1, 1), (x + w, y, -1, 1), (x, y + h, 1, -1), (x + w, y + h, -1, -1)):
        cv2.line(img, (px, py), (px + dx * L, py), color, thick, cv2.LINE_AA)
        cv2.line(img, (px, py), (px, py + dy * L), color, thick, cv2.LINE_AA)


HAND_LINKS = [(0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8), (5, 9), (9, 10), (10, 11), (11, 12),
              (9, 13), (13, 14), (14, 15), (15, 16), (13, 17), (17, 18), (18, 19), (19, 20), (0, 17)]


def skeleton(img, pts, links=HAND_LINKS, color=TEXT_2, tips=(4, 8, 12, 16, 20)):
    for a, b in links:
        cv2.line(img, pts[a], pts[b], color, 1, cv2.LINE_AA)
    for i, p in enumerate(pts):
        cv2.circle(img, p, 2 if i in tips else 1, TEXT, -1, cv2.LINE_AA)


def toast(img, msg, color=CYAN, y_bottom=None, max_w=None):
    """Compact feedback card, bottom centre. Accent carries the state colour; text stays readable."""
    H, W = img.shape[:2]
    max_w = max_w or min(720, W - 64)
    lines = wrap(msg, max_w - 40, 15)[:3]
    w = int(max(measure(l, 15) for l in lines)) + 40
    h = 16 + 22 * len(lines)
    x = (W - w) // 2
    y = (y_bottom or H - 44) - h
    panel(img, x, y, w, h, 0.92, SURFACE_2, accent=None)
    cv2.rectangle(img, (x, y), (x + 2, y + h - 1), color, -1)
    for i, ln in enumerate(lines):
        text(img, ln, x + 20, y + 26 + 22 * i, 15, TEXT)


def hints(img, s, visible=True):
    """Bottom key-hint strip. Quiet: muted, small, and hideable (H)."""
    if not visible:
        return
    H, W = img.shape[:2]
    panel(img, 0, H - 30, W, 30, 0.8, BLACK, border=None, highlight=False)
    text(img, fit(s, W - 32, 11.5), 16, H - 11, 11.5, MUTED)


# ---------- cached layers (JSIL section 41) ----------
class LayerCache:
    """Draws content once, keeps it as colour + alpha, and composites it each frame.

    The alpha is recovered by drawing the same content onto black and onto white
    (difference matting), so any normal drawing code (panels, PIL text, cv2 lines) can be
    cached without a second rendering path. Rebuilt only when `key` changes."""

    def __init__(self):
        self._key = None
        self._layer = None

    def draw(self, img, key, x, y, w, max_h, fn):
        """fn(canvas) draws at local (0, 0) and returns the used height."""
        if key != self._key:
            c0 = np.zeros((max_h, w, 3), np.uint8)
            c1 = np.full((max_h, w, 3), 255, np.uint8)
            h = fn(c0)
            fn(c1)
            a = 1.0 - (c1[:h].astype(np.float32) - c0[:h].astype(np.float32)).mean(-1) / 255.0
            a = np.clip(a, 0, 1)
            col = c0[:h].astype(np.float32) / np.maximum(a, 1e-3)[..., None]
            self._layer = (np.clip(col, 0, 255).astype(np.uint8), a.astype(np.float32), (1 - a).astype(np.float32))
            self._key = key
        col, a, inv = self._layer
        _composite(img, x, y, col, a, inv)
        return y + a.shape[0]


# ---------- real telemetry ----------
class FrameTimer:
    """Measured, never estimated: frame intervals, per-stage times, p50/p95/p99/max,
    this process's CPU share and peak memory."""

    def __init__(self, n=180):
        self.frames = deque(maxlen=n)          # total frame time, ms
        self.intervals = deque(maxlen=n)       # time between frame starts, s
        self.stages: dict[str, deque] = {}
        self._t0 = None
        self._last = None
        self._cpu = (time.process_time(), time.perf_counter(), 0.0)

    def start(self):
        now = time.perf_counter()
        if self._last is not None:
            self.intervals.append(now - self._last)
        self._last = self._t0 = now
        self._mark = now

    def mark(self, stage: str):
        now = time.perf_counter()
        self.stages.setdefault(stage, deque(maxlen=self.frames.maxlen)).append((now - self._mark) * 1000)
        self._mark = now

    def end(self):
        if self._t0 is not None:
            self.frames.append((time.perf_counter() - self._t0) * 1000)

    @staticmethod
    def _pct(xs, q):
        if not xs:
            return None
        s = sorted(xs)
        return s[min(len(s) - 1, int(round(q * (len(s) - 1))))]

    def stats(self) -> dict:
        iv = [i for i in self.intervals if i > 0]
        fps = (1 / (sum(iv) / len(iv))) if iv else None
        pc, wc, last = self._cpu
        pn, wn = time.process_time(), time.perf_counter()
        if wn - wc >= 1.0:
            last = 100 * (pn - pc) / (wn - wc)
            self._cpu = (pn, wn, last)
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        peak_mb = rss / (1024 * 1024) if sys.platform == "darwin" else rss / 1024
        f = list(self.frames)
        return {"fps": fps, "p50": self._pct(f, .5), "p95": self._pct(f, .95), "p99": self._pct(f, .99),
                "max": max(f) if f else None, "cpu": last, "peak_mb": peak_mb, "n": len(f),
                "stages": {k: self._pct(list(v), .5) for k, v in self.stages.items()}}


def telemetry_card(img, x, y, timer: FrameTimer, w=196):
    st = timer.stats()
    fmt = lambda v, u="": "N/A" if v is None else f"{v:.1f}{u}"  # noqa: E731
    rows = [("FPS", fmt(st["fps"])), ("FRAME p50", fmt(st["p50"], " ms")), ("FRAME p95", fmt(st["p95"], " ms")),
            ("FRAME p99", fmt(st["p99"], " ms")), ("FRAME max", fmt(st["max"], " ms")), ("CPU (JAMES)", fmt(st["cpu"], "%")),
            ("PEAK MEM", f"{st['peak_mb']:.0f} MB")]
    rows += [(k.upper(), fmt(v, " ms")) for k, v in st["stages"].items()]
    h = 34 + 18 * len(rows) + 8
    panel(img, x, y, w, h)
    label(img, f"Telemetry  n={st['n']}", x + 12, y + 21)
    for i, (k, v) in enumerate(rows):
        yy = y + 42 + 18 * i
        text(img, k, x + 12, yy, 11, TEXT_2, "mono")
        text(img, v, x + w - 12, yy, 11, TEXT, "mono", anchor="r")
    return y + h
