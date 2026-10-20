"""ATLAS hands, the HoloHand way: one gesture, one command, read continuously.

Every hand here is a clean 21-point hand from james_core.handpose.synth (the same hands the legend draws), fed in
at 30 frames a second, so these tests read like the user's hand over time."""
import math

import numpy as np
import pytest

import config
from agents.lens import LensResult
from atlas import Atlas, StepContext
from james_core import handpose as HP
from james_core.safety_guard import load_policy

RECT = (508, 68, 756, 602)
FPS = 30


def make(policy=True):
    a = Atlas(config.PACK.model(), load_policy() if policy else None, reduced_motion=False, idle_sway=False)
    a.open("P-3", now=0.0)
    a.settle()
    a.update(LensResult(), 0.0)
    a.draw(np.full((720, 1280, 3), 70, np.uint8), RECT, now=0.0)
    return a


def hand(pose, wrist=(900, 600), size=110, angle=0.0, pinching=None, others=(), tip_at=None):
    pts = HP.synth(pose, wrist, size, angle)
    if tip_at is not None:                                   # move the hand so its index tip sits on tip_at
        dx, dy = tip_at[0] - pts[8][0], tip_at[1] - pts[8][1]
        pts = [(x + dx, y + dy) for x, y in pts]
    pin = (pose == "pinch") if pinching is None else pinching
    return LensResult(hand=pts, fingertip=pts[8], pinching=pin, pinch_point=pts[8] if pin else None,
                      others=[list(o) for o in others])


class Clock:
    def __init__(self, t=1.0):
        self.t = t

    def run(self, a, make_frame, secs):
        """Feed make_frame(i) for `secs` seconds; also draws, so the projection follows."""
        n = max(1, int(round(secs * FPS)))
        for i in range(n):
            self.t += 1 / FPS
            a.update(make_frame(i), self.t)
            a.tick(self.t)
        return a


def point_on(a, pid):
    ax, bx = a.proj[pid][:2]
    k = len(ax) // 2
    return int(round((ax[k][0] + bx[k][0]) / 2)), int(round((ax[k][1] + bx[k][1]) / 2))


# ---------------- poses ----------------

@pytest.mark.parametrize("pose,expect", [("open", "palm"), ("half", "palm"), ("fist", "palm"), ("point", "point"),
                                         ("peace", "peace"), ("pinch", "pinch")])
def test_each_clean_hand_reads_as_its_pose(pose, expect):
    t = HP.PoseTracker()
    for i in range(10):
        got = t.update(HP.synth(pose, (600, 500), 110), pose == "pinch", i / FPS)
    assert got == expect


def test_openness_runs_from_fist_to_open_whatever_the_distance():
    for size in (60, 110, 200):
        assert HP.openness(HP.synth("fist", (600, 600), size)) < 0.05
        assert HP.openness(HP.synth("open", (600, 600), size)) > 0.95
        assert 0.05 < HP.openness(HP.synth("half", (600, 600), size)) < 0.6


def test_a_fist_is_never_a_pinch():
    """In a fist the thumb lies on the index finger, so LENS's pinch ratio says 'pinch'. ATLAS must not."""
    fist = HP.synth("fist", (600, 500), 110)
    assert math.dist(fist[4], fist[8]) / HP.palm_size(fist) < config.PINCH_RATIO
    t = HP.PoseTracker()
    for i in range(10):
        got = t.update(fist, True, i / FPS)
    assert got == "palm"


def test_one_odd_frame_changes_nothing():
    t = HP.PoseTracker()
    seen = []
    for i in range(30):
        pose = "point" if i == 12 else "open"                  # one frame looks like a point
        seen.append(t.update(HP.synth(pose, (600, 500), 110), False, i / FPS))
    assert set(seen) == {"palm"}


# ---------------- open slowly / close your hand ----------------

def test_explode_follows_how_open_the_hand_is():
    a, c = make(), Clock()
    c.run(a, lambda i: hand("fist"), 0.6)
    assert a.explode.target == 0.0
    assert a.hands.active == "fist"
    c.run(a, lambda i: hand("half"), 0.6)
    assert 0.0 < a.explode.target < 0.6
    c.run(a, lambda i: hand("open"), 0.6)
    assert a.explode.target == 1.0 and a.hands.active == "open"


def test_turning_into_a_point_or_pinch_keeps_the_explode_the_open_hand_set():
    """Curling three fingers to point lowers the openness for a few frames before the point is recognised.
    Those frames are undone: pointing never collapses the model."""
    a, c = make(), Clock()
    c.run(a, lambda i: hand("open"), 0.6)
    assert a.explode.target == 1.0
    c.run(a, lambda i: hand("half"), 2 / FPS)                  # on the way...
    c.run(a, lambda i: hand("point"), 0.4)                    # ...to a point
    assert a.hands.pose == "point" and a.explode.target == 1.0


# ---------------- move or twist ----------------

def test_a_still_open_hand_with_tracking_jitter_turns_nothing():
    a, c = make(), Clock()
    view0 = (a.yaw.target, a.pitch.target, a.roll.target)
    rng = np.random.default_rng(1)
    c.run(a, lambda i: hand("open", wrist=(900 + rng.integers(-1, 2), 600 + rng.integers(-1, 2))), 2.0)
    assert (a.yaw.target, a.pitch.target, a.roll.target) == view0


def test_moving_the_hand_turns_and_tilts():
    a, c = make(), Clock()
    c.run(a, lambda i: hand("open"), 0.4)
    yaw0, pitch0 = a.yaw.target, a.pitch.target
    c.run(a, lambda i: hand("open", wrist=(900 + 7 * (i + 1), 600)), 1.0)    # 210 px to the right in 1 s
    assert a.yaw.target - yaw0 == pytest.approx(29 * 7 * 0.28, rel=0.05)    # 1:1 once past the 6 px clutch
    assert a.hands.active == "move"
    c.run(a, lambda i: hand("open", wrist=(1110, 600 - 5 * (i + 1))), 0.5)   # up: tilts the other way
    assert a.pitch.target < pitch0


def test_twisting_the_hand_rolls_the_model_the_same_way():
    a, c = make(), Clock()
    c.run(a, lambda i: hand("open"), 0.4)
    c.run(a, lambda i: hand("open", angle=2 * (i + 1)), 0.5)                  # 30 degrees clockwise on screen
    assert -30 <= a.roll.target <= -24                       # 1:1 once past the 3 degree clutch
    a.yaw.target = a.pitch.target = 0.0
    a.settle()
    x_axis = a._rotation() @ np.array([1.0, 0.0, 0.0])
    assert x_axis[1] < 0                                     # model y is up: the right side went down (clockwise)


def test_a_hand_that_just_came_into_view_turns_nothing():
    a, c = make(), Clock()
    yaw0 = a.yaw.target
    c.run(a, lambda i: hand("open", wrist=(700 + 12 * i, 600)), 0.2)         # sweeping in
    assert a.yaw.target == yaw0


# ---------------- point / pinch ----------------

def test_pointing_lights_the_part_and_its_card():
    a, c = make(), Clock()
    p = point_on(a, "coupling_guard")
    c.run(a, lambda i: hand("point", tip_at=p), 0.4)
    assert a.hover == "coupling_guard" and a.hands.active == "point"
    assert a.selected is None
    card = a._card_rows(a.linked(), RECT)
    assert card["rows"][0][1] == "POINTING AT" and card["rows"][1][1] == a.parts["coupling_guard"].label


def test_an_open_hand_lights_nothing():
    a, c = make(), Clock()
    p = point_on(a, "coupling_guard")
    c.run(a, lambda i: hand("open", tip_at=p), 0.4)
    assert a.hover is None


def test_pinch_pulls_the_part_out_drags_it_and_it_springs_back():
    a, c = make(), Clock()
    p = point_on(a, "coupling_guard")
    c.run(a, lambda i: hand("point", tip_at=p), 0.3)
    c.run(a, lambda i: hand("pinch", tip_at=p), 0.1)
    assert a.selected == "coupling_guard" and a.grab_id == "coupling_guard"
    kind, payload = a.pop_events()[-1]
    assert kind == "atlas_part_selected" and payload["part"] == "coupling_guard"
    c.run(a, lambda i: hand("pinch", tip_at=(p[0] + 4 * (i + 1), p[1])), 1.0)  # drag 120 px right
    assert a.grab_x.target == pytest.approx(120, abs=2)
    before = point_on(a, "coupling_guard")
    a.draw(np.full((720, 1280, 3), 70, np.uint8), RECT, now=c.t)
    assert point_on(a, "coupling_guard")[0] > before[0] + 60   # the drawing follows the pinch
    c.run(a, lambda i: hand("open", tip_at=(p[0] + 120, p[1])), 2.0)          # let go
    assert a.grab_x.target == 0 and a.grab_id is None and a.selected == "coupling_guard"


def test_pinching_empty_space_clears_the_pick_and_never_turns():
    a, c = make(), Clock()
    a.select("baseplate", 0.0)
    q = (RECT[0] + 30, RECT[1] + RECT[3] - 60)
    assert a.hit(q) is None
    yaw0 = a.yaw.target
    c.run(a, lambda i: hand("point", tip_at=q), 0.2)
    c.run(a, lambda i: hand("pinch", tip_at=q), 0.2)
    c.run(a, lambda i: hand("point", tip_at=q), 0.3)
    assert a.selected is None and a.yaw.target == yaw0


def test_a_one_frame_hand_dropout_keeps_the_drag():
    a, c = make(), Clock()
    p = point_on(a, "coupling_guard")
    c.run(a, lambda i: hand("pinch", tip_at=p), 0.2)
    c.t += 1 / FPS
    a.update(LensResult(), c.t)                              # no hand this frame
    assert a.hands.grab is not None and a.grab_id == "coupling_guard"


def test_fingertip_only_sources_still_pick_and_highlight():
    """The simulator and older callers send a fingertip and a pinch flag but no 21 points."""
    a = make()
    p = point_on(a, "coupling_guard")
    a.update(LensResult(fingertip=p), 1.0)
    assert a.hover == "coupling_guard"
    a.update(LensResult(fingertip=p, pinching=True, pinch_point=p), 1.1)
    a.update(LensResult(fingertip=p), 1.2)
    assert a.selected == "coupling_guard"


# ---------------- two hands ----------------

def other(x, pose="open"):
    return HP.synth(pose, (x, 600), 110)


def test_two_hands_apart_zoom_in_and_together_zoom_out():
    a, c = make(), Clock()
    c.run(a, lambda i: hand("open", wrist=(600, 600)), 0.4)
    e0, yaw0 = a.explode.target, a.yaw.target
    c.run(a, lambda i: hand("open", wrist=(600, 600), others=[other(900)]), 0.3)
    assert a.hands.active == "two" and a.zoom.target == 1.0
    c.run(a, lambda i: hand("open", wrist=(600, 600), others=[other(900 + 10 * (i + 1))]), 1.0)   # 300 -> 600 px
    assert a.zoom.target == pytest.approx(1.96, abs=0.02)
    assert a.explode.target == e0 and a.yaw.target == yaw0    # one-hand controls wait
    c.run(a, lambda i: hand("open", wrist=(600, 600), others=[other(1200 - 15 * (i + 1))]), 1.0)  # back to 150 px
    assert a.zoom.target < 1.0


def test_a_second_hand_must_stay_a_moment_before_it_zooms():
    a, c = make(), Clock()
    c.run(a, lambda i: hand("open", wrist=(600, 600)), 0.4)
    c.run(a, lambda i: hand("open", wrist=(600, 600), others=[other(900 + 40 * i)]), 0.1)   # a flicker
    c.run(a, lambda i: hand("open", wrist=(600, 600)), 0.5)
    assert a.zoom.target == 1.0 and a.hands.two is None


def test_a_second_hand_lost_for_a_frame_keeps_the_zoom():
    a, c = make(), Clock()
    c.run(a, lambda i: hand("open", wrist=(600, 600), others=[other(900)]), 0.4)
    c.run(a, lambda i: hand("open", wrist=(600, 600)), 1 / FPS)
    c.run(a, lambda i: hand("open", wrist=(600, 600), others=[other(1200)]), 1 / FPS)
    assert a.hands.two is not None and a.zoom.target == pytest.approx(1.96, abs=0.02)


# ---------------- peace ----------------

def test_peace_held_asks_for_the_next_model_once():
    a, c = make(policy=False), Clock()
    c.run(a, lambda i: hand("peace"), 0.35)
    assert a.requests == [] and 0 < a.hands.progress < 1
    c.run(a, lambda i: hand("peace"), 0.4)
    assert a.pop_requests() == ["next"]
    c.run(a, lambda i: hand("peace"), 2.0)                    # still holding: no second switch
    assert a.pop_requests() == []


def test_peace_does_nothing_in_the_maintenance_app():
    a, c = make(policy=True), Clock()
    c.run(a, lambda i: hand("peace"), 1.5)
    assert a.requests == [] and a.selected is None


# ---------------- the rest ----------------

def test_reduced_motion_never_animates():
    a = Atlas(config.PACK.model(), load_policy(), reduced_motion=True)
    a.open("P-3", 0.0)
    c = Clock()
    c.run(a, lambda i: hand("fist"), 0.5)
    a.draw(np.full((720, 1280, 3), 70, np.uint8), RECT, now=c.t)
    assert a.explode.x == a.explode.target == 0.0 and a.yaw.x == a.yaw.target


def test_the_ring_and_strip_draw_for_every_pose_without_crashing():
    a, c = make(), Clock()
    a.ctx = StepContext()
    for pose in ("open", "fist", "point", "pinch", "peace", "half"):
        c.run(a, lambda i: hand(pose), 0.3)
        img = np.full((720, 1280, 3), 70, np.uint8)
        a.draw(img, RECT, now=c.t)
        a.hands.draw_ring(img, a)
    c.run(a, lambda i: hand("open", others=[other(400)]), 0.4)
    a.hands.draw_ring(img, a)


@pytest.mark.parametrize("pose", ["open", "fist", "point"])
def test_noisy_landmarks_on_a_still_hand_change_nothing(pose):
    """2 px of noise on every landmark, every frame, for 3 s: no turn, no roll, no pose flicker."""
    a, c = make(), Clock()
    rng = np.random.default_rng(7)

    def noisy(i):
        r = hand(pose)
        r.hand = [(int(round(x + rng.normal(0, 2))), int(round(y + rng.normal(0, 2)))) for x, y in r.hand]
        r.fingertip = r.hand[8]
        return r
    c.run(a, noisy, 0.4)
    view0, e0, pose0 = (a.yaw.target, a.pitch.target, a.roll.target), a.explode.target, a.hands.pose
    seen = set()
    c.run(a, lambda i: (seen.add(a.hands.pose), noisy(i))[1], 3.0)
    assert (a.yaw.target, a.pitch.target, a.roll.target) == view0
    assert abs(a.explode.target - e0) <= 0.04 and seen == {pose0}


# ---------------- review round: each of these misfired before its fix ----------------

def _blend_fist_to_open(k):
    f, o = HP.synth("fist", (900, 600), 110), HP.synth("open", (900, 600), 110)
    return [(int(round(a[0] + (b[0] - a[0]) * k)), int(round(a[1] + (b[1] - a[1]) * k))) for a, b in zip(f, o)]


def test_opening_a_fist_is_never_a_pinch_even_while_lens_still_says_pinch():
    a, c = make(), Clock()
    c.run(a, lambda i: hand("fist", pinching=True), 0.5)
    poses = []

    def opening(i):
        pts = _blend_fist_to_open(min(1.0, (i + 1) / 30))
        pin = math.dist(pts[4], pts[8]) / HP.palm_size(pts) < config.PINCH_RATIO * 1.4   # LENS's release rule
        poses.append(a.hands.pose)
        return LensResult(hand=pts, fingertip=pts[8], pinching=pin, pinch_point=pts[8] if pin else None)
    c.run(a, opening, 1.2)
    assert "pinch" not in poses and a.grab_id is None and a.selected is None
    assert a.explode.target == 1.0


def test_a_resting_second_hand_or_a_fist_never_starts_two_hand_zoom():
    a, c = make(), Clock()
    c.run(a, lambda i: hand("open", wrist=(600, 600), others=[other(1000, "fist")]), 1.0)
    assert a.hands.two is None
    c.run(a, lambda i: hand("fist", wrist=(600, 600), others=[other(1000)]), 1.0)
    assert a.hands.two is None and a.explode.target == 0.0


def test_the_driving_hand_leaving_mid_zoom_never_jumps_the_zoom():
    """Owner filter off: when the driving hand leaves, LENS promotes the other hand to driver."""
    a, c = make(), Clock()
    c.run(a, lambda i: hand("open", wrist=(600, 600), others=[other(900)]), 0.5)
    z = a.zoom.target
    c.run(a, lambda i: hand("open", wrist=(900 + 2 * i, 600)), 1.0)            # only the other hand now drives
    assert a.zoom.target == pytest.approx(z) and a.hands.two is None


def test_grabbing_a_new_part_while_the_last_one_springs_back_does_not_jump_it():
    a, c = make(), Clock()
    p, q = point_on(a, "coupling_guard"), point_on(a, "motor")
    c.run(a, lambda i: hand("pinch", tip_at=p), 0.1)
    c.run(a, lambda i: hand("pinch", tip_at=(p[0] + 5 * (i + 1), p[1])), 0.8)
    c.run(a, lambda i: hand("point", tip_at=q), 0.25)                          # let go and point at the motor
    assert a.grab_id == "coupling_guard" and abs(a.grab_x.x) > 20             # still springing back
    c.run(a, lambda i: hand("pinch", tip_at=q), 0.1)
    assert a.grab_id == "motor" and abs(a.grab_x.x) < 1.0


def test_keys_and_reset_win_over_the_undo_history():
    a, c = make(), Clock()
    c.run(a, lambda i: hand("open"), 0.4)
    c.run(a, lambda i: hand("open", wrist=(900 + 6 * (i + 1), 600)), 1.0)
    a.reset_view()
    a.toggle_explode()
    view = (a.yaw.target, a.explode.target)
    c.run(a, lambda i: hand("point"), 0.5)
    assert (a.yaw.target, a.explode.target) == view


def test_a_fist_over_a_shelf_tile_never_presses_it():
    from atlas_app import AtlasViewer
    v = AtlasViewer(reduced_motion=True, idle_sway=False)
    v.open(0.0)
    v.render(np.full((720, 1280, 3), 70, np.uint8), None, 0.5)
    x, y, w, h = dict(v.buttons)["model:2"]
    t = 1.0
    for k in [0.0] * 10 + [i / 20 for i in range(21)]:                        # a fist on the tile, then opening
        pts = _blend_fist_to_open(k)
        dx, dy = x + w // 2 - pts[8][0], y + h // 2 - pts[8][1]
        pts = [(px + dx, py + dy) for px, py in pts]
        pin = math.dist(pts[4], pts[8]) / HP.palm_size(pts) < config.PINCH_RATIO * 1.4
        t += 1 / FPS
        v.update(LensResult(hand=pts, fingertip=pts[8], pinching=pin, pinch_point=pts[8] if pin else None), t)
    assert v.i == 0
