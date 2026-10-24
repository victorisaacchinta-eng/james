"""Iteration 10: the WARDEN blocked card is a signal panel (band, signal word, hazard stripes), red kept for state."""
import numpy as np

import jsil as J
from hud import Hud, signal_band


def _view(state):
    return {"state": state, "headline": "WARDEN blocked this step.", "keys": "",
            "readout": ["BLOCKED BY SAFETY POLICY", "Reason: Lockout/tagout not confirmed",
                        "Rule: LOTO-01 · PUMP-UTIL v0.1", "STEP 4 OF 5 · REMOVE THE COUPLING GUARD"]}


def test_blocked_card_has_the_band_and_stripes():
    out = Hud().draw(np.zeros((720, 1280, 3), np.uint8), _view("SAFETY_BLOCKED"))
    y, x = 52 + J.S4 + 4, J.S4 + 200                       # inside the band, left of the stripes
    assert tuple(int(c) for c in out[y, x]) == J.ERROR
    stripes = out[52 + J.S4 + 2:52 + J.S4 + 28, J.S4 + 380:J.S4 + 470].reshape(-1, 3)
    dark = (stripes.sum(axis=1) < 60).mean()
    assert 0.3 < dark < 0.7                                # about half dark: stripes, not a solid block


def test_other_states_have_no_band():
    out = Hud().draw(np.zeros((720, 1280, 3), np.uint8), _view("AWAITING_CONFIRMATION"))
    assert tuple(int(c) for c in out[52 + J.S4 + 4, J.S4 + 200]) != J.ERROR


def test_blocked_reason_is_readable_text_not_red():
    rows = Hud()._card_lines(_view("SAFETY_BLOCKED"), 440)
    assert ("label", "Current task", J.ERROR) not in rows
    assert any(r[0] == "reason" for r in rows)
    assert all(r[2] != J.ERROR for r in rows if r[0] in ("mono", "head"))
    assert ("label", "Why", J.ERROR) in rows               # the one red word in the body


def test_signal_band_fits_narrow_cards():
    img = np.zeros((60, 120, 3), np.uint8)
    signal_band(img, 0, 0, 120, 30, "BLOCKED", "WARDEN")
    assert img[:30].any() and not img[31:].any()
