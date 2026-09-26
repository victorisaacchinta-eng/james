"""PAGE: finds and cites the manual.

Hybrid search: BM25 for exact words (part numbers) plus meaning search with local
embeddings (nomic-embed-text) stored in Qdrant's in-process mode. If embeddings are not
available it runs on BM25 alone and says so. If the first match is weak it rewrites the
query and searches again. Every result carries its page number."""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

import config
from agents import llm
from james_core.schemas import AgentId, Evidence, EvidenceKind

# Fault vocabulary comes from the industry pack (faults/<class>.yaml), not the code.
_FAULTS = config.PACK.faults(config.PRIMARY_CLASS)
SYNONYMS = dict(_FAULTS.symptom_words)
CAUSES = tuple(_FAULTS.causes)
CAUSE_WORDS = {c.label: cid for cid, c in _FAULTS.causes.items()}
SECTION_FOR = {cid: c.manual_section for cid, c in _FAULTS.causes.items()}


def tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9][a-z0-9\-\.]*[a-z0-9]|[a-z0-9]", text.lower())


@dataclass
class Chunk:
    page: int
    title: str
    text: str


@dataclass
class Step:
    number: int
    text: str
    page: int
    section: str


class ManualIndex:
    def __init__(self, path=None, use_embeddings: bool = True):
        """path=None: the active pack's manual, from the bytes the pack verified (never re-read from disk)."""
        import pymupdf
        if path is None:
            self.name, data = config.PACK.manual_bytes(config.PRIMARY_CLASS)
            opened = pymupdf.open(stream=data, filetype="pdf")
        else:
            self.name, opened = path.name, pymupdf.open(path)
        self.chunks: list[Chunk] = []
        self.pages: dict[int, str] = {}
        with opened as doc:
            for i, page in enumerate(doc, 1):
                text = page.get_text()
                self.pages[i] = text
                lines = [l for l in text.splitlines() if l.strip() and not l.startswith("SAMPLE -")
                         and not re.fullmatch(r"p\. \d+", l.strip())]
                title = lines[0] if lines else f"page {i}"
                body = "\n".join(lines[1:])
                for para in re.split(r"\n(?=Symptom:|Step 1:|If the)", body):
                    if para.strip():
                        self.chunks.append(Chunk(i, title, para.strip()))
        self._bm25_init()
        self.backend = "BM25"
        self._qdrant = None
        if use_embeddings:
            self._embed_init()

    # ---- BM25 ----
    def _bm25_init(self, k1=1.5, b=0.75):
        self.k1, self.b = k1, b
        self.docs = [tokens(c.title + " " + c.text) for c in self.chunks]
        self.avgdl = sum(map(len, self.docs)) / len(self.docs)
        df = Counter(t for d in self.docs for t in set(d))
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def _bm25(self, q: list[str]) -> list[float]:
        out = []
        for d in self.docs:
            tf = Counter(d)
            s = 0.0
            for t in q:
                if t in tf:
                    s += self.idf[t] * tf[t] * (self.k1 + 1) / (tf[t] + self.k1 * (1 - self.b + self.b * len(d) / self.avgdl))
            out.append(s)
        return out

    # ---- embeddings in Qdrant (in-process) ----
    def _embed_init(self):
        try:
            vecs = llm.embed([c.title + ". " + c.text for c in self.chunks])
            from qdrant_client import QdrantClient
            from qdrant_client.models import Distance, PointStruct, VectorParams
            q = QdrantClient(":memory:")
            q.create_collection("manual", vectors_config=VectorParams(size=len(vecs[0]), distance=Distance.COSINE))
            q.upsert("manual", points=[PointStruct(id=i, vector=v) for i, v in enumerate(vecs)])
            self._qdrant = q
            self.backend = "BM25 + nomic-embed-text in Qdrant"
        except Exception as e:  # noqa: BLE001  graceful degradation, shown on the HUD
            self.backend = f"BM25 only ({type(e).__name__})"

    def _dense(self, query: str) -> list[float]:
        if not self._qdrant:
            return [0.0] * len(self.chunks)
        try:
            v = llm.embed([query])[0]
            try:
                hits = self._qdrant.query_points("manual", query=v, limit=len(self.chunks)).points
            except AttributeError:
                hits = self._qdrant.search("manual", query_vector=v, limit=len(self.chunks))
            s = [0.0] * len(self.chunks)
            for h in hits:
                s[h.id] = max(0.0, float(h.score))
            return s
        except Exception:
            return [0.0] * len(self.chunks)

    def search(self, query: str, k: int = 3) -> list[tuple[float, Chunk]]:
        q = [SYNONYMS.get(t, t) for t in tokens(query)]
        bm = self._bm25(q)
        top = max(bm) or 1.0
        dense = self._dense(query)
        scores = [0.6 * (x / top) + 0.4 * d if self._qdrant else x / top for x, d in zip(bm, dense)]
        conf = [min(0.99, 0.35 + 0.1 * x) for x in bm]  # absolute match strength, not relative
        ranked = sorted(range(len(scores)), key=lambda i: -scores[i])[:k]
        return [(conf[i], self.chunks[i]) for i in ranked if bm[i] > 0 or dense[i] > 0]

    # ---- what agents ask PAGE for ----
    def troubleshoot(self, symptoms: tuple[str, ...], transcript: str, asset_id: str, query: str | None = None,
                     retry: bool = True) -> tuple[list[Evidence], list[str]]:
        """Return the troubleshooting evidence and a note per search attempt.
        query: a scoped query chosen by FOREMAN; retry=False: one search only (FOREMAN decides what comes next)."""
        notes = []
        query = query or (" ".join(symptoms) + " " + transcript + " symptom likely cause")
        hits = self.search(query, k=6)
        notes.append(f"search: {query.strip()[:60]}")
        if retry and (not hits or hits[0][0] < 0.6):
            rewritten = self._rewrite(query)
            notes.append(f"weak match, search 2: {rewritten[:60]}")
            hits = self.search(rewritten, k=6)
        rows = [(c, s) for s, c in hits if "Likely cause" in c.text]
        wanted = {SYNONYMS.get(t, t) for t in symptoms}
        focused = [(c, s) for c, s in rows if any(w in c.text.lower() for w in wanted)]
        weak = bool(wanted) and not focused and bool(rows)       # rows match 'pump', not the symptom (ST-09)
        rows = focused or rows
        if not rows:
            return [], notes
        if weak:
            notes.append(f"no troubleshooting row mentions {', '.join(sorted(wanted))}")
        page = rows[0][0].page
        causes = []
        for c, _ in rows:
            m = re.search(r"Likely cause: ([a-z ]+)\.", c.text)
            if m:
                causes.append(m.group(1).strip())
        claim = "Troubleshooting: " + "; ".join(dict.fromkeys(causes))
        conf = min(rows[0][1], 0.35) if weak else rows[0][1]
        if weak:
            claim = f"No row for {', '.join(sorted(wanted))}; the table lists: " + "; ".join(dict.fromkeys(causes))
        return [Evidence(agent=AgentId.PAGE, kind=EvidenceKind.MANUAL, asset_id=asset_id, claim=claim,
                         ref=f"manual:{self.name}#p{page}", confidence=round(conf, 2))], notes

    def check_cause(self, cause: str, asset_id: str) -> Evidence | None:
        """Does the manual's troubleshooting table name this cause, and for which symptom? (FOREMAN asks this
        when a sensor or a past job points at a cause, before it is trusted.) None if the manual never says so."""
        label = next((c.label for cid, c in _FAULTS.causes.items() if cid == cause), None)
        if not label:
            return None
        for ch in self.chunks:
            # a troubleshooting ROW only: the chunk starts with "Symptom:" and a line starts "Likely cause: <label>."
            # (not "...is not the likely cause: ...", not an example sentence elsewhere)
            if ch.text.startswith("Symptom:") and re.search(rf"^Likely cause: {re.escape(label)}\.", ch.text, re.M):
                symptom = ch.text.split("\n")[0].replace("Symptom:", "").strip().rstrip(".")
                return Evidence(agent=AgentId.PAGE, kind=EvidenceKind.MANUAL, asset_id=asset_id,
                                claim=f"Manual: '{symptom}' means likely {label}", ref=f"manual:{self.name}#p{ch.page}",
                                confidence=0.9, topic="likely_cause", stance=cause)
        return None

    def step_on_page(self, text: str, page: int) -> bool:
        """The cited page really contains this step (iteration 2, ST-09: a citation must support the step)."""
        norm = lambda t: " ".join(t.split()).lower()
        return norm(text) in norm(self.pages.get(page, ""))

    def _rewrite(self, query: str) -> str:
        words = " ".join(SYNONYMS.get(t, t) for t in tokens(query))
        try:
            out = llm.chat_json("Rewrite the maintenance search query using technical terms. "
                                "Reply JSON {\"query\": str}.", query)
            return words + " " + str(out.get("query", ""))
        except llm.LLMUnavailable:
            return words

    def procedure(self, cause: str, asset_id: str) -> tuple[Evidence | None, list[Step]]:
        section = SECTION_FOR.get(cause)
        if not section:
            return None, []
        for page, text in self.pages.items():
            if re.search(rf"^{re.escape(section)} ", text, re.M):
                # only numbered step LINES of this section become steps, word for word (qualifiers kept);
                # a sentence elsewhere on the page ("Do not ...", "see Step 3") never becomes a step
                steps = [Step(int(n), t.strip(), page, section)
                         for n, t in re.findall(r"^Step (\d+): (.+)$", text, re.M)]
                title = next(l for l in text.splitlines() if l.startswith(section))
                ev = Evidence(agent=AgentId.PAGE, kind=EvidenceKind.MANUAL, asset_id=asset_id,
                              claim=f"Procedure {title} ({len(steps)} steps)",
                              ref=f"manual:{self.name}#p{page}", confidence=0.95)
                return ev, steps
        return None, []
