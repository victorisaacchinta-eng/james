"""Iteration 2 P1-B / P1-C: a change of controlling hand mid-command (ST-14) and old printed cards (ST-15)."""

from test_talk import _james


def _recording(monkeypatch):
    JM, j = _james(monkeypatch)
    started = []

    class Rec:
        active = False
        stats = None

        def start(self):
            self.active = True
            started.append(1)

        def stop(self):
            import numpy as np
            self.active = False
            return np.zeros(16000, np.float32)
    j.rec = Rec()
    j.s.mode = "READY"
    j.set_owner("owner", 1)                        # hand #1 holds the key
    return JM, j


def test_st14_handover_while_recording_discards_the_command(monkeypatch):
    JM, j = _recording(monkeypatch)
    monkeypatch.setattr(JM.James, "_transcribe", lambda self, audio: None)
    j.start_listen("gesture")
    assert j.rec.active
    j.set_owner("owner", 2)                        # the key is now on another hand
    assert not j.rec.active and j.voice["steps"][-1][0].startswith("Discarded")
    log = (JM.config.DATA / "james_demo_log.txt").read_text()
    assert "LISTEN discarded: another hand took control" in log


def test_st14_result_from_the_old_owner_is_ignored(monkeypatch):
    JM, j = _recording(monkeypatch)
    ran = []
    monkeypatch.setattr(JM.James, "run_plan", lambda self, steps: ran.append(steps))
    monkeypatch.setattr(JM.James, "_transcribe", lambda self, audio: None)
    j.start_listen("gesture")
    j.stop_listen(run=True)                        # transcription in flight...
    j.set_owner("owner", 2)                        # ...and someone else takes control
    j.q.put(("heard", "open terminal", 0.9)); j.drain()
    assert ran == []
    assert "IGNORED heard 'open terminal'" in (JM.config.DATA / "james_demo_log.txt").read_text()


def test_owner_stepping_out_of_view_still_runs_their_command(monkeypatch):
    """Intentional: V sign, speak, drop the hand. Hand gone 2 s stops listening and runs it; the key hold is 4 s,
    and after that nobody controls JAMES (status 'searching'), which is not a different owner."""
    JM, j = _recording(monkeypatch)
    ran = []
    monkeypatch.setattr(JM.James, "run_plan", lambda self, steps: ran.append(steps))
    monkeypatch.setattr(JM.James, "_transcribe", lambda self, audio: None)
    j.start_listen("gesture")
    j.stop_listen(run=True)
    j.set_owner("searching", 1)                    # control timed out, nobody took it
    j.q.put(("heard", "open terminal", 0.9)); j.drain()
    assert ran and ran[0][0].kind == "open"


def test_st14_pending_confirmation_is_dropped_on_handover(monkeypatch):
    JM, j = _recording(monkeypatch)
    ran = []
    monkeypatch.setattr(JM.James, "run_plan", lambda self, steps: ran.append(steps))
    j._before_listen = "READY"
    j.on_heard("Open the KPDR search for a go-by request.", 0.43)
    assert j.pending
    j.set_owner("owner", 2)
    j.on_heard("yes", 0.9)
    assert not any(len(r) == 2 for r in ran)


def test_owner_epoch_counts_takeovers():
    import owner
    f = owner.KeyFilter.__new__(owner.KeyFilter)
    f.cand, f.lock = None, None
    import numpy as np
    h = np.zeros((21, 3))
    h[0] = (100, 200, 0); h[9] = (100, 120, 0)
    before = f.epoch
    f._take(h, 0); f._take(h, 10)
    assert f.epoch == before + 2


def test_st15_old_pump_card_is_explained_not_targeted():
    from app import App
    from james_core.session import State
    a = App(sim=True)
    a.toggle_lock()
    assert a.state == State.TARGET_PENDING
    a.key_card_seen()
    assert "owner key, not a machine tag" in a.hud.banner[0] and "tag 4" in a.hud.banner[0]
    assert a.state == State.TARGET_PENDING and a.session.target is None
    a.key_card_seen()                               # not repeated every frame
    assert sum(e["kind"] == "owner_key_shown_as_tag" for e in a.ledger.events(a.session.id)) == 1


def test_owner_metrics_from_the_log():
    from tools.owner_metrics import metrics
    lines = ["01:00:00 OWNER key searching -> owner (hands 1, keys 1)",
             "01:00:10 OWNER key owner -> searching (hands 0, keys 0)",
             "01:00:16 OWNER key paused",
             "01:00:30 OWNER key required",
             "01:00:52 OWNER key searching -> owner (hands 1, keys 1)"]
    m = metrics(lines)
    assert (m["acquired"], m["lost"], m["bypasses"]) == (2, 1, 1)
    assert m["bypass_seconds_after_loss"] == [6] and m["reacquire_seconds"] == [22]
