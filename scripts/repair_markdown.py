"""Repair markdown artifacts in an already-built index, in place.

Why not just rebuild: a full rebuild re-embeds all 2,108 records (~8 min on
this CPU) to fix 201 of them. This re-normalises every record, re-embeds only
the ones whose text actually changed, and splices the new vectors into the
existing matrix.

Correctness note: the stored embedding must match the stored text, otherwise
retrieval scores drift from what the record says. That is why changed records
are re-embedded rather than merely rewritten.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rich.console import Console                                   # noqa: E402

from q2_kb.clean.normalize import normalize_content                # noqa: E402
from q2_kb.index.chunk import estimate_tokens                      # noqa: E402
from q2_kb.index.vector_store import Embedder, VectorStore         # noqa: E402

console = Console()


def main() -> int:
    store = VectorStore()
    store.load()
    assert store.matrix is not None
    console.print(f"loaded index: [bold]{len(store)}[/] records")

    changed: list[int] = []
    for i, rec in enumerate(store.records):
        cleaned = normalize_content(rec.content)
        if cleaned != rec.content:
            rec.content = cleaned
            rec.answer_text = normalize_content(rec.answer_text or cleaned)
            rec.token_estimate = estimate_tokens(cleaned)
            changed.append(i)

    if not changed:
        console.print("[green]nothing to repair[/]")
        return 0

    console.print(f"re-normalised [bold]{len(changed)}[/] records; re-embedding those only")

    embedder = Embedder()
    new_vecs = embedder.encode([store.records[i].embedding_text() for i in changed])

    matrix = store.matrix.copy()
    for slot, i in enumerate(changed):
        matrix[i] = new_vecs[slot]
    store.matrix = matrix
    store.save()

    console.print(f"[green]repaired[/] {len(changed)} records; "
                  f"{len(store) - len(changed)} untouched")

    sample = store.records[changed[0]]
    console.print("\n[dim]sample after repair:[/]")
    console.print(f"  {sample.record_id}")
    console.print(f"  {sample.content[:200]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
