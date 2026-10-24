"""Iteration 3, sections 4G and 4H: grounding edge cases, history dates and review order, and the pack snapshot
used end to end with no file re-read."""
import builtins
import pathlib
import time
from datetime import datetime, timedelta, timezone

import pytest

from agents.page import Chunk, ManualIndex
from james_core.ledger import Ledger


@pytest.fixture
def manual():
    return ManualIndex(use_embeddings=False)


def test_only_real_table_rows_confirm_a_cause(manual):
    manual.chunks = [
        Chunk(4, "5 Troubleshooting", "Note: bearing wear is not the likely cause when the temperature is normal."),
        Chunk(4, "5 Troubleshooting", "Example only. Likely cause: bearing wear."),                  # not a row
        Chunk(4, "5 Troubleshooting", "Symptom: grinding.\nThis is not the Likely cause: bearing wear."),
    ]
    assert manual.check_cause("bearing_wear", "P-3") is None
    manual.chunks.append(Chunk(4, "5 Troubleshooting", "Symptom: hot bearing.\nLikely cause: bearing wear. Go to 6.3"))
    assert manual.check_cause("bearing_wear", "P-3").stance == "bearing_wear"


def test_only_numbered_step_lines_become_steps_word_for_word(manual):
    manual.pages[5] = ("6.3 Bearing and coupling inspection\nDo not remove the guard while the pump runs.\n"
                       "See Step 9: this is a cross-reference, not a step.\n"
                       "Step 1: Isolate the motor at the breaker.\nStep 2: Do not touch the casing above 60 C.")
    _, steps = manual.procedure("bearing_wear", "P-3")
    assert [(s.number, s.text) for s in steps] == [(1, "Isolate the motor at the breaker."),
                                                   (2, "Do not touch the casing above 60 C.")]


def test_steps_carry_their_manual_step_id():
    from app import App
    a = App(sim=True)
    a.toggle_lock(); a.target("P-3", "asset_tag", 0.97); a.pinch()
    a.q.put(("heard", "Pump 3 is vibrating more than usual", 0.95, time.perf_counter(), "TYPED")); a.drain()
    assert [s.source_step for s in a.plan.steps][:2] == ["6.3 step 1, p. 5", "6.3 step 2, p. 5"]
    assert any("6.3 step 1, p. 5" in line for line in a.view()["readout"])


def _led_with(created):
    led = Ledger(":memory:")
    led.add_job("J1", "P-3", ["vibration"], "Bearing job", "senior-01", cause="bearing_wear", created=created)
    return led


@pytest.mark.parametrize("created,words", [
    ((datetime.now(timezone.utc) + timedelta(days=30)).isoformat(), "dated in the future"),
    ((datetime.now(timezone.utc) - timedelta(days=900)).isoformat(), "days old"),
    ("not a date", "no date"),
])
def test_history_dates_are_checked(created, words):
    from agents.recall import recall_checked
    ev, notes = recall_checked(_led_with(created), "P-3", ("vibration",))
    assert ev == [] and words in " ".join(notes)


def test_history_age_limit_is_configurable(monkeypatch):
    import config
    from agents.recall import recall_checked
    led = _led_with((datetime.now(timezone.utc) - timedelta(days=100)).isoformat())
    assert recall_checked(led, "P-3", ("vibration",))[0]
    monkeypatch.setattr(config, "MEMORY_MAX_AGE_DAYS", 30)
    assert recall_checked(led, "P-3", ("vibration",))[0] == []


def test_newest_review_wins_even_with_the_same_timestamp(monkeypatch):
    import james_core.ledger as L
    fixed = datetime(2026, 9, 26, tzinfo=timezone.utc)
    monkeypatch.setattr(L, "datetime", type("D", (), {"now": staticmethod(lambda tz=None: fixed)}))
    led = Ledger(":memory:")
    led.add_job("J1", "P-3", ["vibration"], "Bearing job", None, cause="bearing_wear")
    led.approve_job("J1", "senior-01")
    led.revoke_approval("J1", "senior-02", "did not hold")
    assert led.review_state("J1") == "revoke" and led.recall_approved("P-3", ["vibration"]) == []
    led.db.execute("UPDATE jobs SET approved_by=NULL WHERE job_id='J1'")
    led.approve_job("J1", "senior-03")                                  # re-approved later, same second
    assert led.review_state("J1") == "approve" and led.recall_approved("P-3", ["vibration"])


def test_investigation_never_rereads_pack_files(monkeypatch):
    """4H: after start-up every pack consumer uses the verified snapshot. Any open() of a pack file during a
    full investigation fails the test."""
    from app import App
    a = App(sim=True)
    packs = str(pathlib.Path(__file__).resolve().parents[1] / "packs")
    real_open, real_rb, real_rt = builtins.open, pathlib.Path.read_bytes, pathlib.Path.read_text

    def guard(p):
        if str(p).startswith(packs):
            raise AssertionError(f"pack file re-read: {p}")

    monkeypatch.setattr(builtins, "open", lambda f, *x, **k: (guard(f), real_open(f, *x, **k))[1])
    monkeypatch.setattr(pathlib.Path, "read_bytes", lambda self: (guard(self), real_rb(self))[1])
    monkeypatch.setattr(pathlib.Path, "read_text", lambda self, *x, **k: (guard(self), real_rt(self, *x, **k))[1])
    import config
    monkeypatch.setattr(config.PACK.__class__, "drift", lambda self: [])      # drift() hashes files on purpose
    a.toggle_lock(); a.target("P-3", "asset_tag", 0.97); a.pinch()
    a.q.put(("heard", "Pump 3 is vibrating more than usual", 0.95, time.perf_counter(), "TYPED")); a.drain()
    assert a.plan.status == "ready" and a.plan.pack_snapshot == config.PACK.snapshot


def test_build_environment_is_recorded():
    from tools.build_id import build_id, environment
    bid, n = build_id()
    env = environment()
    assert len(bid) == 12 and n > 50
    assert env["python"] and "snapshot" in env["pack"] and "model" in env and "numpy" in env
