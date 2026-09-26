"""Optional eye tracker (off by default, key G). MediaPipe Face Landmarker with iris points.

Runs on a downscaled frame every other frame so the camera view stays smooth.
Nothing is stored and no face is recognised: it only finds where the irises sit
inside each eye to show a rough gaze direction."""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

import config

# landmark ids (MediaPipe 478-point face mesh)
L_EYE = [33, 7, 163, 144, 145, 153, 154, 155, 133, 173, 157, 158, 159, 160, 161, 246]
R_EYE = [362, 382, 381, 380, 374, 373, 390, 249, 263, 466, 388, 387, 386, 385, 384, 398]
L_IRIS, R_IRIS = [468, 469, 470, 471, 472], [473, 474, 475, 476, 477]
L_CORNERS, R_CORNERS = (33, 133), (362, 263)


@dataclass
class EyeResult:
    left: list | None = None       # eye outline points (pixel)
    right: list | None = None
    iris: list | None = None       # [(x, y, r), (x, y, r)]
    gaze: str = ""


class Eyes:
    def __init__(self, every: int = 2, width: int = 640):
        import mediapipe as mp
        from mediapipe.tasks import python as mpt
        from mediapipe.tasks.python import vision
        self._mp = mp
        opts = vision.FaceLandmarkerOptions(
            base_options=mpt.BaseOptions(model_asset_path=str(config.ASSETS / "face_landmarker.task")),
            running_mode=vision.RunningMode.VIDEO, num_faces=1,
            output_face_blendshapes=False, output_facial_transformation_matrixes=False)
        self._fl = vision.FaceLandmarker.create_from_options(opts)
        self.every, self.width, self._n, self._last = every, width, 0, EyeResult()

    def close(self):
        try:
            self._fl.close()
        except Exception:
            pass

    def process(self, frame_bgr, ts_ms: int) -> EyeResult:
        self._n += 1
        if self._n % self.every:
            return self._last                      # reuse the last result on skipped frames
        H, W = frame_bgr.shape[:2]
        small = cv2.resize(frame_bgr, (self.width, int(H * self.width / W)))
        rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
        out = self._fl.detect_for_video(self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb), ts_ms)
        if not out.face_landmarks or len(out.face_landmarks[0]) < 478:
            self._last = EyeResult()
            return self._last
        lm = out.face_landmarks[0]
        P = lambda i: (int(lm[i].x * W), int(lm[i].y * H))
        irises, ratios = [], []
        for ids, (a, b) in ((L_IRIS, L_CORNERS), (R_IRIS, R_CORNERS)):
            pts = np.array([P(i) for i in ids])
            c = pts[0]
            r = int(np.mean(np.linalg.norm(pts[1:] - c, axis=1)))
            irises.append((int(c[0]), int(c[1]), max(2, r)))
            pa, pb = np.array(P(a)), np.array(P(b))
            span = np.linalg.norm(pb - pa) or 1
            ratios.append(float(np.dot(c - pa, pb - pa) / span ** 2))
        g = float(np.mean(ratios))
        gaze = "left" if g < 0.42 else "right" if g > 0.58 else "centre"   # camera's point of view
        self._last = EyeResult([P(i) for i in L_EYE], [P(i) for i in R_EYE], irises, gaze)
        return self._last


def mirror(e: EyeResult, width: int) -> EyeResult:
    if not e.iris:
        return e
    fx = lambda p: (width - 1 - p[0], p[1])
    flip = {"left": "right", "right": "left"}.get(e.gaze, e.gaze)
    return EyeResult([fx(p) for p in e.left], [fx(p) for p in e.right],
                     [(width - 1 - x, y, r) for x, y, r in e.iris], flip)


def draw(img, e: EyeResult, color=(232, 200, 127)):
    if not e.iris:
        return
    for poly in (e.left, e.right):
        cv2.polylines(img, [np.array(poly, np.int32)], True, color, 1, cv2.LINE_AA)
    for x, y, r in e.iris:
        cv2.circle(img, (x, y), r, (61, 163, 232), 2, cv2.LINE_AA)
        cv2.circle(img, (x, y), 2, (255, 255, 255), -1, cv2.LINE_AA)
