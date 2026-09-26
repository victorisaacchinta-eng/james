"""PAGE: versioned Markdown documents, stable section citations, the step catalog, and
keyword retrieval filtered by asset and category. No AI; document text is data, never
instructions (nothing here can change a rule, a permission or a prompt)."""
from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from .schemas import Evidence

SECTION_RE = re.compile(r"^## (.+?) \{#([A-Z0-9-]+)\}\s*$", re.M)
STEP_RE = re.compile(r"^- \*\*([A-Z]+-\d+)\*\* (.+)$", re.M)
STOP = set("a an the and or of to in on for is are be with by as it at from this that what why how i my "
           "me we our should do does next check more than usual demo".split())


def tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in STOP]


@dataclass(frozen=True)
class Section:
    doc_id: str
    version: str
    sha256: str
    section_id: str
    title: str
    text: str

    @property
    def evidence_id(self) -> str:
        return f"doc-{self.doc_id}-{self.version}#{self.section_id}"


@dataclass
class Document:
    doc_id: str
    version: str
    title: str
    label: str
    sha256: str
    applies_to_category: Optional[str]
    applies_to_asset: Optional[str]
    sections: dict[str, Section] = field(default_factory=dict)
    source: str = ""                  # pack:<id> or site


def parse_document(path: Path, source: str, raw: bytes | None = None) -> Document:
    """raw: the bytes a pack already verified (pass them; the file is then not re-read)."""
    raw = path.read_bytes() if raw is None else raw
    text = raw.decode("utf-8")
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not m:
        raise ValueError(f"{path.name}: missing front matter")
    meta = yaml.safe_load(m.group(1))
    body = text[m.end():]
    doc = Document(doc_id=meta["doc_id"], version=meta["version"], title=meta["title"], label=meta["label"],
                   sha256=hashlib.sha256(raw).hexdigest(), applies_to_category=meta.get("applies_to_category"),
                   applies_to_asset=meta.get("applies_to_asset"), source=source)
    heads = list(SECTION_RE.finditer(body))
    for i, h in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(body)
        sid = h.group(2)
        if sid in doc.sections:
            raise ValueError(f"{path.name}: section id {sid} used twice")
        doc.sections[sid] = Section(doc.doc_id, doc.version, doc.sha256, sid, h.group(1).strip(),
                                    body[h.end():end].strip())
    return doc


@dataclass(frozen=True)
class CatalogStep:
    step_id: str
    section_id: str
    kind: str
    touches: tuple[str, ...]
    records: Optional[str]
    text: str
    doc_id: str
    doc_version: str

    @property
    def evidence_id(self) -> str:
        return f"doc-{self.doc_id}-{self.doc_version}#{self.section_id}"


def load_catalog(path: Path, doc: Document, data: bytes | None = None) -> dict[str, CatalogStep]:
    """Step metadata from the pack, step text from the document. They must agree exactly."""
    raw = yaml.safe_load((path.read_bytes() if data is None else data).decode("utf-8"))
    if (raw["procedure"], raw["version"]) != (doc.doc_id, doc.version):
        raise ValueError(f"catalog is for {raw['procedure']} {raw['version']}, document is {doc.doc_id} {doc.version}")
    in_doc: dict[str, tuple[str, str]] = {}
    for sid, sec in doc.sections.items():
        for sm in STEP_RE.finditer(sec.text):
            if sm.group(1) in in_doc:
                raise ValueError(f"step {sm.group(1)} appears twice in {doc.doc_id}")
            in_doc[sm.group(1)] = (sid, sm.group(2).strip())
    out = {}
    for step_id, meta in raw["steps"].items():
        if step_id not in in_doc:
            raise ValueError(f"catalog step {step_id} is not in {doc.doc_id}")
        sid, text = in_doc[step_id]
        if sid != meta["section"]:
            raise ValueError(f"catalog puts {step_id} in {meta['section']}, the document has it in {sid}")
        out[step_id] = CatalogStep(step_id, sid, meta["kind"], tuple(meta.get("touches") or ()),
                                   meta.get("records"), text, doc.doc_id, doc.version)
    missing = set(in_doc) - set(out)
    if missing:
        raise ValueError(f"document steps missing from the catalog: {sorted(missing)}")
    return out


class Library:
    """All documents the Lab can cite, with BM25-style retrieval."""

    def __init__(self, docs: list[Document]):
        self.docs = {d.doc_id: d for d in docs}
        self.sections = [s for d in docs for s in d.sections.values()]
        self._docs_tokens = [tokens(s.title + " " + s.text) for s in self.sections]
        n = len(self._docs_tokens) or 1
        df = Counter(t for d in self._docs_tokens for t in set(d))
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
        self.avgdl = sum(map(len, self._docs_tokens)) / n

    def section(self, evidence_id: str) -> Optional[Section]:
        m = re.match(r"^doc-(.+)-(v\d+)#(.+)$", evidence_id)
        if not m:
            return None
        d = self.docs.get(m.group(1))
        if not d or d.version != m.group(2):
            return None
        return d.sections.get(m.group(3))

    def applicable(self, doc: Document, asset_id: str, category: str) -> bool:
        if doc.applies_to_asset:
            return doc.applies_to_asset == asset_id
        return doc.applies_to_category == category

    def search(self, query: str, asset_id: str, category: str, k: int = 4) -> list[tuple[float, Section]]:
        q = tokens(query)
        out = []
        for toks, sec in zip(self._docs_tokens, self.sections):
            if not self.applicable(self.docs[sec.doc_id], asset_id, category):
                continue                                   # deterministic filter before scoring
            tf = Counter(toks)
            score = 0.0
            for t in q:
                if t in tf:
                    score += self.idf[t] * tf[t] * 2.5 / (tf[t] + 1.5 * (0.25 + 0.75 * len(toks) / self.avgdl))
            if score > 0:
                out.append((round(score, 3), sec))
        out.sort(key=lambda x: (-x[0], x[1].evidence_id))
        return out[:k]


def section_evidence(sec: Section, doc: Document, asset_id: str, job_id: Optional[str]) -> Evidence:
    first = sec.text.splitlines()[0] if sec.text else ""
    first = re.sub(r"^- ", "", first).replace("**", "")
    return Evidence(evidence_id=sec.evidence_id, source_type="document_section", asset_id=asset_id, job_id=job_id,
                    source_id=doc.doc_id, source_version=f"{doc.version}@{doc.sha256[:12]}", locator=sec.section_id,
                    provenance="synthetic_document", summary=f"{doc.doc_id}-{doc.version} {sec.section_id} {sec.title}: {first[:140]}")
