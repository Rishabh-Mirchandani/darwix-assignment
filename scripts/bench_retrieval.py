"""Steady-state retrieval latency, with and without the reranker.

The eval run reports p95 inflated by first-call model loading. A voice agent
pays that once at startup, not per turn, so the number that matters for the
latency budget is steady state. This also quantifies what the refusal gate
costs, which is the central tradeoff in the retrieval design.
"""
from __future__ import annotations

import statistics
import sys
import time

sys.path.insert(0, ".")
from q2_kb.eval.queries import QUERIES              # noqa: E402
from q2_kb.retrieval.hybrid import HybridRetriever  # noqa: E402

ROUNDS = 3


def bench(use_reranker: bool) -> dict[str, float]:
    retriever = HybridRetriever(use_reranker=use_reranker)

    # Warm up: load models and let ONNX settle before timing anything.
    retriever.search("warm up the model", top_n=4)

    stage_totals: dict[str, list[float]] = {}
    totals: list[float] = []

    for _ in range(ROUNDS):
        for q in QUERIES:
            t0 = time.perf_counter()
            result = retriever.search(q.question)
            totals.append((time.perf_counter() - t0) * 1000)
            for stage, ms in result.timings_ms.items():
                stage_totals.setdefault(stage, []).append(ms)

    totals.sort()
    out = {
        "n": len(totals),
        "p50": totals[len(totals) // 2],
        "p95": totals[int(len(totals) * 0.95) - 1],
        "mean": statistics.mean(totals),
        "max": totals[-1],
    }
    for stage, values in stage_totals.items():
        out[f"stage_{stage}_mean"] = statistics.mean(values)
    return out


def main() -> None:
    print(f"benchmarking {len(QUERIES)} queries x {ROUNDS} rounds\n")

    print("=== WITH reranker (production path) ===")
    with_rr = bench(True)
    for k, v in with_rr.items():
        print(f"  {k:<26}{v:.1f}" if isinstance(v, float) else f"  {k:<26}{v}")

    print("\n=== WITHOUT reranker (ablation) ===")
    without_rr = bench(False)
    for k, v in without_rr.items():
        print(f"  {k:<26}{v:.1f}" if isinstance(v, float) else f"  {k:<26}{v}")

    cost = with_rr["p50"] - without_rr["p50"]
    print(f"\nreranker cost at p50: +{cost:.0f} ms")
    print("bought: refusal accuracy 1/4 -> 4/4 (see docs/RETRIEVAL_EVAL.md)")


if __name__ == "__main__":
    main()
