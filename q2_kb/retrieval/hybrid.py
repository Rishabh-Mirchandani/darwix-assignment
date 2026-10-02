"""Hybrid retrieval: BM25 + dense vectors, with a calibrated refusal gate.

**Why hybrid.** Dense retrieval handles paraphrase ("can I claim for my mum?"
-> "parental coverage") but is weak on rare exact tokens; lexical retrieval is
the reverse. Insurance queries contain both: a caller says "is PED covered"
(exact jargon, BM25 wins) and "what if I was already sick when I signed up"
(pure paraphrase, vectors win). Running only one loses a whole class of query.

**Ranking vs confidence are deliberately separated.** This is the key design
decision in the module:

* **Ranking** uses Reciprocal Rank Fusion. RRF combines two rankings without
  needing their scores to be on the same scale, which avoids the usual bug of
  min-max normalising BM25 per query -- that makes the best result score 1.0
  *every time*, including when it is bad, because normalisation is relative to
  the result set rather than absolute.

* **Confidence** uses a cross-encoder relevance score. The first version of
  this module used cosine similarity, on the reasoning that cosine is absolute
  and therefore thresholdable. Measurement killed that idea: on the 20-query
  eval set, out-of-scope questions that are merely insurance-*shaped* ("what is
  my policy balance") scored 0.716, against a lowest legitimate score of 0.767
  -- a margin of 0.04, and three of four refusals leaked through. Cosine
  measures topic proximity, not whether a passage answers the question. A
  cross-encoder reads (query, passage) jointly and separates the same two
  populations by +3.68. The numbers are in `q2_kb/index/schema.py`.

The refusal gate is the main anti-hallucination control: if the best cosine
similarity falls below the category's floor, the caller is told the
information is not available rather than being given the closest-but-wrong
record. The brief requires exactly this ("the bot must state when information
is unavailable instead of inventing an answer"), and it has to be enforced in
retrieval -- a prompt instruction alone is not a control, because the model
never sees that the evidence was weak.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from rank_bm25 import BM25Okapi

from shared.config import settings
from q2_kb.index.schema import KBRecord
from q2_kb.index.vector_store import Embedder, ScoredRecord, VectorStore

# RRF constant. 60 is the value from the original Cormack et al. paper and is
# insensitive to tuning; it damps the influence of any single ranker's top hit.
RRF_K = 60

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


@dataclass
class RetrievalResult:
    """Everything the caller needs to answer *and* to cite."""

    query: str
    results: list[ScoredRecord] = field(default_factory=list)
    confidence: float = 0.0
    threshold: float = 0.0
    grounded: bool = False
    refusal_reason: str | None = None
    timings_ms: dict[str, float] = field(default_factory=dict)

    def citations(self) -> list[str]:
        return [r.record.citation() for r in self.results]

    def to_dict(self) -> dict:
        return {
            "query": self.query,
            "grounded": self.grounded,
            "confidence": round(self.confidence, 4),
            "threshold": round(self.threshold, 4),
            "refusal_reason": self.refusal_reason,
            "timings_ms": {k: round(v, 2) for k, v in self.timings_ms.items()},
            "results": [
                {
                    "rank": r.rank,
                    "record_id": r.record.record_id,
                    "title": r.record.title,
                    "category": r.record.category,
                    "section_kind": r.record.section_kind,
                    "content": r.record.content,
                    "answer_text": r.record.answer_text,
                    "source_url": r.record.source_url,
                    "citation": r.record.citation(),
                    "rerank_score": round(r.rerank_score, 4),
                    "fused_score": round(r.score, 6),
                    "cosine": round(r.vector_score, 4),
                    "bm25": round(r.lexical_score, 4),
                }
                for r in self.results
            ],
        }


class Reranker:
    """Lazy cross-encoder wrapper.

    Loaded on first use so that importing the retriever (e.g. to inspect the
    index) does not pull an 80 MB model into memory.
    """

    def __init__(self, model_name: str | None = None) -> None:
        self.model_name = model_name or settings.reranker_model
        self._model = None

    def _ensure(self) -> None:
        if self._model is None:
            from fastembed.rerank.cross_encoder import TextCrossEncoder

            self._model = TextCrossEncoder(model_name=self.model_name)

    def score(self, query: str, passages: list[str]) -> list[float]:
        if not passages:
            return []
        self._ensure()
        assert self._model is not None
        return [float(s) for s in self._model.rerank(query, passages)]


class HybridRetriever:
    def __init__(
        self,
        store: VectorStore | None = None,
        use_reranker: bool = True,
    ) -> None:
        self.store = store or VectorStore()
        if self.store.matrix is None:
            self.store.load()
        self.embedder = Embedder()
        # `use_reranker=False` exists so the ablation in the write-up can be
        # reproduced, not as a production option -- the gate depends on it.
        self.use_reranker = use_reranker
        self.reranker = Reranker() if use_reranker else None

        # BM25 indexes title + canonical terms + body. Title and terms are
        # included because a chunk's body often omits the words the caller
        # uses -- the heading carries them.
        corpus = [
            tokenize(
                f"{r.title} {' '.join(r.canonical_terms)} {r.content}"
            )
            for r in self.store.records
        ]
        self.bm25 = BM25Okapi(corpus)

    # ---------------- retrieval ----------------

    def search(
        self,
        query: str,
        top_k: int | None = None,
        top_n: int | None = None,
        category: str | None = None,
        market: str | None = None,
    ) -> RetrievalResult:
        top_k = top_k or settings.retrieval_top_k
        top_n = top_n or settings.rerank_top_n
        timings: dict[str, float] = {}

        # --- dense ---
        t0 = time.perf_counter()
        qvec = self.embedder.encode_one(query)
        timings["embed"] = (time.perf_counter() - t0) * 1000

        t0 = time.perf_counter()
        dense = self.store.search(
            qvec, top_k=top_k, category=category, market=market
        )
        timings["vector_search"] = (time.perf_counter() - t0) * 1000

        # --- lexical ---
        t0 = time.perf_counter()
        bm25_scores = self.bm25.get_scores(tokenize(query))
        lexical_idx = sorted(
            range(len(bm25_scores)), key=lambda i: -bm25_scores[i]
        )[: top_k * 2]
        if category or market:
            lexical_idx = [
                i for i in lexical_idx
                if (market is None or self.store.records[i].market == market)
                and (category is None or self.store.records[i].category == category)
            ][:top_k]
        else:
            lexical_idx = lexical_idx[:top_k]
        timings["bm25"] = (time.perf_counter() - t0) * 1000

        # --- fuse ---
        t0 = time.perf_counter()
        fused = self._rrf(dense, lexical_idx, bm25_scores)
        timings["fusion"] = (time.perf_counter() - t0) * 1000

        # --- rerank ---
        # The cross-encoder sees more candidates than we return, so it can
        # promote a record the fusion ranked 7th. Reranking only the final 4
        # would waste most of its value.
        candidates = fused[: settings.rerank_candidates]

        if self.use_reranker and candidates and self.reranker is not None:
            t0 = time.perf_counter()
            passages = [
                f"{c.record.title}. {c.record.content}" for c in candidates
            ]
            rerank_scores = self.reranker.score(query, passages)
            for cand, rs in zip(candidates, rerank_scores):
                cand.rerank_score = rs
            candidates.sort(key=lambda c: -c.rerank_score)
            for rank, cand in enumerate(candidates):
                cand.rank = rank
            timings["rerank"] = (time.perf_counter() - t0) * 1000

        top = candidates[:top_n]

        # --- gate ---
        # Confidence is the best cross-encoder score, not cosine and not the
        # fused rank score. See the module docstring for the measurement.
        if self.use_reranker:
            confidence = max((r.rerank_score for r in top), default=-99.0)
            signal = "cross-encoder"
        else:
            confidence = max((r.vector_score for r in top), default=0.0)
            signal = "cosine (reranker disabled)"

        threshold = (
            max(r.record.confidence_floor for r in top) if top
            else settings.min_confidence
        )

        grounded = bool(top) and confidence >= threshold
        reason = None
        if not top:
            reason = "no records retrieved"
        elif not grounded:
            reason = (
                f"best {signal} score {confidence:.3f} below {threshold:.3f} "
                f"floor for category '{top[0].record.category}'"
            )

        return RetrievalResult(
            query=query,
            results=top,
            confidence=confidence,
            threshold=threshold,
            grounded=grounded,
            refusal_reason=reason,
            timings_ms=timings,
        )

    # ---------------- fusion ----------------

    def _rrf(
        self,
        dense: list[ScoredRecord],
        lexical_idx: list[int],
        bm25_scores,
    ) -> list[ScoredRecord]:
        """Reciprocal Rank Fusion over the two candidate lists."""
        by_record: dict[str, ScoredRecord] = {}
        rrf: dict[str, float] = {}

        for rank, scored in enumerate(dense):
            rid = scored.record.record_id
            by_record[rid] = scored
            rrf[rid] = rrf.get(rid, 0.0) + settings.vector_weight / (RRF_K + rank + 1)

        for rank, idx in enumerate(lexical_idx):
            record = self.store.records[idx]
            rid = record.record_id
            if rid not in by_record:
                by_record[rid] = ScoredRecord(record=record, score=0.0)
            by_record[rid].lexical_score = float(bm25_scores[idx])
            rrf[rid] = rrf.get(rid, 0.0) + settings.bm25_weight / (RRF_K + rank + 1)

        ordered = sorted(rrf.items(), key=lambda kv: -kv[1])
        out: list[ScoredRecord] = []
        for rank, (rid, score) in enumerate(ordered):
            scored = by_record[rid]
            scored.score = score
            scored.rank = rank
            out.append(scored)
        return out
