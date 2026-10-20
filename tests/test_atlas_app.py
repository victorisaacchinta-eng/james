"""The ATLAS app: the model library (licences, hashes, fail closed), the shelf and keys, the launcher tile in
james.py, and the no-camera demo."""
import shutil
import sys
import types

import numpy as np
import pytest
import yaml

import config
from agents.lens import LensResult
from atlas_app import AtlasViewer, demo, library_models
from james_core.atlas_library import LIBRARY, load_library


# ---------------- the library ----------------

def test_library_ships_real_multi_part_models_with_credits():
    models, problems = load_library()
    assert problems == []
    ids = [m.id for m in models]
    assert ids == ["perseverance", "ingenuity", "jwst", "car_concept", "chronograph", "flight_helmet"]
    for m in models:
        assert len(m.parts) >= 6, m.id
        assert m.credit and m.licence and m.source.startswith("https://raw.githubusercontent.com/")
        if m.licence.startswith("CC-BY"):                 # attribution and "changes were made" are required
            assert "CC BY 4.0" in m.credit and "Converted to a line drawing" in m.credit
        for p in m.parts:
            assert len(m.segs[p.id]) > 0 and len(p.role) > 10
            assert m.segs[p.id].dtype == np.float32 and np.abs(m.segs[p.id]).max() <= 0.75


def test_notice_credits_every_model():
    notice = (LIBRARY / "NOTICE.md").read_text()
    for m in load_library()[0]:
        assert m.label in notice and m.credit in notice


@pytest.fixture
def lib(tmp_path):
    d = tmp_path / "atlas_library"
    shutil.copytree(LIBRARY, d)
    return d


def test_a_changed_model_file_is_left_out_and_the_rest_still_load(lib):
    p = lib / "ingenuity" / "model.yaml"
    p.write_text(p.read_text().replace("Solar panel", "Sun panel"))
    models, problems = load_library(lib)
    assert "ingenuity" not in [m.id for m in models] and len(models) == 5
    assert any("ingenuity" in x and "hash mismatch" in x for x in problems)


def test_a_missing_or_escaping_file_is_refused(lib):
    raw = yaml.safe_load((lib / "library.yaml").read_text())
    raw["models"][0]["files"]["../outside.npz"] = "0" * 64
    (lib / "library.yaml").write_text(yaml.safe_dump(raw))
    (lib / "jwst" / "edges.npz").unlink()
    models, problems = load_library(lib)
    ids = [m.id for m in models]
    assert "perseverance" not in ids and "jwst" not in ids
    assert any("leaves the library" in x for x in problems) and any("missing" in x for x in problems)


def test_the_app_never_needs_a_3d_library():
    import ast
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    for f in ("atlas.py", "atlas_app.py", "james_core/atlas_library.py"):
        mods = set()
        for node in ast.walk(ast.parse((root / f).read_text())):
            if isinstance(node, ast.Import):
                mods |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                mods.add(node.module.split(".")[0])
        assert not mods & {"trimesh", "pygltflib", "DracoPy", "open3d", "pyrender", "requests", "urllib"}, (f, mods)


# ---------------- the viewer ----------------

@pytest.fixture
def viewer():
    v = AtlasViewer(reduced_motion=True, idle_sway=False)
    v.open(0.0)
    v.render(np.full((720, 1280, 3), 70, np.uint8), None, 1.0)
    v.update(LensResult(), 1.0)                    # one frame without a pinch, as after any real open
    return v


def tap(v, p, t):
    v.update(LensResult(fingertip=p, pinching=True, pinch_point=p), t)
    v.update(LensResult(fingertip=p, pinching=False), t + 0.2)


def centre(rect):
    x, y, w, h = rect
    return x + w // 2, y + h // 2


def test_shelf_has_every_model_plus_the_pack_pump(viewer):
    ids = [m.id for m in viewer.models]
    assert ids[-1].startswith("pack-") and len(ids) == 7
    keys = [k for k, _ in viewer.buttons]
    assert keys[-2:] == ["reset", "close"] and len(keys) == 9


def test_tapping_a_tile_switches_model_and_close_leaves(viewer):
    rect = dict(viewer.buttons)["model:3"]
    tap(viewer, centre(rect), 2.0)
    assert viewer.models[viewer.i].id == "car_concept" and viewer.atlas.model.id == "car_concept"
    tap(viewer, centre(dict(viewer.buttons)["close"]), 3.0)
    assert not viewer.active


def test_a_pinch_that_starts_on_the_shelf_never_reaches_the_model(viewer):
    yaw0 = viewer.atlas.yaw.target
    p = centre(dict(viewer.buttons)["model:1"])
    viewer.update(LensResult(fingertip=p, pinching=True, pinch_point=p), 2.0)
    viewer.update(LensResult(fingertip=(p[0] + 300, p[1] - 300), pinching=True), 2.1)   # drags off the shelf
    viewer.update(LensResult(fingertip=(p[0] + 300, p[1] - 300), pinching=False), 2.2)
    assert viewer.atlas.yaw.target == yaw0 and viewer.i == 0                          # no turn, no switch


def test_keys_drive_everything_without_hands(viewer):
    a = viewer.atlas
    yaw0 = a.yaw.target
    for c in "dd":
        viewer.key(c)
    assert a.yaw.target == yaw0 + 40
    viewer.key("=")
    assert a.zoom.target == pytest.approx(1.25)
    viewer.key("e")
    assert a.explode.target == 0.0
    viewer.key("tab")
    assert a.selected == a.model.parts[0].id
    viewer.key("n")
    assert viewer.i == 1
    viewer.key("b")
    viewer.key("b")
    assert viewer.i == len(viewer.models) - 1
    assert not viewer.key("q")                                     # quitting is the host's job


def test_reset_restores_the_view(viewer):
    a = viewer.atlas
    a.turn(70, 20)
    a.zoom_by(3)
    viewer.activate("reset")
    assert (a.yaw.target, a.pitch.target, a.zoom.target) == (-34.0, 16.0, 1.0)


def test_open_on_a_named_model_and_refuse_an_unknown_one():
    v = AtlasViewer(reduced_motion=True)
    v.open(0.0, "jwst")
    assert v.atlas.model.id == "jwst"
    with pytest.raises(KeyError):
        v.open(0.0, "death-star")


def test_demo_writes_a_picture_per_model(tmp_path):
    paths = demo(tmp_path)
    assert len(paths) == len(library_models()[0]) + 1
    import cv2
    img = cv2.imread(str(paths[0]))
    assert img.shape == (config.FRAME_H, config.FRAME_W, 3) and img.std() > 10


def test_render_at_640x480_does_not_crash(viewer):
    viewer.render(np.full((480, 640, 3), 70, np.uint8), None, 2.0)


# ---------------- inside james.py ----------------

def _james(monkeypatch):
    sys.modules.setdefault("sounddevice", types.ModuleType("sounddevice"))
    import james as JM
    monkeypatch.setattr(JM.llm, "available", lambda *a, **k: False)
    return JM, JM.James()


def test_atlas_is_a_launcher_tile_that_opens_inside_james(monkeypatch):
    JM, j = _james(monkeypatch)
    assert "atlas" in j.app_by_id and j.app_by_id["atlas"].how == "internal"
    assert any(b.key == "app:atlas" for b in j.launcher_apps())
    opened = []
    monkeypatch.setattr(JM.LA, "open_target", lambda r: opened.append(r))
    j.press("app:atlas")
    assert j.atlas_requested and not opened                        # never handed to the OS
    assert j.open_atlas(0.0) and j.atlas_on()
    j.close_atlas()
    assert not j.atlas_on()


def test_saying_open_atlas_finds_the_tile(monkeypatch):
    JM, j = _james(monkeypatch)
    ok, said = j.open_by_name("atlas")
    assert ok and j.atlas_requested


def test_atlas_will_not_open_over_a_lock_or_a_task(monkeypatch):
    JM, j = _james(monkeypatch)
    j.s.locked = True
    assert not j.open_atlas(0.0)
    j.s.locked = False
    j.s.mode = "SEARCHING"
    assert not j.open_atlas(0.0) and not j.atlas_on()


def test_the_os_launcher_refuses_an_internal_app():
    import launcher_apps as LA
    with pytest.raises(ValueError):
        LA.open_target(LA.internal_targets()[0])


def test_the_atlas_screen_inside_james_frame_by_frame(monkeypatch):
    JM, j = _james(monkeypatch)
    assert j.open_atlas(0.0)
    frame = np.full((720, 1280, 3), 70, np.uint8)
    view = LensResult(fingertip=(640, 360), hand=[(640, 400)] * 21)
    out = j.atlas_frame(frame.copy(), view, 1.0)
    assert out.shape == frame.shape and j.atlas_on()
    assert j.atlas_key(ord("d"), None, 1.1) is None and j.atlas.atlas.yaw.target == -34 + 20
    close = dict(j.atlas.buttons)["close"]
    p = (close[0] + 20, close[1] + 20)
    j.atlas_frame(frame.copy(), LensResult(fingertip=p, pinching=True, pinch_point=p), 2.0)
    j.atlas_frame(frame.copy(), LensResult(fingertip=p, pinching=False), 2.2)
    assert not j.atlas_on()                                        # Close on the shelf leaves ATLAS
    assert j.open_atlas(3.0)
    j.atlas_key(ord("u"), None, 3.1)
    assert not j.atlas_on() and j.s.locked                         # U closes ATLAS and locks
    assert j.atlas_key(ord("q")) == "quit"


def test_the_standalone_camera_loop_runs_with_fake_hardware(monkeypatch):
    import atlas_app as AA
    import agents.lens as L

    class Cap:
        n = 0
        def __init__(self, *a): pass
        def set(self, *a): pass
        def isOpened(self): return True
        def read(self):
            Cap.n += 1
            return (Cap.n < 12), np.full((720, 1280, 3), 60, np.uint8)
        def release(self): pass

    class FakeLens:
        owner = types.SimpleNamespace(kind="off")
        def __init__(self): self.t = 0
        def process(self, frame, ts):
            self.t += 1
            pinch = 3 <= self.t <= 6
            return LensResult(fingertip=(600 + 10 * self.t, 360), pinching=pinch, hand=[(600, 400)] * 21)
        def close(self): pass

    keys = iter([255, ord("d"), ord("e"), 9, ord("n")] + [255] * 20)
    monkeypatch.setattr(AA.cv2, "VideoCapture", Cap)
    monkeypatch.setattr(L, "Lens", FakeLens)
    monkeypatch.setattr(AA.cv2, "namedWindow", lambda *a, **k: None)
    monkeypatch.setattr(AA.cv2, "imshow", lambda *a, **k: None)
    monkeypatch.setattr(AA.cv2, "waitKey", lambda *a: next(keys))
    monkeypatch.setattr(AA.cv2, "destroyAllWindows", lambda: None)
    AA.run_camera("jwst")
    assert Cap.n == 12


def test_a_pinch_held_from_the_launcher_cannot_press_a_shelf_tile(viewer):
    viewer.open(5.0)                                                 # re-opened while the hand is pinching
    p = centre(dict(viewer.buttons)["close"])
    viewer.update(LensResult(fingertip=p, pinching=True, pinch_point=p), 5.1)
    viewer.update(LensResult(fingertip=p, pinching=False), 5.3)
    assert viewer.active                                             # that release closed nothing


def test_the_credit_line_is_never_cut_at_640_wide():
    import jsil as J
    v = AtlasViewer(reduced_motion=True, idle_sway=False)
    v.open(0.0, "chronograph")
    lines = J.wrap(v.atlas.model.credit, int((640 - 32) * 0.55), 10.5)
    assert len(lines) <= 3 and "Converted to a line drawing" in " ".join(lines)


def test_notice_names_each_source_and_year():
    notice = (LIBRARY / "NOTICE.md").read_text()
    assert notice.count("Source: https://") == 6 and "(c) 2024" in notice and "(c) 2025" in notice


# ---------------- hands and the legend ----------------

def _hand(pose, wrist=(900, 600), others=()):
    from james_core import handpose as HP
    pts = HP.synth(pose, wrist, 110)
    pin = pose == "pinch"
    return LensResult(hand=pts, fingertip=pts[8], pinching=pin, pinch_point=pts[8] if pin else None,
                      others=[list(o) for o in others])


def test_a_held_peace_sign_moves_to_the_next_model_once(viewer):
    t = 2.0
    for _ in range(45):                                               # 1.5 s of peace
        t += 1 / 30
        viewer.update(_hand("peace"), t)
    assert viewer.i == 1 and viewer.atlas.model.id == viewer.models[1].id
    assert viewer.msg and viewer.msg[0] == viewer.models[1].label


def test_the_sidebar_legend_lights_the_gesture_it_sees(viewer):
    import jsil as J
    from atlas_app import SIDE_W
    t = 2.0
    for _ in range(15):
        t += 1 / 30
        viewer.update(_hand("fist"), t)
    assert viewer.hands.active == "fist" and viewer.track == "on" and viewer.n_hands == 1
    img = viewer.render(np.full((720, 1280, 3), 70, np.uint8), _hand("fist"), t)
    x, y, w, h = viewer.side
    assert w == SIDE_W and viewer.atlas.legend_outside
    side = img[y:y + h, x:x + w].astype(int)
    amber = np.all(np.abs(side - np.array(J.CONFIRM)) < 30, axis=2).sum()
    assert amber > 80                                                 # the lit row's border and glyph


def test_narrow_windows_fold_the_legend_into_a_strip(viewer):
    viewer.render(np.full((480, 640, 3), 70, np.uint8), None, 2.0)
    assert viewer.side is None and not viewer.atlas.legend_outside


# ---------------- colour and frame cost ----------------

def test_library_parts_are_coloured_by_family_and_job_parts_stay_grey():
    import jsil as J
    from atlas import FAMILY, Atlas, part_family
    car = next(m for m in library_models()[0] if m.id == "car_concept")
    a = Atlas(car, None)
    assert a.family["wheel_fl"] == "wheel" and a.family["body"] == "body" and a.family["glass"] == "optic"
    assert a.colour("wheel_fl", "base") == FAMILY["wheel"][1] != a.colour("body", "base")
    assert a.colour("wheel_fl", "hover") != a.colour("wheel_fl", "base")
    for m in library_models()[0]:                              # every part is named into a family on purpose
        for i, p in enumerate(m.parts):
            assert part_family(p.id, p.label, 0) == part_family(p.id, p.label, 3), (m.id, p.label)
    from james_core.safety_guard import load_policy
    job = Atlas(config.PACK.model(), load_policy())
    assert job.colour("motor", "base") == J.TEXT_2 and job.colour("motor", "selected") == J.CYAN


def test_a_slow_machine_draws_fewer_lines_only_while_the_view_moves(viewer, monkeypatch):
    monkeypatch.setattr(config, "ATLAS_FRAME_BUDGET_MS", 5.0, raising=False)
    a = viewer.atlas
    a.reduced = False
    bg = np.full((720, 1280, 3), 70, np.uint8)
    viewer.render(bg.copy(), None, 2.0)
    a._full_ms = 12.0                                          # pretend a full drawing costs 12 ms here
    a.turn(5)
    a.settle()
    viewer.render(bg.copy(), None, 2.1)
    assert a.stride == 3
    for i in range(90):                                        # the camera eases in, then it is still: full detail
        viewer.render(bg.copy(), None, 2.2 + i / 30)
    assert a.stride == 1


def test_the_sidebar_and_shelf_are_painted_only_when_they_change(viewer, monkeypatch):
    calls = []
    real = viewer._paint_side
    monkeypatch.setattr(viewer, "_paint_side", lambda img, now: (calls.append(1), real(img, now)))
    bg = np.full((720, 1280, 3), 70, np.uint8)
    for t in (2.0, 2.1, 2.2):
        viewer.render(bg.copy(), None, t)
    assert calls == []                                         # the fixture's first render already painted it
    viewer.key("n")
    viewer.render(bg.copy(), None, 2.3)
    assert len(calls) == 1 and viewer.fps > 0
