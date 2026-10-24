"""Iteration 2 P0-F: the microphone is always released, errors are logged with the device, and a typed path exists."""
import sys

import numpy as np

from test_talk import _james


class _SD:
    def __init__(self, fail_start=0, fail_stop=False):
        self.fail_start, self.fail_stop, self.closed = fail_start, fail_stop, 0

    def _terminate(self):
        pass

    def _initialize(self):
        pass

    def query_devices(self, kind=None):
        return {"name": "MacBook Air Microphone"}

    def InputStream(self, **kw):
        sd = self

        class S:
            def start(self):
                if sd.fail_start > 0:
                    sd.fail_start -= 1
                    raise RuntimeError("Internal PortAudio error [PaErrorCode -9986]")

            def stop(self):
                if sd.fail_stop:
                    raise RuntimeError("stop failed")

            def close(self):
                sd.closed += 1
        return S()


def _rec(monkeypatch, sd):
    from agents import echo
    monkeypatch.setitem(sys.modules, "sounddevice", sd)
    return echo.Recorder()


def test_st16_repeated_cycles_release_the_device(monkeypatch):
    sd = _SD()
    r = _rec(monkeypatch, sd)
    for _ in range(25):
        r.start()
        r._on_audio(np.zeros((160, 1), np.float32))
        assert r.stop().size == 160 and not r.active
    assert sd.closed == 25 and r.stats["opens"] == 25 and r.stats["closes"] == 25


def test_st16_stop_failure_still_releases(monkeypatch):
    sd = _SD(fail_stop=True)
    r = _rec(monkeypatch, sd)
    r.start()
    r.stop()
    assert not r.active and sd.closed == 1 and r.stats["errors"] == 1 and "stop" in r.last_error
    r.start()                                                   # and the next cycle works
    assert r.active


def test_mic_error_is_logged_with_device_and_offers_typing(monkeypatch):
    JM, j = _james(monkeypatch)
    sd = _SD(fail_start=5)
    monkeypatch.setitem(sys.modules, "sounddevice", sd)
    j.s.mode = "READY"
    j.start_listen("key")
    log = (JM.config.DATA / "james_demo_log.txt").read_text()
    assert "MIC error device='MacBook Air Microphone'" in log and "talkback=" in log
    assert "type the command" in j.s.msg[0] and not j.rec.active and j.s.mode == "READY"


def test_mic_open_and_close_are_logged(monkeypatch):
    JM, j = _james(monkeypatch)
    monkeypatch.setitem(sys.modules, "sounddevice", _SD())
    j.s.mode = "READY"
    j.start_listen("key")
    j.stop_listen(run=False)
    log = (JM.config.DATA / "james_demo_log.txt").read_text()
    assert "MIC open device='MacBook Air Microphone'" in log and "MIC close" in log


def test_typed_command_runs_like_speech(monkeypatch):
    JM, j = _james(monkeypatch)
    ran = []
    monkeypatch.setattr(JM.James, "run_plan", lambda self, steps: ran.append([s.kind for s in steps]))
    j.s.mode = "READY"
    j.start_typing()
    for ch in "open terminalx":
        j.type_key(ord(ch))
    j.type_key(127)                                             # backspace
    j.type_key(13)
    assert ran == [["open"]] and j.typing is None
    log = (JM.config.DATA / "james_demo_log.txt").read_text()
    assert "TYPED 'open terminal' (typed input, not the microphone)" in log


def test_typing_escape_runs_nothing(monkeypatch):
    JM, j = _james(monkeypatch)
    ran = []
    monkeypatch.setattr(JM.James, "run_plan", lambda self, steps: ran.append(steps))
    j.start_typing()
    for ch in "close spotify":
        j.type_key(ord(ch))
    j.type_key(27)
    assert ran == [] and j.typing is None
