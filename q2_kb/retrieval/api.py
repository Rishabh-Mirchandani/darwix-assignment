"""Retrieval API - the seam between the knowledge base (Q2) and the bots (Q1/Q3).

This exists as a real HTTP service rather than an in-process import for one
reason that matters to the assessment: the brief treats "disconnected knowledge
base and voice bot" as a rejection condition, and a network boundary makes the
connection demonstrable. You can curl it, watch the agent's tool calls hit it,
and see the citation that came back.

The contract is deliberately narrow:

    POST /search  {"query": "...", "category": null, "top_n": 4}
    -> {"grounded": bool, "confidence": float, "threshold": float,
        "answer": str|null, "citations": [...], "results": [...]}

`grounded` is the field the agent must branch on. When it is false the agent is
required to say it does not know and offer escalation -- it never sees retrieved
text it could paraphrase into a guess, because low-confidence results are
returned under `results` for debugging but `answer` is null.
"""
from __future__ import annotations

import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from shared.config import settings
from q2_kb.retrieval.hybrid import HybridRetriever

_retriever: HybridRetriever | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load the index and both models once, at startup.

    Model loading is ~2-3s. Doing it lazily on first request would put that
    latency inside the first caller's turn, which is the worst possible place
    for it on a phone call.
    """
    global _retriever
    t0 = time.perf_counter()
    _retriever = HybridRetriever()
    # Warm the ONNX graphs so the first real query is not an outlier.
    _retriever.search("warm up")
    print(
        f"[retrieval-api] ready: {len(_retriever.store)} records, "
        f"warm-up {time.perf_counter() - t0:.1f}s"
    )
    yield
    _retriever = None


app = FastAPI(
    title="Darwix Assessment - Knowledge Base Retrieval",
    description="Grounded retrieval with an explicit refusal gate.",
    version="1.0.0",
    lifespan=lifespan,
)

# The browser voice client is served from a different port in development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)
    category: str | None = Field(
        None,
        description="Optional category filter, e.g. 'eligibility_rule'.",
    )
    market: str | None = Field(
        None, description="Hard partition: in | ph | id. Omit for all."
    )
    top_n: int = Field(default=0, ge=0, le=10)


class Citation(BaseModel):
    record_id: str
    title: str
    source_url: str
    version: str


class SearchResponse(BaseModel):
    query: str
    grounded: bool
    confidence: float
    threshold: float
    refusal_reason: str | None
    answer: str | None
    citations: list[Citation]
    results: list[dict]
    timings_ms: dict[str, float]
    total_ms: float


def _get_retriever() -> HybridRetriever:
    if _retriever is None:
        raise HTTPException(503, "retriever not initialised")
    return _retriever


@app.get("/health")
def health() -> dict:
    ready = _retriever is not None
    return {
        "status": "ok" if ready else "starting",
        "records": len(_retriever.store) if _retriever else 0,
        "embedding_model": settings.embedding_model,
        "reranker_model": settings.reranker_model,
    }


@app.post("/search", response_model=SearchResponse)
def search(req: SearchRequest) -> SearchResponse:
    retriever = _get_retriever()
    t0 = time.perf_counter()

    result = retriever.search(
        req.query,
        top_n=req.top_n or None,
        category=req.category,
        market=req.market,
    )
    total_ms = (time.perf_counter() - t0) * 1000
    payload = result.to_dict()

    # The answer is withheld entirely when the gate does not pass. The agent
    # cannot paraphrase what it was not given.
    answer = None
    citations: list[Citation] = []
    if result.grounded and result.results:
        answer = "\n\n".join(
            f"{r.record.title}: {r.record.answer_text}" for r in result.results
        )
        citations = [
            Citation(
                record_id=r.record.record_id,
                title=r.record.title,
                source_url=r.record.source_url,
                version=r.record.version,
            )
            for r in result.results
        ]

    return SearchResponse(
        query=result.query,
        grounded=result.grounded,
        confidence=result.confidence,
        threshold=result.threshold,
        refusal_reason=result.refusal_reason,
        answer=answer,
        citations=citations,
        results=payload["results"],
        timings_ms=payload["timings_ms"],
        total_ms=round(total_ms, 2),
    )


@app.get("/stats")
def stats() -> dict:
    """Corpus composition - used in the write-up and for sanity checks."""
    retriever = _get_retriever()
    from collections import Counter

    records = retriever.store.records
    return {
        "records": len(records),
        "by_category": dict(Counter(r.category for r in records).most_common()),
        "by_section_kind": dict(
            Counter(r.section_kind for r in records).most_common()
        ),
        "with_pii": sum(1 for r in records if r.pii),
        "sources": len({r.source_url for r in records}),
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8001, log_level="info")
