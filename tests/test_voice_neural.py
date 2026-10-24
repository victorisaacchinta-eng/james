"""The neural voice (Kokoro) and the talk-back switch. Audio devices are faked; one test synthesises for real
when kokoro-onnx and the model files are installed (skipped otherwise)."""
import sys
import time

import numpy as np
import pytest

import talk as T


def test_sentences_are_split_for_streaming():
    assert T.sentences("Certainly, sir. Opening Chrome, then searching YouTube. Done!") == \
        ["Certainly, sir.", "Opening Chrome, then searching YouTube.", "Done!"]
    assert T.sentences("Step one: check the lens. It is 3.5 mm wide.") == ["Step one: check the lens.", "It is 3.5 mm wide."]


def test_prefs_round_trip():
    assert T.load_prefs() == {}
    T.save_prefs(talkback=False, voice="bm_george", junk=1)
    assert T.load_prefs() == {"talkback": False, "voice": "bm_george"}


class FakeSD:
    def __init__(self):
        self.played, self.stops = [], 0

    def play(self, audio, sr):
        self.played.append(len(audio))

    def stop(self):
        self.stops += 1


class FakeNeural:
    def __init__(self, voice="bm_fable", speed=0.95, fail_on=""):
        import threading
        self.voice, self.speed, self.sr, self.error, self._k = voice, speed, 24000, "", object()
        self.ready = threading.Event()
        self.ready.set()
        self.fail_on, self.made = fail_on, []

    def load(self):
        pass

    def synth(self, text):
        self.made.append(text)
        if self.fail_on and self.fail_on in text:
            raise ValueError("zero-size array")
        return np.zeros(int(24000 * 0.05), np.float32)


def _neural_talker(monkeypatch, fail_on=""):
    sd = FakeSD()
    monkeypatch.setitem(sys.modules, "sounddevice", sd)
    monkeypatch.setattr(T, "neural_available", lambda: (True, ""))
    monkeypatch.setattr(T, "Neural", lambda voice, speed: FakeNeural(voice, speed, fail_on))
    lines = []
    t = T.Talker(True, "bm_george", log=lines.append)
    t._afplay = None            # these tests cover the in-process path; on a Mac Talker would pick afplay (tests below)
    return t, sd, lines


def _wait(t):
    for _ in range(300):
        if not t.speaking:
            return
        time.sleep(0.01)


def test_neural_engine_speaks_sentence_by_sentence(monkeypatch):
    t, sd, lines = _neural_talker(monkeypatch)
    assert t.engine == "kokoro" and t.voice == "bm_george" and "neural voice bm_george" in t.describe()
    t.say("Certainly, sir. Opening Chrome, then searching YouTube for Arduino. That is all.")
    _wait(t)
    assert len(sd.played) == 3 and t.neural.made[0] == "Certainly, sir."


def test_a_failed_sentence_falls_back_and_is_logged(monkeypatch):
    t, sd, lines = _neural_talker(monkeypatch, fail_on="Arduino")
    monkeypatch.setattr(T.sys, "platform", "linux")               # no macOS say here: just skip that sentence
    t.say("Certainly, sir, searching for Arduino now. Then we stop.")
    _wait(t)
    assert any("neural voice failed" in l for l in lines) and len(sd.played) == 1     # the second sentence still plays


def test_mute_stops_audio_and_queues_nothing(monkeypatch):
    t, sd, lines = _neural_talker(monkeypatch)
    t.set_muted(True)
    assert sd.stops >= 1
    t.say("hello there")
    assert t.q.empty() and "(muted)" in lines[-1]


def test_unknown_voice_name_uses_the_default(monkeypatch):
    sd = FakeSD()
    monkeypatch.setitem(sys.modules, "sounddevice", sd)
    monkeypatch.setattr(T, "neural_available", lambda: (True, ""))
    made = []
    monkeypatch.setattr(T, "Neural", lambda voice, speed: made.append(voice) or FakeNeural(voice, speed))
    T.Talker(True, "Daniel (Enhanced)")
    assert made == [T.DEFAULT_NEURAL]


def test_without_the_neural_voice_the_mac_voice_is_used(monkeypatch):
    monkeypatch.setattr(T.sys, "platform", "darwin")
    monkeypatch.setattr(T, "installed_voices", lambda: {"Daniel": "en_GB"})
    monkeypatch.setattr(T.threading, "Thread", lambda *a, **k: type("X", (), {"start": lambda self: None})())
    t = T.Talker(True, "")
    assert t.engine == "say" and t.voice == "Daniel" and "disabled in tests" in t.describe()


@pytest.mark.skipif(not T.neural_files_ok(), reason="voice files not in assets/voice")
def test_real_synthesis_smoke():
    pytest.importorskip("kokoro_onnx")
    n = T.Neural("bm_fable")
    n.load()
    assert n._k is not None, n.error
    a = n.synth("Certainly, sir.")
    assert a.dtype == np.float32 and len(a) / n.sr > 0.4


# ---- the switch in the HUD ----

def test_switch_toggles_and_is_remembered(monkeypatch):
    from test_talk import _james
    JM, j = _james(monkeypatch)
    monkeypatch.setattr(JM.config, "TALKBACK", True)
    j.s.locked = True                                              # works even when locked
    j.press("voice_toggle")
    assert j.talker.muted and T.load_prefs()["talkback"] is False
    j.press("voice_toggle")
    assert not j.talker.muted and T.load_prefs()["talkback"] is True
    log = (JM.config.DATA / "james_demo_log.txt").read_text()
    assert "TALK muted (switch)" in log and "TALK unmuted (switch)" in log


def test_switch_state_survives_a_restart(monkeypatch):
    import james as JM
    monkeypatch.setattr(JM.config, "TALKBACK", True)
    monkeypatch.setattr(JM.llm, "available", lambda *a, **k: False)
    T.save_prefs(talkback=False)
    j = JM.James()
    assert j.talker.muted


def test_voice_commands_use_the_same_switch(monkeypatch):
    from test_talk import _james
    JM, j = _james(monkeypatch)
    monkeypatch.setattr(JM.config, "TALKBACK", True)
    j._before_listen = "READY"
    j.on_heard("be quiet", 0.9)
    assert j.talker.muted and T.load_prefs()["talkback"] is False
    j.on_heard("talk to me", 0.9)
    assert not j.talker.muted


# ---- Mac playback goes through afplay, never in-process PortAudio (mic_check --talk crash, 2026-09-26 04:14) ----

class FakeAfplay:
    """Stands in for subprocess.Popen(['afplay', wav]). Plays for `secs` unless terminated."""
    def __init__(self, secs=0.05):
        self.secs, self.runs, self.killed, self.frames = secs, [], 0, []

    def __call__(self, args, **kw):
        import wave
        with wave.open(args[1], "rb") as w:
            assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 24000)
            self.frames.append(w.getnframes())
        fake, end = self, time.monotonic() + self.secs

        class P:
            done = False

            def poll(self):
                return 0 if self.done or time.monotonic() > end else None

            def terminate(self):
                self.done = True
                fake.killed += 1
        self.runs.append(args)
        return P()


def _mac_talker(monkeypatch, secs=0.05):
    t, sd, lines = _neural_talker(monkeypatch)
    player = FakeAfplay(secs)
    monkeypatch.setattr(T.subprocess, "Popen", player)
    t._afplay = "/usr/bin/afplay"
    return t, sd, player


def test_mac_plays_each_sentence_with_afplay_and_never_touches_portaudio(monkeypatch):
    t, sd, player = _mac_talker(monkeypatch)
    t.say("Certainly, sir. Opening Chrome. That is all.")
    _wait(t)
    assert [r[0] for r in player.runs] == ["/usr/bin/afplay"] * 3 and player.frames == [1200] * 3
    assert sd.played == [] and sd.stops == 0
    import os
    assert not any(os.path.exists(r[1]) for r in player.runs)       # temp WAVs are removed


def test_mac_stop_terminates_afplay_without_portaudio(monkeypatch):
    t, sd, player = _mac_talker(monkeypatch, secs=5)
    t.say("This sentence would take five seconds to play.")
    for _ in range(200):
        if player.runs:
            break
        time.sleep(0.01)
    t.stop()                                                         # push-to-talk: the mic opens right after this
    _wait(t)
    assert player.killed == 1 and sd.stops == 0 and sd.played == []
