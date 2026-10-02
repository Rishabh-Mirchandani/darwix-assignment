"""Build the searchable index from kb_records.jsonl.

Separate from `q2_kb.build` on purpose: cleaning is deterministic and offline,
while indexing loads a model and is the slow step. Keeping them apart means a
chunking tweak can be re-run and inspected without re-embedding, and the index
can be rebuilt without re-crawling.
"""
from __future__ import annotations

import time

from rich.console import Console

from shared.config import settings
from q2_kb.index.schema import KBRecord
from q2_kb.index.vector_store import Embedder, VectorStore

console = Console()


def load_records() -> list[KBRecord]:
    path = settings.processed_dir / "kb_records.jsonl"
    if not path.exists():
        raise SystemExit(f"No records at {path}. Run: python -m q2_kb.build")
    return [
        KBRecord.model_validate_json(line) for line in path.open(encoding="utf-8")
    ]


def main() -> None:
    records = load_records()
    console.print(f"loaded [bold]{len(records)}[/] records")

    store = VectorStore()
    embedder = Embedder()

    start = time.perf_counter()
    store.build(records, embedder)
    elapsed = time.perf_counter() - start

    store.save()
    console.print(
        f"\n[green]done[/] embedded {len(records)} records in {elapsed:.1f}s "
        f"({len(records) / elapsed:.0f} rec/s)"
    )


if __name__ == "__main__":
    main()
