"""Acceptance tests for the JAMES Workflow Lab (PDD section 14). IDs match the PDD."""
import json
import re
import socket
import sqlite3
from pathlib import Path

import pytest
import yaml

from lab import fixtures, foreman, settings
from lab.schemas import GuidanceProposal, Observation

Q = "Why is DEMO-PUMP-03 vibrating more than usual, and what should I check next?"
TECH = {"X-Demo-Role": "technician"}
REV = {"X-Demo-Role": "reviewer"}
EXPECTED = yaml.safe_load((settings.DEMO / "expected" / "results.yaml").read_text())


def start(client, scenario="abnormal", asset="DEMO-PUMP-03", q=Q, guidance=True):
    j = client.post("/api/jobs", json={"scenario": scenario, "asset_id": asset, "question": q}, headers=TECH).json()
    jid = j["job"]["job_id"]
    ev = client.get(f"/api/jobs/{jid}/evidence")
    g = client.post(f"/api/jobs/{jid}/guidance", json={}, headers=TECH).json() if guidance and ev.status_code == 200 else None
    return jid, ev, g


def ack(client, jid, pid, step, key=None):
    h = dict(TECH, **({"Idempotency-Key": key} if key else {}))
    return client.post(f"/api/jobs/{jid}/steps/{step}/acknowledge", json={"proposal_id": pid}, headers=h)


def report(client, jid, pid, step, result="done", note=""):
    return client.post(f"/api/jobs/{jid}/steps/{step}/report", json={"proposal_id": pid, "result": result, "note": note},
                       headers=TECH)


def prereq(client, jid, pid, fact, step=None):
    return client.post(f"/api/jobs/{jid}/prerequisites", json={"proposal_id": pid, "fact": fact, "step_id": step},
                       headers=TECH)


def complete_job(client, jid, pid, summary="Strainer basket was half blocked; cleaned it (demo report)."):
    for fact, step in (("isolation_confirmed", "PV-01"), ("ppe_confirmed", "PV-02")):
        assert prereq(client, jid, pid, fact, step).status_code == 200
    assert ack(client, jid, pid, "PV-06").status_code == 200
    assert report(client, jid, pid, "PV-06", note="Debris in basket").status_code == 200
    r = client.post(f"/api/jobs/{jid}/close", json={"outcome": "REPORTED_RESOLVED", "summary": summary}, headers=TECH)
    assert r.status_code == 200, r.text


def policy(g, step):
    return next(s["policy"] for s in g["steps"] if s["step_id"] == step)


# ---------- A01-A05: evidence ----------

def test_A01_same_seed_same_bytes_and_same_analysis(tmp_path):
    assert fixtures.build() == fixtures.build()
    on_disk = {p.name: p.read_text() for p in settings.SENSORS.glob("*.csv")}
    assert fixtures.build() == on_disk, "committed fixtures differ from the generator"
    from lab.service import Lab
    a = [Lab(tmp_path / f"{i}.db") for i in (1, 2)]
    outs = []
    for lab in a:
        jid = lab.create_job("abnormal", "DEMO-PUMP-03", Q)["job"]["job_id"]
        s = lab.evidence(jid)["sensor"]
        outs.append((s["dataset_hash"], [(c["channel"], c["baseline_mean"], c["current_mean"], c["flag"]) for c in s["channels"]],
                     [e["evidence_id"] for e in s["evidence"]]))
    assert outs[0] == outs[1]


def test_A02_evidence_is_only_the_selected_asset_and_window(client):
    jid, ev, _ = start(client, guidance=False)
    s = ev.json()["sensor"]
    assert s["asset_id"] == "DEMO-PUMP-03" and len(s["rows"]) == 120
    for e in s["evidence"]:
        src = client.get("/api/sources", params={"ref": e["locator"]}).json()
        assert src["rows"] and {r["asset_id"] for r in src["rows"]} == {"DEMO-PUMP-03"}
        assert all(s["window_start"] <= r["timestamp_utc"] <= s["window_end"] for r in src["rows"])
    fan = start(client, asset="DEMO-FAN-01", guidance=False)[1].json()["sensor"]
    flow = next(c for c in fan["channels"] if c["channel"] == "flow_l_min")
    assert flow["flag"] == "not_applicable" and flow["current_mean"] is None


@pytest.mark.parametrize("scenario", ["abnormal", "healthy", "ambiguous"])
def test_A03_flags_match_expected_and_no_confirmed_cause(client, scenario):
    jid, ev, g = start(client, scenario)
    flags = {c["channel"]: c["flag"] for c in ev.json()["sensor"]["channels"]}
    for ch, want in EXPECTED[scenario]["DEMO-PUMP-03"].items():
        assert flags[ch] == want, (scenario, ch, flags[ch])
    p = g["proposal"]
    assert [c["cause_id"] for c in p["possible_causes"]] == EXPECTED[scenario]["candidates"]
    assert all(c["status"] == "possible" for c in p["possible_causes"])
    assert "confirmed" not in json.dumps(p["observations"] + p["possible_causes"]).lower()
    assert p["mode"] == "fixture" and "AI is not active" in p["mode_label"]


def test_A04_missing_and_stale_are_explicit_and_never_filled(client):
    _, ev, g = start(client, "missing")
    temp = next(c for c in ev.json()["sensor"]["channels"] if c["channel"] == "bearing_temp_c")
    assert temp["flag"] == "unavailable" and temp["current_mean"] is None
    assert any("unavailable" in l and "No value is estimated" in l for l in ev.json()["sensor"]["limitations"])
    facts = [e for e in client.get(f"/api/jobs/{g['proposal']['job_id']}/events").json()
             if e["type"] == "EVIDENCE_RETRIEVED"][0]["payload"]["sensor_facts"]
    assert "bearing_temp_c" not in facts                                # no reading: TEMP-01 will ask, never assume
    _, ev, g = start(client, "stale")
    s = ev.json()["sensor"]
    assert s["freshness"] == "stale" and s["scenario_clock"] == "2026-09-20T11:00:00Z"
    assert any(l.startswith("Stale:") for l in s["limitations"])
    assert g["proposal"]["possible_causes"] == [] and g["proposal"]["questions"]
    facts = [e for e in client.get(f"/api/jobs/{g['proposal']['job_id']}/events").json()
             if e["type"] == "EVIDENCE_RETRIEVED"][0]["payload"]["sensor_facts"]
    assert facts == {}                                                  # stale readings never feed WARDEN
    _, ev, _ = start(client, "abnormal", guidance=False)
    assert ev.json()["sensor"]["freshness"] == "fresh"                  # fixture clock, not today's date


def test_A05_malformed_csv_is_rejected_whole(client, lab):
    jid, ev, _ = start(client, "invalid", guidance=False)
    assert ev.status_code == 422
    probs = "\n".join(ev.json()["problems"])
    assert "malformed timestamp" in probs and "'abc' is not a number" in probs
    names = [r[0] for r in lab.store.db.execute("SELECT name FROM datasets")]
    assert "invalid.csv" not in names
    assert client.post(f"/api/jobs/{jid}/guidance", json={}, headers=TECH).status_code == 409


def test_unit_mismatch_between_site_and_pack_is_rejected(lab):
    from lab.pulse import DatasetInvalid, validate
    meta = json.loads(json.dumps(lab.meta))
    meta["DEMO-PUMP-03"]["channels"]["bearing_temp_c"] = "F"
    with pytest.raises(DatasetInvalid, match="site metadata says 'F', the pack says 'C'"):
        validate("abnormal.csv", (settings.SENSORS / "abnormal.csv").read_bytes(), meta, lab.pack_units())


# ---------- A06-A07: guidance ----------

def test_A06_every_step_resolves_to_a_versioned_section(client):
    _, _, g = start(client)
    assert g["proposal"]["steps"]
    for s in g["steps"]:
        src = client.get("/api/sources", params={"ref": s["citation"]}).json()
        assert src["doc_id"] == "DEMO-PROC-PUMP-VIBRATION" and src["version"] == "v1"
        assert f"**{s['step_id']}**" in src["text"] and "Not an OEM procedure" in src["label"]


@pytest.mark.parametrize("asset,q", [("DEMO-PUMP-03", "How do I update the flow meter firmware?"),
                                     ("DEMO-FAN-01", "Why is the fan vibrating?")])
def test_A07_unsupported_question_or_asset_escalates_without_steps(client, asset, q):
    _, _, g = start(client, asset=asset, q=q)
    assert g["proposal"]["steps"] == [] and g["proposal"]["escalate"] is True


# ---------- A08-A11: WARDEN, scope, idempotency, meanings ----------

def test_A08_dependent_step_is_refused_and_the_reason_recorded(client):
    jid, _, g = start(client)
    pid = g["proposal_id"]
    assert policy(g, "PV-01")["state"] == "allowed"                      # the prerequisite itself is never blocked
    r = ack(client, jid, pid, "PV-06")
    assert r.status_code == 409 and "ISO-01" in r.json()["error"]
    rej = [e for e in client.get(f"/api/jobs/{jid}/events").json() if e["type"] == "STEP_ACK_REJECTED"]
    assert rej and rej[0]["payload"]["rule_ids"] == ["ISO-01", "PPE-01"]
    prereq(client, jid, pid, "isolation_confirmed", "PV-01")
    prereq(client, jid, pid, "ppe_confirmed", "PV-02")
    assert ack(client, jid, pid, "PV-06").status_code == 200
    assert ack(client, jid, pid, "PV-08").status_code == 409             # 67 C is above the demo touch limit: BLOCK


def test_A09_prerequisites_only_count_for_their_job_and_guidance_version(client):
    a, _, ga = start(client)
    b, _, gb = start(client)
    prereq(client, a, ga["proposal_id"], "isolation_confirmed")
    prereq(client, a, ga["proposal_id"], "ppe_confirmed")
    assert ack(client, a, ga["proposal_id"], "PV-06").status_code == 200
    assert ack(client, b, gb["proposal_id"], "PV-06").status_code == 409     # other job: not eligible
    assert ack(client, b, ga["proposal_id"], "PV-06").status_code == 409     # wrong proposal for this job
    g2 = client.post(f"/api/jobs/{a}/guidance", json={}, headers=TECH).json() # new guidance version
    assert policy(g2, "PV-06")["state"] == "needs_information"
    assert ack(client, a, ga["proposal_id"], "PV-06").status_code == 409     # stale confirmation invalidated
    assert prereq(client, a, g2["proposal_id"], "plc_override").status_code == 422


def test_A10_repeated_submission_is_one_event(client):
    jid, _, g = start(client)
    r1 = ack(client, jid, g["proposal_id"], "PV-03", key="k-1")
    r2 = ack(client, jid, g["proposal_id"], "PV-03", key="k-1")
    assert r1.status_code == r2.status_code == 200 and r1.json() == r2.json()
    acks = [e for e in client.get(f"/api/jobs/{jid}/events").json() if e["type"] == "STEP_ACKNOWLEDGED"]
    assert len(acks) == 1


def test_A11_acknowledging_is_not_reporting_work(client):
    jid, _, g = start(client)
    pid = g["proposal_id"]
    assert report(client, jid, pid, "PV-03").status_code == 409          # cannot report work you have not read
    ack(client, jid, pid, "PV-03")
    st = {s["step_id"]: s["status"] for s in client.get(f"/api/jobs/{jid}/steps").json()["steps"]}
    assert st["PV-03"] == "acknowledged"
    report(client, jid, pid, "PV-03", note="Flow gauge 64 L/min")
    evs = client.get(f"/api/jobs/{jid}/events").json()
    kinds = [e["type"] for e in evs if e["payload"].get("step_id") == "PV-03"]
    assert kinds == ["STEP_ACKNOWLEDGED", "STEP_REPORTED"]
    assert "no work is implied" in evs[-2]["payload"]["meaning"] and "not observed" in evs[-1]["payload"]["meaning"]
    assert report(client, jid, pid, "PV-04", result="skipped").status_code == 200   # a skip needs no acknowledgement


# ---------- A12-A14: persistence, reviewed memory, export ----------

def test_A12_restart_keeps_everything(tmp_path):
    from fastapi.testclient import TestClient
    from lab.api import create_app
    from lab.service import Lab
    db = tmp_path / "lab.db"
    c1 = TestClient(create_app(Lab(db)))
    jid, ev, g = start(c1)
    complete_job(c1, jid, g["proposal_id"])
    before = c1.get(f"/api/jobs/{jid}/events").json()
    c2 = TestClient(create_app(Lab(db)))                                 # a fresh process on the same file
    assert c2.get(f"/api/jobs/{jid}/events").json() == before
    assert c2.get(f"/api/jobs/{jid}").json()["job"]["outcome"] == "REPORTED_RESOLVED"
    assert c2.get(f"/api/jobs/{jid}/evidence").json()["sensor"]["dataset_hash"] == ev.json()["sensor"]["dataset_hash"]
    corr = c2.post(f"/api/jobs/{jid}/notes", json={"text": "Basket was one third blocked, not half", "corrects": before[-1]["event_id"]})
    after = c2.get(f"/api/jobs/{jid}/events").json()
    assert after[:len(before)] == before and after[-1]["type"] == "CORRECTION" and corr.status_code == 200


def test_A13_only_approved_resolved_complete_cases_are_recommended(client):
    jid, _, g = start(client)
    complete_job(client, jid, g["proposal_id"])
    _, _, g2 = start(client)
    assert g2["proposal"]["recommended_history"] == []                   # closed but not yet reviewed
    assert client.post(f"/api/jobs/{jid}/reviews", json={"decision": "approve", "reason": "ok"}, headers=TECH).status_code == 403
    assert client.post(f"/api/jobs/{jid}/reviews", json={"decision": "approve", "reason": " "}, headers=REV).status_code == 422
    assert client.post(f"/api/jobs/{jid}/reviews", json={"decision": "approve", "reason": "Readings and fix recorded"},
                       headers=REV).status_code == 200
    _, _, g3 = start(client)
    rec = g3["proposal"]["recommended_history"]
    assert [h["job_id"] for h in rec] == [jid] and "approved" in rec[0]["why"]
    assert g3["proposal"]["steps"] == g2["proposal"]["steps"]            # history never overrides the procedure
    others = {h["job_id"]: h["why"] for h in g3["proposal"]["other_history"]}
    assert "PV-99" in others["H-SEED-02"] and "REJECTED" in others["H-SEED-03"] and "axial_fan" in others["H-SEED-01"]
    client.post(f"/api/jobs/{jid}/reviews", json={"decision": "revoke", "reason": "Fix did not hold"}, headers=REV)
    _, _, g4 = start(client)
    assert g4["proposal"]["recommended_history"] == []
    assert [e["type"] for e in client.get(f"/api/jobs/{jid}/events").json()][-2:] == ["REVIEW_APPROVED", "REVIEW_REVOKED"]


def test_unresolved_or_incomplete_jobs_cannot_be_approved(client):
    jid, _, g = start(client)
    client.post(f"/api/jobs/{jid}/close", json={"outcome": "ESCALATED", "summary": ""}, headers=TECH)
    r = client.post(f"/api/jobs/{jid}/reviews", json={"decision": "approve", "reason": "x"}, headers=REV)
    assert r.status_code == 409


def test_A14_export_keeps_sources_label_policy_and_outcome(client):
    jid, _, g = start(client)
    ack(client, jid, g["proposal_id"], "PV-06")                          # a refused attempt goes on the record
    complete_job(client, jid, g["proposal_id"])
    md = client.get(f"/api/jobs/{jid}/export", params={"format": "markdown"}).text
    for needle in (settings.SYNTHETIC_LABEL, "STEP_ACK_REJECTED", "ISO-01", "REPORTED_RESOLVED",
                   "workflow-lab-pumps 0.1.0", "LAB-PUMP v1", "DEMO-PROC-PUMP-VIBRATION: v1@", "sha256"):
        assert needle in md, needle
    js = json.loads(client.get(f"/api/jobs/{jid}/export", params={"format": "json"}).text)
    assert js["label"] == settings.SYNTHETIC_LABEL and js["proposals"] and js["prerequisite_records"]


# ---------- A15-A18: AI mode, validation, injection, offline ----------

def test_A15_local_model_unavailable_is_an_honest_error(client, monkeypatch):
    monkeypatch.setattr(settings, "OLLAMA_URL", "http://127.0.0.1:9")
    monkeypatch.setattr(settings, "OLLAMA_TIMEOUT_S", 2)
    jid, _, _ = start(client, guidance=False)
    r = client.post(f"/api/jobs/{jid}/guidance", json={"mode": "local_ai"}, headers=TECH)
    assert r.status_code == 503 and r.json()["fallback"] == "fixture" and "AI is not active" in r.json()["fallback_label"]
    assert client.get(f"/api/jobs/{jid}/steps").status_code == 409     # nothing silently stored as guidance
    assert any(e["type"] == "PROVIDER_FAILED" for e in client.get(f"/api/jobs/{jid}/events").json())


def _ctx(lab, jid):
    """Rebuild the validation context the service uses."""
    job = lab.store.job(jid)
    from lab.schemas import SensorEvidence
    ev = lab._last(jid, "EVIDENCE_RETRIEVED")
    pid = lab.meta[job["asset_id"]]["pack"]
    f = lab.packs[pid].faults()
    return foreman.Context(job_id=jid, version=9, asset_id=job["asset_id"], asset_label="x", has_pack=True, question=Q,
                           sensor=SensorEvidence.model_validate(ev["payload"]["sensor"]), sections=[],
                           catalog=lab.catalogs[pid], causes={k: v.label for k, v in f.causes.items()},
                           cause_sections={k: "doc-DEMO-PROC-PUMP-VIBRATION-v1#PROC-VIB-0" for k in f.causes},
                           symptom_words={}, patterns=lab.patterns[pid], recommended=[], other=[])


def test_A16_invented_step_citation_or_number_is_rejected(lab):
    jid = lab.create_job("abnormal", "DEMO-PUMP-03", Q)["job"]["job_id"]
    lab.evidence(jid)
    ctx = _ctx(lab, jid)
    vib = next(c for c in ctx.sensor.channels if c.channel == "vibration_mm_s")
    base = dict(job_id=jid, version=9, mode="local_ai", mode_label="test", asset_id="DEMO-PUMP-03")
    ok = GuidanceProposal(**base, steps=("PV-03",), observations=(Observation(
        text=f"Vibration rose to {vib.current_mean:g} mm/s.", evidence_ids=(vib.evidence_id,)),))
    foreman.validate(ok, ctx)
    bad = [GuidanceProposal(**base, steps=("PV-99",)),
           GuidanceProposal(**base, evidence_ids=("doc-FAKE-v1#X",)),
           GuidanceProposal(**base, observations=(Observation(text="Vibration rose to 9.9 mm/s.",
                                                              evidence_ids=(vib.evidence_id,)),))]
    for p in bad:
        with pytest.raises(foreman.ProposalInvalid):
            foreman.validate(p, ctx)


def test_A17_instructions_inside_data_change_nothing(client):
    _, _, g = start(client)
    assert policy(g, "PV-08")["state"] == "blocked" and policy(g, "PV-06")["state"] == "needs_information"
    note = [e for e in client.get("/api/jobs/H-SEED-02/events").json() if e["type"] == "NOTE_ADDED"][0]
    assert "ignore WARDEN" in note["payload"]["text"]                    # stored as content, verbatim
    assert "H-SEED-02" not in [h["job_id"] for h in g["proposal"]["recommended_history"]]
    assert client.get("/api/jobs/H-SEED-02").json()["job"]["review_status"] == "APPROVED"


def test_A18_fixture_workflow_makes_no_network_calls(client, monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("network call attempted in fixture mode")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    jid, _, g = start(client)
    complete_job(client, jid, g["proposal_id"])
    client.post(f"/api/jobs/{jid}/reviews", json={"decision": "approve", "reason": "ok"}, headers=REV)
    assert start(client)[2]["proposal"]["recommended_history"][0]["job_id"] == jid


# ---------- structural guarantees ----------

def test_ledger_tables_are_append_only(lab):
    jid = lab.create_job("abnormal", "DEMO-PUMP-03", Q)["job"]["job_id"]
    lab.evidence(jid)
    for sql in ("UPDATE job_events SET type='X'", "DELETE FROM job_events", "DELETE FROM reviews", "UPDATE sensor_samples SET quality='x'"):
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            lab.store.db.execute(sql)


def test_runtime_never_reads_fixture_ground_truth():
    for p in (settings.LAB).glob("*.py"):
        text = p.read_text()
        assert "results.yaml" not in text and '"expected"' not in text, p.name


def test_lab_pack_passes_its_seeded_safety_cases():
    from james_core.pack import check
    assert check(settings.PACKS / "workflow-lab-pumps") == 0


def test_no_em_dashes_in_lab_text():
    for p in list(settings.LAB.rglob("*")) + list((settings.PACKS / "workflow-lab-pumps").rglob("*")):
        if p.is_file() and p.suffix in (".py", ".md", ".yaml", ".html", ".js", ".css"):
            assert chr(0x2014) not in p.read_text(encoding="utf-8"), p
