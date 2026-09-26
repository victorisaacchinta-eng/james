"""Industry pack loader: a good pack loads, and every way of breaking one is refused."""
import ast
import os
import shutil
from pathlib import Path

import pytest
import yaml

from james_core.pack import PACKS_DIR, PackError, check, load_pack, rehash, sha256
from tests.test_warden import FORBIDDEN

SRC = PACKS_DIR / "pharma-utility"


@pytest.fixture
def pack_dir(tmp_path):
    d = tmp_path / "pharma-utility"          # the folder name must equal the pack id
    shutil.copytree(SRC, d, ignore=shutil.ignore_patterns(".*"))
    return d


def edit_manifest(d, fn):
    raw = yaml.safe_load((d / "manifest.yaml").read_text())
    fn(raw)
    files = raw.pop("files")
    raw["files"] = files                     # keep files as the last key
    (d / "manifest.yaml").write_text(yaml.safe_dump(raw, sort_keys=False))


def refused(d, *needles):
    with pytest.raises(PackError) as e:
        load_pack(d)
    msg = str(e.value)
    for n in needles:
        assert n in msg, msg
    return msg


def test_demo_pack_loads_and_is_labelled_demo():
    p = load_pack(SRC)
    assert (p.id, p.version, p.status, p.signed) == ("pharma-utility", "0.1.1", "demo", False)
    assert p.asset_tags() == {4: "P-3"} and p.class_of("P-3") == "centrifugal_pump"
    assert p.hud_label == "pharma-utility 0.1.1 DEMO UNSIGNED"
    assert tuple(p.faults().causes) == ("bearing_wear", "misalignment", "cavitation")
    assert p.sample_log("P-3").name == "p3_vibration.csv" and p.sample_log("P-9") is None


def test_runtime_reads_rules_manual_and_tags_from_the_pack():
    import config
    from james_core.safety_guard import load_policy
    assert config.MANUAL == SRC / "docs" / "sample_pump_manual.pdf"
    assert config.ASSET_TAGS == {4: "P-3"}
    assert load_policy().label == "PUMP-UTIL v0.1"


def test_tampered_rule_file_is_refused(pack_dir):
    f = pack_dir / "rules" / "pump_util_v0_1.yaml"
    f.write_text(f.read_text().replace("max: 60", "max: 600"))   # someone loosens the temperature rule
    refused(pack_dir, "rules/pump_util_v0_1.yaml: hash mismatch")


def test_missing_file_is_refused(pack_dir):
    (pack_dir / "docs" / "sample_pump_manual.pdf").unlink()
    refused(pack_dir, "docs/sample_pump_manual.pdf: listed in the manifest but missing")


def test_unlisted_file_is_refused_but_dotfiles_are_ignored(pack_dir):
    (pack_dir / ".DS_Store").write_bytes(b"finder")
    load_pack(pack_dir)                                            # macOS noise is fine
    (pack_dir / "rules" / "extra.yaml").write_text("rules: []\n")
    refused(pack_dir, "rules/extra.yaml: in the pack folder but not listed")


def test_unknown_manifest_field_is_refused(pack_dir):
    edit_manifest(pack_dir, lambda r: r.update(skip_safety=True))
    refused(pack_dir, "skip_safety", "Extra inputs are not permitted")


def test_path_escaping_the_pack_is_refused(pack_dir):
    edit_manifest(pack_dir, lambda r: r["files"].update({"../outside.yaml": "0" * 64}))
    refused(pack_dir, "'../outside.yaml'")


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="no symlinks")
def test_symlinked_file_is_refused(pack_dir, tmp_path):
    target = tmp_path / "evil.yaml"
    f = pack_dir / "faults" / "centrifugal_pump.yaml"
    target.write_text(f.read_text())
    f.unlink()
    f.symlink_to(target)
    refused(pack_dir, "faults/centrifugal_pump.yaml: symlinks are not allowed")


def test_duplicate_asset_tag_is_refused(pack_dir):
    def dup(r):
        a = dict(r["assets"][0], asset_id="P-4")
        r["assets"].append(a)                                      # same aruco_id 4
    edit_manifest(pack_dir, dup)
    refused(pack_dir, "aruco_id 4 used twice")


def test_pack_cannot_claim_approved_with_demo_rules(pack_dir):
    edit_manifest(pack_dir, lambda r: r.update(status="approved"))
    refused(pack_dir, "pack says approved but these rules say status 'demo'")


def test_folder_name_must_match_id(pack_dir, tmp_path):
    other = tmp_path / "other-pack"
    pack_dir.rename(other)
    refused(other, "does not match the folder name")


def test_fault_signature_without_condition_is_refused(pack_dir):
    f = pack_dir / "faults" / "centrifugal_pump.yaml"
    f.write_text(f.read_text() + "  - {cause: cavitation}\n")
    rehash(pack_dir)
    refused(pack_dir, "has no condition")


def test_rehash_accepts_edits_and_keeps_comments(pack_dir):
    f = pack_dir / "sensors" / "centrifugal_pump.yaml"
    f.write_text(f.read_text() + "# tuned on site\n")
    refused(pack_dir, "hash mismatch")
    rehash(pack_dir)
    p = load_pack(pack_dir)
    assert p.manifest.files["sensors/centrifugal_pump.yaml"] == sha256(f)
    assert "# DEMO PACK" in (pack_dir / "manifest.yaml").read_text()


def test_check_runs_the_seeded_cases(pack_dir, capsys):
    assert check(pack_dir) == 0
    out = capsys.readouterr().out
    assert "violations blocked 8/8, false blocks 0/4, PASS" in out and "PACK CHECK: PASS" in out
    (pack_dir / "manifest.yaml").write_text("format: [")           # broken YAML
    assert check(pack_dir) == 1


def test_pulse_uses_pack_channels_and_signatures():
    from agents.pulse import analyze
    ev, facts = analyze("P-3")
    assert ev[0].stance == "bearing_wear" and ev[0].ref.startswith("log:p3_vibration.csv[")
    assert set(facts) == {"casing_temp_c"}


def test_pack_module_imports_no_model_or_network_client():
    src = Path(__file__).resolve().parents[1] / "james_core" / "pack.py"
    names = set()
    for node in ast.walk(ast.parse(src.read_text())):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    assert not names & FORBIDDEN, names & FORBIDDEN
