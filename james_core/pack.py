"""Industry pack loader (james-pack/0.1). The spec is PACK_FORMAT.md.

Plain Python, no model or network client (tests/test_pack.py enforces that).
Every check fails closed: a pack with any problem is refused as a whole, and the
error lists every problem found, so an author can fix them in one pass.

    python -m james_core.pack check  packs/pharma-utility   # validate + run seeded cases
    python -m james_core.pack rehash packs/pharma-utility   # after editing a pack file (dev only)
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

FORMAT = "james-pack/0.1"
PACKS_DIR = Path(__file__).resolve().parents[1] / "packs"
DEFAULT_PACK = "pharma-utility"
MANIFEST = "manifest.yaml"

SLUG = r"[a-z0-9]+(-[a-z0-9]+)*"          # pack ids
CLASS_ID = r"[a-z0-9]+(_[a-z0-9]+)*"      # equipment class ids
SEMVER = r"\d+\.\d+\.\d+"
SHA256 = r"[0-9a-f]{64}"


class PackError(Exception):
    """The pack was refused. `problems` lists every reason."""

    def __init__(self, pack: str, problems):
        self.pack = pack
        self.problems = list(problems)
        super().__init__(f"Pack '{pack}' refused:\n" + "\n".join(f"  - {p}" for p in self.problems))


# ---------- schemas (unknown fields are refused everywhere) ----------

class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class EquipmentClass(_Strict):
    label: str
    rules: str
    rule_tests: str
    faults: str
    sensors: str
    manual: str


class AssetEntry(_Strict):
    asset_id: str = Field(min_length=1)
    class_: str = Field(alias="class")
    label: str
    aruco_id: Optional[int] = Field(default=None, ge=0, le=49)   # ArUco DICT_4X4_50
    sample_log: Optional[str] = None


class Manifest(_Strict):
    format: str
    id: str = Field(pattern=rf"^{SLUG}$")
    version: str = Field(pattern=rf"^{SEMVER}$")
    name: str
    publisher: str
    status: Literal["demo", "pilot", "approved"]
    licence: str
    equipment_classes: dict[str, EquipmentClass] = Field(min_length=1)
    assets: list[AssetEntry] = []
    files: dict[str, str]


class Cause(_Strict):
    label: str
    manual_section: str


class Signature(_Strict):
    cause: str
    vibration_ratio_min: Optional[float] = None
    temp_rise_min_c: Optional[float] = None

    @model_validator(mode="after")
    def _has_condition(self):
        if self.vibration_ratio_min is None and self.temp_rise_min_c is None:
            raise ValueError(f"signature for '{self.cause}' has no condition, so it would always match")
        return self


class Observation(_Strict):
    """A question FOREMAN may ask when the evidence can't separate two causes (iteration 2, P0-E).
    `source` says where the distinction comes from (a manual page), so the question is not invented."""
    id: str = Field(pattern=r"^[a-z0-9_]+$")
    question: str = Field(min_length=8)
    if_yes: str                   # 'yes'/'no' can't be YAML keys: YAML 1.1 reads them as true/false
    if_no: Optional[str] = None
    source: str


class Faults(_Strict):
    causes: dict[str, Cause] = Field(min_length=1)
    symptom_words: dict[str, str] = {}
    sensor_signatures: list[Signature] = []
    observations: list[Observation] = []


class Channel(_Strict):
    column: str
    unit: str
    warden_fact: Optional[str] = None


class Sensors(_Strict):
    timestamp_column: str = "timestamp"
    asset_column: str = "asset_id"
    channels: dict[str, Channel] = Field(min_length=1)


# ---------- the loaded pack ----------

@dataclass(frozen=True)
class Pack:
    """A loaded pack. Every file was read ONCE, hash-checked, and is served from memory (`read`): what JAMES
    uses is exactly what was verified, even if the folder changes later (iteration 2, P1-E / ST-17).
    `snapshot` is the sha256 of the manifest bytes. The manifest lists every file's hash AND the asset
    bindings (ArUco ids, sample logs), so one id pins the whole pack as used on a job. Hashing is integrity,
    not authenticity: an unsigned pack can be edited and re-hashed by anyone with the folder."""
    root: Path
    manifest: Manifest
    faults_by_class: dict
    sensors_by_class: dict
    signed: bool = False          # Step 2 (ed25519) sets this
    blobs: dict = None            # rel path -> verified bytes
    manifest_bytes: bytes = b""

    @property
    def snapshot(self) -> str:
        return hashlib.sha256(self.manifest_bytes).hexdigest()[:16] if self.manifest_bytes else ""

    def read(self, rel: str) -> bytes:
        """The verified bytes of a listed file (never re-read from disk)."""
        if rel not in self.manifest.files or not self.blobs or rel not in self.blobs:
            raise PackError(self.id, [f"{rel}: not listed in the manifest"])
        return self.blobs[rel]

    def read_text(self, rel: str) -> str:
        return self.read(rel).decode("utf-8")

    def drift(self) -> list[str]:
        """Files (or the manifest) that changed on disk since this pack was loaded. JAMES keeps using the
        verified snapshot; this list is for saying so, and for refusing to start a new pack version silently."""
        out = []
        try:
            if (self.root / MANIFEST).read_bytes() != self.manifest_bytes:
                out.append(MANIFEST)
        except OSError:
            out.append(MANIFEST)
        for rel, digest in self.manifest.files.items():
            try:
                if sha256(self.root / rel) != digest:
                    out.append(rel)
            except OSError:
                out.append(rel)
        return out

    @property
    def id(self) -> str:
        return self.manifest.id

    @property
    def version(self) -> str:
        return self.manifest.version

    @property
    def status(self) -> str:
        return self.manifest.status

    @property
    def label(self) -> str:
        return f"{self.id} {self.version}"

    @property
    def hud_label(self) -> str:
        return f"{self.label} {self.status.upper()} {'SIGNED' if self.signed else 'UNSIGNED'}"

    @property
    def primary_class(self) -> str:
        return next(iter(self.manifest.equipment_classes))

    def path(self, rel: str) -> Path:
        """Absolute path of a file the manifest lists. Anything else is refused."""
        if rel not in self.manifest.files:
            raise PackError(self.id, [f"{rel}: not listed in the manifest"])
        return self.root / rel

    def _cls(self, cls: Optional[str]) -> EquipmentClass:
        cls = cls or self.primary_class
        try:
            return self.manifest.equipment_classes[cls]
        except KeyError:
            raise PackError(self.id, [f"unknown equipment class '{cls}'"]) from None

    def rules_path(self, cls: Optional[str] = None) -> Path:
        return self.path(self._cls(cls).rules)

    def rules_bytes(self, cls: Optional[str] = None) -> bytes:
        return self.read(self._cls(cls).rules)

    def rule_tests_bytes(self, cls: Optional[str] = None) -> bytes:
        return self.read(self._cls(cls).rule_tests)

    def manual_bytes(self, cls: Optional[str] = None) -> tuple[str, bytes]:
        rel = self._cls(cls).manual
        return Path(rel).name, self.read(rel)

    def sample_log_bytes(self, asset_id: str) -> Optional[tuple[str, bytes]]:
        a = self.asset(asset_id)
        return (Path(a.sample_log).name, self.read(a.sample_log)) if a and a.sample_log else None

    def rule_tests_path(self, cls: Optional[str] = None) -> Path:
        return self.path(self._cls(cls).rule_tests)

    def manual_path(self, cls: Optional[str] = None) -> Path:
        return self.path(self._cls(cls).manual)

    def faults(self, cls: Optional[str] = None) -> Faults:
        return self.faults_by_class[cls or self.primary_class]

    def sensors(self, cls: Optional[str] = None) -> Sensors:
        return self.sensors_by_class[cls or self.primary_class]

    def asset(self, asset_id: str) -> Optional[AssetEntry]:
        return next((a for a in self.manifest.assets if a.asset_id == asset_id), None)

    def class_of(self, asset_id: str) -> Optional[str]:
        a = self.asset(asset_id)
        return a.class_ if a else None

    def asset_tags(self) -> dict[int, str]:
        return {a.aruco_id: a.asset_id for a in self.manifest.assets if a.aruco_id is not None}

    def sample_log(self, asset_id: str) -> Optional[Path]:
        a = self.asset(asset_id)
        return self.path(a.sample_log) if a and a.sample_log else None


# ---------- loading ----------

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 16), b""):
            h.update(block)
    return h.hexdigest()


def _ignored(rel: Path) -> bool:
    return any(part.startswith(".") or part == "__pycache__" for part in rel.parts)


def _bad_rel(rel: str) -> Optional[str]:
    if not rel or rel.startswith("/") or "\\" in rel or re.match(r"^[A-Za-z]:", rel):
        return f"{rel!r}: paths must be relative, with forward slashes"
    parts = rel.split("/")
    if any(p in ("", ".", "..") for p in parts):
        return f"{rel!r}: paths may not contain '.', '..' or empty parts"
    if any(p.startswith(".") for p in parts):
        return f"{rel!r}: hidden files are not allowed in a pack"
    if rel == MANIFEST:
        return f"{rel!r}: the manifest does not list itself"
    return None


def _through_symlink(root: Path, rel: str) -> bool:
    p = root
    for part in rel.split("/"):
        p = p / part
        if p.is_symlink():
            return True
    return False


def _yaml(path, problems: list, what: str):
    """path: a Path to read, or the bytes already read (and verified)."""
    try:
        data = path if isinstance(path, bytes) else path.read_bytes()
        return yaml.safe_load(data.decode("utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError, OSError) as e:
        problems.append(f"{what}: not valid YAML ({type(e).__name__})")
        return None


def _schema_errors(what: str, e: ValidationError) -> list[str]:
    out = []
    for err in e.errors():
        loc = ".".join(str(x) for x in err["loc"])
        out.append(f"{what}: {loc + ': ' if loc else ''}{err['msg']}")
    return out


def load_pack(root: str | Path) -> Pack:
    root = Path(root)
    name = root.name
    mf = root / MANIFEST
    if not root.is_dir():
        raise PackError(name, [f"pack folder not found: {root}"])
    if not mf.is_file() or mf.is_symlink():
        raise PackError(name, [f"{MANIFEST} is missing"])
    problems: list[str] = []
    mf_bytes = mf.read_bytes()                      # read once: this exact text is what the snapshot pins
    raw = _yaml(mf_bytes, problems, MANIFEST)
    if problems:
        raise PackError(name, problems)
    if not isinstance(raw, dict):
        raise PackError(name, [f"{MANIFEST} must be a mapping"])
    if raw and list(raw)[-1] != "files":
        problems.append("'files' must be the last key in the manifest")
    try:
        m = Manifest.model_validate(raw)
    except ValidationError as e:
        raise PackError(name, problems + _schema_errors(MANIFEST, e)) from None

    if m.format != FORMAT:
        problems.append(f"format '{m.format}' is not supported (this runtime reads {FORMAT})")
    if m.id != name:
        problems.append(f"id '{m.id}' does not match the folder name '{name}'")

    # 1. every listed file: safe path, exists, hash matches. Each file is read ONCE here and kept.
    blobs: dict[str, bytes] = {}
    for rel, digest in m.files.items():
        bad = _bad_rel(rel)
        if bad:
            problems.append(bad)
        elif not re.fullmatch(SHA256, str(digest)):
            problems.append(f"{rel}: sha256 must be 64 lowercase hex characters")
        elif _through_symlink(root, rel):
            problems.append(f"{rel}: symlinks are not allowed in a pack")
        elif not (root / rel).is_file():
            problems.append(f"{rel}: listed in the manifest but missing")
        else:
            data = (root / rel).read_bytes()
            if hashlib.sha256(data).hexdigest() != digest:
                problems.append(f"{rel}: hash mismatch (the file changed after the manifest was written)")
            else:
                blobs[rel] = data

    # 2. nothing in the folder that the manifest does not cover
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if _ignored(rel) or (p.is_dir() and not p.is_symlink()) or rel.as_posix() == MANIFEST:
            continue
        if rel.as_posix() not in m.files:
            problems.append(f"{rel.as_posix()}: in the pack folder but not listed in the manifest")

    # 3. references and identities
    for cid, ec in m.equipment_classes.items():
        if not re.fullmatch(CLASS_ID, cid):
            problems.append(f"class '{cid}': ids use lowercase letters, digits and underscores")
        for field in ("rules", "rule_tests", "faults", "sensors", "manual"):
            ref = getattr(ec, field)
            if ref not in m.files:
                problems.append(f"class {cid}: {field} '{ref}' is not listed in files")
    ids, tags = set(), set()
    for a in m.assets:
        if a.asset_id in ids:
            problems.append(f"asset {a.asset_id}: asset_id used twice")
        ids.add(a.asset_id)
        if a.aruco_id is not None:
            if a.aruco_id in tags:
                problems.append(f"asset {a.asset_id}: aruco_id {a.aruco_id} used twice")
            tags.add(a.aruco_id)
        if a.class_ not in m.equipment_classes:
            problems.append(f"asset {a.asset_id}: unknown class '{a.class_}'")
        if a.sample_log and a.sample_log not in m.files:
            problems.append(f"asset {a.asset_id}: sample_log '{a.sample_log}' is not listed in files")
    if problems:
        raise PackError(name, problems)

    # 4. the files each class uses parse and agree with each other
    faults, sensors = {}, {}
    for cid, ec in m.equipment_classes.items():
        f = _yaml(blobs[ec.faults], problems, ec.faults)
        if f is not None:
            try:
                faults[cid] = Faults.model_validate(f)
                for s in faults[cid].sensor_signatures:
                    if s.cause not in faults[cid].causes:
                        problems.append(f"{ec.faults}: signature cause '{s.cause}' is not in causes")
                for o in faults[cid].observations:
                    for c in (o.if_yes, o.if_no):
                        if c is not None and c not in faults[cid].causes:
                            problems.append(f"{ec.faults}: observation '{o.id}' points to unknown cause '{c}'")
            except ValidationError as e:
                problems += _schema_errors(ec.faults, e)
        s = _yaml(blobs[ec.sensors], problems, ec.sensors)
        if s is not None:
            try:
                sensors[cid] = Sensors.model_validate(s)
            except ValidationError as e:
                problems += _schema_errors(ec.sensors, e)
        rules = _yaml(blobs[ec.rules], problems, ec.rules)
        tests = _yaml(blobs[ec.rule_tests], problems, ec.rule_tests)
        if isinstance(rules, dict):
            missing = [k for k in ("suite", "version", "allowlisted_actions", "rules") if k not in rules]
            if missing:
                problems.append(f"{ec.rules}: missing {', '.join(missing)}")
            if m.status == "approved" and rules.get("status") != "approved":
                problems.append(f"{ec.rules}: pack says approved but these rules say "
                                f"status '{rules.get('status')}'")
            if isinstance(tests, dict) and (tests.get("suite"), str(tests.get("version"))) != (
                    rules.get("suite"), str(rules.get("version"))):
                problems.append(f"{ec.rule_tests}: cases are for {tests.get('suite')} v{tests.get('version')}, "
                                f"rules are {rules.get('suite')} v{rules.get('version')}")
        elif rules is not None:
            problems.append(f"{ec.rules}: must be a mapping")
    if problems:
        raise PackError(name, problems)
    return Pack(root=root, manifest=m, faults_by_class=faults, sensors_by_class=sensors, blobs=blobs,
                manifest_bytes=mf_bytes)


@lru_cache(maxsize=None)
def active_pack(name: Optional[str] = None) -> Pack:
    """The pack this app runs: JAMES_PACK from the environment, else the default."""
    name = name or os.environ.get("JAMES_PACK", DEFAULT_PACK)
    if not re.fullmatch(SLUG, name):
        raise PackError(name, ["JAMES_PACK must be a pack id such as 'pharma-utility'"])
    return load_pack(PACKS_DIR / name)


# ---------- dev tools ----------

def rehash(root: str | Path) -> list[str]:
    """Rewrite the manifest's `files:` block from what is in the folder. Dev only:
    it trusts whatever is on disk, which is exactly what signing (Step 2) will stop."""
    root = Path(root)
    mf = root / MANIFEST
    if not mf.is_file():
        raise PackError(root.name, [f"{MANIFEST} is missing"])
    text = mf.read_text(encoding="utf-8")
    hit = re.search(r"^files:.*$", text, re.M)
    if not hit:
        raise PackError(root.name, ["no 'files:' line in the manifest"])
    if re.search(r"^[^\s#]", text[hit.end():], re.M):
        raise PackError(root.name, ["'files' must be the last key in the manifest"])
    lines = []
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if _ignored(rel) or (p.is_dir() and not p.is_symlink()) or rel.as_posix() == MANIFEST:
            continue
        if p.is_symlink():
            raise PackError(root.name, [f"{rel.as_posix()}: symlinks are not allowed in a pack"])
        lines.append(f"  {json.dumps(rel.as_posix())}: \"{sha256(p)}\"")
    mf.write_text(text[:hit.start()] + "files:\n" + "\n".join(lines) + "\n", encoding="utf-8")
    return lines


def check(root: str | Path) -> int:
    """Validate a pack and run every class's seeded safety cases. 0 = pass."""
    try:
        pack = load_pack(root)
    except PackError as e:
        print(e)
        print("PACK CHECK: FAIL")
        return 1
    from .suite import run   # local import: suite imports safety_guard, which imports this module
    m = pack.manifest
    print(f"PACK {pack.label} ({m.status.upper()}, {'signed' if pack.signed else 'unsigned'}) by {m.publisher}")
    print(f"Files verified: {len(m.files)}/{len(m.files)} (sha256)")
    print(f"Assets: {', '.join(a.asset_id for a in m.assets) or 'none'}")
    ok = True
    for cid in m.equipment_classes:
        r = run(pack.rule_tests_path(cid), pack.rules_path(cid))
        ok &= r["all_pass"]
        print(f"Class {cid}: {r['suite']} v{r['version']}, violations blocked "
              f"{r['violations_blocked']}/{r['violations_total']}, false blocks "
              f"{r['false_blocks']}/{r['compliant_total']}, {'PASS' if r['all_pass'] else 'FAIL'}")
    print("PACK CHECK: PASS" if ok else "PACK CHECK: FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] not in ("check", "rehash"):
        raise SystemExit("usage: python -m james_core.pack check|rehash <pack folder>")
    if sys.argv[1] == "rehash":
        print("\n".join(rehash(sys.argv[2])))
        print("Manifest file hashes rewritten. Run `check` next.")
        raise SystemExit(0)
    raise SystemExit(check(sys.argv[2]))
