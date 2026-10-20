"""Equipment models for ATLAS (the exploded view). Format james-model/0.1, shipped inside an industry pack.

A model is a list of named parts. Each part is built from a few simple shapes (cylinder, box,
impeller) and has an explode offset, the WARDEN hazard tag it carries (the same tags the pack's
rules use), and plain words that link it to manual steps. That is enough for an honest SCHEMATIC
of a machine: where each part sits and which rule guards it. It is not OEM CAD, and the HUD says so.

Geometry is returned as line segments (N, 2, 3) in millimetres. No rendering here and no AI:
the loader validates it with the rest of the pack and fails closed, like every other pack file.
"""
from __future__ import annotations

import math
import re
from typing import Annotated, Literal, Optional

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

FORMAT = "james-model/0.1"
Vec3 = tuple[float, float, float]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class Shape(_Strict):
    kind: Literal["cylinder", "box", "impeller"]
    at: Vec3 = (0.0, 0.0, 0.0)           # centre, mm
    axis: Literal["x", "y", "z"] = "x"    # cylinder / impeller axis
    radius: Optional[float] = Field(default=None, gt=0)
    radius2: Optional[float] = Field(default=None, gt=0)   # cylinder end radius (a cone if it differs)
    length: Optional[float] = Field(default=None, gt=0)    # along the axis
    size: Optional[Vec3] = None           # box: x, y, z
    segments: int = Field(default=28, ge=6, le=96)         # points per ring
    lines: int = Field(default=8, ge=0, le=48)             # lines along a cylinder
    rings: int = Field(default=2, ge=2, le=12)             # rings along a cylinder
    vanes: int = Field(default=6, ge=2, le=16)             # impeller

    @model_validator(mode="after")
    def _fields_for_kind(self):
        need = {"cylinder": ("radius", "length"), "impeller": ("radius", "length"), "box": ("size",)}[self.kind]
        missing = [f for f in need if getattr(self, f) is None]
        if missing:
            raise ValueError(f"{self.kind} needs {', '.join(missing)}")
        if self.kind == "box" and min(self.size) <= 0:
            raise ValueError("box size must be positive")
        return self


class Part(_Strict):
    id: str = Field(pattern=r"^[a-z0-9]+(_[a-z0-9]+)*$")
    label: str = Field(min_length=1)
    role: str = Field(min_length=3)       # one line: what it does, shown on the part card
    hazard: Optional[str] = None          # a hazard tag from the rules file (e.g. coupling_guard), or none
    keywords: list[Annotated[str, Field(min_length=2)]] = []   # extra words that link a manual step to this part (e.g. "motor")
    manual_section: Optional[str] = None
    explode: Vec3 = (0.0, 0.0, 0.0)       # where the part moves when the view is exploded, mm
    shapes: list[Shape] = Field(min_length=1)


class Model(_Strict):
    format: str
    class_: str = Field(alias="class")
    label: str
    source: str = Field(min_length=3)     # who drew it and from what; demo models say they are not OEM CAD
    units: Literal["mm"] = "mm"
    parts: list[Part] = Field(min_length=1)


def parse(raw, hazard_tags: Optional[set] = None, class_id: Optional[str] = None) -> tuple[Optional[Model], list[str]]:
    """Validate a model mapping. Returns (model, problems); model is None if there is any problem."""
    problems: list[str] = []
    if not isinstance(raw, dict):
        return None, ["model file must be a mapping"]
    try:
        m = Model.model_validate(raw)
    except ValidationError as e:
        for err in e.errors():
            loc = ".".join(str(x) for x in err["loc"])
            problems.append(f"{loc}: {err['msg']}")
        return None, problems
    if m.format != FORMAT:
        problems.append(f"format '{m.format}' is not supported (this runtime reads {FORMAT})")
    if class_id is not None and m.class_ != class_id:
        problems.append(f"model is for class '{m.class_}' but is listed under '{class_id}'")
    seen = set()
    for p in m.parts:
        if p.id in seen:
            problems.append(f"part '{p.id}' is listed twice")
        seen.add(p.id)
        if hazard_tags is not None and p.hazard is not None and p.hazard not in hazard_tags:
            # a typo here would silently drop the red WARDEN highlight, so it is refused
            problems.append(f"part '{p.id}': hazard '{p.hazard}' is not a hazard tag in the rules file")
    return (None if problems else m), problems


# ---------------- geometry: shapes -> line segments (N, 2, 3) ----------------

def _place(local: np.ndarray, axis: str, at) -> np.ndarray:
    """local points are (a, u, v): a along the axis, u/v across it."""
    a, u, v = local[..., 0], local[..., 1], local[..., 2]
    xyz = {"x": (a, u, v), "y": (u, a, v), "z": (u, v, a)}[axis]
    return np.stack(xyz, axis=-1) + np.asarray(at, dtype=float)


def _ring(a: float, r: float, n: int) -> np.ndarray:
    th = np.linspace(0, 2 * math.pi, n + 1)
    pts = np.stack([np.full_like(th, a), r * np.cos(th), r * np.sin(th)], axis=-1)
    return np.stack([pts[:-1], pts[1:]], axis=1)                 # (n, 2, 3)


def _cylinder(s: Shape) -> np.ndarray:
    r1, r2, L = s.radius, s.radius2 or s.radius, s.length
    segs = []
    for i in range(s.rings):
        f = i / (s.rings - 1)
        segs.append(_ring(-L / 2 + f * L, r1 + (r2 - r1) * f, s.segments))
    for k in range(s.lines):
        th = 2 * math.pi * k / s.lines
        segs.append(np.array([[[-L / 2, r1 * math.cos(th), r1 * math.sin(th)],
                               [L / 2, r2 * math.cos(th), r2 * math.sin(th)]]]))
    return _place(np.concatenate(segs), s.axis, s.at)


def _box(s: Shape) -> np.ndarray:
    hx, hy, hz = (d / 2 for d in s.size)
    c = np.array([[x, y, z] for x in (-hx, hx) for y in (-hy, hy) for z in (-hz, hz)])
    edges = [(i, j) for i in range(8) for j in range(i + 1, 8) if bin(i ^ j).count("1") == 1]
    return np.array([[c[i], c[j]] for i, j in edges]) + np.asarray(s.at, dtype=float)


def _impeller(s: Shape) -> np.ndarray:
    r, L = s.radius, s.length
    hub = r * 0.28
    segs = [_ring(-L / 2, r, s.segments), _ring(L / 2, r, s.segments), _ring(0, hub, max(8, s.segments // 2))]
    for k in range(s.vanes):                                     # backward-curved vanes, hub to rim
        th0 = 2 * math.pi * k / s.vanes
        n = 7
        rr = np.linspace(hub, r, n)
        th = th0 + np.linspace(0, math.radians(68), n)
        pts = np.stack([np.zeros(n), rr * np.cos(th), rr * np.sin(th)], axis=-1)
        segs.append(np.stack([pts[:-1], pts[1:]], axis=1))
    return _place(np.concatenate(segs), s.axis, s.at)


_BUILD = {"cylinder": _cylinder, "box": _box, "impeller": _impeller}


def shape_segments(s: Shape) -> np.ndarray:
    return _BUILD[s.kind](s).astype(np.float32)


def part_segments(p: Part) -> np.ndarray:
    return np.concatenate([shape_segments(s) for s in p.shapes]).astype(np.float32)


# ---------------- linking: manual step -> parts (deterministic, no AI) ----------------

def _keyword_spans(model: Model, text: str) -> list[tuple[int, int, str]]:
    spans = []
    for p in model.parts:
        for k in p.keywords:
            for m in re.finditer(rf"(?<![a-z]){re.escape(k.lower())}(?![a-z])", text):
                spans.append((m.start(), m.end(), p.id))
    # longest match wins: "coupling guard" names the guard, so the "coupling" inside it does not
    return [a for a in spans if not any(b is not a and b[0] <= a[0] and b[1] >= a[1] and b[1] - b[0] > a[1] - a[0]
                                        for b in spans)]


def parts_for_step(model: Model, step_text: str, touches=()) -> list[str]:
    """Part ids a manual step refers to. Deterministic, no AI:
    1. each WARDEN hazard tag on the step selects the parts carrying that tag (narrowed to the ones the
       step names, when several parts share a tag, e.g. rotating_parts);
    2. then any part named by one of its own keywords (longest match wins)."""
    text = (step_text or "").lower()
    named = list(dict.fromkeys(pid for *_, pid in sorted(_keyword_spans(model, text))))
    hits: list[str] = []
    for h in dict.fromkeys(touches or ()):
        cands = [p.id for p in model.parts if p.hazard == h]
        narrowed = [c for c in cands if c in named]
        hits += [c for c in (narrowed or cands) if c not in hits]
    hits += [n for n in named if n not in hits]
    return hits
