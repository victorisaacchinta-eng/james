"""SIGHT: is the camera seeing the hand well enough to trust a gesture?

Plain Python and NumPy, no MediaPipe, so it is testable without a camera. Three signals, all cheap:

- light:    mean brightness of a 32x18 thumbnail of the frame (0..255). Below SIGHT_LOW_LIGHT for
            SIGHT_HINT_AFTER_S, JAMES says "low light".
- unsteady: the driving hand appearing and vanishing over and over (SIGHT_FLICKER transitions inside
            SIGHT_WINDOW_S). That is what dark gloves, low contrast against clothing and backlight look like
            to a hand tracker: it finds the hand, loses it, finds it again.
- lost:     the hand was tracked a moment ago and is gone now. The HUD freezes an amber X where it was
            last seen, so nothing pretends to still track it, and any held gesture has already stopped.

Why: users of webcam hand tracking report low contrast with their clothes, gloves the tracker cannot see,
and a long lag before the app admits the hand is gone (research notes in docs/ITERATIONS.md, iteration 10).
SIGHT only reports. It never presses anything and never unlocks anything.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np

LOW_LIGHT = 55          # mean brightness (0..255) below this counts as dim
FLICKER = 4             # this many found/lost changes inside WINDOW_S = unsteady tracking
WINDOW_S = 3.0
HINT_AFTER_S = 2.0      # a condition must last this long before a hint appears (no flicker of the hint itself)
LOST_SHOW_S = 2.5       # how long the amber X stays where the hand was last seen
LOST_AFTER_S = 0.12     # gaps shorter than this are tracker blips, not a lost hand


def brightness(frame_bgr: np.ndarray) -> float:
    """Mean brightness on a 32x18 thumbnail: about 0.05 ms, independent of frame size."""
    small = cv2.resize(frame_bgr, (32, 18), interpolation=cv2.INTER_AREA)
    if small.ndim == 3:
        small = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    return float(small.mean())


@dataclass
class SightState:
    light: float = 255.0
    low_light: bool = False
    flicker: int = 0
    unsteady: bool = False
    lost_at: Optional[tuple[int, int]] = None      # where to draw the amber X, while it shows
    lost_for: float = 0.0
    hint: Optional[str] = None                     # one short line for the HUD, or None

    @property
    def code(self) -> str:
        """Short state for the log: OK | LOW_LIGHT | UNSTEADY | LOW_LIGHT+UNSTEADY."""
        parts = [p for p, on in (("LOW_LIGHT", self.low_light), ("UNSTEADY", self.unsteady)) if on]
        return "+".join(parts) or "OK"


class Sight:
    def __init__(self, low_light: float = LOW_LIGHT, flicker: int = FLICKER, window_s: float = WINDOW_S,
                 hint_after_s: float = HINT_AFTER_S, lost_show_s: float = LOST_SHOW_S,
                 lost_after_s: float = LOST_AFTER_S):
        self.low_light, self.flicker_n, self.window_s = low_light, flicker, window_s
        self.hint_after_s, self.lost_show_s, self.lost_after_s = hint_after_s, lost_show_s, lost_after_s
        self._changes: deque[float] = deque()        # times the hand appeared or vanished
        self._present: Optional[bool] = None
        self._last_tip: Optional[tuple[int, int]] = None
        self._last_seen: Optional[float] = None
        self._dim_since: Optional[float] = None
        self._light: Optional[float] = None         # no sample yet: the first one is taken as-is

    def reset(self) -> None:
        """A screen that owns the hand (ATLAS, CANTUS) just closed: start fresh."""
        self.__init__(self.low_light, self.flicker_n, self.window_s, self.hint_after_s, self.lost_show_s,
                      self.lost_after_s)

    def update(self, t: float, tip: Optional[tuple[int, int]], light: Optional[float] = None) -> SightState:
        """t: monotonic seconds. tip: the driving fingertip this frame, or None. light: brightness(), or None to
        keep the last value (callers may measure light every few frames only)."""
        if light is not None:
            self._light = light if self._light is None else 0.7 * self._light + 0.3 * light
        present = tip is not None
        if self._present is not None and present != self._present:
            self._changes.append(t)
        self._present = present
        while self._changes and t - self._changes[0] > self.window_s:
            self._changes.popleft()
        if present:
            self._last_tip, self._last_seen = (int(tip[0]), int(tip[1])), t

        level = 255.0 if self._light is None else self._light
        st = SightState(light=round(level, 1), flicker=len(self._changes))
        dim = level < self.low_light
        self._dim_since = (self._dim_since or t) if dim else None
        st.low_light = dim and t - self._dim_since >= self.hint_after_s
        st.unsteady = len(self._changes) >= self.flicker_n     # the window itself is the hold time
        if not present and self._last_seen is not None:
            gone = t - self._last_seen
            if self.lost_after_s <= gone <= self.lost_show_s:
                st.lost_at, st.lost_for = self._last_tip, round(gone, 2)
        if st.low_light and st.unsteady:
            st.hint = "Low light and unsteady tracking. Add light in front of you."
        elif st.low_light:
            st.hint = "Low light. Face a light source for steadier tracking."
        elif st.unsteady:
            st.hint = "Tracking keeps dropping. Bare hands or light gloves, against a plain background."
        return st
