"""Synthetic hands for tests: 21 MediaPipe-style landmarks and a simple rendered hand."""
import cv2
import numpy as np

# open hand, fingers up, in a 200 px box (wrist at bottom)
OPEN = [(100, 190), (70, 170), (50, 145), (34, 120), (18, 98),        # wrist, thumb
        (72, 110), (68, 78), (66, 58), (64, 40),                        # index
        (95, 106), (95, 70), (95, 48), (95, 28),                        # middle
        (117, 110), (120, 76), (122, 55), (123, 38),                    # ring
        (137, 118), (143, 92), (147, 76), (150, 62)]                    # pinky
FINGERS = [(0, 1, 2, 3, 4), (0, 5, 6, 7, 8), (9, 10, 11, 12), (13, 14, 15, 16), (0, 17, 18, 19, 20)]
SKIN = (120, 150, 205)          # BGR


def hand(ox=0, oy=0, k=1.0, pose=None):
    return [(int(ox + x * k), int(oy + y * k)) for x, y in (pose or OPEN)]


def render(img, pts, ring=None, skin=SKIN, k=1.0):
    """Draw a hand; ring = BGR colour of a band on the ring finger's first bone (or None)."""
    th = max(3, int(16 * k))
    cv2.fillConvexPoly(img, np.array([pts[i] for i in (0, 1, 5, 9, 13, 17)], np.int32), skin)
    for f in FINGERS:
        for a, b in zip(f, f[1:]):
            cv2.line(img, pts[a], pts[b], skin, th)
    if ring is not None:
        p0, p1 = np.array(pts[13], float), np.array(pts[14], float)
        c = p0 + (p1 - p0) * 0.36
        d = (p1 - p0) / np.linalg.norm(p1 - p0)
        v = np.array([-d[1], d[0]])
        L = 0.16 * np.linalg.norm(p1 - p0) + 2
        quad = np.array([c - d * L + v * th, c + d * L + v * th, c + d * L - v * th, c - d * L - v * th], np.int32)
        cv2.fillConvexPoly(img, quad, ring)
    return img


def _fold(pose, finger):
    """Curl one finger (tip and dip pulled back towards the MCP, below the PIP)."""
    p = list(pose)
    mcp, pip, dip, tip = finger
    mx, my = p[mcp]
    px, py = p[pip]
    p[dip] = (px + (mx - px) * 0.2 + 4, py + 14)
    p[tip] = (mx + (px - mx) * 0.3 + 2, my + 6)
    return p


PEACE = OPEN
for _f in ((13, 14, 15, 16), (17, 18, 19, 20)):
    PEACE = _fold(PEACE, _f)
PEACE = [PEACE[i] if i not in (8, 12) else ((50, 40) if i == 8 else (100, 28)) for i in range(21)]  # index and middle apart
PEACE[4] = (70, 140)                                                         # thumb tucked
FIST = OPEN
for _f in ((5, 6, 7, 8), (9, 10, 11, 12), (13, 14, 15, 16), (17, 18, 19, 20)):
    FIST = _fold(FIST, _f)
