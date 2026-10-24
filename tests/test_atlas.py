"""ATLAS, the exploded view: the pack model, step-to-part links, the WARDEN preview, gestures, and the
rule that a pinch inside ATLAS can never confirm a step."""
import shutil
import time

import numpy as np
import pytest
import yaml

import config
import jsil as J
from agents.lens import LensResult
from atlas import Atlas, StepContext
from james_core import model3d
from james_core.pack import PACKS_DIR, PackError, load_pack, rehash
from james_core.safety_guard import load_policy
from james_core.session import State

SRC = PACKS_DIR / "pharma-utility"
RECT = (508, 68, 756, 602)


# ---------------- the model in the pack ----------------

@pytest.fixture
def pack_dir(tmp_path):
    d = tmp_path / "pharma-utility"
    shutil.copytree(SRC, d, ignore=shutil.ignore_patterns(".*"))
    return d


def edit_model(d, fn):
    p = d / "models" / "centrifugal_pump.yaml"
    raw = yaml.safe_load(p.read_text())
    fn(raw)
    p.write_text(yaml.safe_dump(raw, sort_keys=False))
    rehash(d)


def test_demo_pack_ships_the_pump_model():
    m = config.PACK.model("centrifugal_pump")
    assert m is not None and m.format == model3d.FORMAT
    assert "not OEM CAD" in m.source
    ids = {p.id for p in m.parts}
    assert {"coupling_guard", "bearing_housing", "impeller", "motor"} <= ids


def test_every_hazard_tag_in_the_rules_has_a_part():
    m = config.PACK.model()
    tags = set(load_policy().hazard_keywords)
    assert tags <= {p.hazard for p in m.parts if p.hazard}


def test_model_with_an_unknown_hazard_tag_is_refused(pack_dir):
    def typo(raw):
        raw["parts"][0]["hazard"] = "electrcal"
    edit_model(pack_dir, typo)
    with pytest.raises(PackError) as e:
        load_pack(pack_dir)
    assert "electrcal" in str(e.value)


def test_model_with_a_duplicate_part_is_refused(pack_dir):
    edit_model(pack_dir, lambda raw: raw["parts"].append(dict(raw["parts"][0])))
    with pytest.raises(PackError) as e:
        load_pack(pack_dir)
    assert "listed twice" in str(e.value)


def test_model_with_a_bad_shape_is_refused(pack_dir):
    edit_model(pack_dir, lambda raw: raw["parts"][0]["shapes"][0].pop("radius"))
    with pytest.raises(PackError) as e:
        load_pack(pack_dir)
    assert "cylinder needs radius" in str(e.value)


def test_model_listed_under_the_wrong_class_is_refused(pack_dir):
    edit_model(pack_dir, lambda raw: raw.__setitem__("class", "air_handler"))
    with pytest.raises(PackError) as e:
        load_pack(pack_dir)
    assert "listed under 'centrifugal_pump'" in str(e.value)


def test_an_empty_keyword_is_refused_so_it_cannot_light_every_step(pack_dir):
    edit_model(pack_dir, lambda raw: raw["parts"][0]["keywords"].append(""))
    with pytest.raises(PackError):
        load_pack(pack_dir)


def test_model_is_optional(pack_dir):
    raw = yaml.safe_load((pack_dir / "manifest.yaml").read_text())
    raw["equipment_classes"]["centrifugal_pump"].pop("model")
    files = raw.pop("files")
    files.pop("models/centrifugal_pump.yaml")
    raw["files"] = files
    (pack_dir / "manifest.yaml").write_text(yaml.safe_dump(raw, sort_keys=False))
    shutil.rmtree(pack_dir / "models")
    assert load_pack(pack_dir).model() is None


def test_model_file_is_hash_checked_like_every_pack_file(pack_dir):
    p = pack_dir / "models" / "centrifugal_pump.yaml"
    p.write_text(p.read_text().replace("Coupling guard", "Guard"))
    with pytest.raises(PackError) as e:
        load_pack(pack_dir)
    assert "hash mismatch" in str(e.value)


# ---------------- step -> part (deterministic) ----------------

@pytest.mark.parametrize("text,touches,expect", [
    ("Remove the coupling guard.", ("coupling_guard",), ["coupling_guard"]),      # not the coupling inside it
    ("Isolate the motor at the breaker and apply your personal lock and tag.", (), ["motor"]),
    ("Inspect the bearing housing for heat, noise and grease leakage and record findings.", ("bearing_housing",),
     ["bearing_housing"]),
    ("Check coupling alignment with a straight edge and record findings.", (), ["coupling"]),
    ("Check that the suction valve is fully open.", (), ["suction_valve"]),
    ("Check the suction strainer differential pressure.", (), ["strainer"]),
    ("Review the vibration and temperature trend and confirm the fault.", (), []),
])
def test_step_text_lights_the_right_part(text, touches, expect):
    assert model3d.parts_for_step(config.PACK.model(), text, touches) == expect


def test_a_shared_hazard_tag_narrows_to_the_part_the_step_names():
    m = config.PACK.model()
    assert model3d.parts_for_step(m, "Turn the shaft by hand.", ("rotating_parts",)) == ["shaft"]
    assert set(model3d.parts_for_step(m, "Keep clear while it runs.", ("rotating_parts",))) == {
        "coupling", "shaft", "impeller"}


# ---------------- WARDEN preview ----------------

def make_atlas(**kw):
    a = Atlas(config.PACK.model(), load_policy(), **kw)
    a.open("P-3", now=0.0)
    a.settle()
    a.update(LensResult(), 0.0)                    # one frame with no pinch: ATLAS is ready for a new one
    return a


def test_preview_is_the_real_warden_verdict_for_that_part():
    a = make_atlas()
    a.ctx = StepContext(facts={})
    status, text, col = a.preview("coupling_guard")
    assert status == "gate" and "LOTO-01" in text and "ZERO-01" in text and col == J.ERROR
    a.ctx = StepContext(facts={"loto_confirmed": True, "zero_energy_verified": True,
                               "ppe_gloves": True, "ppe_eye": True})
    status, text, _ = a.preview("coupling_guard")
    assert status == "allow" and "LOTO-01" in text
    assert a.preview("baseplate")[0] == "none"


# ---------------- gestures ----------------

def frame(a):
    img = np.full((720, 1280, 3), 70, np.uint8)
    a.draw(img, RECT, now=time.monotonic())
    return img


def point_on(a, pid):
    """A screen point on one of the part's lines."""
    ax, ay = a.proj[pid][:2]
    return int(round((ax[0][0] + ay[0][0]) / 2)), int(round((ax[0][1] + ay[0][1]) / 2))


def hand_pinching_at(x, y, pinch=True):
    """21 points: wrist at (x, y+120), middle MCP 80 px up, thumb tip next to (or away from) the index tip."""
    pts = [(x, y + 120)] + [(x, y + 60)] * 20
    pts[9] = (x, y + 40)
    pts[8] = (x, y)
    pts[4] = (x + 8, y + 4) if pinch else (x + 70, y + 40)
    return pts


def test_a_pinch_carried_in_from_whatever_opened_atlas_does_nothing():
    a = Atlas(config.PACK.model(), load_policy())
    a.open("P-3", now=0.0)
    a.settle()
    frame(a)
    p = point_on(a, "coupling_guard")
    a.update(LensResult(fingertip=p, pinching=True, pinch_point=p), 0.1)     # still held from the launcher
    a.update(LensResult(fingertip=p, pinching=False), 0.3)                  # released inside ATLAS
    assert a.selected is None
    a.update(LensResult(fingertip=p, pinching=True, pinch_point=p), 0.5)     # a fresh pinch works
    a.update(LensResult(fingertip=p, pinching=False), 0.6)
    assert a.selected == "coupling_guard"


def test_hands_swapping_roles_never_spin_the_model():
    a = make_atlas()
    frame(a)
    yaw0 = a.yaw.target
    left, right = (500, 400), (800, 400)
    a.update(LensResult(fingertip=left, pinching=True, others=[hand_pinching_at(*right)]), 1.0)
    a.update(LensResult(fingertip=left, pinching=True, others=[hand_pinching_at(*right)]), 1.03)
    a.update(LensResult(fingertip=right, pinching=True, others=[hand_pinching_at(*left)]), 1.06)  # driver swapped
    a.update(LensResult(fingertip=right, pinching=True, others=[hand_pinching_at(*left)]), 1.09)
    assert abs(a.yaw.target - yaw0) < 1 and a.zoom.target == pytest.approx(1.0)


def test_idle_sway_redraws_a_few_times_a_second_not_every_frame():
    a = make_atlas()
    a.reduced = False
    a._sway = 1.0                                                                 # fully swaying
    img = np.full((720, 1280, 3), 70, np.uint8)
    redraws = 0
    t = 5.0                                                                       # no hand for 5 s: swaying
    for i in range(60):                                                           # two seconds at 30 FPS
        before = getattr(a, "_layer_key", None)
        a.draw(img, RECT, now=t + i / 30)
        redraws += a._layer_key != before
    assert redraws < 25, redraws


def test_blocked_step_part_is_drawn_red_and_allowed_step_part_amber():
    def colour_pixels(a, col):
        img = frame(a)
        return int(np.all(np.abs(img.astype(int) - np.array(col)) < 40, axis=2).sum())
    a = make_atlas()
    a.ctx = StepContext(text="Remove the coupling guard.", touches=("coupling_guard",), index=4, total=5,
                        blocked=True)
    assert colour_pixels(a, J.ERROR) > 200
    a.ctx = StepContext(text="Remove the coupling guard.", touches=("coupling_guard",), index=4, total=5)
    assert colour_pixels(a, J.CONFIRM) > 200


# ---------------- inside the app: ATLAS never confirms ----------------

@pytest.fixture
def app_at_step():
    import app as A
    a = A.App(sim=True)
    a.toggle_lock()
    a.target("P-3", "asset_tag", 0.97)
    a.pinch()                                                      # PPE
    a.q.put(("heard", A.SCRIPTED, 0.95, time.perf_counter(), "SCRIPTED"))
    a.drain()
    assert a.state == State.AWAITING_CONFIRMATION
    return a


def test_atlas_needs_a_target():
    import app as A
    a = A.App(sim=True)
    a.toggle_atlas()
    assert not a.atlas_open()                                      # locked
    a.toggle_lock()
    a.toggle_atlas()
    assert not a.atlas_open()                                      # no machine targeted yet


def test_pinch_and_confirm_keys_do_nothing_while_atlas_is_open(app_at_step):
    a = app_at_step
    a.toggle_atlas()
    assert a.atlas_open()
    before = (a.state, a.step_i, dict(a.session.facts), len(a.ledger.events(a.session.id)))
    a.pinch()
    for k in "ctlzpyn":
        a.key(k)
    after = (a.state, a.step_i, dict(a.session.facts))
    assert after == before[:3]
    kinds = [e["kind"] for e in a.ledger.events(a.session.id)[before[3]:]]
    assert kinds == [], kinds                                      # nothing reached the session (no confirmed, no facts)
    a.key("x")                                                     # close: confirming works again
    assert not a.atlas_open()
    a.pinch()
    assert a.step_i == 1


def test_atlas_follows_the_job_and_is_logged(app_at_step):
    a = app_at_step
    a.key("x")
    v = a.view()
    assert v["atlas"] is a.atlas and a.atlas.ctx.index == 1
    kinds = [e["kind"] for e in a.ledger.events(a.session.id)]
    assert "atlas_opened" in kinds
    a.key("r")                                                     # new job: ATLAS closes with the old one
    assert not a.atlas_open()


def test_card_shows_the_real_block_not_a_green_preview_for_the_blocked_steps_part():
    a = make_atlas()
    facts = {"loto_confirmed": True, "zero_energy_verified": True, "ppe_gloves": True, "ppe_eye": True}
    a.ctx = StepContext(text="Remove the coupling guard.", touches=("coupling_guard",), index=4, total=5,
                        blocked=True, rule_ids=("TEMP-01",), facts=facts)
    frame(a)
    card = a._card_rows(a.linked(), (508, 68, 756, 602))
    warden = [r for r in card["rows"] if r[0] == "kv" and r[1] == "WARDEN"][0]
    assert "blocked by TEMP-01" in warden[2] and warden[3] == J.ERROR


def test_small_windows_never_crash_or_draw_inverted():
    a = make_atlas()
    a.ctx = StepContext(text="Remove the coupling guard.", touches=("coupling_guard",), index=4, total=5)
    for rect in ((0, 0, 30, 30), (300, 60, 260, 240), (300, 60, 120, 400), (500, 60, -20, 300)):
        img = np.full((480, 640, 3), 70, np.uint8)
        a.draw(img, rect, now=1.0)
        for ax, bx, *_ in a.proj.values():                 # whatever was drawn sits inside its stage
            x, y, w, h = a._rect
            pts = np.concatenate([ax, bx])
            assert w > 0 and h > 0
            assert pts[:, 0].min() >= x - 2 and pts[:, 0].max() <= x + w + 2


def test_hud_at_640x480_does_not_crash_with_atlas_open(app_at_step):
    a = app_at_step
    a.key("x")
    img = np.full((480, 640, 3), 70, np.uint8)
    a.hud.draw(img, a.view())


def test_idle_lock_and_new_target_close_atlas(app_at_step):
    a = app_at_step
    a.key("x")
    a.safe(__import__("james_core.session", fromlist=["Lock"]).Lock(reason="idle"))
    v = a.view()
    assert not a.atlas_open() and "atlas" not in v
    closed = [e for e in a.ledger.events(a.session.id) if e["kind"] == "atlas_closed"]
    assert closed and closed[-1]["payload"]["reason"] == "locked"


def test_voice_and_scripted_request_are_blocked_while_atlas_is_open(app_at_step):
    a = app_at_step
    a.key("x")
    n = len(a.ledger.events(a.session.id))
    a.key("v")
    a.key("1")
    assert not a.recorder.active and a.q.empty()
    assert len(a.ledger.events(a.session.id)) == n


def test_a_pinch_held_through_closing_atlas_must_be_released_before_it_confirms(app_at_step):
    import app as A
    a, gate = app_at_step, A.PinchHold()
    a.key("x")
    t = 100.0
    for _ in range(20):                                   # pinching in ATLAS (picking parts) for 2 s
        gate.step(a, True, t)
        t += 0.1
    a.key("x")                                            # close ATLAS with the pinch still held
    a.session.proposal_at -= __import__("datetime").timedelta(seconds=5)
    for _ in range(20):                                   # keep holding: nothing may be confirmed
        gate.step(a, True, t)
        t += 0.1
    assert a.step_i == 0 and a.state == State.AWAITING_CONFIRMATION
    gate.step(a, False, t)                                # release, then a deliberate pinch and hold
    for _ in range(10):
        t += 0.1
        gate.step(a, True, t)
    assert a.step_i == 1
