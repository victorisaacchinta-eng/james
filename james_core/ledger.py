"""LEDGER storage: an append-only event log in SQLite, the repair record,
and recall of senior-approved past jobs.

UPDATE and DELETE on events are refused by triggers, so the log can only grow.

Hash chain (iteration 10): every event also gets a row in `chain` holding SHA-256(previous hash + the event),
written in the same transaction. Triggers stop casual edits; the chain makes any edit that slips past them (for
example after someone drops the triggers) visible to `verify()` and `python -m tools.verify_ledger`. It is
tamper-EVIDENT, not tamper-proof: someone who rewrites every row and recomputes every hash, or removes the newest
events with their links, can hide it from the database alone. So every repair record prints the head (event number
and full hash); `python -m tools.verify_ledger --expect SEQ:HASH` checks a saved head against the database.
Events written before the chain existed are sealed ONCE, the first time this version opens the database; the
event number it sealed up to is recorded in `chain_meta` and never moves, so anything written around the ledger
later is reported as unlinked, never sealed."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS events(
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  session_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  accepted INTEGER NOT NULL,
  payload TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
CREATE TABLE IF NOT EXISTS job_reviews(
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  job_id TEXT NOT NULL,
  decision TEXT NOT NULL CHECK (decision IN ('approve', 'revoke')),
  by TEXT NOT NULL,
  reason TEXT NOT NULL DEFAULT ''
);
CREATE TRIGGER IF NOT EXISTS reviews_no_update BEFORE UPDATE ON job_reviews
BEGIN SELECT RAISE(ABORT, 'reviews are append-only'); END;
CREATE TRIGGER IF NOT EXISTS reviews_no_delete BEFORE DELETE ON job_reviews
BEGIN SELECT RAISE(ABORT, 'reviews are append-only'); END;
CREATE TABLE IF NOT EXISTS chain(
  seq INTEGER PRIMARY KEY,
  prev TEXT NOT NULL,
  hash TEXT NOT NULL,
  sealed TEXT NOT NULL CHECK (sealed IN ('write', 'upgrade'))
);
CREATE TRIGGER IF NOT EXISTS chain_no_update BEFORE UPDATE ON chain
BEGIN SELECT RAISE(ABORT, 'chain is append-only'); END;
CREATE TRIGGER IF NOT EXISTS chain_no_delete BEFORE DELETE ON chain
BEGIN SELECT RAISE(ABORT, 'chain is append-only'); END;
CREATE TABLE IF NOT EXISTS chain_meta(
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS chain_meta_no_update BEFORE UPDATE ON chain_meta
BEGIN SELECT RAISE(ABORT, 'chain_meta is append-only'); END;
CREATE TRIGGER IF NOT EXISTS chain_meta_no_delete BEFORE DELETE ON chain_meta
BEGIN SELECT RAISE(ABORT, 'chain_meta is append-only'); END;
CREATE TABLE IF NOT EXISTS jobs(
  job_id TEXT PRIMARY KEY,
  asset_id TEXT NOT NULL,
  symptoms TEXT NOT NULL,
  fix TEXT NOT NULL,
  approved_by TEXT,
  sample_data INTEGER NOT NULL DEFAULT 1,
  cause TEXT,
  created TEXT
);
"""


GENESIS = "0" * 64


def link(prev: str, seq: int, ts: str, session_id: str, kind: str, accepted: int, payload: str,
         sealed: str = "write") -> str:
    """One chain link. The payload is hashed exactly as stored (its JSON text), so nothing is re-serialised. How the
    link was made (at write, or sealed at upgrade) is hashed too, so it cannot be relabelled."""
    body = json.dumps([seq, ts, session_id, kind, int(accepted), payload, sealed], separators=(",", ":"),
                      ensure_ascii=False)
    return hashlib.sha256((prev + "\n" + body).encode("utf-8")).hexdigest()


def _default(o: Any):
    if isinstance(o, datetime):
        return o.isoformat()
    if hasattr(o, "model_dump"):
        return o.model_dump(mode="json")
    if hasattr(o, "value"):
        return o.value
    return str(o)


def _locked(fn):
    def wrapper(self, *a, **kw):
        with self._lock:
            return fn(self, *a, **kw)
    wrapper.__name__, wrapper.__doc__ = fn.__name__, fn.__doc__
    return wrapper


class Ledger:
    def __init__(self, path: str = ":memory:", readonly: bool = False):
        """readonly: open an existing database without creating tables or sealing anything (the checker uses it)."""
        # Shared across the HUD loop and agent threads; every access goes through one lock.
        self._lock = threading.RLock()
        self.readonly = readonly
        self._verified: tuple[int, str] | None = None      # (seq, hash) this process has already checked up to
        self.sealed_on_open = 0
        if readonly:
            from pathlib import Path
            self.db = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, check_same_thread=False)
            return
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.executescript(SCHEMA)
        self.sealed_on_open = self._seal_legacy()

    # ---- event log ----
    @_locked
    def append(self, session_id: str, kind: str, payload: dict, accepted: bool = True) -> int:
        ts, text = datetime.now(timezone.utc).isoformat(), json.dumps(payload, default=_default, sort_keys=True)
        try:
            cur = self.db.execute("INSERT INTO events(ts, session_id, kind, accepted, payload) VALUES (?,?,?,?,?)",
                                  (ts, session_id, kind, int(accepted), text))
            seq, prev = cur.lastrowid, self._head()[1]
            self.db.execute("INSERT INTO chain(seq, prev, hash, sealed) VALUES (?,?,?,'write')",
                            (seq, prev, link(prev, seq, ts, session_id, kind, int(accepted), text)))
            self.db.commit()
        except Exception:
            self.db.rollback()                         # never an event without its link, or a link without its event
            raise
        return seq

    def _head(self) -> tuple[int, str]:
        r = self.db.execute("SELECT seq, hash FROM chain ORDER BY seq DESC LIMIT 1").fetchone()
        return (r[0], r[1]) if r else (0, GENESIS)

    def _meta(self, key: str):
        try:
            r = self.db.execute("SELECT value FROM chain_meta WHERE key=?", (key,)).fetchone()
        except sqlite3.OperationalError:              # read-only open of a database that never had a chain
            return None
        return r[0] if r else None

    @_locked
    def _seal_legacy(self) -> int:
        """Once per database, ever: chain the events an older JAMES wrote before the chain existed, and record how
        far that went. Every later open does nothing here, so an event written around the ledger stays unlinked
        (and verify() reports it) instead of being sealed by the app."""
        if self._meta("legacy_sealed_upto") is not None:
            return 0
        self.db.commit()
        try:
            self.db.execute("BEGIN IMMEDIATE")         # no other process can append between the read and the seal
        except sqlite3.OperationalError:               # another JAMES holds the write lock: seal on a later open
            return 0
        try:
            if self._meta("legacy_sealed_upto") is not None:
                self.db.rollback()
                return 0
            last, prev = self._head()
            n, upto = 0, last
            wrote = self.db.execute("SELECT 1 FROM chain WHERE sealed='write' LIMIT 1").fetchone()
            for seq, ts, sid, kind, acc, text in ([] if wrote else self.db.execute(
                    "SELECT seq, ts, session_id, kind, accepted, payload FROM events WHERE seq > ? ORDER BY seq",
                    (last,)).fetchall()):                   # a chain this version wrote is never "legacy": seal nothing
                h = link(prev, seq, ts, sid, kind, acc, text, "upgrade")
                self.db.execute("INSERT INTO chain(seq, prev, hash, sealed) VALUES (?,?,?,'upgrade')", (seq, prev, h))
                prev, upto, n = h, seq, n + 1
            self.db.execute("INSERT INTO chain_meta(key, value) VALUES ('legacy_sealed_upto', ?)", (str(upto),))
            self.db.execute("INSERT INTO chain_meta(key, value) VALUES ('legacy_sealed_at', ?)",
                            (datetime.now(timezone.utc).isoformat(),))
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return n

    @_locked
    def verify(self, since: tuple[int, str] | None = None) -> dict:
        """Recompute the chain. Full walk by default; since=(seq, hash) continues from a point already checked (the
        repair record uses that, so it costs only the new events). ok is False on an edited, removed, reordered or
        unlinked event; problems lists what was found (at most 20 lines). head is (seq, hash) of the last link."""
        if not self._has_table("events"):
            return dict(ok=False, events=0, head=(0, GENESIS), first_bad=None, sealed_at_upgrade=None, unchained=True,
                        not_a_ledger=True, problems=["this file is not a JAMES ledger (no events table)"])
        upto_s = self._meta("legacy_sealed_upto")
        if upto_s is None and not self._has_chain():
            return dict(ok=False, events=self.db.execute("SELECT COUNT(*) FROM events").fetchone()[0], head=(0, GENESIS),
                        first_bad=None, sealed_at_upgrade=None, unchained=True,
                        problems=["this database has no chain yet: open it once with this version of JAMES"])
        upto = int(upto_s or 0)
        start, expected = since or (0, GENESIS)
        problems: list[str] = []
        first_bad = None
        if since is None:
            for (s_,) in self.db.execute("SELECT c.seq FROM chain c LEFT JOIN events e ON e.seq = c.seq "
                                         "WHERE e.seq IS NULL ORDER BY c.seq LIMIT 10"):
                problems.append(f"event #{s_} is gone but its link remains (an event was removed)")
                first_bad = first_bad if first_bad is not None else s_
        if since is not None and start:                # the point we continue from must still be what we checked
            r = self.db.execute("SELECT e.ts, e.session_id, e.kind, e.accepted, e.payload, c.prev, c.hash, c.sealed "
                                "FROM events e JOIN chain c ON c.seq = e.seq WHERE e.seq=?", (start,)).fetchone()
            if r is None or r[6] != expected or link(r[5], start, *r[:5], r[7]) != expected:
                problems.append(f"event #{start} changed or was removed after it was checked earlier in this run")
                first_bad = start
        n, head, upgrade, seen_write = 0, (start, expected), [], False
        cur = self.db.execute("SELECT e.seq, e.ts, e.session_id, e.kind, e.accepted, e.payload, c.prev, c.hash, "
                              "c.sealed FROM events e LEFT JOIN chain c ON c.seq = e.seq WHERE e.seq > ? "
                              "ORDER BY e.seq", (start,))
        for seq, ts, sid, kind, acc, text, prev, h, sealed in cur:
            n += 1
            before = len(problems)
            if h is None:
                problems.append(f"event #{seq} has no link (written around the ledger)")
            else:
                if prev != expected:
                    problems.append(f"link before event #{seq} does not follow the previous one (removed or reordered)")
                if link(prev, seq, ts, sid, kind, acc, text, sealed) != h:
                    problems.append(f"event #{seq} ({kind}) was changed after it was written")
                if sealed == "upgrade":
                    if seq > upto:
                        problems.append(f"event #{seq} claims to be sealed at upgrade but is newer than the upgrade")
                    elif seen_write:
                        problems.append(f"event #{seq} is sealed at upgrade after an event written with its link")
                    upgrade.append(seq)
                else:
                    seen_write = True
                expected, head = h, (seq, h)
            if len(problems) > before and first_bad is None:
                first_bad = seq
        ok = not problems
        if ok:
            self._verified = head
        return dict(ok=ok, events=n if since else self.db.execute("SELECT COUNT(*) FROM events").fetchone()[0],
                    head=head, first_bad=first_bad, problems=problems[:20], unchained=False,
                    sealed_at_upgrade=(upgrade[0], upgrade[-1], len(upgrade)) if upgrade else None,
                    legacy_sealed_upto=upto, legacy_sealed_at=self._meta("legacy_sealed_at"))

    def _has_table(self, name: str) -> bool:
        return self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None

    def _has_chain(self) -> bool:
        return self._has_table("chain")

    @_locked
    def check_head(self, seq: int, h: str) -> bool:
        """A head saved earlier (from a repair record) is still in the chain, unchanged."""
        r = self.db.execute("SELECT c.hash FROM chain c JOIN events e ON e.seq = c.seq WHERE c.seq=?", (seq,)).fetchone()
        return bool(r) and r[0] == h

    @_locked
    def events(self, session_id: str) -> list[dict]:
        rows = self.db.execute(
            "SELECT seq, ts, kind, accepted, payload FROM events WHERE session_id=? ORDER BY seq",
            (session_id,)).fetchall()
        return [dict(seq=s, ts=t, kind=k, accepted=bool(a), payload=json.loads(p)) for s, t, k, a, p in rows]

    # ---- memory of past jobs ----
    @_locked
    def add_job(self, job_id: str, asset_id: str, symptoms: Iterable[str], fix: str,
                approved_by: str | None = None, sample_data: bool = True, cause: str | None = None,
                created: str | None = None) -> None:
        self.db.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?)",
                        (job_id, asset_id, json.dumps(sorted(symptoms)), fix, approved_by, int(sample_data),
                         cause, created or datetime.now(timezone.utc).isoformat()))
        self.db.commit()

    @_locked
    def job_count(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]

    @_locked
    def latest_pending(self) -> dict | None:
        r = self.db.execute("SELECT job_id, fix, cause FROM jobs WHERE approved_by IS NULL AND job_id LIKE 'J-%' "
                            "ORDER BY created DESC LIMIT 1").fetchone()
        return dict(job_id=r[0], fix=r[1], cause=r[2]) if r else None

    @_locked
    def approve_job(self, job_id: str, by: str) -> None:
        """Approval is the only edit allowed on a job, and it is itself logged as an event and a review row."""
        self.db.execute("UPDATE jobs SET approved_by=? WHERE job_id=? AND approved_by IS NULL", (by, job_id))
        self._review(job_id, "approve", by, "")
        self.db.commit()
        self.append("ledger", "job_approved", {"job_id": job_id, "by": by})

    @_locked
    def revoke_approval(self, job_id: str, by: str, reason: str) -> None:
        """A senior withdraws an approval (iteration 2, ST-19). Nothing is edited or deleted: a 'revoke' review row
        and an event are added, and recall stops offering the job from then on."""
        if not reason.strip():
            raise ValueError("a revocation needs a reason")
        self._review(job_id, "revoke", by, reason)
        self.db.commit()
        self.append("ledger", "job_revoked", {"job_id": job_id, "by": by, "reason": reason})

    def _review(self, job_id: str, decision: str, by: str, reason: str) -> None:
        self.db.execute("INSERT INTO job_reviews(ts, job_id, decision, by, reason) VALUES (?,?,?,?,?)",
                        (datetime.now(timezone.utc).isoformat(), job_id, decision, by, reason))

    @_locked
    def review_state(self, job_id: str) -> str:
        """'approve', 'revoke' or '' from the latest review row (seeded approvals have no row)."""
        r = self.db.execute("SELECT decision FROM job_reviews WHERE job_id=? ORDER BY seq DESC LIMIT 1",
                            (job_id,)).fetchone()
        return r[0] if r else ""

    @_locked
    def recall_approved(self, asset_id: str, symptoms: Iterable[str]) -> list[dict]:
        """Only senior-approved jobs are ever reused. Ranked by symptom overlap.
        (The real build ranks by meaning with Qdrant; the approval filter stays.)"""
        want = set(symptoms)
        rows = self.db.execute(
            "SELECT job_id, asset_id, symptoms, fix, approved_by, sample_data, cause, created FROM jobs "
            "WHERE approved_by IS NOT NULL").fetchall()
        revoked = {r[0] for r in self.db.execute(
            "SELECT r.job_id FROM job_reviews r WHERE r.seq = (SELECT MAX(seq) FROM job_reviews x "
            "WHERE x.job_id = r.job_id) AND r.decision = 'revoke'").fetchall()}
        scored = []
        for jid, aid, sym, fix, by, sample, cause, created in rows:
            if jid in revoked:
                continue
            s = set(json.loads(sym))
            score = len(want & s) + (1 if aid == asset_id else 0)
            if score:
                scored.append((score, created or "", dict(job_id=jid, asset_id=aid, symptoms=sorted(s), fix=fix,
                                           approved_by=by, sample_data=bool(sample), cause=cause,
                                           created=created or "")))
        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)   # best match, then most recent
        return [j for _, _, j in scored]

    # ---- repair record ----
    @_locked
    def repair_record(self, session_id: str) -> str:
        ev = self.events(session_id)
        lines = [f"# Repair record: session {session_id}", "",
                 "Guidance and logging only. This record does not verify the physical repair.", ""]
        for e in ev:
            if not e["accepted"]:
                lines.append(f"- {e['ts']}  REFUSED {e['kind']}: {e['payload'].get('reason', '')}")
                continue
            p = e["payload"]
            if e["kind"] in ("safety_decision", "safety_recheck"):
                label = "WARDEN re-check at pinch" if e["kind"] == "safety_recheck" else "WARDEN"
                rules = f" {', '.join(p['rule_ids'])}" if p["rule_ids"] else ""
                lines.append(f"- {e['ts']}  {label}: {p['decision']}{rules} "
                             f"({p['policy_suite']} v{p['policy_version']})")
            elif e["kind"] == "confirmed":
                lines.append(f"- {e['ts']}  CONFIRMED by pinch: {p['step_text']} "
                             f"[sources: {', '.join(p['sources'])}]")
            elif e["kind"] == "skipped":
                lines.append(f"- {e['ts']}  SKIPPED (not confirmed): {p['step_text']}")
            elif e["kind"] == "facts_updated":
                lines.append(f"- {e['ts']}  FACTS: {', '.join(f'{k}={v}' for k, v in p['facts'].items())}")
            elif e["kind"] == "investigation":
                lines.append(f"- {e['ts']}  FOREMAN investigation: {p['status']}"
                             + (f", cause {p['cause'].replace('_', ' ')}" if p.get("cause") not in (None, "unknown") else "")
                             + f" ({p['backend']})" + (f". {p['why']}" if p.get("why") else ""))
                if p.get("stop_reason"):
                    d = p.get("decisions") or {}
                    lines.append(f"    stopped: {p['stop_reason']} · model decisions accepted {d.get('model_accepted', 0)}"
                                 f" of {d.get('model_attempts', 0)} asked · rules {d.get('rules', 0)}"
                                 f" · rule fallbacks {d.get('rules_fallback', 0)}")
                lines += [f"    {r}" for r in p.get("rounds", []) if not r.startswith("R0 ")]
                lines += [f"    left out: {x}" for x in p.get("excluded", [])]
                if p.get("pack_snapshot"):
                    lines.append(f"    pack snapshot: {p['pack_snapshot']}")
            elif e["kind"] == "cancelled_by_restart":
                lines.append(f"- {e['ts']}  CANCELLED BY RESTART while {p['was']}"
                             + (f" (step not confirmed: {p['pending_step']})" if p.get("pending_step") else ""))
            elif e["kind"] == "pack_changed_on_disk":
                lines.append(f"- {e['ts']}  PACK FILES CHANGED ON DISK: {', '.join(p['files'])}; "
                             f"this job used verified snapshot {p['using_snapshot']}")
            elif e["kind"] == "observation":
                lines.append(f"- {e['ts']}  TECHNICIAN answered: {p['question']} -> {'yes' if p['answer'] else 'no'}")
            elif e["kind"] in ("escalated", "complete", "target_confirmed", "intent_captured"):
                lines.append(f"- {e['ts']}  {e['kind'].upper()}")
        since = self._verified                         # full walk on the first record of a run, then only new events
        v = self.verify(since=since)
        if v.get("unchained"):
            lines += ["", "Ledger chain: none yet (older database)"]
        elif not v["ok"]:
            lines += ["", f"Ledger chain: BROKEN at event #{v['first_bad']}: {v['problems'][0]}"]
        else:
            scope = (f"all {v['events']} events checked" if since is None else
                     f"{v['events']} new events checked after #{since[0]} (checked earlier in this run)")
            lines += ["", f"Ledger chain: intact, {scope}. Head #{v['head'][0]} {v['head'][1]}",
                      f"Check this head later: python -m tools.verify_ledger --expect {v['head'][0]}:{v['head'][1]}"]
        return "\n".join(lines) + "\n"
