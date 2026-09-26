"""Owner key: the hand wearing the printed ArUco key (id config.OWNER_KEY_ID) drives JAMES.
Runs without a camera: synthetic hands (tests/hands.py) and a real rendered marker for detection."""
import cv2
import numpy as np

import config
import owner as OW
from hands import FIST, PEACE, hand


def _strap(h):
    """Where a watch-strap key sits: a little down the forearm from the wrist."""
    w, m = np.array(h[0], float), np.array(h[9], float)
    return tuple(w - (m - w) * 0.4)


def _back(h):
    """Back of the hand: between the wrist and the middle knuckle."""
    return tuple((np.array(h[0], float) + np.array(h[9], float)) / 2)


A, B = hand(200, 150, 2.0), hand(800, 150, 2.0)      # owner (A) and someone else (B), same size


def test_key_is_reserved_and_not_an_asset_tag():
    assert config.OWNER_MODE == "key" and config.OWNER_KEY_ID not in config.ASSET_TAGS


def test_key_hand_assignment():
    assert OW.key_hand([A, B], [_strap(A)]) == 0
    assert OW.key_hand([A, B], [_back(B)]) == 1
    assert OW.key_hand([A, B], [(600, 700)]) is None               # key on the table, far from both hands


def test_key_needs_three_of_five_frames_then_follows_the_hand():
    f = OW.KeyFilter(config.OWNER_KEY_ID)
    got = [f.select(None, [A, B], t * 33, [_strap(A)])[:2] for t in range(3)]
    assert got[0] == (None, "searching") and got[1] == (None, "searching") and got[2] == (0, "owner")
    # V sign / fist / palm turned: the key is hidden, the owner keeps control and is followed as it moves
    A2 = hand(230, 170, 2.0, PEACE)
    assert f.select(None, [B, A2], 200, [])[:2] == (1, "owner")
    A3 = hand(260, 190, 2.0, FIST)
    assert f.select(None, [A3, B], 233, [])[:2] == (0, "owner")


def test_no_key_nobody_drives():
    f = OW.KeyFilter(config.OWNER_KEY_ID)
    for t in range(10):
        assert f.select(None, [A, B], t * 33, [])[:2] == (None, "searching")


def test_one_lucky_frame_is_not_enough():
    f = OW.KeyFilter(config.OWNER_KEY_ID)
    seq = [[_strap(B)], [], [], [_strap(B)], [], []]
    assert all(f.select(None, [A, B], t * 33, k)[0] is None for t, k in enumerate(seq))


def test_owner_leaves_then_nobody_takes_over_until_the_key_is_seen_again():
    f = OW.KeyFilter(config.OWNER_KEY_ID)
    for t in range(3):
        f.select(None, [A, B], t * 33, [_back(A)])
    assert f.select(None, [B], 200, [])[:2] == (None, "owner")          # briefly gone: B does not take over
    assert f.select(None, [B], 200 + OW.KEY_LOST_MS + 1, [])[:2] == (None, "searching")
    assert f.select(None, [A, B], 200 + OW.KEY_LOST_MS + 400, [])[:2] == (None, "searching")  # back, key not shown yet


def test_key_moves_to_another_hand_hands_over():
    f = OW.KeyFilter(config.OWNER_KEY_ID)
    for t in range(3):
        f.select(None, [A, B], t * 33, [_strap(A)])
    out = [f.select(None, [A, B], 100 + t * 33, [_strap(B)])[:2] for t in range(3)]
    assert out[0] == (0, "owner") and out[-1] == (1, "owner")


def test_real_marker_is_split_from_asset_tags():
    """Render the real key and a real asset tag, detect them with OpenCV, and split them like Lens does."""
    from agents.lens import split_markers
    d = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    img = np.full((400, 800), 255, np.uint8)
    img[100:260, 100:260] = cv2.aruco.generateImageMarker(d, config.OWNER_KEY_ID, 160)
    tag_id = next(iter(config.ASSET_TAGS))
    img[100:260, 500:660] = cv2.aruco.generateImageMarker(d, tag_id, 160)
    corners, ids, _ = cv2.aruco.ArucoDetector(d, cv2.aruco.DetectorParameters()).detectMarkers(img)
    tags, keys = split_markers(corners, ids, config.OWNER_KEY_ID)
    assert [k.id for k in keys] == [config.OWNER_KEY_ID] and [t.id for t in tags] == [tag_id]
    assert abs(keys[0].center[0] - 180) < 3 and tags[0].asset_id == config.ASSET_TAGS[tag_id]


def test_lens_key_mode_and_pause():
    from agents.lens import Lens, LensResult
    L = object.__new__(Lens)
    L._pinched, L.owner, L._enroll, L.owner_paused = False, OW.KeyFilter(config.OWNER_KEY_ID), None, False
    frame = np.zeros((720, 1280, 3), np.uint8)
    for t in range(3):
        r = LensResult()
        L.gestures(r, frame, [B, A], ["Left", "Right"], t * 33, [_strap(A)])
    assert r.owner == "owner" and r.owner_kind == "key" and r.hand == A and r.others == [B]
    assert L.toggle_pause() is True
    r = LensResult()
    L.gestures(r, frame, [B, A], ["Left", "Right"], 200, [])
    assert r.owner == "off" and r.owner_paused and r.hand is not None             # any (largest) hand drives
    assert L.toggle_pause() is False
    r = LensResult()
    L.gestures(r, frame, [B, A], ["Left", "Right"], 233, [])
    assert r.owner == "searching" and r.hand is None                           # key required again


def test_mirror_keeps_keys():
    from agents.lens import LensResult, Tag
    from app import mirror_result
    c = np.array([[10, 10], [30, 10], [30, 30], [10, 30]], np.float32)
    r = LensResult(keys=[Tag(config.OWNER_KEY_ID, None, c, (20.0, 20.0))])
    m = mirror_result(r, 1280)
    assert m.keys[0].center == (1259.0, 20.0) and m.keys[0].corners[:, 0].max() == 1269


def test_owner_key_files(tmp_path, monkeypatch):
    import importlib.util
    from pathlib import Path
    monkeypatch.setattr(config, "ASSETS", tmp_path)
    spec = importlib.util.spec_from_file_location("mk", Path(__file__).resolve().parents[1] / "tools" / "make_owner_key.py")
    mk = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mk)
    mk.main()
    png = cv2.imread(str(tmp_path / f"owner_key_{config.OWNER_KEY_ID}.png"), 0)
    d = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    _, ids, _ = cv2.aruco.ArucoDetector(d, cv2.aruco.DetectorParameters()).detectMarkers(png)
    assert ids is not None and ids.flatten().tolist() == [config.OWNER_KEY_ID]
    assert (tmp_path / f"owner_key_{config.OWNER_KEY_ID}.pdf").stat().st_size > 1000


def test_key_mode_hud_renders(monkeypatch):
    """Header, owner-key outline, 'not owner' hand and the 'show the key' hint draw at the usual sizes.
    Set JAMES_QA_DIR to also save the frames for a visual check."""
    import os
    import james as JM
    from agents.lens import LensResult, Tag
    monkeypatch.setattr(JM.llm, "available", lambda *a, **k: False)
    j = JM.James()
    qa = os.environ.get("JAMES_QA_DIR")
    for W, H in ((1280, 720), (1920, 1080)):
        o, other = hand(300, 250, 2.0, FIST), hand(900, 250, 1.6)
        s = _strap(o)
        c = np.array([[s[0] - 28, s[1] - 28], [s[0] + 28, s[1] - 28], [s[0] + 28, s[1] + 28], [s[0] - 28, s[1] + 28]], np.float32)
        views = {
            "owner": LensResult(hand=o, fingertip=o[8], fist=True, others=[other], owner="owner", owner_kind="key",
                                keys=[Tag(config.OWNER_KEY_ID, None, c, s)]),
            "searching": LensResult(others=[o, other], owner="searching", owner_kind="key"),
            "paused": LensResult(hand=o, fingertip=o[8], others=[other], owner="off", owner_kind="key", owner_paused=True),
        }
        for name, v in views.items():
            out = j.draw(np.full((H, W, 3), 40, np.uint8), v, None, 0.0)
            assert out.shape == (H, W, 3) and out.any()
            if qa:
                cv2.imwrite(os.path.join(qa, f"key_{name}_{W}.png"), out)


def test_key_held_between_the_fingers_counts():
    """A 3 cm key pinched by the thumb and index finger sits just past the fingertips, off the wrist line."""
    tip = np.array(A[8], float)
    held = tuple(tip + (np.array(A[4], float) - tip) * 0.5 + (0, -30))      # between thumb and index, a bit above
    assert OW.key_hand([A, B], [held]) == 0


def test_wrong_marker_hint():
    import james as JM
    from agents.lens import LensResult, Tag
    c = np.zeros((4, 2), np.float32)
    tag_id = next(iter(config.ASSET_TAGS))
    v = LensResult(owner="searching", owner_kind="key", tags=[Tag(tag_id, config.ASSET_TAGS[tag_id], c, (0.0, 0.0))])
    hint = JM.wrong_key_hint(v)
    assert hint and f"id {tag_id}" in hint and f"id {config.OWNER_KEY_ID}" in hint
    v.keys = [Tag(config.OWNER_KEY_ID, None, c, (0.0, 0.0))]
    assert JM.wrong_key_hint(v) is None
