"""LENS: where the index finger points, which asset tag it points at, and the pinch.

MediaPipe Hand Landmarker (Tasks API) for 21 hand points; OpenCV ArUco for the asset
tag. The tag, not a vision model, decides which machine it is. Faces are not tracked
and frames are never stored."""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import cv2
import numpy as np

import config
import owner as OW

TIP, PIP, MCP, THUMB_TIP, WRIST, MID_MCP = 8, 6, 5, 4, 0, 9


@dataclass
class Tag:
    id: int
    asset_id: str | None
    corners: np.ndarray
    center: tuple[float, float]


@dataclass
class LensResult:
    tags: list[Tag] = field(default_factory=list)
    hand: list[tuple[int, int]] | None = None
    fingertip: tuple[int, int] | None = None
    pinch_ratio: float | None = None
    pinching: bool = False
    open_palm: bool = False
    pointed: Tag | None = None
    point_conf: float = 0.0
    peace: bool = False               # V sign: index + middle up, ring + pinky folded (push-to-talk)
    fist: bool = False                # all four fingers folded (stop talking and run the command)
    others: list = field(default_factory=list)   # hands in view that are NOT allowed to drive input
    owner: str = "off"                # off (filter off or paused) | owner (owner hand found) | searching
    owner_score: float = 0.0
    owner_kind: str = "off"           # key | ring | off: what identifies the owner (config.OWNER_MODE)
    owner_paused: bool = False        # X in key mode: any hand drives until X again
    keys: list = field(default_factory=list)     # owner key markers seen this frame (Tag), never asset tags
    enroll: float | None = None       # enrolment progress 0..1 while K enrolment runs
    enroll_msg: tuple | None = None   # (ok, message) once, when an enrolment finishes


def owner_key_id() -> int | None:
    return getattr(config, "OWNER_KEY_ID", None) if getattr(config, "OWNER_MODE", "ring") == "key" else None


def make_owner():
    """The owner filter config.OWNER_MODE asks for: key (printed marker), ring (enrolled with K) or off."""
    mode = getattr(config, "OWNER_MODE", "ring")
    if mode == "key":
        return OW.KeyFilter(config.OWNER_KEY_ID)
    if mode == "off":
        return OW.OwnerFilter(None)
    return OW.OwnerFilter.load(getattr(config, "OWNER_RING", config.DATA / "owner_ring.json"))


def split_markers(corners, ids, key_id: int | None) -> tuple[list, list]:
    """ArUco detections -> (asset tags, owner keys). The owner key is never an asset tag, so the owner's own
    wrist can never be 'pointed at' or picked as a machine."""
    tags, keys = [], []
    if ids is not None:
        for c, i in zip(corners, np.asarray(ids).flatten()):
            pts = np.asarray(c, np.float32).reshape(4, 2)
            t = Tag(int(i), config.ASSET_TAGS.get(int(i)), pts, tuple(float(v) for v in pts.mean(0)))
            (keys if key_id is not None and int(i) == key_id else tags).append(t)
    return tags, keys


class Lens:
    def __init__(self):
        from mediapipe.tasks import python as mpt
        from mediapipe.tasks.python import vision
        import mediapipe as mp
        self._mp = mp
        opts = vision.HandLandmarkerOptions(
            base_options=mpt.BaseOptions(model_asset_path=str(config.HAND_MODEL)),
            running_mode=vision.RunningMode.VIDEO, num_hands=getattr(config, "MAX_HANDS", 4),
            min_hand_detection_confidence=0.5, min_tracking_confidence=0.5)
        self._hands = vision.HandLandmarker.create_from_options(opts)
        d = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
        self._aruco = cv2.aruco.ArucoDetector(d, cv2.aruco.DetectorParameters())
        self._stable_since: int | None = None     # ms timestamp when the current tag was first pointed at
        self._last_id = None
        self._pinched = False
        self.owner = make_owner()
        self.owner_paused = False
        self._enroll: OW.Enroller | None = None

    # ---------- owner (key or ring) ----------
    def toggle_pause(self) -> bool:
        """X in key mode: let any hand drive (True) or require the key again (False)."""
        self.owner_paused = not self.owner_paused
        if getattr(self.owner, "kind", "") == "key":
            self.owner.forget()
        return self.owner_paused

    def start_enroll(self, ts_ms: int):
        self._enroll = OW.Enroller(ts_ms)

    def forget_owner(self):
        self.owner.forget(getattr(config, "OWNER_RING", config.DATA / "owner_ring.json"))

    def close(self):
        try:
            self._hands.close()
        except Exception:
            pass

    def process(self, frame_bgr: np.ndarray, ts_ms: int) -> LensResult:
        h, w = frame_bgr.shape[:2]
        res = LensResult()
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        corners, ids, _ = self._aruco.detectMarkers(gray)
        res.tags, res.keys = split_markers(corners, ids, owner_key_id())
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        out = self._hands.detect_for_video(self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb), ts_ms)
        hands = [[(int(p.x * w), int(p.y * h)) for p in lm] for lm in (out.hand_landmarks or [])]
        handed = [(hd[0].category_name if hd else "") for hd in (getattr(out, "handedness", None) or [[]] * len(hands))]
        self.gestures(res, frame_bgr, hands, handed, ts_ms, [k.center for k in res.keys])
        # stability: the same tag for POINT_STABLE_MS before it counts (time-based, so it feels the same at any FPS)
        pid = res.pointed.id if res.pointed else None
        if pid is None:
            self._stable_since = None
        elif pid != self._last_id or self._stable_since is None:
            self._stable_since = ts_ms
        self._last_id = pid
        if res.pointed and ts_ms - self._stable_since < config.POINT_STABLE_MS:
            res.point_conf = min(res.point_conf, 0.5)
        return res

    def gestures(self, res: LensResult, frame_bgr, hands: list, handed: list, ts_ms: int, keys=()):
        """Pick the one hand that may drive input, then read its gestures. Split out so it is testable
        without MediaPipe: hands are lists of 21 (x, y) pixel points."""
        if self._enroll is not None:                       # K: enrol the ring on the largest hand in view
            big = OW.OwnerFilter.largest(hands)
            if big is not None:
                self._enroll.add(frame_bgr, hands[big], handed[big] if big < len(handed) else "")
            res.enroll = self._enroll.progress(ts_ms)
            if self._enroll.done(ts_ms):
                sig, msg = self._enroll.finish()
                if sig:
                    self.owner.save(getattr(config, "OWNER_RING", config.DATA / "owner_ring.json"), sig)
                res.enroll_msg, res.enroll, self._enroll = (sig is not None, msg), None, None
        res.owner_kind = getattr(self.owner, "kind", "ring")
        res.owner_paused = getattr(self, "owner_paused", False)
        if res.owner_paused:
            idx, res.owner, res.owner_score = OW.OwnerFilter.largest(hands), "off", 0.0
        else:
            idx, res.owner, res.owner_score = self.owner.select(frame_bgr, hands, ts_ms, keys)
        res.others = [hd for i, hd in enumerate(hands) if i != idx]
        if idx is None:
            self._pinched = False
            return
        pts = hands[idx]
        res.hand, res.fingertip = pts, pts[TIP]
        palm = math.dist(pts[WRIST], pts[MID_MCP]) or 1.0
        res.pinch_ratio = math.dist(pts[THUMB_TIP], pts[TIP]) / palm
        # hysteresis: close below PINCH_RATIO, open only above 1.4x, so jitter can't make a double pinch
        if self._pinched:
            self._pinched = res.pinch_ratio < config.PINCH_RATIO * 1.4
        else:
            self._pinched = res.pinch_ratio < config.PINCH_RATIO
        res.pinching = self._pinched
        res.open_palm = (not res.pinching) and self._is_open_palm(pts, palm)
        res.peace = (not res.pinching) and self._is_peace(pts, palm)
        res.fist = self._is_fist(pts)
        res.pointed, res.point_conf = self._pointing(pts, res.tags)

    @staticmethod
    def _ext(pts, tip, pip) -> bool:
        w = pts[WRIST]
        return math.dist(w, pts[tip]) > math.dist(w, pts[pip]) * 1.15

    @staticmethod
    def _folded(pts, tip, pip) -> bool:
        w = pts[WRIST]
        return math.dist(w, pts[tip]) < math.dist(w, pts[pip]) * 1.0

    @classmethod
    def _is_peace(cls, pts, palm) -> bool:
        """V sign: index and middle straight and apart, ring and pinky folded."""
        return (cls._ext(pts, 8, 6) and cls._ext(pts, 12, 10) and cls._folded(pts, 16, 14) and cls._folded(pts, 20, 18)
                and math.dist(pts[8], pts[12]) > 0.18 * palm)

    @classmethod
    def _is_fist(cls, pts) -> bool:
        """All four fingers folded (the thumb can be anywhere)."""
        return all(cls._folded(pts, t, p) for t, p in ((8, 6), (12, 10), (16, 14), (20, 18)))

    @staticmethod
    def _is_open_palm(pts, palm) -> bool:
        """All four fingers straight and the thumb out."""
        w = pts[WRIST]
        straight = sum(math.dist(w, pts[t]) > math.dist(w, pts[p]) * 1.15
                       for t, p in ((8, 6), (12, 10), (16, 14), (20, 18)))
        thumb_out = math.dist(pts[THUMB_TIP], pts[MCP]) > 0.55 * palm
        return straight == 4 and thumb_out

    @staticmethod
    def _pointing(pts, tags) -> tuple[Tag | None, float]:
        """Angle between the finger ray (MCP -> tip) and the direction tip -> tag centre."""
        if not tags:
            return None, 0.0
        ray = np.subtract(pts[TIP], pts[MCP]).astype(float)
        if np.linalg.norm(ray) < 5:
            return None, 0.0
        best, best_conf = None, 0.0
        for t in tags:
            inside = cv2.pointPolygonTest(t.corners.astype(np.float32), pts[TIP], False) >= 0
            to_tag = np.subtract(t.center, pts[TIP]).astype(float)
            if inside:
                conf = 0.97
            else:
                cos = ray @ to_tag / (np.linalg.norm(ray) * np.linalg.norm(to_tag) + 1e-6)
                angle = math.degrees(math.acos(max(-1.0, min(1.0, cos))))
                conf = max(0.0, 0.99 - angle / 100)
            if conf > best_conf:
                best, best_conf = t, conf
        return (best, round(best_conf, 2)) if best_conf > 0.5 else (None, 0.0)
