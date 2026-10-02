"""Append records to an existing index without re-embedding everything.

Embedding the 2,084 India records takes ~8 minutes on this machine. Adding 24
market records should not cost that again, so this loads the existing matrix,
embeds only the new rows, and concatenates.

Idempotent: re-running with the same input replaces those record_ids rather
than duplicating them, so the script can be run repeatedly while the market
content is being edited.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from rich.console import Console                                   # noqa: E402

from shared.config import settings                                 # noqa: E402
from q2_kb.index.schema import KBRecord                            # noqa: E402
from q2_kb.index.vector_store import Embedder, VectorStore         # noqa: E402

console = Console()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("jsonl", help="records file to add")
    args = ap.parse_args()

    path = Path(args.jsonl)
    if not path.exists():
        console.print(f"[red]no such file:[/] {path}")
        return 2

    new_records = [
        KBRecord.model_validate_json(line) for line in path.open(encoding="utf-8")
    ]
    console.print(f"loaded [bold]{len(new_records)}[/] records from {path.name}")

    store = VectorStore()
    store.load()
    console.print(f"existing index: [bold]{len(store)}[/] records")

    # Drop any prior copies of these ids so re-running is idempotent.
    new_ids = {r.record_id for r in new_records}
    keep_idx = [
        i for i, r in enumerate(store.records) if r.record_id not in new_ids
    ]
    removed = len(store.records) - len(keep_idx)
    if removed:
        console.print(f"  replacing {removed} existing record(s) with same ids")

    kept_records = [store.records[i] for i in keep_idx]
    assert store.matrix is not None
    kept_matrix = store.matrix[keep_idx]

    embedder = Embedder()
    console.print(f"embedding {len(new_records)} new records...")
    new_matrix = embedder.encode([r.embedding_text() for r in new_records])

    store.records = kept_records + new_records
    store.matrix = np.vstack([kept_matrix, new_matrix]).astype(np.float32)
    store.save()

    from collections import Counter
    by_market = Counter(r.market for r in store.records)
    console.print(f"\n[green]index now holds {len(store)} records[/]")
    for market, n in by_market.most_common():
        console.print(f"  market={market:<4} {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
