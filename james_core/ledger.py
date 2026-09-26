"""LEDGER storage: an append-only event log in SQLite, the repair record,
and recall of senior-approved past jobs.

UPDATE and DELETE on events are refused by triggers, so the log can only grow."""
from __future__ import annotations

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
    def __init__(self, path: str = ":memory:"):
        # Shared across the HUD loop and agent threads; every access goes through one lock.
        self.db = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.RLock()
        self.db.executescript(SCHEMA)

    # ---- event log ----
    @_locked
    def append(self, session_id: str, kind: str, payload: dict, accepted: bool = True) -> int:
        cur = self.db.execute(
            "INSERT INTO events(ts, session_id, kind, accepted, payload) VALUES (?,?,?,?,?)",
            (datetime.now(timezone.utc).isoformat(), session_id, kind, int(accepted),
             json.dumps(payload, default=_default, sort_keys=True)))
        self.db.commit()
        return cur.lastrowid

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
        return "\n".join(lines) + "\n"
