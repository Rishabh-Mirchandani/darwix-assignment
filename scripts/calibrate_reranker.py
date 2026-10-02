"""Measure whether a cross-encoder separates answerable from unanswerable.

The dense-only gate failed: out-of-scope questions that are *topically*
insurance ("what is my policy balance", "net profit last quarter") scored
cosine ~0.72, overlapping legitimate questions at ~0.77. Cosine measures topic
proximity, not whether a passage answers the question, so no threshold on it
can separate the two populations.

A cross-encoder reads (query, passage) jointly and scores relevance directly.
This script checks whether its scores actually separate the two groups on our
own data -- and prints the margin, so the threshold is chosen from measurement
rather than taste.
"""
from __future__ import annotations

import sys
import time

sys.path.insert(0, ".")
from q2_kb.eval.queries import QUERIES          # noqa: E402
from q2_kb.retrieval.hybrid import HybridRetriever  # noqa: E402


def main() -> None:
    from fastembed.rerank.cross_encoder import TextCrossEncoder

    print("loading retriever + reranker...", flush=True)
    retriever = HybridRetriever()
    encoder = TextCrossEncoder(model_name="Xenova/ms-marco-MiniLM-L-6-v2")

    answerable: list[float] = []
    unanswerable: list[float] = []
    rows: list[tuple[str, str, float, float, str]] = []

    total_rerank_ms = 0.0

    for q in QUERIES:
        result = retriever.search(q.question, top_n=8)
        if not result.results:
            continue

        passages = [
            f"{r.record.title}. {r.record.content}" for r in result.results
        ]
        t0 = time.perf_counter()
        scores = list(encoder.rerank(q.question, passages))
        total_rerank_ms += (time.perf_counter() - t0) * 1000

        best = max(scores)
        cosine = result.confidence

        (answerable if q.expect == "answer" else unanswerable).append(best)
        rows.append((q.qid, q.expect, cosine, best, q.question[:52]))

    print(f"\n{'qid':<5}{'expect':<9}{'cosine':>8}{'rerank':>10}  question")
    print("-" * 86)
    for qid, expect, cos, rr, question in rows:
        flag = ""
        print(f"{qid:<5}{expect:<9}{cos:>8.3f}{rr:>10.3f}  {question}{flag}")

    print("\n--- separation ---")
    if answerable and unanswerable:
        print(f"answerable   n={len(answerable):<3} "
              f"min={min(answerable):.3f}  mean={sum(answerable)/len(answerable):.3f}  "
              f"max={max(answerable):.3f}")
        print(f"unanswerable n={len(unanswerable):<3} "
              f"min={min(unanswerable):.3f}  mean={sum(unanswerable)/len(unanswerable):.3f}  "
              f"max={max(unanswerable):.3f}")
        margin = min(answerable) - max(unanswerable)
        print(f"\nmargin (min answerable - max unanswerable): {margin:+.3f}")
        if margin > 0:
            thr = (min(answerable) + max(unanswerable)) / 2
            print(f"CLEAN SEPARATION -> threshold {thr:.3f} classifies all 20 correctly")
        else:
            print("OVERLAP -- no single threshold separates these groups")

    n = len(rows)
    print(f"\nrerank cost: {total_rerank_ms/n:.0f} ms per query (8 passages, CPU)")


if __name__ == "__main__":
    main()
