"""Minimal fastembed smoke test, to isolate crashes from the full build."""
from __future__ import annotations

import sys
import time
import traceback

sys.path.insert(0, ".")


def main() -> int:
    print("step 1: import fastembed", flush=True)
    try:
        from fastembed import TextEmbedding
    except Exception:
        traceback.print_exc()
        return 1

    print("step 2: list supported models", flush=True)
    try:
        names = [m["model"] for m in TextEmbedding.list_supported_models()]
        print(f"  {len(names)} models available", flush=True)
        print(f"  bge-small-en-v1.5 present: "
              f"{'BAAI/bge-small-en-v1.5' in names}", flush=True)
    except Exception:
        traceback.print_exc()
        return 1

    print("step 3: construct model (may download ~130MB)", flush=True)
    t0 = time.perf_counter()
    try:
        model = TextEmbedding(model_name="BAAI/bge-small-en-v1.5")
    except Exception:
        traceback.print_exc()
        return 1
    print(f"  loaded in {time.perf_counter() - t0:.1f}s", flush=True)

    print("step 4: embed 5 short texts", flush=True)
    t0 = time.perf_counter()
    try:
        vecs = list(model.embed([
            "What is the waiting period for pre-existing diseases?",
            "Maternity benefit covers delivery expenses.",
            "Cashless treatment at network hospitals.",
            "Co-payment of 20 percent applies above age 60.",
            "Room rent limit is one percent of sum insured.",
        ]))
    except Exception:
        traceback.print_exc()
        return 1
    print(f"  embedded 5 in {time.perf_counter() - t0:.2f}s", flush=True)
    print(f"  shape: {len(vecs)} x {len(vecs[0])}", flush=True)

    print("step 5: embed 200 texts (throughput probe)", flush=True)
    t0 = time.perf_counter()
    try:
        batch = [f"Health insurance policy clause number {i}. "
                 f"Coverage details and waiting period rules apply."
                 for i in range(200)]
        out = list(model.embed(batch, batch_size=32))
    except Exception:
        traceback.print_exc()
        return 1
    dt = time.perf_counter() - t0
    print(f"  200 texts in {dt:.1f}s -> {200/dt:.1f} texts/sec", flush=True)
    print(f"  projected for 2084 records: {2084/(200/dt):.0f}s", flush=True)

    print("\nOK", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
