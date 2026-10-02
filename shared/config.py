"""Central configuration.

Every tunable lives here so the write-up can point at one file instead of
hunting constants across modules. Values come from .env (see .env.example);
nothing secret is ever hardcoded.
"""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    # ---- Credentials (free tiers; see .env.example for signup links) ----
    groq_api_key: str = ""
    google_api_key: str = ""

    # ---- Models (all verified live; see scripts/verify_providers.py) ----
    #
    # ASR: whisper-large-v3-turbo, not large-v3. Measured on synthesised
    # Taglish, turbo transcribed the English loanword "due na" correctly while
    # large-v3 collapsed it to "duna". Code-switching fidelity is the whole
    # point of Q3, and turbo is also ~20% faster. Short chunks are posted
    # rather than whole files, which turns a batch endpoint into a streaming
    # one for Q4 and keeps per-chunk latency measurable.
    groq_asr_model: str = "whisper-large-v3-turbo"

    # One chat model for conversation AND nudges. Measured alternatives:
    #   qwen/qwen3.8-27b      TTFT  119ms   total  152ms   good Taglish
    #   openai/gpt-oss-20b    returned empty content (reasoning model: emits
    #                         reasoning tokens first, so a short max_tokens
    #                         budget never reaches visible output)
    #   openai/gpt-oss-120b   same, and 4x slower
    #   gemini-flash-latest   3429ms, then 503 "high demand" on the free tier
    # Gemini was the original plan for conversation. It was dropped from the
    # critical path because 3.4s per turn is not a voice latency budget, and
    # free-tier availability proved unreliable mid-benchmark.
    # Switched from qwen/qwen3.8-27b after exhausting its 200k tokens-per-day
    # free-tier cap during evaluation. Each Groq model carries its own TPD
    # budget, so moving model is the recovery path. Tradeoff measured:
    # qwen TTFT 119ms and more natural Taglish; gpt-oss-120b ~800ms and
    # slightly more formal, but it works and qwen no longer does today.
    groq_chat_model: str = "qwen/qwen3.8-27b"
    groq_nudge_model: str = "qwen/qwen3.8-27b"

    # Optional, non-critical. Retained for offline/batch work (e.g. rewriting
    # records into speakable form) where multi-second latency is irrelevant.
    gemini_model: str = "gemini-flash-latest"

    # ---- Embeddings ----
    # bge-small via ONNX (fastembed). 384-dim, ~130MB, no torch dependency.
    # Chosen over bge-base/bge-m3 because this runs on a 4-core CPU with 8GB RAM.
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_dim: int = 384

    # ---- Chunking ----
    # Sized against the voice use case: a chunk must be short enough that the
    # agent can speak it without truncation, long enough to carry a complete
    # rule (a waiting period with its exception is useless when split).
    chunk_target_tokens: int = 220
    chunk_max_tokens: int = 380
    chunk_overlap_tokens: int = 40

    # ---- Retrieval ----
    retrieval_top_k: int = 20          # candidates from each retriever
    rerank_candidates: int = 8         # passages sent to the cross-encoder
    rerank_top_n: int = 4              # what the agent actually sees
    bm25_weight: float = 0.4
    vector_weight: float = 0.6

    # Cross-encoder reranker. This is what makes the refusal gate work:
    # cosine similarity could not separate out-of-scope questions from real
    # ones (margin +0.04), while the cross-encoder separates them by +3.68.
    # See scripts/calibrate_reranker.py and q2_kb/index/schema.py.
    reranker_model: str = "Xenova/ms-marco-MiniLM-L-6-v2"
    # Fallback floor when no category-specific floor applies.
    min_confidence: float = 1.0

    # ---- Crawl politeness ----
    crawl_delay_seconds: float = 1.5
    crawl_timeout_seconds: int = 25
    crawl_max_pages: int = 120
    user_agent: str = (
        "DarwixAssessmentBot/1.0 (AI engineer assessment; respects robots.txt)"
    )

    # ---- Paths ----
    data_dir: Path = ROOT / "data"
    raw_dir: Path = ROOT / "data" / "raw"
    interim_dir: Path = ROOT / "data" / "interim"
    processed_dir: Path = ROOT / "data" / "processed"
    audio_dir: Path = ROOT / "data" / "audio"
    index_dir: Path = ROOT / "data" / "index"

    def ensure_dirs(self) -> None:
        for d in (
            self.data_dir, self.raw_dir, self.interim_dir,
            self.processed_dir, self.audio_dir, self.index_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)


settings = Settings()
settings.ensure_dirs()
