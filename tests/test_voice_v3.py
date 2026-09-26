"""Voice v3: every command that went wrong in the 2026-09-26 00:31 to 00:59 log, as a regression test,
plus media control (pause / resume / next) and Spotify playing the actual track."""
import subprocess
import sys
import types

import pytest

sys.modules.setdefault("sounddevice", types.ModuleType("sounddevice"))

import commands as C          # noqa: E402
import launcher_apps as LA    # noqa: E402
import media                  # noqa: E402


def plan(t):
    return [(s.kind, s.arg, s.service or s.site, s.browser) for s in C.plan(t)]


@pytest.mark.parametrize("said,want", [
    # 00:32:07 preamble swallowed "open asphalt"; "play the game" searched YouTube for "the game"
    ("So this is what is happening. I want you to open Asphalt and Play the Game.", [("open", "asphalt", "", "")]),
    # 00:56:29 opened YouTube's home page AND the video: two tabs
    ("Open YouTube and play Paradise Songs.", [("play", "paradise songs", "youtube", "")]),
    # 00:57:24 "Spouse" = misheard "Pause"
    ("Spouse the video that is playing on YouTube.", [("media", "pause", "youtube", "")]),
    # 00:57:43 searched YouTube for "that is playing"
    ("Play the video that is playing on YouTube.", [("media", "play", "youtube", "")]),
    # 00:58:07 the comma after Open broke the command
    ("Open, Command Prompt on my MacBook.", [("open", "command prompt on my macbook", "", "")]),
    # 00:58:21 "on" heard as "and": played "despacito and spotify" on YouTube
    ("Play Despacito and Spotify.", [("play", "despacito", "spotify", "")]),
    # 00:58:36 "Play" heard as "Note"
    ("Note Despacito on Spotify.", [("play", "despacito", "spotify", "")]),
    ("Play Despacito on Spotify.", [("play", "despacito", "spotify", "")]),
    # 00:59:12 the whole sentence was taken as an app name
    ("Open GitHub and try to find the top best repose for IoT and hardware.", [("search", "iot and hardware", "github", "")]),
    # 00:59:21 no 'and': opened Chrome, dropped the search
    ("Open Chrome search for top GitHub reports for IOT and hardware and computer vision.",
     [("search", "iot and hardware and computer vision", "github", "Google Chrome")]),
    ("pause", [("media", "pause", "", "")]),
    ("next song", [("media", "next", "", "")]),
    ("resume the music on spotify", [("media", "play", "spotify", "")]),
    ("open spotify and play", [("media", "play", "spotify", "")]),
    ("stop", []),                                                      # Esc's job, not a media key
    ("play don't stop believing", [("play", "don't stop believing", "youtube", "")]),
    ("search github for yolo", [("search", "yolo", "github", "")]),
    ("it is a redmi note 10", []),
    ("I'll leave.", []),
])
def test_log_sentences(said, want):
    assert plan(said) == want


def test_github_top_sorts_by_stars():
    st = C.plan("Open GitHub and try to find the top best repose for IoT and hardware.")[0]
    assert st.top and C.site_search_url(st.site, st.arg, st.top) == \
        "https://github.com/search?q=iot+and+hardware&type=repositories&s=stars&o=desc"
    assert "s=stars" not in C.site_search_url("github", "yolo", False)


def test_open_the_game_asphalt_and_command_prompt():
    apps = {"Asphalt": "/Applications/Asphalt.app", "Terminal": "/System/Applications/Utilities/Terminal.app",
            "Game Center": "/System/Applications/Game Center.app", "GOT Kingsroad": "/Applications/GOTKingsroad.app"}
    assert LA.find_app("the game asphalt on my macbook", apps)[0] == "Asphalt"                 # 00:32:25
    assert LA.find_app("command prompt on my macbook", apps)[0] == "Terminal"                   # 00:58:07
    assert LA.find_app("game of thrones game", apps)[0] == "GOT Kingsroad"                      # still works
    assert LA.find_app("game center", apps)[0] == "Game Center"


def test_spotify_track_id_is_strict():
    ok = [{"url": "https://open.spotify.com/track/6habFhsOp2NvshLv26DqMb"}]
    assert C.spotify_track_id(ok) == "6habFhsOp2NvshLv26DqMb"
    bad = [{"url": 'https://open.spotify.com/track/6habFhsOp2Nv"; do shell script "x'},
           {"url": "https://evil.example/track/6habFhsOp2NvshLv26DqMb"}]
    assert C.spotify_track_id(bad) is None
    assert media.spotify_play_track('abc" & do shell script "rm') == (False, "bad track id")


def test_media_scripts_are_fixed(monkeypatch):
    """Only fixed scripts reach osascript; the spoken words never do."""
    calls = []

    def fake_run(args, **k):
        calls.append(args)
        out = "true" if "is running" in args[-1] else "ok"
        return subprocess.CompletedProcess(args, 0, out, "")
    monkeypatch.setattr(media.sys, "platform", "darwin")
    monkeypatch.setattr(media.subprocess, "run", fake_run)
    assert media.control("pause", "spotify") == (True, "done", "spotify")
    assert calls[-1] == ["osascript", "-e", 'tell application "Spotify" to pause']
    ok, _, where = media.control("next", "youtube")
    assert ok and where == "youtube" and "ytp-next-button" in calls[-1][-1]
    assert media.control("rm -rf", "spotify")[0] is False


def test_media_falls_through_to_the_open_player(monkeypatch):
    def fake_run(args, **k):
        s = args[-1]
        if 'application "Spotify" is running' in s:
            return subprocess.CompletedProcess(args, 0, "false", "")
        if "is running" in s:
            return subprocess.CompletedProcess(args, 0, "true", "")
        return subprocess.CompletedProcess(args, 0, "ok", "")
    monkeypatch.setattr(media.sys, "platform", "darwin")
    monkeypatch.setattr(media.subprocess, "run", fake_run)
    assert media.control("pause", "") == (True, "done", "youtube")


def test_chrome_javascript_off_says_how_to_turn_it_on(monkeypatch):
    def fake_run(args, **k):
        if "is running" in args[-1]:
            return subprocess.CompletedProcess(args, 0, "true", "")
        return subprocess.CompletedProcess(args, 1, "", "Executing JavaScript through AppleScript is turned off.")
    monkeypatch.setattr(media.sys, "platform", "darwin")
    monkeypatch.setattr(media.subprocess, "run", fake_run)
    ok, msg = media.chrome_youtube("pause")
    assert not ok and "Allow JavaScript from Apple Events" in msg


def _james(monkeypatch):
    import james as JM
    monkeypatch.setattr(JM.llm, "available", lambda *a, **k: False)
    return JM, JM.James()


def test_low_confidence_noise_is_not_called_an_unknown_command(monkeypatch):
    JM, j = _james(monkeypatch)
    j._before_listen = "READY"
    j.on_heard("I'll leave.", 0.33)
    assert "Didn't catch that clearly" in j.s.msg[0]
    j.on_heard("make me a sandwich", 0.9)
    assert "Say 'open'" in j.s.msg[0]


def test_spotify_plays_the_found_track(monkeypatch):
    JM, j = _james(monkeypatch)
    monkeypatch.setattr(JM.scout, "search", lambda q, n=5: [{"url": "https://open.spotify.com/track/6habFhsOp2NvshLv26DqMb",
                                                           "title": "Despacito", "site": "open.spotify.com"}])
    monkeypatch.setattr(JM.James, "browser_where", lambda self, n: "/Applications/Spotify.app")
    monkeypatch.setattr(JM.sys, "platform", "darwin")
    played = []
    monkeypatch.setattr(JM.media, "spotify_play_track", lambda tid: played.append(tid) or (True, "playing in Spotify"))
    j._play_spotify(0, "despacito")
    assert played == ["6habFhsOp2NvshLv26DqMb"]
    msgs = []
    while not j.q.empty():
        msgs.append(j.q.get())
    assert ("step", 0, "ok", "playing in Spotify") in msgs
    assert any(m[0] == "log" and "spotify:track:6habFhsOp2NvshLv26DqMb" in m[1] for m in msgs)


def test_open_chrome_search_top_github_keeps_top_and_chrome():
    st = C.plan("Open Chrome search for top GitHub reports for IOT and hardware and computer vision.")[0]
    assert (st.site, st.top, st.browser) == ("github", True, "Google Chrome")
