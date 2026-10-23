"""SIGHT (tracking quality), ARM TIME (fatigue nudge) and the pinch guards (fist latch, reach).
All camera-free: synthetic hands from tests/hands.py and synthetic times."""
import numpy as np
import pytest

from tests.hands import FIST, OPEN, hand
import owner as OW


# SIGHT

def test_brightness_is_cheap_and_correct():
    from james_core.sight import brightness
    assert brightness(np.full((720, 1280, 3), 30, np.uint8)) == pytest.approx(30, abs=0.5)
    assert brightness(np.full((480, 640, 3), 200, np.uint8)) == pytest.approx(200, abs=0.5)


def test_low_light_needs_to_last_before_the_hint_shows():
    from james_core.sight import Sight
    s = Sight()
    st = s.update(0.0, (100, 100), 30)
    assert not st.low_light and st.hint is None                 # a single dark frame is not a condition
    for i in range(1, 25):
        st = s.update(i * 0.1, (100, 100), 30)
    assert st.low_light and "Low light" in st.hint and st.code == "LOW_LIGHT"
    for i in range(25, 40):                                     # lights on: the smoothed level recovers
        st = s.update(i * 0.1, (100, 100), 180)
    assert not st.low_light and st.hint is None and st.code == "OK"


def test_a_hand_that_keeps_dropping_is_unsteady_and_a_steady_one_is_not():
    from james_core.sight import Sight
    s = Sight()
    for i in range(30):                                         # found 0.3 s, lost 0.3 s, over and over
        st = s.update(i * 0.1, (100, 100) if (i // 3) % 2 == 0 else None, 160)
    assert st.unsteady and "dropping" in st.hint and st.code == "UNSTEADY"
    s2 = Sight()
    for i in range(30):
        st2 = s2.update(i * 0.1, (100, 100), 160)
    assert not st2.unsteady and st2.hint is None
    for i in range(30, 70):                                     # the shaky spell ends: the window empties
        st = s.update(i * 0.1, (100, 100), 160)
    assert not st.unsteady


def test_lost_hand_marks_the_last_place_for_a_short_time_only():
    from james_core.sight import Sight, LOST_SHOW_S
    s = Sight()
    s.update(0.0, (300, 200), 160)
    s.update(0.1, (310, 205), 160)
    assert s.update(0.15, None, None).lost_at is None            # a 50 ms blip is not a lost hand
    st = s.update(0.5, None, None)
    assert st.lost_at == (310, 205) and st.lost_for == pytest.approx(0.4)
    assert s.update(0.1 + LOST_SHOW_S + 0.05, None, None).lost_at is None
    assert s.update(5.0, (1, 1), 160).lost_at is None           # back in view: no X


def test_sight_reset_forgets_everything():
    from james_core.sight import Sight
    s = Sight()
    for i in range(30):
        s.update(i * 0.1, (1, 1) if i % 2 else None, 20)
    s.reset()
    st = s.update(10.0, (1, 1), 160)
    assert st.code == "OK" and st.flicker == 0


def test_sight_x_draws_and_never_raises():
    import jsil as J
    from james_core.sight import SightState
    img = np.zeros((720, 1280, 3), np.uint8)
    J.sight(img, None)
    J.sight(img, SightState())
    assert img.sum() == 0                                       # nothing to show, nothing drawn
    J.sight(img, SightState(lost_at=(640, 360)))
    assert img[352:369, 632:649].sum() > 0                      # the X is there
    J.sight(img, SightState(lost_at=(1279, 719)))               # at the very edge: clipped, not an error


# ARM TIME

def test_arm_time_nudges_once_after_sustained_high_use_and_rearms_after_rest():
    from james_core.armtime import ArmTime
    a = ArmTime(nudge_s=45)
    nudges = [t for t in np.arange(0, 60, 0.1) if a.update(t, 200, 720).nudge]   # wrist high: 1.5x
    assert len(nudges) == 1 and 29.5 <= nudges[0] <= 30.5                         # 45 / 1.5 = 30 s
    for t in np.arange(60, 90, 0.1):                                              # a real rest
        a.update(t, None, 720)
    again = [t for t in np.arange(90, 150, 0.1) if a.update(t, 200, 720).nudge]
    assert len(again) == 1 and a.nudges == 2
    assert "nudges=2" in a.summary()


def test_arm_time_low_hands_count_less_and_quiet_suppresses_the_nudge():
    from james_core.armtime import ArmTime, weight
    assert weight(100, 720) == 1.5 and weight(400, 720) == 1.0 and weight(650, 720) == 0.5
    a = ArmTime(nudge_s=45)
    assert not any(a.update(t, 650, 720).nudge for t in np.arange(0, 60, 0.1))   # 0.5x: 30 s of load in 60 s
    b = ArmTime(nudge_s=45)
    assert not any(b.update(t, 100, 720, quiet=True).nudge for t in np.arange(0, 60, 0.1))
    assert b.load > 45                                          # load still counted while voice was in use


def test_arm_time_ignores_stalls_between_frames():
    from james_core.armtime import ArmTime
    a = ArmTime()
    a.update(0.0, 100, 720)
    rep = a.update(20.0, 100, 720)                              # the app froze for 20 s
    assert rep.load <= 0.5 * 1.5 + 1e-9 and rep.up_s <= 0.5


# pinch guards

def _lens():
    from agents.lens import Lens
    L = object.__new__(Lens)
    L._pinched, L.owner, L._enroll = False, OW.OwnerFilter(None), None
    return L


def _pinch_pose(base=OPEN):
    p = list(base)
    p[4] = (p[8][0] + 2, p[8][1] + 4)                           # thumb tip on the index tip
    return p


FIST_THUMB_ON_INDEX = _pinch_pose(FIST)                         # closing a fist: the thumb lands on the index


def _step(L, pose, k=2.0):
    from agents.lens import LensResult
    r = LensResult()
    L.gestures(r, np.zeros((720, 1280, 3), np.uint8), [hand(300, 200, k, pose)], ["Left"], 0)
    return r


def test_a_real_pinch_still_pinches():
    L = _lens()
    assert not _step(L, OPEN).pinching
    r = _step(L, _pinch_pose())
    assert r.pinching and not r.reaching and not r.fist


def test_a_pinch_that_begins_as_a_fist_is_ignored_until_the_hand_opens():
    L = _lens()
    r = _step(L, FIST_THUMB_ON_INDEX)
    assert r.fist and not r.pinching                            # the fist gesture still reads as a fist
    assert not _step(L, _pinch_pose()).pinching                 # still latched: the fingers never opened
    _step(L, OPEN)                                              # open the hand
    assert _step(L, _pinch_pose()).pinching                     # now a pinch counts


def test_fist_latch_can_be_switched_off(monkeypatch):
    import config
    monkeypatch.setattr(config, "PINCH_FIST_LATCH", False, raising=False)
    assert _step(_lens(), FIST_THUMB_ON_INDEX).pinching


def test_a_pinch_whose_hand_grows_is_a_reach_and_stays_one_until_release():
    L = _lens()
    assert _step(L, _pinch_pose(), 2.0).pinching
    r = _step(L, _pinch_pose(), 2.8)                            # hand 40% bigger: moved toward the camera
    assert r.pinching and r.reaching
    assert _step(L, _pinch_pose(), 2.0).reaching                # sticky for this pinch
    assert not _step(L, OPEN, 2.0).reaching                     # released
    assert not _step(L, _pinch_pose(), 2.0).reaching            # a fresh pinch starts clean


def test_small_size_changes_are_not_a_reach():
    L = _lens()
    _step(L, _pinch_pose(), 2.0)
    assert not _step(L, _pinch_pose(), 2.3).reaching            # 15%: hand jitter and small moves


def test_losing_the_hand_clears_the_guards():
    L = _lens()
    _step(L, FIST_THUMB_ON_INDEX)
    from agents.lens import LensResult
    L.gestures(LensResult(), np.zeros((720, 1280, 3), np.uint8), [], [], 0)
    assert not L._fist_latch and L._pinch_palm is None and not L._reached


# the step confirm (app.py) never fires on a reach

class _FakeApp:
    class session:
        PINCH_HOLD_MS = 600

    def __init__(self):
        self.pinched = 0
        self.flashes = []
        app = self

        class H:
            def flash(self, m, c):
                app.flashes.append(m)
        self.hud = H()

    def atlas_open(self):
        return False

    def pinch(self, gesture):
        self.pinched += 1


def test_pinch_hold_confirms_normally_but_not_on_a_reach():
    from app import PinchHold
    a, g = _FakeApp(), PinchHold()
    for i in range(10):
        g.step(a, True, i * 0.1)
    assert a.pinched == 1
    b, g2 = _FakeApp(), PinchHold()
    g2.step(b, True, 0.0)
    g2.step(b, True, 0.3, reaching=True)                        # grabbed a tool mid-hold
    for i in range(4, 20):
        g2.step(b, True, i * 0.1, reaching=True)
    assert b.pinched == 0 and len(b.flashes) == 1 and "Pinch dropped" in b.flashes[0]
    g2.step(b, False, 2.1)                                      # release, then a clean pinch works again
    for i in range(22, 32):
        g2.step(b, True, i * 0.1)
    assert b.pinched == 1


def test_arm_load_is_capped_so_recovery_is_bounded():
    from james_core.armtime import ArmTime
    a = ArmTime(nudge_s=45)
    for t in np.arange(0, 600, 0.1):                            # ten minutes, nudge ignored
        a.update(t, 100, 720)
    assert a.load <= 45 * 1.5 + 1e-9


# review round

def _curled_pinch():
    """Index half out (ratio about 1.25, which ATLAS accepts as a pinch), middle, ring and pinky tucked."""
    from tests.hands import _fold
    p = OPEN
    for f in ((9, 10, 11, 12), (13, 14, 15, 16), (17, 18, 19, 20)):
        p = _fold(p, f)
    p = list(p)
    w, m = p[0], p[5]
    p[8] = (int(w[0] + (m[0] - w[0]) * 1.25), int(w[1] + (m[1] - w[1]) * 1.25))
    p[4] = (p[8][0] + 2, p[8][1] + 3)
    return p


def test_a_pinch_with_the_other_fingers_curled_still_pinches():
    from james_core import handpose as HP
    pose = _curled_pinch()
    assert HP.PINCH_MIN_INDEX < HP.ratios(hand(300, 200, 2.0, pose))[0] < 1.37
    L = _lens()
    r = _step(L, pose)
    assert r.pinching and not L._fist_latch


def test_the_latch_releases_when_the_thumb_leaves_even_if_the_hand_stays_closed():
    L = _lens()
    _step(L, FIST_THUMB_ON_INDEX)
    assert L._fist_latch
    _step(L, FIST)                                              # thumb off the index, fingers still folded
    assert not L._fist_latch


def test_tilting_the_hand_is_not_a_reach():
    """Pitching the hand toward the camera shortens wrist-to-knuckle by about 30% but not the knuckle width."""
    L = _lens()
    pose = _pinch_pose()
    _step(L, pose)
    w = pose[0]
    tilted = [(x, int(w[1] + (y - w[1]) * 0.7)) for x, y in pose]
    assert not _step(L, tilted).reaching


def test_reach_baseline_resets_when_another_hand_takes_over():
    from agents.lens import LensResult
    L = _lens()
    _step(L, _pinch_pose(), 2.0)
    r = LensResult()                                           # a different, bigger hand far away drives now
    L.gestures(r, np.zeros((720, 1280, 3), np.uint8), [hand(900, 300, 2.8, _pinch_pose())], ["Left"], 0)
    assert r.pinching and not r.reaching


def test_low_light_after_a_reset_is_judged_from_the_first_real_sample():
    from james_core.sight import Sight
    s = Sight()
    s.reset()
    s.update(0.0, (1, 1), None)                                 # first frame after ATLAS: no light sample yet
    for i in range(1, 30):
        st = s.update(i * 0.1, (1, 1), 30 if i % 5 == 0 else None)
        if st.low_light:
            break
    assert st.low_light and i * 0.1 <= 2.6                     # about 2 s after the first dark sample, not 3+


def test_the_toast_moves_above_the_sight_row():
    from hud import Hud
    from james_core.sight import SightState
    import jsil as J
    h = Hud()
    h.flash("Pinch dropped: your hand moved toward or away from the camera. Release and pinch again.", J.CONFIRM)
    v = {"state": "TARGET_CONFIRMED", "headline": "x", "keys": "", "sight": SightState(hint="Low light.")}
    out = h.draw(np.zeros((720, 1280, 3), np.uint8), v)
    row = out[720 - 30 - J.S3 - 26:720 - 30 - J.S3, J.S4 + 84:J.S4 + 300]
    assert (row.sum(axis=2) > 300).any()                       # the SIGHT text is still visible under nothing
    toast_bottom = 720 - 30 - J.S4 - 26 - J.S2
    assert out[toast_bottom - 4, 640].sum() > 0                 # the toast sits above the row
