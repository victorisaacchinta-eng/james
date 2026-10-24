"""Voice v2 (plans, V-sign push-to-talk, the voice window), the owner-ring hand filter and the
Universal Display links. Everything here runs without a camera, a microphone or the network."""
import plistlib
import sys
import time
import types

import numpy as np
import pytest

sys.modules.setdefault("sounddevice", types.ModuleType("sounddevice"))

import commands as C          # noqa: E402
import launcher_apps as LA    # noqa: E402
import owner as OW            # noqa: E402
from hands import FIST, OPEN, PEACE, hand, render   # noqa: E402

SILVER, GOLD = (185, 185, 190), (40, 170, 220)


# ---------- the three failures from the demo log ----------

def test_open_chrome_and_play_is_one_play_step_in_chrome():
    steps = C.plan("Open chrome and play Despacito.")
    assert [(s.kind, s.arg, s.service, s.browser) for s in steps] == [("play", "despacito", "youtube", "Google Chrome")]


@pytest.mark.parametrize("said,want", [
    ("play believer on spotify", [("play", "believer", "spotify")]),
    ("open spotify and play shape of you", [("play", "shape of you", "spotify")]),
    ("open notes and then play lofi beats", [("open", "notes", ""), ("play", "lofi beats", "youtube")]),
    ("search for pump seal kits and open github", [("search", "pump seal kits", ""), ("open", "github", "")]),
    ("open tom and jerry", [("open", "tom and jerry", "")]),                 # 'and' inside a name is not a split
    ("Hey James, can you open Asphalt 8 on my laptop?", [("open", "asphalt 8 on my laptop", "")]),
    ("what is this", [("identify", "", "")]),
    ("it is a redmi note 10", []),                                           # a model name, not a command
])
def test_plans(said, want):
    assert [(s.kind, s.arg, s.service) for s in C.plan(said)] == want


def _mac_app(root, name, display=None):
    c = root / f"{name}.app" / "Contents"
    c.mkdir(parents=True)
    if display:
        (c / "Info.plist").write_bytes(plistlib.dumps({"CFBundleDisplayName": display, "CFBundleName": name}))


def test_game_of_thrones_finds_gotkingsroad(tmp_path, monkeypatch):
    """Log: 'open game of thrones game on my laptop' was cut to 'of thrones'. The app is GOTKingsroad.app."""
    _mac_app(tmp_path, "GOTKingsroad")
    _mac_app(tmp_path, "Asphalt")
    _mac_app(tmp_path, "Game Center")
    _mac_app(tmp_path, "Google Chrome")
    monkeypatch.setattr(LA, "MAC_DIRS", [str(tmp_path)])
    apps = LA.installed_apps("darwin")
    assert "GOT Kingsroad" in apps                                            # camel case split
    for said in ("game of thrones game on my laptop", "game of thrones", "Game of Thrones."):
        hit = LA.find_app(said, apps)
        assert hit and hit[1].endswith("GOTKingsroad.app"), (said, hit)
    assert LA.find_app("asphalt game on my laptop", apps)[0] == "Asphalt"
    assert LA.clean_query("open the game of thrones game on my laptop") == "open the game of thrones game"
    assert LA.clean_query("the notes app") == "notes"


def test_info_plist_display_name_is_an_alias(tmp_path, monkeypatch):
    _mac_app(tmp_path, "zoom.us", display="Zoom")
    monkeypatch.setattr(LA, "MAC_DIRS", [str(tmp_path)])
    hit = LA.find_app("zoom", LA.installed_apps("darwin"))
    assert hit and hit[1].endswith("zoom.us.app")


# ---------- URLs ----------

def test_universal_display_slug_matches_their_rule():
    assert C.ud_slug("Redmi Note 10 Pro+") == "redmi-note-10-pro"
    assert C.ud_slug("  Galaxy  A52 (5G) ") == "galaxy-a52-5g"
    assert C.ud_slug("iPhone 13 -- mini") == "iphone-13-mini"
    assert C.ud_url("Redmi Note 10") == "https://universaldisplay.in/display/redmi-note-10"
    assert C.ud_url("iPhone 13", "glass") == "https://universaldisplay.in/tempered-glass/iphone-13"
    assert C.ud_url("") == C.UD_HOME
    assert [(s.kind, s.arg) for s in C.plan("universal display for redmi note 10")] == [("display", "redmi note 10")]
    assert [(s.kind, s.arg) for s in C.plan("tempered glass for iPhone 13")] == [("glass", "iphone 13")]
    assert [(s.kind, s.arg) for s in C.plan("open universal display")] == [("display", "")]


def test_first_youtube_video_and_prompt():
    html = 'xx"videoRenderer":{"videoId":"kJQP7kiw5Fk","thumbnail"'
    assert C.first_youtube_video(html) == "https://www.youtube.com/watch?v=kJQP7kiw5Fk"
    assert C.first_youtube_video("<html>nothing</html>") is None
    p = C.whisper_prompt(["Asphalt", "GOT Kingsroad", "Spotify", "A" * 60] + [f"App{i}" for i in range(300)])
    assert "Asphalt" in p and "Spotify" in p and "A" * 60 not in p and len(p) <= 600


def test_transcriber_uses_the_vocabulary_prompt(monkeypatch):
    from agents import echo
    seen = {}

    class M:
        def transcribe(self, audio, **kw):
            seen.update(kw)
            return iter([types.SimpleNamespace(text="open asphalt", avg_logprob=-0.2)]), None
    t = echo.Transcriber()
    t._model = M()
    txt, conf = t.transcribe(np.zeros(16000, np.float32), "Apps: Asphalt.")
    assert txt == "open asphalt" and seen["initial_prompt"] == "Apps: Asphalt." and seen["beam_size"] == 5


# ---------- running a plan ----------

def _james(monkeypatch):
    import james as JM
    monkeypatch.setattr(JM.llm, "available", lambda *a, **k: False)
    return JM, JM.James()


def test_chrome_despacito_opens_the_top_video_in_chrome(monkeypatch):
    JM, j = _james(monkeypatch)
    j.installed = {"Google Chrome": "/Applications/Google Chrome.app"}
    opened = []
    monkeypatch.setattr(JM.LA, "open_url", lambda url, where=None, **k: opened.append((url, where)))

    class R:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self, n): return b'"videoRenderer":{"videoId":"kJQP7kiw5Fk"'
    monkeypatch.setattr(JM.egress, "urlopen", lambda *a, **k: R())
    j._before_listen = "READY"
    j.on_heard("Open chrome and play Despacito.", 0.47)
    j.on_heard("yes", 0.9)                                    # iteration 3: heard at 0.47, so it is confirmed first
    for _ in range(100):
        if opened:
            break
        time.sleep(0.02)
    assert opened == [("https://www.youtube.com/watch?v=kJQP7kiw5Fk", "/Applications/Google Chrome.app")]
    time.sleep(0.05)
    j.drain()
    assert j.voice["heard"] == "yes"                         # the last thing heard was the confirmation
    assert ['Play "despacito" on YouTube in Google Chrome', "ok"] in [r[:2] for r in j.voice["steps"]]


def test_youtube_falls_back_to_the_results_page_offline(monkeypatch):
    JM, j = _james(monkeypatch)
    opened = []
    monkeypatch.setattr(JM.LA, "open_url", lambda url, where=None, **k: opened.append(url))
    def boom(*a, **k):
        raise OSError("offline")
    monkeypatch.setattr(JM.egress, "urlopen", boom)
    j._play_youtube(0, "despacito", "")
    assert opened == ["https://www.youtube.com/results?search_query=despacito"]


def test_universal_display_actions_after_a_phone_is_identified(monkeypatch):
    JM, j = _james(monkeypatch)
    opened = []
    monkeypatch.setattr(JM.LA, "open_url", lambda url, where=None, **k: opened.append(url))
    j.s.mode, j.s.result = "DONE", {"object": "smartphone", "brand": "Xiaomi", "model": "Redmi Note 10"}
    keys = [b.key for b in j.task_actions()]
    assert keys == ["open", "ud_display", "ud_glass", "done"]
    j.press("ud_display")
    j.press("ud_glass")
    assert opened == ["https://universaldisplay.in/display/redmi-note-10",
                      "https://universaldisplay.in/tempered-glass/redmi-note-10"]
    j.s.result = {"object": "centrifugal pump", "model": "CP-40"}
    assert "ud_display" not in [b.key for b in j.task_actions()]


# ---------- V-sign push-to-talk ----------

class FakeRec:
    def __init__(self):
        self.active, self.level, self.started = False, 0.3, 0.0

    def start(self):
        self.active = True

    def stop(self):
        self.active = False
        return np.zeros(16000, np.float32)


def _view(pose=None):
    from agents.lens import LensResult
    r = LensResult()
    if pose is not None:
        r.hand = hand(300, 200, 2.0, pose)
        r.peace, r.fist, r.open_palm = pose is PEACE, pose is FIST, pose is OPEN
    return r


def test_v_sign_starts_fist_stops_and_runs(monkeypatch):
    JM, j = _james(monkeypatch)
    j.rec = FakeRec()
    heard = []
    monkeypatch.setattr(j, "_transcribe", lambda audio: j.q.put(("heard", "open asphalt", 0.8)))
    monkeypatch.setattr(j, "open_by_name", lambda s: (heard.append(s), (True, "Asphalt opened"))[1])
    monkeypatch.setattr(JM.threading, "Thread", lambda target, args=(), daemon=None: types.SimpleNamespace(
        start=lambda: target(*args)))
    g = {"peace": None, "fist": None, "cancel": None, "lost": None}
    t = 100.0
    JM.voice_gestures(j, _view(PEACE), t, g)
    assert not j.rec.active                                            # a flicker of V is not enough
    JM.voice_gestures(j, _view(PEACE), t + JM.PEACE_HOLD_S + 0.01, g)
    assert j.rec.active and j.s.mode == "LISTENING" and j.voice["via"] == "gesture"
    j.voice["t0"] = t
    JM.voice_gestures(j, _view(PEACE), t + 2, g)                      # still holding V: still listening
    JM.voice_gestures(j, _view(None), t + 2.5, g)                     # moving between poses
    assert j.rec.active
    JM.voice_gestures(j, _view(FIST), t + 3, g)
    JM.voice_gestures(j, _view(FIST), t + 3 + JM.FIST_HOLD_S + 0.01, g)
    assert not j.rec.active and j.s.mode == "THINKING"
    j.drain()
    assert heard == ["asphalt"] and j.s.mode == "READY"
    assert j.voice["steps"][0][:2] == ["Open asphalt", "ok"]


def test_open_palm_cancels_listening_and_nothing_runs(monkeypatch):
    JM, j = _james(monkeypatch)
    j.rec = FakeRec()
    ran = []
    monkeypatch.setattr(j, "_transcribe", lambda audio: ran.append(1))
    g = {"peace": None, "fist": None, "cancel": None, "lost": None}
    j.start_listen("gesture")
    t = j.voice["t0"]
    JM.voice_gestures(j, _view(OPEN), t + 1, g)
    JM.voice_gestures(j, _view(OPEN), t + 1 + JM.CANCEL_HOLD_S + 0.01, g)
    assert not j.rec.active and ran == [] and j.s.mode == "READY" and j.voice["state"] == "cancelled"


def test_listening_stops_by_itself(monkeypatch):
    JM, j = _james(monkeypatch)
    j.rec = FakeRec()
    ran = []
    monkeypatch.setattr(j, "_transcribe", lambda audio: ran.append(1))
    monkeypatch.setattr(JM.threading, "Thread", lambda target, args=(), daemon=None: types.SimpleNamespace(
        start=lambda: target(*args)))
    g = {"peace": None, "fist": None, "cancel": None, "lost": None}
    j.start_listen("gesture")
    JM.voice_gestures(j, _view(PEACE), j.voice["t0"] + JM.VOICE_MAX_S + 0.1, g)
    assert ran == [1] and not j.rec.active


def test_v_sign_does_nothing_when_locked_or_busy(monkeypatch):
    JM, j = _james(monkeypatch)
    j.rec = FakeRec()
    g = {"peace": None, "fist": None, "cancel": None, "lost": None}
    for setup in ("locked", "IDENTIFYING", "SEARCHING"):
        j.s.locked = setup == "locked"
        j.s.mode = "READY" if setup == "locked" else setup
        JM.voice_gestures(j, _view(PEACE), 10.0, g)
        JM.voice_gestures(j, _view(PEACE), 11.0, g)
        assert not j.rec.active, setup


# ---------- gestures and the owner ring ----------

def _lens():
    from agents.lens import Lens
    L = object.__new__(Lens)
    L._pinched, L.owner, L._enroll = False, OW.OwnerFilter(None), None
    return L


def test_gesture_shapes():
    from agents.lens import LensResult
    L = _lens()
    got = {}
    for name, pose in (("open", OPEN), ("peace", PEACE), ("fist", FIST)):
        r = LensResult()
        L._pinched = False
        L.gestures(r, np.zeros((720, 1280, 3), np.uint8), [hand(300, 200, 2.0, pose)], ["Left"], 0)
        got[name] = (r.open_palm, r.peace, r.fist)
    assert got == {"open": (True, False, False), "peace": (False, True, False), "fist": (False, False, True)}


def _scene(owner_at=(260, 150), owner_pose=OPEN, ring=SILVER, extra=True):
    img = np.full((720, 1280, 3), (60, 70, 60), np.uint8)
    hands = []
    h = hand(*owner_at, 2.0, owner_pose)
    render(img, h, ring, k=2.0)
    hands.append(h)
    if extra:
        for (x, y, k, r) in ((760, 120, 2.4, None), (1000, 380, 1.3, GOLD)):   # a bigger bare hand, a gold ring
            hh = hand(x, y, k)
            render(img, hh, r, k=k)
            hands.append(hh)
    return img, hands


def _enrolled_filter():
    enr = OW.Enroller(0)
    for i in range(20):
        img, hands = _scene(extra=False)
        img = np.clip(img.astype(int) + np.random.default_rng(i).integers(-4, 5, img.shape), 0, 255).astype(np.uint8)
        enr.add(img, hands[0], "Left")
    sig, msg = enr.finish()
    assert sig, msg
    f = OW.OwnerFilter()
    f.save(__import__("config").OWNER_RING, sig)
    return f


def test_without_a_ring_the_largest_hand_drives():
    img, hands = _scene()
    idx, status, _ = OW.OwnerFilter(None).select(img, hands, 0)
    assert (idx, status) == (1, "off")                                   # the bigger bare hand


def test_only_the_ring_hand_drives_input():
    import config
    f = _enrolled_filter()
    assert OW.OwnerFilter.load(config.OWNER_RING).enrolled               # saved, and loads back
    img, hands = _scene()
    got = [f.select(img, hands, t * 33) for t in range(OW.VOTE_WINDOW)]
    assert got[0][0] is None and got[0][1] == "searching"               # one frame is not enough
    assert got[-1][0] == 0 and got[-1][1] == "owner"
    # the owner makes a fist (ring hidden) and moves a little: still the owner; the others stay ignored
    img2, hands2 = _scene(owner_at=(280, 160), owner_pose=FIST)
    idx, status, _ = f.select(img2, hands2, 400)
    assert (idx, status) == (0, "owner")
    # owner leaves: nobody else takes over, then JAMES looks again
    img3 = np.full((720, 1280, 3), (60, 70, 60), np.uint8)
    others = hands2[1:]
    for hh, k, r in zip(others, (2.4, 1.3), (None, GOLD)):
        render(img3, hh, r, k=k)
    assert f.select(img3, others, 500)[0] is None
    assert f.select(img3, others, 500 + OW.LOST_MS + 10)[1] == "searching"
    assert all(f.select(img3, others, 2100 + t * 33)[0] is None for t in range(12))   # bare hand and gold ring never pass


def test_enrolment_refuses_a_ring_it_cannot_see():
    enr = OW.Enroller(0)
    for _ in range(20):
        img, hands = _scene(ring=None, extra=False)
        enr.add(img, hands[0])
    sig, msg = enr.finish()
    assert sig is None and "too much like the skin" in msg
    sig, msg = OW.Enroller(0).finish()
    assert sig is None and "Only 0 clear views" in msg


def test_lens_reports_other_hands_and_enrols(monkeypatch):
    import config
    from agents.lens import LensResult
    L = _lens()
    img, hands = _scene(extra=False)
    L.start_enroll(0)
    for t in range(0, OW.ENROLL_MS + 100, 100):
        r = LensResult()
        L.gestures(r, img, hands, ["Left"], t)
    assert r.enroll_msg and r.enroll_msg[0] and L.owner.enrolled and config.OWNER_RING.exists()
    img, hands = _scene()
    for t in range(10):
        r = LensResult()
        L.gestures(r, img, hands, ["Left"] * 3, 4000 + t * 33)
    assert r.owner == "owner" and r.hand == hands[0] and len(r.others) == 2
    L.forget_owner()
    assert not config.OWNER_RING.exists() and not L.owner.enrolled


# ---------- rendering ----------

@pytest.mark.parametrize("W,H", [(1280, 720), (1440, 900), (1920, 1080)])
def test_voice_window_owner_and_enrol_render(monkeypatch, W, H):
    JM, j = _james(monkeypatch)
    j.rec = FakeRec()
    from agents.lens import LensResult
    img = np.zeros((H, W, 3), np.uint8)
    lens = LensResult(hand=hand(300, 200, 2.0, PEACE), fingertip=(400, 260), peace=True,
                      others=[hand(900, 200, 1.5)], owner="owner")
    j.start_listen("gesture")
    out = j.draw(img.copy(), lens, None, 0.0)
    assert out.shape == (H, W, 3)
    j.rec.active = False
    j.voice.update(state="done", heard="open chrome and play despacito, and also a very long tail " * 3, conf=0.62)
    j.voice_step('Play "despacito" on YouTube in Google Chrome', "ok", "top video opened in Google Chrome")
    j.voice_step("Open game of thrones game", "fail", 'no app called "game of thrones game" found')
    j.voice_step("Search the web", "running")
    j.s.mode = "READY"
    lens.enroll, lens.owner = 0.4, "searching"
    out = j.draw(img.copy(), lens, None, 0.0)
    assert out.shape == (H, W, 3) and out.any()


def test_no_loose_guess_for_a_different_app():
    apps = {"Photos": "Photos", "Wispr Flow": "Wispr Flow"}
    assert LA.find_app("photoshop", apps) is None                     # not installed: say so, don't open Photos
    assert LA.find_app("wisper flow", apps)[0] == "Wispr Flow"          # a mis-transcribed spelling still works


def test_live_model_cannot_invent_a_symptom(monkeypatch):
    """Seen on the Mac with Ollama running: the model returned 'noise' for "uh what". Unsaid symptoms are dropped."""
    from agents import echo
    monkeypatch.setattr(echo.llm, "chat_json", lambda *a, **k: {"pump_number": None, "symptoms": ["noise"],
                                                                "request": "diagnose"})
    i, backend = echo.parse_intent("uh what", 0.9, "P-3")
    assert i.symptoms == () and i.confidence < 0.6 and "word check" in backend
    monkeypatch.setattr(echo.llm, "chat_json", lambda *a, **k: {"pump_number": 3, "symptoms": ["vibration", "noise"],
                                                                "request": "diagnose"})
    i, _ = echo.parse_intent("pump 3 is vibrating", 0.9, "P-3")
    assert i.symptoms == ("vibration",) and i.asset_id == "P-3"
