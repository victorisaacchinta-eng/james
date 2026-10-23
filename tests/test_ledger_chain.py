"""The ledger hash chain. Tamper-evident against edits that get past the triggers."""
import sqlite3

import pytest

from james_core.ledger import GENESIS, Ledger, link


def _led(tmp_path, n=5):
    led = Ledger(str(tmp_path / "j.db"))
    for i in range(n):
        led.append("ses-1", "confirmed", {"step_text": f"step {i}", "sources": ["manual p.4"]})
    return led


def _bypass(led):
    """What an attacker with the file would do first: drop the append-only triggers."""
    for t in ("events_no_update", "events_no_delete", "chain_no_update", "chain_no_delete"):
        led.db.execute(f"DROP TRIGGER {t}")


def test_fresh_ledger_is_intact_and_links_follow_each_other(tmp_path):
    led = _led(tmp_path)
    v = led.verify()
    assert v["ok"] and v["events"] == 5 and v["problems"] == [] and v["sealed_at_upgrade"] is None
    rows = led.db.execute("SELECT prev, hash FROM chain ORDER BY seq").fetchall()
    assert rows[0][0] == GENESIS and all(rows[i][0] == rows[i - 1][1] for i in range(1, 5))
    assert v["head"] == (5, rows[-1][1])


def test_chain_rows_are_append_only(tmp_path):
    led = _led(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        led.db.execute("UPDATE chain SET hash='x'")
    with pytest.raises(sqlite3.IntegrityError):
        led.db.execute("DELETE FROM chain")


def test_an_edited_event_is_caught(tmp_path):
    led = _led(tmp_path)
    _bypass(led)
    led.db.execute("UPDATE events SET payload=? WHERE seq=3", ('{"sources":[],"step_text":"isolate later"}',))
    v = led.verify()
    assert not v["ok"] and v["first_bad"] == 3 and "event #3 (confirmed) was changed" in v["problems"][0]


def test_a_removed_event_is_caught(tmp_path):
    led = _led(tmp_path)
    _bypass(led)
    led.db.execute("DELETE FROM events WHERE seq=2")
    v = led.verify()
    assert not v["ok"] and any("#2 is gone" in p for p in v["problems"])
    assert any("does not follow" in p for p in v["problems"])                  # #3 now follows #1


def test_removing_an_event_and_its_link_is_still_caught(tmp_path):
    led = _led(tmp_path)
    _bypass(led)
    led.db.execute("DELETE FROM events WHERE seq=2")
    led.db.execute("DELETE FROM chain WHERE seq=2")
    v = led.verify()
    assert not v["ok"] and v["first_bad"] == 3


def test_an_event_written_around_the_ledger_is_caught(tmp_path):
    led = _led(tmp_path)
    led.db.execute("INSERT INTO events(ts, session_id, kind, accepted, payload) VALUES ('t','s','complete',1,'{}')")
    v = led.verify()
    assert not v["ok"] and "has no link" in v["problems"][0]


def test_an_older_database_is_sealed_once_and_reported(tmp_path):
    p = tmp_path / "old.db"
    db = sqlite3.connect(p)                                  # the schema of the previous release: no chain table
    db.executescript("CREATE TABLE events(seq INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, session_id TEXT "
                     "NOT NULL, kind TEXT NOT NULL, accepted INTEGER NOT NULL, payload TEXT NOT NULL);")
    for i in range(3):
        db.execute("INSERT INTO events(ts, session_id, kind, accepted, payload) VALUES (?,?,?,?,?)",
                   (f"2026-09-26T0{i}:00:00", "ses-old", "complete", 1, "{}"))
    db.commit(); db.close()
    led = Ledger(str(p))
    assert led.sealed_on_open == 3
    led.append("ses-new", "complete", {})
    v = led.verify()
    assert v["ok"] and v["events"] == 4 and v["sealed_at_upgrade"] == (1, 3, 3)
    assert Ledger(str(p)).sealed_on_open == 0                 # never sealed twice


def test_repair_record_ends_with_the_chain_status(tmp_path):
    led = _led(tmp_path)
    rec = led.repair_record("ses-1").rstrip().splitlines()
    head = led.verify()["head"]
    assert rec[-2] == f"Ledger chain: intact, all 5 events checked. Head #5 {head[1]}"
    assert rec[-1].endswith(f"--expect 5:{head[1]}")
    led.append("ses-1", "complete", {})                        # the next record checks only what is new
    assert "1 new events checked after #5" in led.repair_record("ses-1")
    _bypass(led)
    led.db.execute("UPDATE events SET kind='complete' WHERE seq=1")
    led.db.commit()
    assert "Ledger chain: BROKEN at event #1" in Ledger(str(tmp_path / "j.db")).repair_record("ses-1")


def test_link_hashes_the_stored_text_exactly():
    a = link(GENESIS, 1, "t", "s", "k", 1, '{"a":1}')
    assert a != link(GENESIS, 1, "t", "s", "k", 1, '{"a": 1}')          # whitespace in the stored JSON counts
    assert a != link(GENESIS, 1, "t", "s", "k", 0, '{"a":1}')
    assert len(a) == 64


def test_verify_tool_exit_codes(tmp_path, capsys):
    from tools.verify_ledger import main
    assert main([str(tmp_path / "missing.db")]) == 2
    led = _led(tmp_path)
    assert main([str(tmp_path / "j.db")]) == 0 and "INTACT" in capsys.readouterr().out
    _bypass(led)
    led.db.execute("UPDATE events SET accepted=0 WHERE seq=4")
    led.db.commit()
    assert main([str(tmp_path / "j.db")]) == 1 and "BROKEN at event #4" in capsys.readouterr().out


def test_a_failed_append_leaves_neither_half(tmp_path, monkeypatch):
    import james_core.ledger as L
    led = _led(tmp_path, 2)
    monkeypatch.setattr(L, "link", lambda *a: (_ for _ in ()).throw(RuntimeError("disk")))
    with pytest.raises(RuntimeError):
        led.append("ses-1", "complete", {})
    assert led.db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 2
    monkeypatch.undo()
    assert led.verify()["ok"]


# review round: attacks the first version missed

def test_a_forged_event_is_never_sealed_by_reopening(tmp_path):
    led = _led(tmp_path)
    led.db.execute("INSERT INTO events(ts, session_id, kind, accepted, payload) VALUES ('t','s','confirmed',1,'{}')")
    led.db.commit()
    again = Ledger(str(tmp_path / "j.db"))                     # the app restarts
    assert again.sealed_on_open == 0
    v = again.verify()
    assert not v["ok"] and "#6 has no link" in v["problems"][0]


def test_the_checker_is_read_only_and_seals_nothing(tmp_path, capsys):
    from tools.verify_ledger import main
    led = _led(tmp_path)
    led.db.execute("INSERT INTO events(ts, session_id, kind, accepted, payload) VALUES ('t','s','confirmed',1,'{}')")
    led.db.commit()
    assert main([str(tmp_path / "j.db")]) == 1 and "#6 has no link" in capsys.readouterr().out
    n = sqlite3.connect(tmp_path / "j.db").execute("SELECT COUNT(*) FROM chain").fetchone()[0]
    assert n == 5                                             # still unlinked: the checker wrote nothing


def test_dropping_links_and_editing_the_tail_is_caught(tmp_path):
    led = _led(tmp_path)
    _bypass(led)
    led.db.execute("DELETE FROM chain WHERE seq >= 4")
    led.db.execute("UPDATE events SET payload='{}' WHERE seq >= 4")
    led.db.commit()
    v = Ledger(str(tmp_path / "j.db")).verify()
    assert not v["ok"] and v["first_bad"] == 4


def test_removing_the_newest_events_is_caught_by_a_saved_head(tmp_path, capsys):
    from tools.verify_ledger import main
    led = _led(tmp_path)
    seq, h = led.verify()["head"]
    _bypass(led)
    led.db.execute("DELETE FROM events WHERE seq >= 4")
    led.db.execute("DELETE FROM chain WHERE seq >= 4")
    led.db.commit()
    assert led.verify()["ok"]                                 # the database alone cannot see a cut tail
    assert main([str(tmp_path / "j.db"), "--expect", f"{seq}:{h}"]) == 1
    assert "DOES NOT MATCH" in capsys.readouterr().out
    assert main([str(tmp_path / "j.db"), "--expect", "nonsense"]) == 1


def test_relabelling_how_a_link_was_made_is_caught(tmp_path):
    led = _led(tmp_path)
    _bypass(led)
    led.db.execute("UPDATE chain SET sealed='upgrade' WHERE seq=2")
    assert not led.verify()["ok"]


def test_an_older_database_without_a_chain_is_reported_not_broken(tmp_path, capsys):
    from tools.verify_ledger import main
    p = tmp_path / "old.db"
    db = sqlite3.connect(p)
    db.executescript("CREATE TABLE events(seq INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, session_id TEXT "
                     "NOT NULL, kind TEXT NOT NULL, accepted INTEGER NOT NULL, payload TEXT NOT NULL);")
    db.execute("INSERT INTO events(ts, session_id, kind, accepted, payload) VALUES ('t','s','complete',1,'{}')")
    db.commit(); db.close()
    assert main([str(p)]) == 3 and "NO CHAIN YET" in capsys.readouterr().out
    assert sqlite3.connect(p).execute("SELECT name FROM sqlite_master WHERE name='chain'").fetchone() is None


def test_legacy_seal_is_recorded_and_bounded(tmp_path):
    p = tmp_path / "old.db"
    db = sqlite3.connect(p)
    db.executescript("CREATE TABLE events(seq INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, session_id TEXT "
                     "NOT NULL, kind TEXT NOT NULL, accepted INTEGER NOT NULL, payload TEXT NOT NULL);")
    for i in range(2):
        db.execute("INSERT INTO events(ts, session_id, kind, accepted, payload) VALUES ('t','s','complete',1,'{}')")
    db.commit(); db.close()
    led = Ledger(str(p))
    assert led.sealed_on_open == 2 and led.verify()["legacy_sealed_upto"] == 2
    led.append("s", "complete", {})
    _bypass(led)
    led.db.execute("DELETE FROM chain WHERE seq=3")             # try to pass a new event off as legacy
    prev = led.db.execute("SELECT hash FROM chain WHERE seq=2").fetchone()[0]
    row = led.db.execute("SELECT ts, session_id, kind, accepted, payload FROM events WHERE seq=3").fetchone()
    led.db.execute("INSERT INTO chain VALUES (3, ?, ?, 'upgrade')", (prev, link(prev, 3, *row, "upgrade")))
    v = led.verify()
    assert not v["ok"] and "newer than the upgrade" in v["problems"][0]


# second review round

def test_wiping_chain_meta_does_not_let_the_app_seal_a_forged_tail(tmp_path):
    led = _led(tmp_path)
    _bypass(led)
    for t in ("chain_meta_no_update", "chain_meta_no_delete"):
        led.db.execute(f"DROP TRIGGER {t}")
    led.db.execute("DELETE FROM chain WHERE seq >= 4")
    led.db.execute("DELETE FROM chain_meta")
    led.db.execute("UPDATE events SET payload='{}' WHERE seq >= 4")
    led.db.commit()
    again = Ledger(str(tmp_path / "j.db"))
    assert again.sealed_on_open == 0                           # a chain this version wrote is never legacy
    v = again.verify()
    assert not v["ok"] and "#4 has no link" in v["problems"][0]


def test_an_upgrade_link_after_a_written_one_is_caught(tmp_path):
    led = _led(tmp_path)
    _bypass(led)
    led.db.execute("DELETE FROM chain WHERE seq >= 4")
    prev = led.db.execute("SELECT hash FROM chain WHERE seq=3").fetchone()[0]
    for seq in (4, 5):
        row = led.db.execute("SELECT ts, session_id, kind, accepted, payload FROM events WHERE seq=?", (seq,)).fetchone()
        h = link(prev, seq, *row, "upgrade")
        led.db.execute("INSERT INTO chain VALUES (?, ?, ?, 'upgrade')", (seq, prev, h))
        prev = h
    v = led.verify()
    assert not v["ok"]                                         # newer than the upgrade, and after written links


def test_checker_handles_files_that_are_not_ledgers(tmp_path, capsys):
    from tools.verify_ledger import main
    (tmp_path / "empty.db").write_bytes(b"")
    assert main([str(tmp_path / "empty.db")]) == 2
    other = tmp_path / "other.db"
    sqlite3.connect(other).executescript("CREATE TABLE t(x);")
    assert main([str(other)]) == 2
    (tmp_path / "junk.db").write_bytes(b"not sqlite at all, just text" * 100)
    assert main([str(tmp_path / "junk.db")]) == 2
    assert "Traceback" not in capsys.readouterr().out


def test_checker_handles_odd_folder_names(tmp_path):
    from tools.verify_ledger import main
    for name in ("a#b", "50%25", "with space"):
        d = tmp_path / name
        d.mkdir()
        led = Ledger(str(d / "j.db"))
        led.append("s", "complete", {})
        assert main([str(d / "j.db")]) == 0, name


def test_an_event_edited_after_a_repair_record_checked_it_is_caught_next_time(tmp_path):
    led = _led(tmp_path)
    led.repair_record("ses-1")                                  # checks #1..#5
    _bypass(led)
    led.db.execute("UPDATE events SET payload=? WHERE seq=5", ('{"sources":[],"step_text":"isolate later"}',))
    led.db.commit()
    led.append("ses-1", "complete", {})
    assert "BROKEN at event #5" in led.repair_record("ses-1")


def test_a_locked_database_never_stops_the_app_from_starting(tmp_path):
    p = tmp_path / "old.db"
    db = sqlite3.connect(p)
    db.executescript("CREATE TABLE events(seq INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, session_id TEXT "
                     "NOT NULL, kind TEXT NOT NULL, accepted INTEGER NOT NULL, payload TEXT NOT NULL);")
    db.execute("INSERT INTO events(ts, session_id, kind, accepted, payload) VALUES ('t','s','complete',1,'{}')")
    db.commit()
    fresh = p                                                   # an older ledger, first open by this version
    holder = sqlite3.connect(fresh, timeout=0)
    holder.execute("BEGIN IMMEDIATE")                           # another JAMES is writing
    led = Ledger.__new__(Ledger)
    import threading
    led._lock, led.readonly, led._verified = threading.RLock(), False, None
    led.db = sqlite3.connect(fresh, timeout=0.2, check_same_thread=False)
    assert led._seal_legacy() in (0,)                           # skipped, not raised
    holder.rollback()
