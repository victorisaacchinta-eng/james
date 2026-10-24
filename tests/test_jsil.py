"""JSIL design layer: rendering is correct, cached output matches direct drawing, text never
overflows, telemetry is measured, and every HUD state renders at the three QA resolutions."""
import io
import contextlib
import time

import numpy as np
import pytest

import jsil as J


def frame(W=1280, H=720, seed=0):
    rng = np.random.default_rng(seed)
    return (rng.random((H, W, 3)) * 200).astype(np.uint8)


def test_tokens_are_the_spec_hex_values():
    assert J.hex_bgr("#35E0D0") == (0xD0, 0xE0, 0x35) == J.CYAN
    assert J.BLACK == J.hex_bgr("#080D0F") and J.TEXT == J.hex_bgr("#EAF4F4")
    assert J.SPACE == (4, 8, 12, 16, 24, 32, 48, 64)


def test_fit_never_exceeds_width_and_wrap_respects_width():
    s = "Replace the display assembly, or take it to a repair shop near the plant gate"
    for w in (40, 120, 300):
        assert J.measure(J.fit(s, w, 14), 14) <= w
    for ln in J.wrap(s, 200, 15):
        assert J.measure(ln, 15) <= 200 or " " not in ln


def test_layer_cache_matches_direct_drawing():
    """The cached card must composite to the same pixels as drawing it straight onto the frame."""
    def draw(c):
        J.panel(c, 0, 0, 300, 120, accent=J.CYAN)
        J.label(c, "Current task", 16, 30)
        J.text(c, "Remove the coupling guard", 16, 60, 19, J.TEXT, "semi")
        return 120
    base = frame()
    direct = base.copy()
    sub = direct[50:170, 40:340].copy()
    draw(sub)
    direct[50:170, 40:340] = sub
    cached = base.copy()
    J.LayerCache().draw(cached, "k", 40, 50, 300, 200, draw)
    diff = np.abs(direct.astype(int) - cached.astype(int))
    assert diff.max() <= 3, diff.max()          # uint8 rounding only


def test_layer_cache_rebuilds_only_when_the_key_changes():
    calls = []
    def draw(c):
        calls.append(1); J.panel(c, 0, 0, 100, 40); return 40
    lc, img = J.LayerCache(), frame()
    for _ in range(5):
        lc.draw(img, "same", 0, 0, 100, 60, draw)
    lc.draw(img, "new", 0, 0, 100, 60, draw)
    assert len(calls) == 4                       # 2 renders (black + white) per build, 2 builds


def test_mark_and_cursor_states_render():
    m = J.mark(26)
    assert m.shape == (26, 26, 4) and m[..., 3].max() > 200
    img = frame()
    for st in J.CURSOR_COLORS:
        J.cursor(img, (300, 300), st, 0.5)
    J.cursor(img, None, "TRACKING")              # no hand: nothing drawn, no error


def test_frame_timer_reports_measured_values_only():
    t = J.FrameTimer()
    assert t.stats()["fps"] is None and t.stats()["p50"] is None      # nothing measured yet: N/A, not zero
    for _ in range(20):
        t.start(); time.sleep(0.004); t.mark("render"); t.end()
    s = t.stats()
    assert 3.5 < s["p50"] < 40 and s["p95"] >= s["p50"] and s["max"] >= s["p99"]
    assert 20 < s["fps"] < 300 and s["stages"]["render"] > 3


@pytest.mark.parametrize("W,H", [(1280, 720), (1440, 900), (1920, 1080)])
def test_every_maintenance_state_renders(W, H):
    import app as A
    from hud import Hud
    with contextlib.redirect_stdout(io.StringIO()):
        a = A.run_sim(save_frames=False)
    hud = Hud()
    views = [{"state": s, "headline": "x " * 30, "keys": "U lock"} for s in
             ("LOCKED", "TARGET_PENDING", "TARGET_CONFIRMED", "AWAITING_CONFIRMATION", "SAFETY_BLOCKED", "COMPLETE", "ESCALATED")]
    views.append(a.view())
    for v in views:
        out = hud.draw(frame(W, H), v)
        assert out.shape == (H, W, 3)


def test_hud_keeps_the_old_names_for_callers():
    import hud
    for n in ("AMBER", "RED", "GREEN", "CYAN", "WHITE", "GREY", "INK", "FONT", "ascii_", "text", "wrap", "panel", "chip", "Hud"):
        assert hasattr(hud, n), n
    assert hud.RED == J.ERROR                     # WARDEN blocks stay red


def test_no_iron_man_or_jarvis_branding_in_the_ui():
    from pathlib import Path
    root = Path(J.__file__).parent
    for f in ("jsil.py", "hud.py", "james.py", "app.py"):
        t = (root / f).read_text().lower()
        for bad in ("iron man", "ironman", "jarvis", "marvel"):
            assert bad not in t, (f, bad)


# ---------- launcher: allowlist, per-OS resolution, gesture ownership ----------

def _james(monkeypatch):
    import sys, types
    sys.modules.setdefault("sounddevice", types.ModuleType("sounddevice"))
    import james as JM
    monkeypatch.setattr(JM.llm, "available", lambda *a, **k: False)
    return JM, JM.James()


def test_launcher_targets_are_the_requested_list():
    import launcher_apps as LA
    ids = [t.id for t in LA.targets("me@example.com")]
    assert ids == ["youtube", "chrome", "github", "terminal", "calendar", "calculator", "clock", "music",
                   "mail", "drive", "claude"]
    mail = next(t for t in LA.targets("me@example.com") if t.id == "mail")
    assert mail.url == "https://mail.google.com/mail/?authuser=me@example.com"


def test_missing_apps_are_hidden_not_faked(tmp_path, monkeypatch):
    import launcher_apps as LA
    monkeypatch.setattr(LA, "MAC_DIRS", [str(tmp_path)])            # a Mac with none of the apps installed
    got = {r.id: r for r in LA.resolve("me@example.com", platform="darwin")}
    assert set(got) == {"youtube", "github", "calendar", "mail", "drive", "claude"}   # web links only
    assert all(r.how == "url" for r in got.values())
    assert got["youtube"].icon is not None and got["github"].icon is not None


def test_installed_mac_app_wins_over_the_web_link(tmp_path, monkeypatch):
    import launcher_apps as LA
    (tmp_path / "Claude.app" / "Contents").mkdir(parents=True)
    (tmp_path / "Music.app" / "Contents").mkdir(parents=True)
    monkeypatch.setattr(LA, "MAC_DIRS", [str(tmp_path)])
    monkeypatch.setattr(LA, "_mac_icon", lambda app, out: False)    # no sips/qlmanage here
    got = {r.id: r for r in LA.resolve(None, platform="darwin")}
    assert got["claude"].how == "app" and got["claude"].where.endswith("Claude.app")
    assert got["claude"].icon is not None                            # falls back to the shipped web icon
    assert got["music"].label == "Music"                             # Spotify absent: the music app that exists


def test_requested_icons(tmp_path, monkeypatch):
    """Calculator and Clock always use the generic icons, Spotify its logo, Terminal the Mac's own icon."""
    import launcher_apps as LA
    for n in ("Calculator", "Clock", "Spotify", "Terminal"):
        (tmp_path / f"{n}.app" / "Contents").mkdir(parents=True)
    extracted = []
    def fake_icon(app, out):
        extracted.append(app.name)
        return False
    icons = tmp_path / "icons"                                       # isolated cache: ignore icons a real run saved
    icons.mkdir()
    import shutil
    for f in LA.ICONS.glob("*.png"):
        if not f.name.startswith("app_"):
            shutil.copy(f, icons / f.name)
    monkeypatch.setattr(LA, "ICONS", icons)
    monkeypatch.setattr(LA, "MAC_DIRS", [str(tmp_path)])
    monkeypatch.setattr(LA, "_mac_icon", fake_icon)
    got = {r.id: r for r in LA.resolve(None, platform="darwin")}
    assert extracted == ["Terminal.app"]                              # only Terminal asks macOS for its icon
    for k in ("calculator", "clock", "music", "terminal"):
        assert got[k].icon is not None, k
    spot = LA._load_icon(LA.ICONS / "web_spotify.png")
    assert got["music"].label == "Spotify" and np.array_equal(got["music"].icon, spot)
    assert spot[0, 0, 3] == 0                                        # floating logo: transparent corners


def test_open_palm_cannot_open_the_launcher_during_a_task(monkeypatch):
    JM, j = _james(monkeypatch)
    now = time.monotonic() + 10
    assert j.palm_allowed(now)
    for mode in JM.TASK_MODES:
        j.s.mode = mode
        assert not j.palm_allowed(now), mode
        assert j.task_actions(), mode                                # the task offers its own actions instead
    j.s.mode = "READY"
    j.end_task("Task done")
    assert not j.palm_allowed(time.monotonic())                      # short block right after a task ends
    assert j.palm_allowed(time.monotonic() + JM.PALM_BLOCK_S + 0.1)


def test_late_model_result_after_cancel_is_ignored(monkeypatch):
    JM, j = _james(monkeypatch)
    j.s.mode = "IDENTIFYING"
    j.end_task("Task cancelled")
    j.q.put(("identified", {"object": "phone"}))
    j.drain()
    assert j.s.mode == "READY" and j.s.result is None


def test_only_allowlisted_targets_can_open(monkeypatch):
    JM, j = _james(monkeypatch)
    opened = []
    monkeypatch.setattr(JM.LA, "open_target", lambda r: opened.append(r.id))
    j.press("app:definitely-not-allowed")
    assert opened == [] and "not on the allowed list" in j.s.msg[0]
    first = j.apps[0]
    j.press(f"app:{first.id}")
    assert opened == [first.id]


# ---------- voice: open any installed app (launch-only) ----------

def _fake_mac(tmp_path, names):
    for n in names:
        (tmp_path / f"{n}.app" / "Contents").mkdir(parents=True)
    (tmp_path / "Games" / "Asphalt 8 Airborne.app" / "Contents").mkdir(parents=True)   # one folder deep


def test_find_any_installed_app_from_speech(tmp_path, monkeypatch):
    import launcher_apps as LA
    _fake_mac(tmp_path, ["Google Chrome", "Notes", "Numbers", "Asphalt Racing Tips"])
    monkeypatch.setattr(LA, "MAC_DIRS", [str(tmp_path)])
    apps = LA.installed_apps("darwin")
    assert "Asphalt 8 Airborne" in apps
    for said, want in [("asphalt on my laptop.", "Asphalt 8 Airborne"),    # the exact phrase from the log
                       ("Asphalt eight", "Asphalt 8 Airborne"), ("asphalt 8 please", "Asphalt 8 Airborne"),
                       ("chrome", "Google Chrome"), ("the notes app", "Notes")]:
        hit = LA.find_app(said, apps)
        assert hit and hit[0] == want, (said, hit)
    assert LA.find_app("zebra photoshop", apps) is None                   # nothing close: no guess
    assert LA.find_app("rm -rf /", apps) is None


def test_voice_open_uses_the_installed_app_and_never_a_shell(tmp_path, monkeypatch):
    import launcher_apps as LA
    _fake_mac(tmp_path, ["Notes"])
    monkeypatch.setattr(LA, "MAC_DIRS", [str(tmp_path)])
    JM, j = _james(monkeypatch)
    j.installed = LA.installed_apps("darwin")
    calls = []
    monkeypatch.setattr(JM.LA.subprocess, "Popen", lambda args, **kw: calls.append((args, kw)))
    monkeypatch.setattr(JM.LA, "sys", type("S", (), {"platform": "darwin"}))
    monkeypatch.setattr(JM.LA, "open_installed", lambda where: calls.append((["open", "-a", where], {})))
    j._before_listen = "READY"
    j.on_heard("open asphalt on my laptop.", 0.63)
    assert calls and calls[-1][0][:2] == ["open", "-a"] and calls[-1][0][2].endswith("Asphalt 8 Airborne.app")
    assert all("shell" not in kw for _, kw in calls)
    assert "Opening Asphalt 8 Airborne" in j.s.msg[0]
    n = len(calls)
    j.on_heard("open zebra photoshop", 0.9)
    assert len(calls) == n and "No app called" in j.s.msg[0]


def test_tests_never_write_to_the_real_demo_log():
    import config
    assert "pytest" in str(config.DATA) or "tmp" in str(config.DATA)
