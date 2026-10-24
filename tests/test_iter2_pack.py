"""Iteration 2 P1-E: the pack is used exactly as verified (ST-17), and a restart cancels what was pending (ST-12)."""
import shutil
from datetime import timedelta

import pytest

from james_core.pack import PACKS_DIR, PackError, load_pack


@pytest.fixture
def pack_dir(tmp_path):
    d = tmp_path / "pharma-utility"
    shutil.copytree(PACKS_DIR / "pharma-utility", d, ignore=shutil.ignore_patterns(".*"))
    return d


def test_st17_content_is_frozen_at_load(pack_dir):
    p = load_pack(pack_dir)
    before = p.rules_bytes()
    (pack_dir / "rules" / "pump_util_v0_1.yaml").write_text("suite: TAMPERED\n")
    assert p.rules_bytes() == before and b"TAMPERED" not in p.rules_bytes()      # no re-read after the check
    assert p.drift() == ["rules/pump_util_v0_1.yaml"]
    with pytest.raises(PackError):
        load_pack(pack_dir)                                                         # a fresh load refuses it


def test_st17_snapshot_covers_the_asset_binding(pack_dir):
    p = load_pack(pack_dir)
    snap = p.snapshot
    mf = pack_dir / "manifest.yaml"
    mf.write_text(mf.read_text().replace("aruco_id: 4", "aruco_id: 5"))
    q = load_pack(pack_dir)                                  # the manifest is not in its own file list ...
    assert q.asset_tags() == {5: "P-3"} and q.snapshot != snap   # ... but the snapshot id changes with it
    assert p.drift() == ["manifest.yaml"]


def test_only_listed_files_can_be_read(pack_dir):
    p = load_pack(pack_dir)
    with pytest.raises(PackError):
        p.read("../../etc/passwd")


def test_active_pack_consumers_use_verified_bytes():
    import config
    from agents.page import ManualIndex
    from agents.pulse import analyze
    from james_core.safety_guard import load_policy
    assert ManualIndex(use_embeddings=False).name == "sample_pump_manual.pdf"
    assert analyze("P-3")[0][0].ref.startswith("log:p3_vibration.csv[")
    assert load_policy().suite and len(config.PACK.snapshot) == 16


def test_app_says_when_the_pack_changed_mid_job(monkeypatch):
    import time
    import config
    from app import App
    a = App(sim=True)
    a.toggle_lock(); a.target("P-3", "asset_tag", 0.97); a.pinch()
    monkeypatch.setattr(config.PACK.__class__, "drift", lambda self: ["rules/pump_util_v0_1.yaml"])
    a.q.put(("heard", "Pump 3 is vibrating more than usual", 0.95, time.perf_counter(), "TYPED")); a.drain()
    ev = [e for e in a.ledger.events(a.session.id) if e["kind"] == "pack_changed_on_disk"]
    assert ev and ev[0]["payload"]["using_snapshot"] == config.PACK.snapshot
    assert a.plan.pack_snapshot == config.PACK.snapshot


def test_st12_restart_cancels_the_pending_step():
    from app import App
    from james_core.session import Session, State
    import time
    a = App(sim=True)
    a.toggle_lock(); a.target("P-3", "asset_tag", 0.97); a.pinch()
    a.q.put(("heard", "Pump 3 is vibrating more than usual", 0.95, time.perf_counter(), "TYPED")); a.drain()
    assert a.state == State.AWAITING_CONFIRMATION
    sid = a.session.id
    s2 = Session.restore(a.ledger, a.policy, sid)                        # the app restarted
    assert s2.state == State.LOCKED and s2.proposal is None
    ev = [e for e in a.ledger.events(sid) if e["kind"] == "cancelled_by_restart"]
    assert ev and ev[0]["payload"]["was"] == "AWAITING_CONFIRMATION"
    assert ev[0]["payload"]["pending_step"].startswith("Review the vibration")
    assert "CANCELLED BY RESTART" in a.ledger.repair_record(sid)
    s2.last_activity -= timedelta(seconds=1)
