"""Embedding and vector index.

**Why there is no vector database here.** The corpus is ~2.1k records. Exact
cosine similarity over a (2084 x 384) float32 matrix is a single BLAS matmul --
roughly 3 MB of memory and well under a millisecond on CPU. An ANN index
(Chroma/FAISS/Qdrant) exists to avoid an O(N) scan, but at this N the scan is
already faster than the index lookup's own overhead, and ANN introduces recall
error that would have to be tuned away. It would also add a large dependency
tree to a machine with 8 GB of RAM.

The honest scaling note, which belongs in the write-up rather than in a
comment pretending this is universal: exact search stops being the right call
somewhere around 10^5-10^6 vectors, at which point HNSW is the natural
successor. The storage format here (one .npy matrix + one .jsonl of records)
is deliberately boring so that swap is mechanical.

Embeddings come from `bge-small-en-v1.5` served through fastembed's ONNX
runtime -- no PyTorch, ~130 MB of weights, CPU-friendly.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from rich.console import Console

from shared.config import settings
from q2_kb.index.schema import KBRecord

console = Console()

_MATRIX_FILE = "embeddings.npy"
_RECORDS_FILE = "records.jsonl"
_META_FILE = "index_meta.json"


@dataclass
class ScoredRecord:
    record: KBRecord
    score: float                 # fused rank score (RRF)
    vector_score: float = 0.0    # cosine similarity
    lexical_score: float = 0.0   # raw BM25
    rerank_score: float = 0.0    # cross-encoder relevance logit
    rank: int = 0


class Embedder:
    """Lazy wrapper around fastembed.

    Loaded lazily because importing fastembed pulls onnxruntime and may trigger
    a one-time model download; a caller that only reads cached vectors (the
    common path at serve time) should not pay for that.
    """

    def __init__(self, model_name: str | None = None) -> None:
        self.model_name = model_name or settings.embedding_model
        self._model = None

    def _ensure(self) -> None:
        if self._model is None:
            from fastembed import TextEmbedding  # imported lazily on purpose

            console.print(f"[dim]loading embedding model {self.model_name}...[/]")
            self._model = TextEmbedding(model_name=self.model_name)

    def encode(self, texts: list[str], batch_size: int = 64) -> np.ndarray:
        self._ensure()
        assert self._model is not None
        vectors = list(self._model.embed(texts, batch_size=batch_size))
        arr = np.asarray(vectors, dtype=np.float32)
        return _l2_normalise(arr)

    def encode_one(self, text: str) -> np.ndarray:
        return self.encode([text])[0]


def _l2_normalise(matrix: np.ndarray) -> np.ndarray:
    """Normalise rows so a dot product is cosine similarity."""
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return matrix / norms


class VectorStore:
    """Exact-search vector index over KB records, persisted to disk."""

    def __init__(self, directory: Path | None = None) -> None:
        self.dir = directory or settings.index_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.records: list[KBRecord] = []
        self.matrix: np.ndarray | None = None

    # ---------------- build ----------------

    def build(self, records: list[KBRecord], embedder: Embedder) -> None:
        if not records:
            raise ValueError("no records to index")

        texts = [r.embedding_text() for r in records]
        console.print(f"[cyan]embedding {len(texts)} records...[/]")
        self.matrix = embedder.encode(texts)
        self.records = records
        console.print(
            f"  matrix: {self.matrix.shape}  "
            f"({self.matrix.nbytes / 1024 / 1024:.1f} MB)"
        )

    def save(self) -> None:
        if self.matrix is None:
            raise RuntimeError("nothing to save; call build() first")

        np.save(self.dir / _MATRIX_FILE, self.matrix)
        with (self.dir / _RECORDS_FILE).open("w", encoding="utf-8") as fh:
            for rec in self.records:
                fh.write(rec.model_dump_json() + "\n")

        (self.dir / _META_FILE).write_text(
            json.dumps(
                {
                    "embedding_model": settings.embedding_model,
                    "embedding_dim": int(self.matrix.shape[1]),
                    "record_count": len(self.records),
                    "search": "exact cosine (no ANN) - see module docstring",
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        console.print(f"[green]index saved[/] -> {self.dir}")

    # ---------------- load ----------------

    def load(self) -> None:
        matrix_path = self.dir / _MATRIX_FILE
        records_path = self.dir / _RECORDS_FILE
        if not matrix_path.exists() or not records_path.exists():
            raise SystemExit(
                f"No index in {self.dir}. Build it with:\n"
                f"  python -m q2_kb.index.build_index"
            )
        self.matrix = np.load(matrix_path)
        self.records = [
            KBRecord.model_validate_json(line)
            for line in records_path.open(encoding="utf-8")
        ]
        if len(self.records) != self.matrix.shape[0]:
            raise RuntimeError(
                f"index corrupt: {len(self.records)} records vs "
                f"{self.matrix.shape[0]} vectors"
            )

    # ---------------- search ----------------

    def search(
        self,
        query_vector: np.ndarray,
        top_k: int = 20,
        category: str | None = None,
        market: str | None = None,
    ) -> list[ScoredRecord]:
        if self.matrix is None:
            raise RuntimeError("index not loaded")

        scores = self.matrix @ query_vector  # cosine, both sides normalised

        # Market filtering is a hard partition, not a ranking preference:
        # an Indian waiting-period rule is simply wrong for a Philippine life
        # policy, so it must be unreachable rather than merely down-ranked.
        if market or category:
            mask = np.array(
                [
                    (market is None or r.market == market)
                    and (category is None or r.category == category)
                    for r in self.records
                ],
                dtype=bool,
            )
            scores = np.where(mask, scores, -1.0)

        k = min(top_k, len(self.records))
        # argpartition avoids a full sort of 2k elements for a top-20 request.
        idx = np.argpartition(-scores, k - 1)[:k]
        idx = idx[np.argsort(-scores[idx])]

        return [
            ScoredRecord(
                record=self.records[i],
                score=float(scores[i]),
                vector_score=float(scores[i]),
                rank=rank,
            )
            for rank, i in enumerate(idx)
            if scores[i] > -1.0
        ]

    def __len__(self) -> int:
        return len(self.records)
