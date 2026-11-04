"""The retrieval layer both RAG variants share: chunking + search.

    corpus/*.md ──chunk by "## " section──▶ chunks ("doc-id#n": title, section, text)
                                               │
              query ──▶ BM25 keyword ranking ──┤
                        (+ embeddings, when    ├──▶ top-k chunks
                         EMBEDDING_MODEL set)  │
                        fused by rank (RRF) ───┘

The variants differ ONLY in how they use this layer (one search with the
user's words vs. an agent deciding what to search), never in the layer
itself — that keeps the comparison fair.

Search backends:
  * BM25 (default, no API key): classic keyword ranking. Rewards rare words
    that appear in both query and chunk; knows nothing about synonyms.
  * HYBRID (set EMBEDDING_MODEL): BM25 + embedding similarity, merged with
    Reciprocal Rank Fusion. Embeddings catch paraphrases ("time off" ≈
    "PTO"); BM25 catches exact names and codes. Industry default.
"""

import asyncio
import logging
import math
import os
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from app.core.config import MOCK_MODELS, load_model
from app.core.llm import embed

log = logging.getLogger(__name__)

CORPUS_DIR = Path(__file__).parent / "corpus"
TOP_K = max(1, int(os.getenv("RAG_TOP_K", "4")))
MAX_SECTION_CHARS = 900  # longer sections are split on paragraph breaks


@dataclass(frozen=True)
class Chunk:
    id: str        # "<doc-id>#<n>" — the citation handle models use
    doc_id: str
    title: str     # document title (its "# " line)
    section: str   # "## " heading, or "" for the intro
    text: str


@dataclass(frozen=True)
class Hit:
    chunk: Chunk
    score: float


# ── Corpus + chunking ───────────────────────────────────────────────────────


def load_docs() -> dict[str, str]:
    """doc id (file stem) → markdown text."""
    return {p.stem: p.read_text() for p in sorted(CORPUS_DIR.glob("*.md"))}


def doc_title(text: str) -> str:
    first = text.strip().splitlines()[0]
    return first.lstrip("# ").strip()


def chunk_doc(doc_id: str, text: str) -> list[Chunk]:
    """One chunk per "## " section (split further if long). Each chunk is
    searchable on its own, so its text is prefixed with title + section."""
    title = doc_title(text)
    body = text.strip().split("\n", 1)[1] if "\n" in text.strip() else ""
    sections: list[tuple[str, str]] = []
    heading, lines = "", []
    for line in body.splitlines():
        if line.startswith("## "):
            if "".join(lines).strip():
                sections.append((heading, "\n".join(lines).strip()))
            heading, lines = line[3:].strip(), []
        else:
            lines.append(line)
    if "".join(lines).strip():
        sections.append((heading, "\n".join(lines).strip()))

    chunks: list[Chunk] = []
    for heading, section_text in sections:
        for piece in _split_long(section_text):
            chunks.append(Chunk(f"{doc_id}#{len(chunks) + 1}", doc_id, title, heading, piece))
    return chunks


def _split_long(text: str) -> list[str]:
    if len(text) <= MAX_SECTION_CHARS:
        return [text]
    pieces, current = [], ""
    for para in text.split("\n\n"):
        if current and len(current) + len(para) > MAX_SECTION_CHARS:
            pieces.append(current.strip())
            current = ""
        current += para + "\n\n"
    if current.strip():
        pieces.append(current.strip())
    return pieces


# ── BM25 ────────────────────────────────────────────────────────────────────

STOPWORDS = set(
    "a an and are as at be by for from has have how i in is it its of on or our "
    "the this to was we what when where which who why will with does do did can "
    "you your there their they them than then that these those into about".split()
)


def tokenize(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    # Crude plural folding ("seats" → "seat"); a real system would stem.
    return [w[:-1] if len(w) > 3 and w.endswith("s") else w for w in words if w not in STOPWORDS]


class Index:
    """BM25 over all chunks, plus lazily computed embeddings when configured."""

    K1, B = 1.5, 0.75  # standard BM25 constants: term-frequency saturation, length normalization

    def __init__(self, chunks: list[Chunk]) -> None:
        self.chunks = chunks
        self.by_id = {c.id: c for c in chunks}
        self._tokens = [tokenize(f"{c.title} {c.section} {c.text}") for c in chunks]
        self._tf = [Counter(t) for t in self._tokens]
        self._avg_len = sum(map(len, self._tokens)) / max(1, len(self._tokens))
        df = Counter(term for toks in self._tokens for term in set(toks))
        n = len(chunks)
        self._idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
        self.embedding_model = None if MOCK_MODELS else load_model("EMBEDDING_MODEL")
        self._vectors: list[list[float]] | None = None
        self._lock = asyncio.Lock()

    @property
    def backend(self) -> str:
        if self.embedding_model:
            return f"hybrid: BM25 + {self.embedding_model.label} (RRF)"
        return "BM25 keyword search"

    def bm25(self, query: str) -> list[tuple[int, float]]:
        q = tokenize(query)
        scores = []
        for i, tf in enumerate(self._tf):
            length = len(self._tokens[i])
            s = 0.0
            for term in q:
                if term in tf:
                    f = tf[term]
                    s += self._idf[term] * f * (self.K1 + 1) / (
                        f + self.K1 * (1 - self.B + self.B * length / self._avg_len)
                    )
            if s > 0:
                scores.append((i, s))
        return sorted(scores, key=lambda x: -x[1])

    async def _dense(self, query: str) -> list[tuple[int, float]]:
        async with self._lock:  # embed the corpus once, on first use
            if self._vectors is None:
                texts = [f"{c.title} — {c.section}\n{c.text}" for c in self.chunks]
                self._vectors = await embed(self.embedding_model, texts)
        [qv] = await embed(self.embedding_model, [query])
        sims = [(i, _cosine(qv, v)) for i, v in enumerate(self._vectors)]
        return sorted(sims, key=lambda x: -x[1])

    async def search(self, query: str, k: int = TOP_K) -> list[Hit]:
        sparse = self.bm25(query)
        if not self.embedding_model:
            return [Hit(self.chunks[i], round(s, 2)) for i, s in sparse[:k]]
        try:
            dense = await self._dense(query)
        except Exception as exc:  # embedding provider down → keyword search still works
            log.warning("embedding search failed, using BM25 only: %s", exc)
            return [Hit(self.chunks[i], round(s, 2)) for i, s in sparse[:k]]
        # Reciprocal Rank Fusion: combine RANKS, not raw scores (BM25 and
        # cosine scores live on different scales). 60 is the customary damping.
        fused: Counter = Counter()
        for ranking in (sparse, dense[: max(20, k)]):
            for rank, (i, _) in enumerate(ranking):
                fused[i] += 1 / (60 + rank + 1)
        return [Hit(self.chunks[i], round(s * 100, 2)) for i, s in fused.most_common(k)]


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


_index: Index | None = None


def get_index() -> Index:
    global _index
    if _index is None:
        chunks = [c for doc_id, text in load_docs().items() for c in chunk_doc(doc_id, text)]
        _index = Index(chunks)
    return _index


# ── Formatting for models ───────────────────────────────────────────────────


def format_hits(hits: list[Hit]) -> str:
    """Search results as the model sees them: chunk id first, so it can cite."""
    if not hits:
        return "No matching passages."
    return "\n\n".join(
        f"[{h.chunk.id}] {h.chunk.title}{' › ' + h.chunk.section if h.chunk.section else ''}\n{h.chunk.text}"
        for h in hits
    )


def format_doc(doc_id: str) -> str | None:
    """A whole document, section by section, labelled with chunk ids."""
    chunks = [c for c in get_index().chunks if c.doc_id == doc_id]
    if not chunks:
        return None
    return f"# {chunks[0].title}\n\n" + "\n\n".join(
        f"[{c.id}]{' ' + c.section if c.section else ''}\n{c.text}" for c in chunks
    )


def hit_summary(h: Hit) -> dict:
    """A hit as sent to the UI."""
    text = " ".join(h.chunk.text.split())
    return {
        "id": h.chunk.id, "doc_id": h.chunk.doc_id, "title": h.chunk.title,
        "section": h.chunk.section, "score": h.score,
        "snippet": text if len(text) <= 220 else text[:220] + "…",
    }
