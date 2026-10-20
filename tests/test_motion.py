"""Motion helpers (james_core/motion.py) and how LENS uses them: smoothing, pre-pinch aim, swipes."""
import math

import numpy as np

from agents.lens import Lens, LensResult
from james_core.motion import HandSmoother, OneEuro, Swipe, Trail, point_segment_dist, swept_hit


def test_one_euro_steadies_a_still_hand():
    rng = np.random.default_rng(0)
    f = OneEuro()
    raw, out = [], []
    for i in range(90):                                   # 3 s at 30 FPS, ~3 px of tracker noise
        x = np.array([[640.0, 360.0]]) + rng.normal(0, 3, (1, 2))
        raw.append(x[0])
        out.append(f(x, i / 30)[0])
    assert np.std(np.array(out[30:]), axis=0).max() < np.std(np.array(raw[30:]), axis=0).max() / 2.3   # measured 2.6x steadier at the defaults


def test_one_euro_keeps_up_with_a_fast_hand():
    f, slow = OneEuro(), OneEuro(beta=0.0)                # beta 0 = a plain low-pass with the same rest smoothing
    for i in range(30):                                   # 1500 px/s sweep at 30 FPS
        x = np.array([[100.0 + 50 * i, 360.0]])
        y, y_slow = f(x, i / 30), slow(x, i / 30)
    lag, lag_slow = abs(x[0, 0] - y[0, 0]), abs(x[0, 0] - y_slow[0, 0])
    assert lag < 25 and lag < lag_slow / 4, (lag, lag_slow)


def hand_at(x, y=400):
    return [(x + i, y - i) for i in range(21)]


def test_smoother_never_mixes_two_hands_when_mediapipe_swaps_their_order():
    s = HandSmoother()
    s([hand_at(100), hand_at(900)], 0.0)
    out = s([hand_at(905), hand_at(102)], 1 / 30)         # same two hands, order swapped
    assert abs(out[0][0][0] - 905) < 10 and abs(out[1][0][0] - 102) < 10


def test_smoother_starts_fresh_for_a_hand_that_appears_far_away():
    s = HandSmoother(max_jump=180)
    s([hand_at(100)], 0.0)
    out = s([hand_at(1000)], 1 / 30)
    assert out[0][0][0] == 1000                           # no smoothing from the old hand's position


def test_trail_returns_the_earlier_point_and_clears_on_a_jump():
    tr = Trail()
    for i in range(10):
        tr.push(i * 0.03, (100 + i, 100))
    assert tr.at(0.15) == (105.0, 100.0)
    tr.push(0.31, (900, 100))                             # another hand took over
    assert tr.at(0.0) == (900.0, 100.0)


def test_swipe_detects_a_fast_straight_move_once():
    tr, sw = Trail(), Swipe()
    got = []
    for i in range(8):                                    # 3+ palm widths (palm 80 px) in 0.24 s
        t = i * 0.03
        tr.push(t, (300 + 40 * i, 400 + (i % 2)))
        got.append(sw(tr, t, 80.0))
    assert [g for g in got if g] == ["right"]


def test_slow_drift_and_diagonal_moves_are_not_swipes():
    for step, dy in ((6, 0), (40, 35)):                   # too slow; too diagonal
        tr, sw = Trail(), Swipe()
        got = []
        for i in range(10):
            t = i * 0.03
            tr.push(t, (300 + step * i, 400 + dy * i))
            got.append(sw(tr, t, 80.0))
        assert not any(got), (step, dy, got)


def test_swept_hit_catches_a_flick_that_jumps_over_the_target():
    target, r = (500, 300), 20
    p0, p1 = (400, 300), (620, 302)                       # both frame positions are far from the target
    assert math.dist(p0, target) > r and math.dist(p1, target) > r
    assert swept_hit(p0, p1, target, r)
    assert not swept_hit((400, 500), (620, 500), target, r)


def test_point_segment_distance():
    d = point_segment_dist((5, 5), np.array([[0.0, 0.0], [10.0, 0.0]]), np.array([[10.0, 0.0], [10.0, 10.0]]))
    assert np.allclose(d, [5, 5])


def bare_lens():
    """A Lens without MediaPipe: only what motion() needs."""
    from james_core.motion import Swipe as S, Trail as T
    lens = Lens.__new__(Lens)
    lens._trail, lens._swipe, lens._anchor, lens._was_pinching = T(), S(), None, False
    return lens


def test_pinch_selects_where_the_finger_aimed_before_the_pinch():
    lens = bare_lens()
    hand = hand_at(500)
    for i in range(10):                                   # steady aim at (500, 300)
        r = LensResult(hand=hand, fingertip=(500, 300))
        lens.motion(r, i / 30)
    t = 10 / 30
    for j in range(8):                                    # pinch: the tip slides 25 px down while it closes
        r = LensResult(hand=hand, fingertip=(500, 300 + 3 * (j + 1)), pinching=True)
        lens.motion(r, t + j / 30)
        assert r.pinch_point == (500, 300), r.pinch_point
    r = LensResult(hand=hand, fingertip=(500, 330), pinching=False)
    lens.motion(r, t + 9 / 30)
    assert r.pinch_point is None


def test_pinch_point_looks_back_past_the_drift_before_the_pinch_registers():
    """The tip starts sliding before the pinch ratio crosses the threshold, so the last pre-pinch frame is
    already off target. The anchor is taken PINCH_AIM_LOOKBACK_MS back, from before the slide began."""
    lens = bare_lens()
    hand = hand_at(500)
    t = 0.0
    for i in range(10):                                   # steady aim at (500, 300)
        lens.motion(LensResult(hand=hand, fingertip=(500, 300)), t)
        t += 1 / 30
    for j in range(3):                                    # closing the fingers: 6 px per frame, not yet a pinch
        lens.motion(LensResult(hand=hand, fingertip=(500, 306 + 6 * j)), t)
        t += 1 / 30
    r = LensResult(hand=hand, fingertip=(500, 324), pinching=True)
    lens.motion(r, t)
    assert r.pinch_point == (500, 300), r.pinch_point


def test_releasing_a_fast_pinch_drag_is_not_a_swipe():
    lens = bare_lens()
    hand = hand_at(500)
    got = []
    for i in range(8):                                    # pinch + fast drag (turning a model in ATLAS)
        r = LensResult(hand=hand, fingertip=(300 + 40 * i, 400), pinching=True)
        lens.motion(r, i * 0.03)
    for i in range(4):                                    # release while the hand is still moving
        r = LensResult(hand=hand, fingertip=(620 + 40 * i, 400), pinching=False)
        lens.motion(r, 0.24 + i * 0.03)
        got.append(r.swipe)
    assert not any(got), got
