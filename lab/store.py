"""LEDGER storage for the Lab: SQLite, one file, survives restarts.

Events, proposals, prerequisite records and reviews are append-only (UPDATE and DELETE are
refused by triggers). The `jobs` row holds derived state for fast listing; the events are the
record. This is an append-only application interface, not a tamper-proof database, and no
compliance claim is made."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .pulse import CHANNELS, Dataset, Sample, fmt_ts, parse_ts
from .schemas import new_id

APPEND_ONLY = ("job_events", "proposals", "prerequisite_records", "reviews", "sensor_samples", "datasets")

SCHEMA = """
CREATE TABLE IF NOT EXISTS datasets(
  dataset_id TEXT PRIMARY KEY, name TEXT NOT NULL, sha256 TEXT NOT NULL UNIQUE,
  imported_at TEXT NOT NULL, rows INTEGER NOT NULL, validation TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sensor_samples(
  dataset_id TEXT NOT NULL, row_no INTEGER NOT NULL, ts TEXT NOT NULL, asset_id TEXT NOT NULL,
  vibration_mm_s REAL, bearing_temp_c REAL, motor_current_a REAL, flow_l_min REAL, quality TEXT NOT NULL,
  PRIMARY KEY(dataset_id, row_no));
CREATE TABLE IF NOT EXISTS jobs(
  job_id TEXT PRIMARY KEY, scenario TEXT, asset_id TEXT NOT NULL, category TEXT, question TEXT NOT NULL,
  state TEXT NOT NULL, outcome TEXT, review_status TEXT NOT NULL, dataset_id TEXT, scenario_clock TEXT,
  created_at TEXT NOT NULL, closed_at TEXT, seeded INTEGER NOT NULL DEFAULT 0, current_proposal TEXT);
CREATE TABLE IF NOT EXISTS proposals(
  proposal_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, version INTEGER NOT NULL, mode TEXT NOT NULL,
  body TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(job_id, version));
CREATE TABLE IF NOT EXISTS job_events(
  seq INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE, job_id TEXT NOT NULL,
  type TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL,
  idempotency_key TEXT, corrects TEXT, UNIQUE(job_id, idempotency_key));
CREATE TABLE IF NOT EXISTS idempotency(
  job_id TEXT NOT NULL, key TEXT NOT NULL, response TEXT NOT NULL, PRIMARY KEY(job_id, key));
CREATE TABLE IF NOT EXISTS prerequisite_records(
  record_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, asset_id TEXT NOT NULL, proposal_id TEXT NOT NULL,
  fact TEXT NOT NULL, value INTEGER NOT NULL, step_id TEXT, reporter TEXT NOT NULL,
  source_type TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS reviews(
  review_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, decision TEXT NOT NULL, reason TEXT NOT NULL,
  reviewer TEXT NOT NULL, created_at TEXT NOT NULL, revokes TEXT);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class Store:
    def __init__(self, path: str | Path):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.db.executescript(SCHEMA)
            for t in APPEND_ONLY:
                self.db.executescript(f"""
                CREATE TRIGGER IF NOT EXISTS {t}_no_update BEFORE UPDATE ON {t}
                BEGIN SELECT RAISE(ABORT, '{t} is append-only'); END;
                CREATE TRIGGER IF NOT EXISTS {t}_no_delete BEFORE DELETE ON {t}
                BEGIN SELECT RAISE(ABORT, '{t} is append-only'); END;""")

    # ---------- datasets ----------
    def dataset_by_hash(self, sha: str) -> Optional[Dataset]:
        with self.lock:
            d = self.db.execute("SELECT * FROM datasets WHERE sha256=?", (sha,)).fetchone()
            if not d:
                return None
            ds = Dataset(name=d["name"], sha256=sha)
            for r in self.db.execute("SELECT * FROM sensor_samples WHERE dataset_id=? ORDER BY row_no", (d["dataset_id"],)):
                ds.samples.append(Sample(r["row_no"], parse_ts(r["ts"]), r["asset_id"],
                                         {c: r[c] for c in CHANNELS}, r["quality"]))
            return ds

    def dataset_meta(self, dataset_id: str) -> Optional[dict]:
        with self.lock:
            r = self.db.execute("SELECT * FROM datasets WHERE dataset_id=?", (dataset_id,)).fetchone()
            return dict(r) if r else None

    def save_dataset(self, ds: Dataset) -> str:
        """All rows in one transaction: a failure stores nothing."""
        with self.lock:
            imported = now()
            self.db.execute("BEGIN")
            try:
                self.db.execute("INSERT INTO datasets VALUES(?,?,?,?,?,?)",
                                (ds.dataset_id, ds.name, ds.sha256, imported, len(ds.samples), "valid"))
                self.db.executemany("INSERT INTO sensor_samples VALUES(?,?,?,?,?,?,?,?,?)", [
                    (ds.dataset_id, s.row, fmt_ts(s.ts), s.asset_id, *(s.values.get(c) for c in CHANNELS), s.quality)
                    for s in ds.samples])
                self.db.execute("COMMIT")
            except Exception:
                self.db.execute("ROLLBACK")
                raise
            return imported

    # ---------- jobs ----------
    def create_job(self, **kw) -> dict:
        with self.lock:
            job = dict(job_id=kw.pop("job_id", None) or new_id("JOB").upper(),
                       state=kw.pop("state", "DRAFT"), review_status=kw.pop("review_status", "NOT_SUBMITTED"),
                       created_at=kw.pop("created_at", None) or now(), **kw)
            cols = ",".join(job)
            self.db.execute(f"INSERT INTO jobs({cols}) VALUES({','.join('?' * len(job))})", tuple(job.values()))
            return self.job(job["job_id"])

    def job(self, job_id: str) -> Optional[dict]:
        with self.lock:
            r = self.db.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            return dict(r) if r else None

    def jobs(self) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.db.execute("SELECT * FROM jobs ORDER BY created_at DESC")]

    def update_job(self, job_id: str, **fields):
        with self.lock:
            sets = ",".join(f"{k}=?" for k in fields)
            self.db.execute(f"UPDATE jobs SET {sets} WHERE job_id=?", (*fields.values(), job_id))

    # ---------- events ----------
    def append(self, job_id: str, type_: str, payload: dict, key: Optional[str] = None,
               corrects: Optional[str] = None) -> dict:
        with self.lock:
            eid = new_id("evt")
            self.db.execute("INSERT INTO job_events(event_id,job_id,type,payload,created_at,idempotency_key,corrects) "
                            "VALUES(?,?,?,?,?,?,?)", (eid, job_id, type_, json.dumps(payload, default=str), now(), key, corrects))
            return self.event(eid)

    def event(self, event_id: str) -> Optional[dict]:
        with self.lock:
            r = self.db.execute("SELECT * FROM job_events WHERE event_id=?", (event_id,)).fetchone()
            return self._ev(r) if r else None

    @staticmethod
    def _ev(r) -> dict:
        d = dict(r)
        d["payload"] = json.loads(d["payload"])
        return d

    def events(self, job_id: str) -> list[dict]:
        with self.lock:
            return [self._ev(r) for r in self.db.execute("SELECT * FROM job_events WHERE job_id=? ORDER BY seq", (job_id,))]

    # ---------- idempotency ----------
    def remembered(self, job_id: str, key: Optional[str]) -> Optional[dict]:
        if not key:
            return None
        with self.lock:
            r = self.db.execute("SELECT response FROM idempotency WHERE job_id=? AND key=?", (job_id, key)).fetchone()
            return json.loads(r["response"]) if r else None

    def remember(self, job_id: str, key: Optional[str], response: dict):
        if key:
            with self.lock:
                self.db.execute("INSERT OR IGNORE INTO idempotency VALUES(?,?,?)", (job_id, key, json.dumps(response, default=str)))

    # ---------- proposals ----------
    def add_proposal(self, p) -> None:
        with self.lock:
            self.db.execute("INSERT INTO proposals VALUES(?,?,?,?,?,?)",
                            (p.proposal_id, p.job_id, p.version, p.mode, p.model_dump_json(), p.created_at))

    def proposals(self, job_id: str) -> list[dict]:
        with self.lock:
            return [json.loads(r["body"]) for r in
                    self.db.execute("SELECT body FROM proposals WHERE job_id=? ORDER BY version", (job_id,))]

    def proposal(self, proposal_id: str) -> Optional[dict]:
        with self.lock:
            r = self.db.execute("SELECT body FROM proposals WHERE proposal_id=?", (proposal_id,)).fetchone()
            return json.loads(r["body"]) if r else None

    # ---------- prerequisites ----------
    def add_prereq(self, job_id, asset_id, proposal_id, fact, value, step_id, reporter) -> dict:
        with self.lock:
            rid = new_id("pre")
            self.db.execute("INSERT INTO prerequisite_records VALUES(?,?,?,?,?,?,?,?,?,?)",
                            (rid, job_id, asset_id, proposal_id, fact, int(bool(value)), step_id, reporter,
                             "operator_attestation", now()))
            return dict(self.db.execute("SELECT * FROM prerequisite_records WHERE record_id=?", (rid,)).fetchone())

    def prereqs(self, job_id: str, asset_id: Optional[str] = None, proposal_id: Optional[str] = None) -> list[dict]:
        q, args = "SELECT * FROM prerequisite_records WHERE job_id=?", [job_id]
        if asset_id is not None:
            q, args = q + " AND asset_id=?", args + [asset_id]
        if proposal_id is not None:
            q, args = q + " AND proposal_id=?", args + [proposal_id]
        with self.lock:
            return [dict(r) for r in self.db.execute(q + " ORDER BY created_at", args)]

    # ---------- reviews ----------
    def add_review(self, job_id, decision, reason, reviewer, revokes=None) -> dict:
        with self.lock:
            rid = new_id("rev")
            self.db.execute("INSERT INTO reviews VALUES(?,?,?,?,?,?,?)", (rid, job_id, decision, reason, reviewer, now(), revokes))
            return dict(self.db.execute("SELECT * FROM reviews WHERE review_id=?", (rid,)).fetchone())

    def reviews(self, job_id: str) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.db.execute("SELECT * FROM reviews WHERE job_id=? ORDER BY created_at", (job_id,))]
