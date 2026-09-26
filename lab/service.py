"""The Lab's workflow: job lifecycle, evidence, guidance, WARDEN checks, work reports,
closure, review and reviewed reuse. The API is a thin layer over this class, so every rule
here holds even if a client bypasses the UI."""
from __future__ import annotations

import json
import time
from collections import deque
from datetime import timedelta
from pathlib import Path
from typing import Optional

import yaml

from james_core.pack import load_pack
from james_core.safety_guard import load_policy

from . import foreman, settings, warden
from .page import Library, load_catalog, parse_document, section_evidence, tokens
from .pulse import DatasetInvalid, analyze, parse_ts, series, validate
from .schemas import Evidence, GuidanceProposal, HistoryRef, SensorEvidence
from .store import Store

OUTCOMES = ("REPORTED_RESOLVED", "UNRESOLVED", "ESCALATED")
REPORT_RESULTS = ("done", "not_done", "unsuccessful", "skipped")


class LabError(Exception):
    def __init__(self, status: int, message: str, detail: Optional[dict] = None):
        self.status, self.message, self.detail = status, message, detail or {}
        super().__init__(message)


class Lab:
    def __init__(self, db_path=None):
        from james_core import egress
        egress.set_mode("maintenance")              # iteration 2 P0-D: the Lab is local only
        t0 = time.perf_counter()
        self.meta = yaml.safe_load((settings.METADATA / "assets.yaml").read_text())["assets"]
        self.cfg = yaml.safe_load((settings.METADATA / "scenarios.yaml").read_text())
        self.packs, self.policies, self.catalogs, self.patterns, self.procs = {}, {}, {}, {}, {}
        docs = []
        for aid, m in self.meta.items():
            pid = m.get("pack")
            if pid and pid not in self.packs:
                pack = load_pack(settings.PACKS / pid)            # hash-verified, fails closed
                self.packs[pid] = pack
                cls = pack.class_of(aid)
                self.policies[pid] = load_policy(pack.rules_bytes(cls))          # verified bytes (iteration 2 P1-E)
                proc = parse_document(pack.manual_path(cls), f"pack:{pid}", pack.manual_bytes(cls)[1])
                self.procs[pid] = proc
                docs.append(proc)
                for rel in pack.manifest.files:
                    if rel.startswith("docs/") and rel.endswith(".md") and pack.path(rel) != pack.manual_path(cls):
                        docs.append(parse_document(pack.path(rel), f"pack:{pid}", pack.read(rel)))
                self.catalogs[pid] = load_catalog(pack.path("procedures/pump_vibration_steps.yaml"), proc,
                                                  pack.read("procedures/pump_vibration_steps.yaml"))
                self.patterns[pid] = yaml.safe_load(pack.read_text("guidance/pump_patterns.yaml"))
                self._check_policy_doc(pid, docs)
        for p in sorted(settings.DOCUMENTS.glob("*.md")):
            docs.append(parse_document(p, "site"))
        self.library = Library(docs)
        self.store = Store(db_path or settings.DB_PATH)
        self.log: deque = deque(maxlen=60)
        self._seed_history()
        self.startup_ms = round((time.perf_counter() - t0) * 1000, 1)

    def _check_policy_doc(self, pid: str, docs):
        """Every WARDEN rule id must be explained in the pack's policy document."""
        pol = self.policies[pid]
        text = "\n".join(s.title for d in docs if d.source == f"pack:{pid}" for s in d.sections.values())
        missing = [r["id"] for r in pol.rules if r["id"] not in text]
        if missing:
            raise RuntimeError(f"pack {pid}: rules {missing} are not documented in its policy document")

    # ---------- helpers ----------
    def _trace(self, what: str, t0: float, **info) -> float:
        ms = round((time.perf_counter() - t0) * 1000, 1)
        self.log.appendleft({"at": time.strftime("%H:%M:%S"), "call": what, "ms": ms, **info})
        return ms

    def _job(self, job_id: str) -> dict:
        j = self.store.job(job_id)
        if not j:
            raise LabError(404, f"job {job_id} not found")
        return j

    def _open(self, job: dict):
        if job["state"] == "CLOSED":
            raise LabError(409, f"job {job['job_id']} is closed; add a correction instead")

    def asset(self, asset_id: str) -> dict:
        if asset_id not in self.meta:
            raise LabError(404, f"unknown asset {asset_id}")
        return self.meta[asset_id]

    def pack_for(self, asset_id: str):
        pid = self.asset(asset_id).get("pack")
        return pid, self.packs.get(pid) if pid else None

    def pack_units(self) -> dict:
        out = {}
        for aid, m in self.meta.items():
            pack = self.packs.get(m.get("pack"))
            if pack and pack.class_of(aid):
                s = pack.sensors(pack.class_of(aid))
                out[aid] = {c.column: c.unit for c in s.channels.values()}
        return out

    # ---------- scenarios and datasets ----------
    def scenarios(self) -> list[dict]:
        return [{"id": k, **v, "assets": list(self.meta)} for k, v in self.cfg["scenarios"].items()]

    def scenario(self, sid: str) -> dict:
        if sid not in self.cfg["scenarios"]:
            raise LabError(404, f"unknown scenario {sid}")
        return self.cfg["scenarios"][sid]

    def dataset(self, sid: str):
        sc = self.scenario(sid)
        path = settings.SENSORS / sc["dataset"]
        raw = path.read_bytes()
        import hashlib
        sha = hashlib.sha256(raw).hexdigest()
        ds = self.store.dataset_by_hash(sha)
        if ds is None:
            ds = validate(path.name, raw, self.meta, self.pack_units())   # raises DatasetInvalid, nothing stored
            self.store.save_dataset(ds)
        imported = self.store.dataset_meta(ds.dataset_id)["imported_at"]
        clock = parse_ts(sc["clock"]) if sc.get("clock") else max(s.ts for s in ds.samples)
        return ds, imported, clock

    # ---------- jobs ----------
    def create_job(self, scenario: str, asset_id: str, question: str) -> dict:
        self.scenario(scenario)
        m = self.asset(asset_id)
        q = (question or "").strip()
        if not q:
            raise LabError(422, "enter a maintenance question")
        job = self.store.create_job(scenario=scenario, asset_id=asset_id, category=m["category"], question=q[:500])
        self.store.append(job["job_id"], "JOB_CREATED", {"scenario": scenario, "asset_id": asset_id, "question": q[:500],
                                                          "synthetic": True, "roles": settings.ROLES_LABEL})
        return self.job_view(job["job_id"])

    def evidence(self, job_id: str) -> dict:
        t0 = time.perf_counter()
        job = self._job(job_id)
        try:
            ds, imported, clock = self.dataset(job["scenario"])
        except DatasetInvalid as e:
            self.store.append(job_id, "EVIDENCE_REJECTED", {"dataset": self.scenario(job["scenario"])["dataset"],
                                                             "problems": e.problems})
            raise LabError(422, str(e), {"problems": e.problems}) from None
        sev = analyze(ds, job["asset_id"], self.asset(job["asset_id"]), clock, self.cfg, job_id=job_id,
                      ingested_at=imported)
        pid, pack = self.pack_for(job["asset_id"])
        hits = self.library.search(job["question"], job["asset_id"], job["category"])
        sections = [section_evidence(s, self.library.docs[s.doc_id], job["asset_id"], job_id) for _, s in hits]
        facts = self._sensor_facts(sev, pack, job["asset_id"])
        ms = self._trace("evidence", t0, job=job_id, rows=len(sev.rows))
        self.store.append(job_id, "EVIDENCE_RETRIEVED", {
            "dataset_id": ds.dataset_id, "dataset": ds.name, "sha256": ds.sha256, "scenario_clock": sev.scenario_clock,
            "window": [sev.window_start, sev.window_end], "freshness": sev.freshness,
            "flags": {c.channel: c.flag for c in sev.channels}, "limitations": list(sev.limitations),
            "sensor": json.loads(sev.model_dump_json()), "sections": [s.evidence_id for s in sections],
            "sensor_facts": facts, "timing_ms": ms})
        if job["state"] == "DRAFT":
            self.store.update_job(job_id, state="INVESTIGATING", dataset_id=ds.dataset_id, scenario_clock=sev.scenario_clock)
        return {"sensor": json.loads(sev.model_dump_json()), "series": series(ds, job["asset_id"], sev),
                "sections": [json.loads(s.model_dump_json()) for s in sections],
                "dataset": self.store.dataset_meta(ds.dataset_id), "timing_ms": ms,
                "pack": pack.hud_label if pack else None}

    def _sensor_facts(self, sev: SensorEvidence, pack, asset_id: str) -> dict:
        """Measured facts WARDEN may use. Stale or unavailable readings are left out, so a rule that
        needs them asks for them instead of trusting an old value."""
        if not pack or sev.freshness != "fresh":
            return {}
        s = pack.sensors(pack.class_of(asset_id))
        out = {}
        for ch in s.channels.values():
            if ch.warden_fact:
                c = next((c for c in sev.channels if c.channel == ch.column), None)
                if c and c.current_latest is not None:
                    out[ch.warden_fact] = c.current_latest
        return out

    def _last(self, job_id: str, type_: str) -> Optional[dict]:
        evs = [e for e in self.store.events(job_id) if e["type"] == type_]
        return evs[-1] if evs else None

    # ---------- guidance ----------
    def guidance(self, job_id: str, mode: Optional[str] = None) -> dict:
        t0 = time.perf_counter()
        job = self._job(job_id)
        self._open(job)
        ev = self._last(job_id, "EVIDENCE_RETRIEVED")
        if not ev:
            raise LabError(409, "retrieve the evidence before asking for guidance")
        mode = mode or settings.MODE
        if mode not in ("fixture", "local_ai"):
            raise LabError(422, f"unknown mode {mode}")
        sev = SensorEvidence.model_validate(ev["payload"]["sensor"])
        pid, pack = self.pack_for(job["asset_id"])
        catalog = self.catalogs.get(pid, {})
        sections = [section_evidence(s, self.library.docs[s.doc_id], job["asset_id"], job_id)
                    for s in (self.library.section(i) for i in ev["payload"]["sections"]) if s]
        rec, other = self.history(job["asset_id"], job["question"], exclude=job_id)
        faults = pack.faults(pack.class_of(job["asset_id"])) if pack else None
        version = len(self.store.proposals(job_id)) + 1
        ctx = foreman.Context(
            job_id=job_id, version=version, asset_id=job["asset_id"], asset_label=self.asset(job["asset_id"])["label"],
            has_pack=bool(pack), question=job["question"], sensor=sev, sections=sections, catalog=catalog,
            causes={k: v.label for k, v in faults.causes.items()} if faults else {},
            cause_sections={k: f"doc-{self.procs[pid].doc_id}-{self.procs[pid].version}#{v.manual_section}"
                            for k, v in faults.causes.items()} if faults else {},
            symptom_words=dict(faults.symptom_words) if faults else {},
            patterns=self.patterns.get(pid, {"patterns": [], "default_steps": [], "closing_steps": []}),
            recommended=rec, other=other)
        use_ai = mode == "local_ai" and pack and foreman.symptoms_in(job["question"], ctx.symptom_words)
        try:
            p = foreman.local_ai_proposal(ctx) if use_ai else foreman.fixture_proposal(ctx)
        except foreman.ProviderUnavailable as e:
            self.store.append(job_id, "PROVIDER_FAILED", {"mode": mode, "error": str(e)})
            raise LabError(503, str(e), {"fallback": "fixture",
                                         "fallback_label": settings.FIXTURE_LABEL}) from None
        if mode == "local_ai" and not use_ai:
            p = p.model_copy(update={"mode_label": "No AI call: no applicable procedure or symptom; deterministic escalation"})
        try:
            p = foreman.validate(p, ctx)
        except foreman.ProposalInvalid as e:
            self.store.append(job_id, "PROPOSAL_REJECTED", {"mode": p.mode, "problems": e.problems})
            raise LabError(422, str(e), {"problems": e.problems}) from None
        self.store.add_proposal(p)
        ms = self._trace("guidance", t0, job=job_id, mode=p.mode, steps=len(p.steps))
        self.store.append(job_id, "PROPOSAL_CREATED", {"proposal_id": p.proposal_id, "version": p.version, "mode": p.mode,
                                                        "steps": list(p.steps), "timing_ms": ms,
                                                        "recommended_history": [h.job_id for h in p.recommended_history]})
        self.store.update_job(job_id, state="GUIDANCE_READY" if job["state"] in ("DRAFT", "INVESTIGATING", "GUIDANCE_READY")
                              else job["state"], current_proposal=p.proposal_id)
        view = self.steps(job_id)
        self.store.append(job_id, "POLICY_EVALUATED", {"proposal_id": p.proposal_id,
                          "decisions": {s["step_id"]: s["policy"] for s in view["steps"]}})
        return {"proposal": json.loads(p.model_dump_json()), "timing_ms": ms, **view}

    # ---------- steps and WARDEN ----------
    def _proposal(self, job: dict) -> tuple[GuidanceProposal, dict]:
        if not job.get("current_proposal"):
            raise LabError(409, "no guidance yet for this job")
        p = GuidanceProposal.model_validate(self.store.proposal(job["current_proposal"]))
        pid, _ = self.pack_for(job["asset_id"])
        return p, self.catalogs.get(pid, {})

    def facts(self, job: dict, proposal_id: str) -> dict:
        ev = self._last(job["job_id"], "EVIDENCE_RETRIEVED")
        facts = dict(ev["payload"].get("sensor_facts", {})) if ev else {}
        for r in self.store.prereqs(job["job_id"], job["asset_id"], proposal_id):   # this job, asset and version only
            facts[r["fact"]] = bool(r["value"])
        return facts

    def policy_for(self, job: dict, p: GuidanceProposal, step_id: str):
        pid, _ = self.pack_for(job["asset_id"])
        catalog = self.catalogs[pid]
        step = catalog[step_id]
        ev_ids = (step.evidence_id,) + tuple(i for o in p.observations for i in o.evidence_ids)
        return warden.check(step, self.facts(job, p.proposal_id), ev_ids, self.policies[pid])

    def steps(self, job_id: str) -> dict:
        job = self._job(job_id)
        p, catalog = self._proposal(job)
        evs = [e for e in self.store.events(job_id) if e["payload"].get("proposal_id") == p.proposal_id]
        recorded = {r["fact"] for r in self.store.prereqs(job_id, job["asset_id"], p.proposal_id)}
        out, current = [], None
        for sid in p.steps:
            st = catalog[sid]
            status = "pending"
            for e in evs:
                if e["payload"].get("step_id") != sid:
                    continue
                if e["type"] == "STEP_ACKNOWLEDGED" and status == "pending":
                    status = "acknowledged"
                elif e["type"] == "STEP_REPORTED":
                    status = e["payload"]["result"]
            if st.kind == "prerequisite" and st.records in recorded:
                status = "recorded"
            pol = self.policy_for(job, p, sid)
            if current is None and status in ("pending", "acknowledged"):
                current = sid
            out.append({"step_id": sid, "text": st.text, "kind": st.kind, "records": st.records,
                        "section": st.section_id, "citation": st.evidence_id, "touches": list(st.touches),
                        "status": status, "policy": json.loads(pol.model_dump_json())})
        return {"proposal_id": p.proposal_id, "version": p.version, "steps": out, "current": current,
                "prerequisites": self.store.prereqs(job_id, job["asset_id"], p.proposal_id)}

    def _guard(self, job_id: str, proposal_id: str, step_id: Optional[str] = None):
        job = self._job(job_id)
        self._open(job)
        p, catalog = self._proposal(job)
        if proposal_id != p.proposal_id:
            raise LabError(409, f"proposal {proposal_id} is not the current guidance (v{p.version}); reload the job")
        if step_id is not None and step_id not in p.steps:
            raise LabError(409, f"step {step_id} is not part of guidance v{p.version}")
        return job, p, catalog

    def record_prerequisite(self, job_id: str, proposal_id: str, fact: str, reporter: str,
                            step_id: Optional[str] = None, key: Optional[str] = None) -> dict:
        done = self.store.remembered(job_id, key)
        if done:
            return done
        job, p, catalog = self._guard(job_id, proposal_id, step_id)
        allowed = {s.records for s in catalog.values() if s.records and s.step_id in p.steps}
        if fact not in allowed:
            raise LabError(422, f"'{fact}' is not a prerequisite of guidance v{p.version} (expected one of {sorted(allowed)})")
        if step_id and catalog[step_id].records != fact:
            raise LabError(422, f"step {step_id} records '{catalog[step_id].records}', not '{fact}'")
        rec = self.store.add_prereq(job_id, job["asset_id"], p.proposal_id, fact, True, step_id, reporter or "demo-technician")
        self.store.append(job_id, "PREREQUISITE_RECORDED", {"proposal_id": p.proposal_id, "fact": fact, "step_id": step_id,
                          "record_id": rec["record_id"], "meaning": "operator attestation in a demo; not verified by the Lab"}, key)
        self.store.update_job(job_id, state="IN_PROGRESS")
        resp = {"record": rec, **self.steps(job_id)}
        self.store.remember(job_id, key, resp)
        return resp

    def acknowledge(self, job_id: str, step_id: str, proposal_id: str, key: Optional[str] = None) -> dict:
        done = self.store.remembered(job_id, key)
        if done:
            return done
        job, p, _ = self._guard(job_id, proposal_id, step_id)
        pol = self.policy_for(job, p, step_id)
        if pol.state != "allowed":
            self.store.append(job_id, "STEP_ACK_REJECTED", {"proposal_id": p.proposal_id, "step_id": step_id,
                              "policy_state": pol.state, "rule_ids": list(pol.rule_ids), "reasons": list(pol.reasons)})
            raise LabError(409, f"WARDEN: {step_id} {'is blocked' if pol.state == 'blocked' else 'needs information'} ({', '.join(pol.rule_ids)}): "
                                + "; ".join(pol.reasons), {"policy": json.loads(pol.model_dump_json())})
        self.store.append(job_id, "STEP_ACKNOWLEDGED", {"proposal_id": p.proposal_id, "step_id": step_id,
                          "meaning": "operator has read the instruction; no work is implied",
                          "policy": json.loads(pol.model_dump_json())}, key)
        self.store.update_job(job_id, state="IN_PROGRESS")
        resp = self.steps(job_id)
        self.store.remember(job_id, key, resp)
        return resp

    def report(self, job_id: str, step_id: str, proposal_id: str, result: str, note: str = "",
               key: Optional[str] = None) -> dict:
        done = self.store.remembered(job_id, key)
        if done:
            return done
        job, p, _ = self._guard(job_id, proposal_id, step_id)
        if result not in REPORT_RESULTS:
            raise LabError(422, f"result must be one of {REPORT_RESULTS}")
        if result in ("done", "unsuccessful"):
            acked = any(e["type"] == "STEP_ACKNOWLEDGED" and e["payload"].get("step_id") == step_id
                        and e["payload"].get("proposal_id") == p.proposal_id for e in self.store.events(job_id))
            if not acked:
                raise LabError(409, f"acknowledge {step_id} before reporting work on it")
            pol = self.policy_for(job, p, step_id)
            if pol.state != "allowed":
                self.store.append(job_id, "STEP_REPORT_REJECTED", {"proposal_id": p.proposal_id, "step_id": step_id,
                                  "rule_ids": list(pol.rule_ids), "reasons": list(pol.reasons)})
                raise LabError(409, f"WARDEN: {step_id} {'is blocked' if pol.state == 'blocked' else 'needs information'}: " + "; ".join(pol.reasons))
        self.store.append(job_id, "STEP_REPORTED", {"proposal_id": p.proposal_id, "step_id": step_id, "result": result,
                          "note": (note or "")[:1000], "meaning": "operator report of what they did; not observed by the Lab"}, key)
        self.store.update_job(job_id, state="IN_PROGRESS")
        resp = self.steps(job_id)
        self.store.remember(job_id, key, resp)
        return resp

    def add_note(self, job_id: str, text: str, corrects: Optional[str] = None, key: Optional[str] = None) -> dict:
        done = self.store.remembered(job_id, key)
        if done:
            return done
        self._job(job_id)
        if not (text or "").strip():
            raise LabError(422, "note is empty")
        if corrects:
            target = self.store.event(corrects)
            if not target or target["job_id"] != job_id:
                raise LabError(404, f"event {corrects} not found on this job")
        ev = self.store.append(job_id, "CORRECTION" if corrects else "NOTE_ADDED", {"text": text[:2000]}, key, corrects)
        resp = {"event": ev}
        self.store.remember(job_id, key, resp)
        return resp

    # ---------- closure and review ----------
    def completeness(self, job_id: str) -> dict:
        job = self._job(job_id)
        evs = self.store.events(job_id)
        types = {e["type"] for e in evs}
        closed = self._last(job_id, "JOB_CLOSED")
        checks = {
            "question": bool(job["question"]),
            "evidence": "EVIDENCE_RETRIEVED" in types,
            "guidance": "PROPOSAL_CREATED" in types,
            "work_reported": any(e["type"] == "STEP_REPORTED" and e["payload"]["result"] == "done" for e in evs),
            "outcome": bool(job["outcome"]),
            "resolution_summary": bool(closed and closed["payload"].get("summary", "").strip()),
        }
        return {"checks": checks, "complete": all(checks.values()), "score": f"{sum(checks.values())}/{len(checks)}",
                "missing": [k for k, v in checks.items() if not v]}

    def close(self, job_id: str, outcome: str, summary: str, key: Optional[str] = None) -> dict:
        done = self.store.remembered(job_id, key)
        if done:
            return done
        job = self._job(job_id)
        self._open(job)
        if outcome not in OUTCOMES:
            raise LabError(422, f"outcome must be one of {OUTCOMES}")
        if job["state"] == "DRAFT":
            raise LabError(409, "retrieve evidence before closing")
        if outcome == "REPORTED_RESOLVED" and not (summary or "").strip():
            raise LabError(422, "say what resolved it: a reported-resolved job needs a summary")
        self.store.append(job_id, "JOB_CLOSED", {"outcome": outcome, "summary": (summary or "")[:2000],
                          "meaning": "operator-reported outcome"}, key)
        self.store.update_job(job_id, state="CLOSED", outcome=outcome, closed_at=self._last(job_id, "JOB_CLOSED")["created_at"],
                              review_status="PENDING")
        resp = self.job_view(job_id)
        self.store.remember(job_id, key, resp)
        return resp

    def review(self, job_id: str, decision: str, reason: str, reviewer: str, key: Optional[str] = None) -> dict:
        done = self.store.remembered(job_id, key)
        if done:
            return done
        job = self._job(job_id)
        if not (reason or "").strip():
            raise LabError(422, "a review needs a reason")
        status = job["review_status"]
        if decision == "approve":
            if status != "PENDING":
                raise LabError(409, f"only a pending job can be approved (this one is {status})")
            if job["outcome"] != "REPORTED_RESOLVED":
                raise LabError(409, "only a reported-resolved job can be approved for reuse")
            comp = self.completeness(job_id)
            if not comp["complete"]:
                raise LabError(409, "record incomplete: missing " + ", ".join(comp["missing"]))
            new, revokes = "APPROVED", None
        elif decision == "reject":
            if status != "PENDING":
                raise LabError(409, f"only a pending job can be rejected (this one is {status})")
            new, revokes = "REJECTED", None
        elif decision == "revoke":
            if status != "APPROVED":
                raise LabError(409, "only an approved job can be revoked")
            last = [r for r in self.store.reviews(job_id) if r["decision"] == "APPROVED"][-1]
            new, revokes = "REVOKED", last["review_id"]
        else:
            raise LabError(422, "decision must be approve, reject or revoke")
        rev = self.store.add_review(job_id, new, reason.strip()[:1000], reviewer or "demo-reviewer", revokes)
        self.store.append(job_id, "REVIEW_" + new, {"review_id": rev["review_id"], "reason": rev["reason"],
                          "reviewer": rev["reviewer"], "revokes": revokes, "roles": settings.ROLES_LABEL}, key)
        self.store.update_job(job_id, review_status=new)
        resp = self.job_view(job_id)
        self.store.remember(job_id, key, resp)
        return resp

    # ---------- memory: reviewed reuse ----------
    def _steps_done(self, job_id: str) -> list[str]:
        out = []
        for e in self.store.events(job_id):
            if e["type"] == "SEEDED_HISTORY":
                out += e["payload"]["steps_done"]
            elif e["type"] == "STEP_REPORTED" and e["payload"]["result"] == "done":
                out.append(e["payload"]["step_id"])
        return list(dict.fromkeys(out))

    def history(self, asset_id: str, question: str, exclude: Optional[str] = None) -> tuple[list[HistoryRef], list[HistoryRef]]:
        m = self.asset(asset_id)
        pid = m.get("pack")
        catalog = self.catalogs.get(pid, {})
        want = set(tokens(question))
        rec, other = [], []
        for j in self.store.jobs():
            if j["job_id"] == exclude or j["state"] != "CLOSED":
                continue
            closed = self._last(j["job_id"], "JOB_CLOSED")
            summary = (closed["payload"].get("summary") if closed else "") or ""
            overlap = sorted(want & set(tokens(j["question"] + " " + summary)))
            same_asset, same_cat = j["asset_id"] == asset_id, j["category"] == m["category"]
            if not (same_asset or same_cat or overlap):
                continue
            done = self._steps_done(j["job_id"])
            unknown = [s for s in done if s not in catalog]
            reasons = []
            if not same_cat:
                reasons.append(f"different equipment category ({j['category']})")
            if j["outcome"] != "REPORTED_RESOLVED":
                reasons.append(f"outcome {j['outcome']}")
            if j["review_status"] != "APPROVED":
                reasons.append(f"review status {j['review_status']}")
            if unknown:
                reasons.append(f"uses steps not in the current procedure ({', '.join(unknown)})")
            if not j["seeded"] and not self.completeness(j["job_id"])["complete"]:
                reasons.append("record incomplete")
            why_match = ("same asset" if same_asset else "same category" if same_cat else "different asset") + \
                        (f"; shared terms: {', '.join(overlap[:5])}" if overlap else "")
            ref = HistoryRef(job_id=j["job_id"], asset_id=j["asset_id"], summary=summary[:300], outcome=j["outcome"],
                             review=j["review_status"], seeded=bool(j["seeded"]),
                             why=(f"Recommended: {why_match}; reported resolved; approved by a demo reviewer"
                                  if not reasons else f"Not recommended: {'; '.join(reasons)} ({why_match})"),
                             evidence_id=f"job:{j['job_id']}")
            score = (2 if same_asset else 0) + (1 if same_cat else 0) + len(overlap)
            (other if reasons else rec).append((score, j["closed_at"] or "", ref))
        rec.sort(key=lambda x: (-x[0], x[1]), reverse=False)
        rec = sorted(rec, key=lambda x: (x[0], x[1]), reverse=True)
        other = sorted(other, key=lambda x: (x[0], x[1]), reverse=True)
        return [r for _, _, r in rec[:3]], [r for _, _, r in other[:5]]

    def _seed_history(self):
        seed = yaml.safe_load(settings.HISTORY_SEED.read_text())["jobs"]
        for h in seed:
            if self.store.job(h["job_id"]):
                continue
            m = self.asset(h["asset_id"])
            self.store.create_job(job_id=h["job_id"], scenario=None, asset_id=h["asset_id"], category=m["category"],
                                  question=h["question"], state="CLOSED", outcome=h["outcome"], review_status=h["review"],
                                  seeded=1, created_at="2026-09-01T09:00:00+00:00", closed_at="2026-09-01T10:00:00+00:00")
            self.store.append(h["job_id"], "SEEDED_HISTORY", {"label": "SEEDED DEMO HISTORY (fictional)",
                              "steps_done": h["steps_done"]})
            self.store.append(h["job_id"], "NOTE_ADDED", {"text": h["note"]})
            self.store.append(h["job_id"], "JOB_CLOSED", {"outcome": h["outcome"], "summary": h["summary"]})
            self.store.add_review(h["job_id"], h["review"], "seeded demo history", "seed")

    # ---------- views, sources, exports ----------
    def job_view(self, job_id: str) -> dict:
        job = self._job(job_id)
        return {"job": job, "completeness": self.completeness(job_id), "reviews": self.store.reviews(job_id),
                "synthetic_label": settings.SYNTHETIC_LABEL}

    def events(self, job_id: str) -> list[dict]:
        self._job(job_id)
        return self.store.events(job_id)

    def source(self, ref: str) -> dict:
        if ref.startswith("doc-"):
            s = self.library.section(ref)
            if not s:
                raise LabError(404, f"no section {ref}")
            d = self.library.docs[s.doc_id]
            return {"kind": "document_section", "ref": ref, "doc_id": d.doc_id, "version": d.version,
                    "sha256": d.sha256, "label": d.label, "title": s.title, "section_id": s.section_id,
                    "text": s.text, "source": d.source}
        if ref.startswith("job:"):
            v = self.job_view(ref[4:])
            return {"kind": "history_case", "ref": ref, **v, "events": self.store.events(ref[4:])}
        if "#asset=" in ref:
            name, q = ref.split("#", 1)
            params = dict(x.split("=", 1) for x in q.split("&"))
            t0, t1 = (parse_ts(x) for x in params["t"].split(".."))
            with self.store.lock:
                d = self.store.db.execute("SELECT dataset_id, sha256 FROM datasets WHERE name=? ORDER BY imported_at DESC",
                                          (name,)).fetchone()
            if not d:
                raise LabError(404, f"dataset {name} not ingested")
            ds = self.store.dataset_by_hash(d["sha256"])
            rows = [{"row": s.row, "timestamp_utc": s.ts.strftime("%Y-%m-%dT%H:%M:%SZ"), "asset_id": s.asset_id,
                     params["channel"]: s.values.get(params["channel"]), "quality": s.quality}
                    for s in ds.samples if s.asset_id == params["asset"] and t0 <= s.ts <= t1]
            return {"kind": "dataset_rows", "ref": ref, "dataset_id": d["dataset_id"], "sha256": d["sha256"],
                    "label": settings.SYNTHETIC_LABEL, "rows": rows}
        raise LabError(404, f"unknown source reference {ref}")

    def export(self, job_id: str, fmt: str = "json") -> tuple[str, str]:
        v = self.job_view(job_id)
        evs = self.store.events(job_id)
        props = self.store.proposals(job_id)
        prereqs = self.store.prereqs(job_id)
        record = {"label": settings.SYNTHETIC_LABEL, "roles": settings.ROLES_LABEL, **v, "proposals": props,
                  "prerequisite_records": prereqs, "events": evs,
                  "sources": {"packs": {k: p.hud_label for k, p in self.packs.items()},
                              "policies": {k: p.label for k, p in self.policies.items()},
                              "documents": {d.doc_id: f"{d.version}@{d.sha256[:12]}" for d in self.library.docs.values()}}}
        if fmt == "json":
            return json.dumps(record, indent=2, default=str), "application/json"
        j = v["job"]
        lines = [f"# Job record {j['job_id']}", "", f"> {settings.SYNTHETIC_LABEL} {settings.ROLES_LABEL}.", "",
                 f"- Asset: {j['asset_id']} ({j['category']})", f"- Scenario: {j['scenario']}",
                 f"- Question: {j['question']}", f"- State: {j['state']}; outcome: {j['outcome'] or 'open'}; "
                 f"review: {j['review_status']}", f"- Record completeness: {v['completeness']['score']}", "",
                 "## Sources", ""]
        for k, s in record["sources"].items():
            for name, ver in s.items():
                lines.append(f"- {k[:-1]} {name}: {ver}")
        ev = next((e for e in evs if e["type"] == "EVIDENCE_RETRIEVED"), None)
        if ev:
            lines += ["", "## Sensor evidence (observed, synthetic)", "",
                      f"Dataset {ev['payload']['dataset']} sha256 {ev['payload']['sha256'][:16]}, window "
                      f"{ev['payload']['window'][0]} to {ev['payload']['window'][1]}, {ev['payload']['freshness']}.", ""]
            for e in ev["payload"]["sensor"]["evidence"]:
                lines.append(f"- {e['summary']} [`{e['evidence_id']}`]")
            for lim in ev["payload"]["limitations"]:
                lines.append(f"- Limitation: {lim}")
        for p in props:
            lines += ["", f"## Guidance v{p['version']} ({p['mode_label']})", ""]
            lines += [f"- Observed: {o['text']}" for o in p["observations"]]
            lines += [f"- Possible cause: {c['label']}. {c['why']}" for c in p["possible_causes"]]
            lines += [f"- Unknown: {u}" for u in p["unknowns"]]
            lines += [f"- Question: {q}" for q in p["questions"]]
            lines.append(f"- Steps: {', '.join(p['steps']) or 'none'}")
        lines += ["", "## Events (append-only)", "", "| # | time (UTC) | type | detail |", "|---|---|---|---|"]
        for e in evs:
            pl = e["payload"]
            detail = pl.get("step_id") or pl.get("fact") or pl.get("outcome") or pl.get("text") or ""
            if e["type"] in ("STEP_ACK_REJECTED", "STEP_REPORT_REJECTED"):
                detail = f"{pl['step_id']}: {', '.join(pl['rule_ids'])}"
            if e["type"] == "STEP_REPORTED":
                detail = f"{pl['step_id']}: {pl['result']}" + (f", note: {pl['note']}" if pl.get("note") else "")
            if e["corrects"]:
                detail = f"corrects {e['corrects']}: {detail}"
            lines.append(f"| {e['seq']} | {e['created_at'][:19]} | {e['type']} | {str(detail).replace('|', '/')[:160]} |")
        lines += ["", "## Reviews", ""] + [f"- {r['decision']} by {r['reviewer']} at {r['created_at'][:19]}: {r['reason']}"
                                           for r in v["reviews"]] + [""]
        return "\n".join(lines), "text/markdown"

    def developer(self) -> dict:
        return {"mode": settings.MODE, "mode_label": settings.FIXTURE_LABEL if settings.MODE == "fixture" else
                f"Local AI: {settings.OLLAMA_MODEL} at {settings.OLLAMA_URL}",
                "packs": {k: {"label": p.hud_label, "files": len(p.manifest.files)} for k, p in self.packs.items()},
                "policies": {k: p.label for k, p in self.policies.items()},
                "documents": [{"doc_id": d.doc_id, "version": d.version, "sha256": d.sha256[:16], "source": d.source,
                               "sections": list(d.sections)} for d in self.library.docs.values()],
                "scenario_settings": {k: v for k, v in self.cfg.items() if k != "scenarios"},
                "db": str(self.store.db.execute("PRAGMA database_list").fetchone()[2] or ":memory:"),
                "policy_tests": self.policy_tests(), "startup_ms": self.startup_ms, "calls": list(self.log)}

    def policy_tests(self) -> dict:
        """Run each pack's seeded WARDEN cases now (defined tests passed / executed)."""
        from james_core.suite import run
        out = {}
        for pid, pack in self.packs.items():
            cls = pack.primary_class
            r = run(pack.rule_tests_path(cls), pack.rules_path(cls))
            out[pid] = {"passed": sum(c["pass_"] for c in r["cases"]), "executed": len(r["cases"]),
                        "violations_blocked": f"{r['violations_blocked']}/{r['violations_total']}",
                        "false_blocks": f"{r['false_blocks']}/{r['compliant_total']}"}
        return out
