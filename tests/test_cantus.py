"""CANTUS: the built-in songs, beat maps from notes and from audio, the slicing judge, scoring, the screens, and
the launcher tile in james.py."""
import json
import math
from pathlib import Path
import sys
import types

import numpy as np
import pytest

import config
from agents.lens import LensResult
from james_core import beatmap as BM
from james_core import cantus_songs as CS
from james_core import handpose as HP


# songs

def test_builtins_are_public_domain_classics_only():
    songs = CS.builtin_songs()
    assert [s.id for s in songs] == ["ode_to_joy", "fur_elise", "canon_in_d", "turkish_march"]
    for s in songs:
        assert s.composer in ("Ludwig van Beethoven", "Johann Pachelbel", "Wolfgang Amadeus Mozart")
        assert 25 < s.seconds < 70 and len(s.notes_seconds()) > 40


def test_note_names_parse():
    assert (CS.midi("C4"), CS.midi("A4"), CS.midi("F#5"), CS.midi("Bb3")) == (60, 69, 78, 58)
    mel = CS.parse("C4 r:.5 E4:2 | G4", unit=1.0)
    assert mel == [(0.0, 1.0, 60), (1.0, 0.5, None), (1.5, 2.0, 64), (3.5, 1.0, 67)]


def test_render_is_the_right_length_and_never_clips():
    s = CS.builtin_songs()[0]
    a = CS.render(s)
    assert abs(len(a) / CS.SR - (s.seconds + 1.0)) < 0.05
    assert 0.8 < float(np.abs(a).max()) <= 0.851


# placement

@pytest.mark.parametrize("song", CS.builtin_songs(), ids=lambda s: s.id)
@pytest.mark.parametrize("diff", list(BM.DIFFICULTY))
def test_every_chart_is_playable(song, diff):
    blocks = BM.chart_for_song(song).blocks[diff]
    cfg = BM.DIFFICULTY[diff]
    assert len(blocks) >= 20
    last = {0: None, 1: None}
    for a, b in zip(blocks, blocks[1:]):
        assert b.t - a.t >= cfg["min_gap"] - 1e-6
    for b in blocks:
        assert b.hand == (1 if b.col >= 2 else 0)                 # red on the left, blue on the right
        assert b.dir in BM.DIRS and b.t >= 1.0
        prev = last[b.hand]
        if prev is not None:
            assert b.t - prev.t >= cfg["min_gap"] - 1e-6
            if b.t - prev.t >= cfg["hand_gap"] and prev.dir in "UD" and b.dir in "UD":
                assert b.dir != prev.dir                            # down, up, down
        last[b.hand] = b


def test_charts_are_the_same_every_time():
    s = CS.builtin_songs()[2]
    assert BM.chart_for_song(s).to_json() == BM.chart_for_song(s).to_json()


def test_harder_means_more_blocks():
    for s in CS.builtin_songs():
        c = BM.chart_for_song(s)
        assert len(c.blocks["easy"]) <= len(c.blocks["normal"]) <= len(c.blocks["hard"])


# audio analysis

def clicks(bpm=100, dur=24, sr=22050, start=0.5):
    y = np.random.default_rng(0).normal(0, 0.01, sr * dur).astype(np.float32)
    beats = np.arange(start, dur - 0.5, 60 / bpm)
    for b in beats:
        i = int(b * sr)
        y[i:i + 300] += (np.hanning(600)[300:] * np.sin(np.arange(300) * 0.9)).astype(np.float32)
    return y, beats


def test_tempo_and_onsets_from_a_click_track():
    y, beats = clicks(100)
    a = BM.analyse(y, 22050)
    assert a["bpm"] == pytest.approx(100, rel=0.01)
    ev = np.array([e["t"] for e in a["events"]])
    err = [np.min(np.abs(ev - b)) for b in beats]
    assert np.median(err) < 0.015 and max(err) < 0.04


def test_onsets_of_a_real_melody_land_on_its_notes():
    s = CS.builtin_songs()[2]                                       # Canon in D
    y = BM.resample(CS.render(s), CS.SR, 22050)
    a = BM.analyse(y, 22050)
    assert a["bpm"] == pytest.approx(72, rel=0.02) or a["bpm"] == pytest.approx(144, rel=0.02)
    ev = np.array([e["t"] for e in a["events"]])
    err = np.array([np.min(np.abs(ev - n[0])) for n in s.notes_seconds()])
    assert np.mean(err < 0.035) > 0.9


def test_wav_reading_mixes_to_mono_and_resamples(tmp_path):
    import wave
    p = tmp_path / "st.wav"
    x = (np.stack([np.ones(4410), -np.ones(4410)], 1) * 0.5 * 32767).astype("<i2")
    with wave.open(str(p), "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(44100); w.writeframes(x.tobytes())
    y = BM.decode(p, 22050)
    assert len(y) == 2205 and abs(float(y.mean())) < 1e-3


def test_a_dropped_in_file_is_mapped_once_and_cached(tmp_path, monkeypatch):
    s = CS.builtin_songs()[0]
    f = tmp_path / "Beethoven - Joy test.wav"
    CS.write_wav(f, CS.render(s))
    ch = BM.chart_for_file(f, tmp_path / "maps")
    assert ch.title == "Joy test" and ch.artist == "Beethoven" and ch.source == "import"
    assert ch.bpm == pytest.approx(104, rel=0.02) and len(ch.blocks["normal"]) > 20
    assert len(list((tmp_path / "maps").glob("*.json"))) == 1
    monkeypatch.setattr(BM, "analyse", lambda *a: (_ for _ in ()).throw(AssertionError("re-analysed")))
    again = BM.chart_for_file(f, tmp_path / "maps")
    assert again.to_json()["blocks"] == ch.to_json()["blocks"]


def test_an_unreadable_file_fails_with_a_fix_not_a_crash(tmp_path, monkeypatch):
    bad = tmp_path / "noise.mp3"
    bad.write_bytes(b"not audio" * 100)
    monkeypatch.setattr(BM.shutil, "which", lambda name: None)    # no afconvert, no ffmpeg
    monkeypatch.setitem(sys.modules, "miniaudio", None)
    with pytest.raises(RuntimeError, match="ffmpeg"):
        BM.decode(bad)


# the judge

import cantus as C                                                  # noqa: E402


def test_segment_rectangle_crossing():
    r = (100, 100, 200, 200)
    assert C.seg_hits_rect((50, 150), (250, 150), r)
    assert C.seg_hits_rect((150, 50), (150, 120), r)
    assert not C.seg_hits_rect((50, 50), (90, 250), r)
    assert not C.seg_hits_rect((210, 90), (300, 300), r)


def test_a_slow_hand_is_not_a_swing():
    bl = C.Blade()
    for i in range(6):
        bl.push(i / 30, (500 + 3 * i, 400), 100)                    # 90 px/s
    assert not bl.swinging()
    for i in range(6, 9):
        bl.push(i / 30, (500 + 3 * 5 + 40 * (i - 5), 400), 100)     # 1200 px/s
    assert bl.swinging()
    vx, vy = bl.velocity()
    assert vx > 0 and abs(vy) < 1


def chart(blocks, dur=10.0):
    return BM.Chart("t", "Test", "Me", "builtin", dur, 120, "", {"normal": blocks})


def test_multiplier_climbs_like_beat_saber_and_a_miss_drops_it():
    run = C.Run(chart([BM.Block(1 + i, 0, 1, 0, "D") for i in range(20)]), "normal")
    mults = []
    for lv in run.blocks[:14]:
        run.hit(lv, 0.0, 0.0, (0, 1))
        mults.append(run.mult)
    assert mults[:2] == [1, 2] and mults[5] == 4 and mults[13] == 8
    run.fault(run.blocks[14], "MISS", 0.0)
    assert run.mult == 4 and run.combo == 0 and run.counts["MISS"] == 1
    assert run.max_score == run.best_so_far[-1] and 0 < run.accuracy < 1


@pytest.fixture
def app(tmp_path):
    a = C.CantusApp(C.Library(tmp_path))
    a.player = C._SilentPlayer()
    a.open(0.0)
    a.render(np.full((720, 1280, 3), 60, np.uint8), None, 0.1)
    return a


def start_custom(app, blocks, now=1.0):
    e = C.Entry(chart(blocks), path="")
    app.lib.entries.append(e)
    app.play(len(app.lib.entries) - 1, now)
    return app.run


def swipe_frames(app, blocks_dirs, hands_per_frame=1, side_x=None):
    """Feed frames where a fingertip sweeps through each block on time in direction d."""
    g = C.stage(1280, 720)
    now = app.start_at
    end = max(b.t for b, _ in blocks_dirs) + app.start_at + app.offset + 0.6
    while now < end:
        now += 1 / 30
        t = app.song_t(now)
        hands = []
        for b, d in blocks_dirs:
            if abs(b.t - t) < 0.12:
                x, y, half = C.block_rect(g, b, b.t - t, 1.5)
                k = (t - b.t) / 0.12
                tip = (x + d[0] * half * 2.5 * k, y + d[1] * half * 2.5 * k)
                pts = HP.synth("point", (0, 0), 90)
                dx, dy = tip[0] - pts[8][0], tip[1] - pts[8][1]
                hands.append([(int(a + dx), int(c + dy)) for a, c in pts])
        view = LensResult(hand=hands[0] if hands else None, fingertip=hands[0][8] if hands else None, others=hands[1:])
        app.update(view, now)
    return now


def test_on_time_swings_the_right_way_score_perfect(app):
    bs = [BM.Block(1.0 + i, i % 4, 1, int(i % 4 >= 2), "D" if i % 2 == 0 else "U") for i in range(6)]
    run = start_custom(app, bs)
    now = swipe_frames(app, [(b, BM.DIRS[b.dir]) for b in bs])
    assert run.counts["PERFECT"] + run.counts["GOOD"] == 6 and run.counts["MISS"] == 0
    while app.screen == "play" and now < app.start_at + 20:            # the song plays out
        now += 0.1
        app.update(LensResult(), now)
    assert app.screen == "results" and C.rank_for(run.accuracy) in ("SS", "S")


def test_a_swing_the_wrong_way_is_a_bad_cut(app):
    b = BM.Block(1.0, 0, 1, 0, "D")
    run = start_custom(app, [b])
    swipe_frames(app, [(b, (0.0, -1.0))])                          # swung up through a down block
    assert run.counts["BAD"] == 1 and run.combo == 0


def test_a_dot_takes_any_direction(app):
    b = BM.Block(1.0, 1, 0, 0, "A")
    run = start_custom(app, [b])
    swipe_frames(app, [(b, (1.0, 0.0))])
    assert run.counts["PERFECT"] + run.counts["GOOD"] == 1


def test_a_block_left_alone_is_a_miss(app):
    run = start_custom(app, [BM.Block(1.0, 2, 1, 1, "D")])
    now = app.start_at
    while app.screen == "play" and now < app.start_at + 8:
        now += 1 / 30
        app.update(LensResult(), now)
    assert run.counts["MISS"] == 1 and app.screen == "results"


def test_pause_freezes_the_song_clock(app):
    start_custom(app, [BM.Block(5.0, 0, 1, 0, "D")])
    app.pause(True, 2.0)
    t = app.song_t(2.0)
    assert app.song_t(9.0) == t and app.screen == "paused"
    app.pause(False, 9.0)
    assert app.song_t(9.0) == pytest.approx(t)


# screens

def tap(app, p, t):
    app.update(LensResult(fingertip=p, pinching=True, pinch_point=p), t)
    app.update(LensResult(fingertip=p, pinching=False), t + 0.15)


def centre(rect):
    x, y, w, h = rect
    return x + w // 2, y + h // 2


def test_pinching_a_card_plays_it_and_pinching_hard_changes_difficulty(app):
    app.update(LensResult(), 0.5)                                   # the pinch from the launcher has ended
    tap(app, centre(dict(app.buttons)["diff:hard"]), 1.0)
    assert app.diff == "hard"
    app.render(np.full((720, 1280, 3), 60, np.uint8), None, 1.5)
    tap(app, centre(dict(app.buttons)["song:1"]), 2.0)
    assert app.screen == "play" and app.run.chart.id == "fur_elise" and app.run.diff == "hard"


def test_keys(app):
    app.key("d", 1.0)
    assert app.diff == "hard"
    app.key("1", 1.0)
    assert app.screen == "play" and app.run.chart.id == "ode_to_joy"
    app.key(" ", 2.0)
    assert app.screen == "paused"
    app.key("esc", 2.5)
    assert app.screen == "select"
    app.key("esc", 3.0)
    assert not app.active


def test_best_scores_are_kept(tmp_path):
    lib = C.Library(tmp_path)
    assert lib.record("x", "normal", 1000, 0.5)
    assert not lib.record("x", "normal", 900, 0.45)
    assert C.Library(tmp_path).best("x", "normal")["rank"] == "B"


def test_dropped_in_files_appear_and_a_bad_one_says_why(tmp_path):
    lib = C.Library(tmp_path)
    (tmp_path / "songs").mkdir()
    CS.write_wav(tmp_path / "songs" / "mine.wav", CS.render(CS.builtin_songs()[3]))
    (tmp_path / "songs" / "broken.wav").write_bytes(b"RIFF....junk")
    assert lib.scan(background=False) == 2
    st = {e.path.split("/")[-1]: e for e in lib.entries if e.song is None}
    assert st["mine.wav"].status == "ready" and st["mine.wav"].chart.bpm == pytest.approx(116, rel=0.02)
    assert st["broken.wav"].status == "error" and st["broken.wav"].error


def test_demo_writes_every_screen(tmp_path, monkeypatch):
    paths = C.demo(tmp_path)
    assert [p.name for p in paths] == ["01_select.png", "02_play.png", "03_paused.png", "04_results.png"]
    assert not (tmp_path / "_lib").exists()


def test_built_in_audio_is_rendered_once(tmp_path):
    lib = C.Library(tmp_path)
    path, audio = lib.audio_for(lib.entries[0])
    assert audio is not None and Path(path).name.startswith("ode_to_joy-") and path.endswith(".wav")
    path2, audio2 = lib.audio_for(lib.entries[0])
    assert path2 == path and audio2 is None


# inside james.py

def _james(monkeypatch):
    sys.modules.setdefault("sounddevice", types.ModuleType("sounddevice"))
    import james as JM
    monkeypatch.setattr(JM.llm, "available", lambda *a, **k: False)
    return JM, JM.James()


def test_cantus_is_a_launcher_tile_that_opens_inside_james(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CANTUS_DIR", tmp_path, raising=False)
    JM, j = _james(monkeypatch)
    assert "cantus" in j.app_by_id and j.app_by_id["cantus"].how == "internal"
    j.press("app:cantus")
    assert j.cantus_requested and not j.atlas_requested
    assert j.open_cantus(0.0) and j.cantus_on() and j.inner_on()
    frame = np.full((720, 1280, 3), 60, np.uint8)
    out = j.cantus_frame(frame.copy(), LensResult(), 0.5)
    assert out.shape == frame.shape
    j.cantus_key(27, None, 1.0)                                     # Esc on the song list closes it
    assert not j.cantus_on()
    j.s.locked = True
    assert not j.open_cantus(2.0)


def test_saying_open_cantus_finds_the_tile(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CANTUS_DIR", tmp_path, raising=False)
    JM, j = _james(monkeypatch)
    ok, said = j.open_by_name("cantus")
    assert ok and j.cantus_requested


# review round: each of these misfired before its fix

def test_a_fast_swing_at_low_frame_rate_still_counts():
    bl = C.Blade()
    for i in range(4):
        bl.push(i * 0.1, (300 + 150 * i, 400), 100)                  # 10 fps, 1500 px/s
    assert bl.swinging() and bl.velocity()[0] == pytest.approx(1500, rel=0.01)


def test_a_partner_hand_dropping_out_for_a_frame_is_not_a_swing(app):
    run = start_custom(app, [BM.Block(3.0, 0, 1, 0, "D"), BM.Block(3.0 + 1.2, 3, 1, 1, "D")])
    left, right = HP.synth("open", (700, 600), 100), HP.synth("open", (1000, 600), 100)
    now = app.start_at
    for i in range(int(3.6 * 30)):
        now += 1 / 30
        both = i != 60                                                 # the right hand vanishes for one frame
        app.update(LensResult(hand=left, fingertip=left[8], others=[right] if both else []), now)
    assert run.counts["BAD"] == 0


def test_there_is_a_close_button_on_the_song_list(app):
    assert "close" in dict(app.buttons)
    app.update(LensResult(), 0.5)
    tap(app, centre(dict(app.buttons)["close"]), 1.0)
    assert not app.active


def test_resuming_does_not_pause_again_while_the_peace_sign_is_still_up(app):
    start_custom(app, [BM.Block(20.0, 0, 1, 0, "D")])
    peace = HP.synth("peace", (900, 600), 110)
    now = app.start_at
    while app.screen == "play" and now < app.start_at + 3:
        now += 1 / 30
        app.update(LensResult(hand=peace, fingertip=peace[8]), now)
    assert app.screen == "paused"
    app.key(" ", now)
    for _ in range(10):                                                # a third of a second, sign still up
        now += 1 / 30
        app.update(LensResult(hand=peace, fingertip=peace[8]), now)
    assert app.screen == "play"


def test_a_changed_song_gets_a_new_audio_file():
    s = CS.builtin_songs()[0]
    a = s.fingerprint
    s.bpm += 1
    assert s.fingerprint != a


def test_your_songs_and_maps_are_never_committed():
    import subprocess
    root = Path(__file__).resolve().parents[1]
    for f in ("data/cantus/songs/x.mp3", "data/cantus/maps/a.json", "data/cantus/scores.json", "data/cantus_frames/a.png"):
        r = subprocess.run(["git", "check-ignore", "-q", f], cwd=root)
        assert r.returncode == 0, f


def test_decode_tries_ffmpeg_when_afconvert_fails(tmp_path, monkeypatch):
    src = tmp_path / "t.ogg"
    src.write_bytes(b"x" * 100)
    good = tmp_path / "good.wav"
    CS.write_wav(good, np.zeros(22050, np.float32), 22050)

    def run(cmd, **k):
        if cmd[0] == "afconvert":
            return types.SimpleNamespace(returncode=1)
        Path(cmd[-1]).write_bytes(good.read_bytes())
        return types.SimpleNamespace(returncode=0)
    monkeypatch.setattr(BM.shutil, "which", lambda n: "/usr/bin/" + n)
    monkeypatch.setattr(BM.subprocess, "run", run)
    assert len(BM.decode(src)) == 22050


def test_pause_stops_the_music_without_afplay_too(monkeypatch):
    calls = []
    sd = types.SimpleNamespace(play=lambda a, sr: calls.append(("play", len(a))), stop=lambda: calls.append(("stop",)))
    monkeypatch.setitem(sys.modules, "sounddevice", sd)
    monkeypatch.setattr(C.sys, "platform", "linux")
    p = C.Player()
    p.start("", np.zeros(44100 * 4, np.float32), 44100)
    p.t_play -= 1.0                                                    # a second has played
    p.pause(True)
    p.pause(False)
    assert calls[0] == ("play", 44100 * 4) and calls[1] == ("stop",)
    assert calls[2][0] == "play" and calls[2][1] == pytest.approx(44100 * 3, abs=2000)


def test_every_song_is_reachable_by_hand_at_640x480(app):
    app.render(np.full((480, 640, 3), 60, np.uint8), None, 1.0)
    keys = dict(app.buttons)
    assert all(f"song:{i}" in keys for i in range(4)) and "close" in keys
    for k, (x, y, w, h) in keys.items():
        assert x >= 0 and y >= 52 and x + w <= 640 and y + h <= 480 - 30, k


def test_a_rescan_retries_a_file_that_failed(tmp_path):
    lib = C.Library(tmp_path)
    (tmp_path / "songs").mkdir()
    f = tmp_path / "songs" / "late.wav"
    f.write_bytes(b"RIFF..half copied")
    lib.scan(background=False)
    assert lib.entries[-1].status == "error"
    CS.write_wav(f, CS.render(CS.builtin_songs()[0]))                 # the copy finished
    lib.scan(background=False)
    assert lib.entries[-1].status == "ready"


def test_a_silent_file_is_refused_not_given_made_up_beats():
    with pytest.raises(ValueError, match="silent"):
        BM.analyse(np.zeros(22050 * 20, np.float32), 22050)


def test_a_zero_score_is_never_a_best(tmp_path):
    assert not C.Library(tmp_path).record("x", "normal", 0, 0.0)
