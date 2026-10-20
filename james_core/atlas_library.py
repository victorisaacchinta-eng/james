"""The ATLAS library: openly licensed 3D models, already converted to line drawings (atlas_library/).

Read with NumPy only: no 3D library at run time. Every file is hash-checked against library.yaml before it is
used, the same fail-closed rule as industry packs; a model whose files do not match is left out and the
reason is reported, the rest still load. Credits and licences travel with each model and ATLAS shows them.

The pack's own models (e.g. the centrifugal pump schematic) can join the shelf too (`from_pack`).
"""
from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import yaml

ROOT = Path(__file__).resolve().parents[1]
LIBRARY = ROOT / "atlas_library"
FORMAT = "james-atlas/0.1"


@dataclass(frozen=True)
class LibPart:
    id: str
    label: str
    role: str
    explode: tuple = (0.0, 0.0, 0.0)
    hazard: Optional[str] = None          # library models carry no WARDEN tags
    keywords: tuple = ()


@dataclass
class LibModel:
    id: str
    label: str
    kind: str
    licence: str
    credit: str
    source: str
    parts: list
    segs: dict = field(repr=False, default_factory=dict)     # part id -> (N, 2, 3) float32: feature edges, then wire
    weights: dict = field(repr=False, default_factory=dict)  # part id -> (N,) float32: 1.0 feature edge, 0.45 wire
    format: str = FORMAT

    @property
    def segment_count(self) -> int:
        return sum(len(v) for v in self.segs.values())


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _load_one(root: Path, entry: dict) -> LibModel:
    blobs = {}
    for rel, digest in entry["files"].items():
        if ".." in rel or rel.startswith("/"):
            raise ValueError(f"{rel}: path leaves the library")
        p = root / rel
        if not p.is_file() or p.is_symlink():
            raise ValueError(f"{rel}: missing")
        data = p.read_bytes()
        if _sha(data) != digest:
            raise ValueError(f"{rel}: hash mismatch (the file changed after the manifest was written)")
        blobs[rel] = data
    mid = entry["id"]
    meta = yaml.safe_load(blobs[f"{mid}/model.yaml"])
    if meta.get("format") != FORMAT or meta.get("id") != mid:
        raise ValueError(f"{mid}/model.yaml: wrong format or id")
    with np.load(io.BytesIO(blobs[f"{mid}/edges.npz"]), allow_pickle=False) as z:
        segs = {k: np.asarray(z[k], np.float32) for k in z.files}
    parts, lines, weights = [], {}, {}
    for p in meta["parts"]:
        pid = p["id"]
        feat = segs.get(pid, np.zeros((0, 2, 3), np.float32))
        wire = segs.get(f"{pid}~wire", np.zeros((0, 2, 3), np.float32))
        for s in (feat, wire):
            if s.ndim != 3 or s.shape[1:] != (2, 3) or not np.isfinite(s).all():
                raise ValueError(f"{mid}: part {pid} lines are malformed")
        if len(feat) + len(wire) == 0:
            raise ValueError(f"{mid}: part {pid} has no lines")
        lines[pid] = np.concatenate([feat, wire])
        weights[pid] = np.concatenate([np.ones(len(feat), np.float32), np.full(len(wire), 0.45, np.float32)])
        parts.append(LibPart(pid, p["label"], p["role"], tuple(float(x) for x in p.get("explode", (0, 0, 0)))))
    if not parts:
        raise ValueError(f"{mid}: no parts")
    return LibModel(id=mid, label=meta["label"], kind=meta.get("kind", ""), licence=meta["licence"],
                    credit=meta["credit"], source=meta.get("source_url", ""), parts=parts, segs=lines,
                    weights=weights)


def load_library(root: Path | str = LIBRARY) -> tuple[list[LibModel], list[str]]:
    """(models that verified, problems). Never raises for one bad model; raises if the manifest itself is bad."""
    root = Path(root)
    mf = root / "library.yaml"
    if not mf.is_file():
        return [], [f"{mf} is missing"]
    raw = yaml.safe_load(mf.read_text())
    if not isinstance(raw, dict) or raw.get("format") != FORMAT:
        raise ValueError("library.yaml: unsupported format")
    models, problems = [], []
    for entry in raw.get("models") or []:
        try:
            models.append(_load_one(root, entry))
        except (ValueError, KeyError, OSError) as e:
            problems.append(f"{entry.get('id', '?')}: {e}")
    return models, problems


def from_pack(pack, cls: Optional[str] = None) -> Optional[LibModel]:
    """A pack's ATLAS model (james-model/0.1 shapes) as a library entry, scaled like the others."""
    from james_core import model3d
    m = pack.model(cls)
    if m is None:
        return None
    raw = {p.id: model3d.part_segments(p) for p in m.parts}
    allp = np.concatenate([v.reshape(-1, 3) for v in raw.values()])
    lo, hi = allp.min(0), allp.max(0)
    c, s = (lo + hi) / 2, float((hi - lo).max())
    parts = [LibPart(p.id, p.label, p.role, tuple(float(x) / s for x in p.explode), p.hazard, tuple(p.keywords))
             for p in m.parts]
    return LibModel(id=f"pack-{pack.manifest.id}-{m.class_}", label=f"{m.label} (schematic)", kind="Industry pack",
                    licence=pack.manifest.licence, credit=m.source, source="", parts=parts,
                    segs={k: ((v - c) / s).astype(np.float32) for k, v in raw.items()})
