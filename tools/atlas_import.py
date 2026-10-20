"""Build atlas_library/ from openly licensed 3D models (dev only).

    pip install -r tools/requirements-atlas.txt
    python -m tools.atlas_import              # every model in tools/atlas_sources.yaml
    python -m tools.atlas_import perseverance # one model

For each recipe: fetch the original file (cached in data/atlas_src/), group its mesh nodes into named parts,
and keep only the lines a draughtsman would draw: feature edges (where the surface folds by more than a set
angle) and open boundaries. The result is a few tens of thousands of line segments per model, stored as
float16 in atlas_library/<id>/edges.npz with a model.yaml (parts, credit, licence) and a hash manifest
(atlas_library/library.yaml) that the app verifies before it shows anything.

Textures, materials and logos are dropped on purpose: ATLAS draws lines in JSIL, and the CC-BY models must
say they were changed (they do, in their credit line).
"""
from __future__ import annotations

import hashlib
import logging
import re
import sys
import urllib.request
from pathlib import Path

import numpy as np
import yaml

ROOT = Path(__file__).resolve().parents[1]
RECIPES = ROOT / "tools" / "atlas_sources.yaml"
CACHE = ROOT / "data" / "atlas_src"
OUT = ROOT / "atlas_library"
FORMAT = "james-atlas/0.1"
MAX_FACES_PER_GROUP = 40000


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch(rec: dict) -> Path:
    dest = CACHE / rec["file"]
    dest.parent.mkdir(parents=True, exist_ok=True)
    urls = [(rec["url"], dest)]
    for extra in rec.get("extra_files", []):
        urls.append((rec["url"].rsplit("/", 1)[0] + "/" + extra, dest.parent / extra))
    for url, path in urls:
        if not path.exists():
            print(f"  downloading {url}")
            urllib.request.urlretrieve(url, path)                        # noqa: S310  fixed, reviewed URLs only
    return dest


def world_meshes(path: Path, strip: str = "") -> list[tuple[str, "object"]]:
    import trimesh
    logging.disable(logging.CRITICAL)
    scene = trimesh.load(path, force="scene", process=False)
    out = []
    for node in scene.graph.nodes_geometry:
        T, gname = scene.graph[node]
        g = scene.geometry[gname]
        if not hasattr(g, "faces") or len(g.faces) == 0:
            continue
        m = trimesh.Trimesh(vertices=g.vertices, faces=g.faces, process=False)
        m.apply_transform(T)
        name = re.sub(r"_[0-9a-f]{6}$", "", str(node))
        out.append((name.removeprefix(strip) if strip else name, m))
    return out


def group_nodes(meshes, groups) -> dict[str, list]:
    pats = [(g["id"], [re.compile(p) for p in g["match"]]) for g in groups]
    out = {g["id"]: [] for g in groups}
    loose = []
    for name, m in meshes:
        gid = next((gid for gid, ps in pats if any(p.search(name) for p in ps)), None)
        (out[gid].append(m) if gid else loose.append((name, m)))
    centres = {k: np.concatenate([m.vertices for m in v]).mean(0) for k, v in out.items() if v}
    for name, m in loose:                                              # nothing matched: nearest named group
        c = m.vertices.mean(0)
        gid = min(centres, key=lambda k: float(np.linalg.norm(centres[k] - c)))
        out[gid].append(m)
    return {k: v for k, v in out.items() if v}, len(loose)


def feature_edges(mesh, angle_deg: float) -> np.ndarray:
    """Feature edges (dihedral angle above angle_deg) and open boundary edges, as (N, 2, 3) segments."""
    import trimesh
    m = mesh.copy()
    m.merge_vertices(merge_tex=True, merge_norm=True)
    m.remove_unreferenced_vertices()
    if len(m.faces) > MAX_FACES_PER_GROUP:
        try:
            m = m.simplify_quadric_decimation(face_count=MAX_FACES_PER_GROUP)
        except Exception as e:  # noqa: BLE001
            print(f"    (decimation skipped: {e})")
    keep = []
    if len(m.face_adjacency):
        sharp = m.face_adjacency_angles > np.radians(angle_deg)
        keep.append(m.face_adjacency_edges[sharp])
    once = trimesh.grouping.group_rows(m.edges_sorted, require_count=1)
    if len(once):
        keep.append(m.edges_sorted[once])
    if not keep:
        return np.zeros((0, 2, 3), np.float32)
    e = np.unique(np.sort(np.concatenate(keep), axis=1), axis=0)
    return m.vertices[e].astype(np.float32)


def coarse_wire(mesh, faces: int) -> np.ndarray:
    """A light wireframe of the part's overall shape: the folds of a heavily simplified copy (at 10 degrees),
    plus its open edges. Smooth parts (tubes, shells, blades) have almost no feature edges at full detail, so
    without this they would nearly vanish; flat panels add nothing, because their diagonals do not fold."""
    import trimesh
    m = mesh.copy()
    m.merge_vertices(merge_tex=True, merge_norm=True)
    if len(m.faces) > faces:
        try:
            m = m.simplify_quadric_decimation(face_count=max(8, faces))
        except Exception:  # noqa: BLE001
            pass
    keep = []
    if len(m.face_adjacency):
        keep.append(m.face_adjacency_edges[m.face_adjacency_angles > np.radians(10)])
    once = trimesh.grouping.group_rows(m.edges_sorted, require_count=1)
    if len(once):
        keep.append(m.edges_sorted[once])
    if not keep:
        return np.zeros((0, 2, 3), np.float32)
    e = np.unique(np.sort(np.concatenate(keep), axis=1), axis=0)
    return m.vertices[e].astype(np.float32)


def build(rec: dict) -> dict:
    print(f"== {rec['id']}: {rec['label']}")
    src = fetch(rec)
    meshes = world_meshes(src, rec.get("strip_prefix", ""))
    grouped, loose = group_nodes(meshes, rec["groups"])
    allv = np.concatenate([m.vertices for ms in grouped.values() for m in ms])
    lo, hi = allv.min(0), allv.max(0)
    centre, scale = (lo + hi) / 2, float((hi - lo).max())
    budget = int(rec.get("budget", 24000))
    wire_faces = int(rec.get("wire_faces", 2400))
    angle = float(rec.get("angle", 32))
    import trimesh
    merged_by = {gid: trimesh.util.concatenate(ms) for gid, ms in grouped.items()}
    area = {gid: float(m.area) for gid, m in merged_by.items()}
    total_area = sum(area.values()) or 1.0
    wires = {}
    for gid, m in merged_by.items():
        faces = int(np.clip(area[gid] / total_area * wire_faces, 24, 700))
        w = (coarse_wire(m, faces) - centre) / scale
        ln = np.linalg.norm(w[:, 1] - w[:, 0], axis=1)
        keep = ln > 0.002
        w, ln = w[keep], ln[keep]
        cap = int(faces * 1.6)                                         # decimation cannot always get there
        if len(w) > cap:
            w = w[np.argsort(ln)[::-1][:cap]]
        wires[gid] = w
    while True:
        segs = {}
        for gid, merged in merged_by.items():
            s = (feature_edges(merged, angle) - centre) / scale
            length = np.linalg.norm(s[:, 1] - s[:, 0], axis=1)
            segs[gid] = s[length > 0.0015]                             # drop dust (under 0.15% of the model)
        total = sum(len(v) for v in segs.values())
        if total <= budget or angle >= 70:
            break
        angle += 8
    if total > budget:                                                 # still too many: keep the longest
        lens = np.concatenate([np.linalg.norm(v[:, 1] - v[:, 0], axis=1) for v in segs.values()])
        cut = np.sort(lens)[::-1][budget - 1]
        segs = {k: v[np.linalg.norm(v[:, 1] - v[:, 0], axis=1) >= cut] for k, v in segs.items()}
    out = OUT / rec["id"]
    out.mkdir(parents=True, exist_ok=True)
    arrays = {k: v.astype(np.float16) for k, v in segs.items()}
    arrays.update({f"{k}~wire": v.astype(np.float16) for k, v in wires.items()})
    np.savez_compressed(out / "edges.npz", **arrays)
    labels = {g["id"]: g for g in rec["groups"]}
    model = {
        "format": FORMAT, "id": rec["id"], "label": rec["label"], "kind": rec.get("kind", ""),
        "licence": rec["licence"], "credit": rec["credit"], "source_url": rec["url"],
        "source_sha256": sha256(src), "units": "model (longest side = 1.0)",
        "notes": f"Feature edges at {angle:.0f} degrees; {sum(len(v) for v in segs.values())} segments plus "
                 f"{sum(len(v) for v in wires.values())} light wireframe lines; "
                 f"{loose} unnamed nodes joined their nearest part.",
        "parts": [{"id": gid, "label": labels[gid]["label"], "role": labels[gid]["role"],
                   "explode": [float(x) for x in labels[gid].get("explode", [0, 0, 0])]}
                  for gid in (g["id"] for g in rec["groups"]) if gid in segs and len(segs[gid]) + len(wires[gid])],
    }
    (out / "model.yaml").write_text("# Generated by tools/atlas_import.py. Do not edit by hand.\n"
                                    + yaml.safe_dump(model, sort_keys=False, allow_unicode=True, width=120))
    print(f"   {len(model['parts'])} parts, {sum(len(v) for v in segs.values())} segments "
          f"+ {sum(len(v) for v in wires.values())} wire, angle {angle:.0f}, "
          f"{loose} loose nodes, {(out / 'edges.npz').stat().st_size // 1024} KB")
    return {"id": rec["id"], "label": rec["label"], "licence": rec["licence"], "credit": rec["credit"],
            "files": {f"{rec['id']}/model.yaml": sha256(out / "model.yaml"),
                      f"{rec['id']}/edges.npz": sha256(out / "edges.npz")}}


def write_manifest(entries: list[dict]):
    old = {}
    mf = OUT / "library.yaml"
    if mf.exists():
        old = {e["id"]: e for e in (yaml.safe_load(mf.read_text()) or {}).get("models", [])}
    for e in entries:
        old[e["id"]] = e
    order = [r["id"] for r in yaml.safe_load(RECIPES.read_text())["models"]]
    models = [old[i] for i in order if i in old]
    mf.write_text("# ATLAS library manifest. Written by tools/atlas_import.py; the app refuses any file whose\n"
                  "# hash does not match. Credits and licences: NOTICE.md.\n"
                  + yaml.safe_dump({"format": FORMAT, "models": models}, sort_keys=False, width=120))
    notice = ["# ATLAS library: credits and licences", "",
              "These line drawings were made from the 3D models below by `tools/atlas_import.py`. Only edges were",
              "kept; textures, materials and logos were dropped. JAMES is not endorsed by NASA, Khronos or any",
              "model author.", ""]
    recipes = {r["id"]: r for r in yaml.safe_load(RECIPES.read_text())["models"]}
    for m in models:
        src = recipes.get(m["id"], {}).get("url", "")
        notice += [f"- **{m['label']}**: {m['credit'].rstrip('.')}. Licence: {m['licence']}. Source: {src}"]
    notice += ["", "CC BY 4.0: https://creativecommons.org/licenses/by/4.0/  ·  CC0 1.0: "
               "https://creativecommons.org/publicdomain/zero/1.0/  ·  NASA media guidelines: "
               "https://www.nasa.gov/nasa-brand-center/images-and-media/"]
    (OUT / "NOTICE.md").write_text("\n".join(notice) + "\n")


def main(argv):
    recipes = yaml.safe_load(RECIPES.read_text())["models"]
    want = set(argv)
    entries = [build(r) for r in recipes if not want or r["id"] in want]
    write_manifest(entries)
    print("library.yaml and NOTICE.md written")


if __name__ == "__main__":
    main(sys.argv[1:])
