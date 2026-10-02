"""Chunking strategy.

The governing constraint here is that the consumer is a *voice* agent, which
changes the usual RAG tradeoffs:

* **Upper bound is set by speech, not by the context window.** A retrieved
  chunk the bot reads aloud must fit in roughly 15-20 seconds or the caller
  interrupts. That is ~380 tokens, far below what a text RAG system would use.

* **A chunk must be self-contained.** In chat a user can scroll back; on a call
  they cannot. Splitting "36 months waiting period" away from "for pre-existing
  diseases" produces a chunk that is not merely unhelpful but dangerous -- the
  agent would state a waiting period without saying what it applies to. So we
  split on semantic boundaries (headings, then paragraphs, then sentences) and
  never mid-sentence.

* **FAQ, definition and table sections are never split.** A question/answer
  pair, a named benefit block and a linearised table row set are each atomic.
  If one exceeds the budget it is kept whole and flagged, because a truncated
  table -- or half a benefit rule -- is worse than a long one.

Overlap is applied only between prose chunks of the same section, carrying one
trailing sentence forward so a rule split across a boundary stays recoverable.
"""
from __future__ import annotations

import re

from shared.config import settings
from q2_kb.clean.extract import Section

_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z(])")


def estimate_tokens(text: str) -> int:
    """Word-count based estimate (~0.75 words per token).

    Deliberately not a real tokenizer: this runs over every section on every
    build, and being 10% off on a chunk boundary costs nothing, whereas pulling
    a tokenizer into ingest costs a dependency and startup time.
    """
    return int(len(text.split()) / 0.75)


def split_sentences(text: str) -> list[str]:
    parts = [s.strip() for s in _SENTENCE_RE.split(text) if s.strip()]
    return parts or ([text.strip()] if text.strip() else [])


def split_paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def _pack(units: list[str], target: int, maximum: int) -> list[str]:
    """Greedily pack units into chunks under `maximum`, aiming for `target`."""
    chunks: list[str] = []
    current: list[str] = []
    current_tokens = 0

    for unit in units:
        unit_tokens = estimate_tokens(unit)

        # A single unit over the max cannot be packed; emit it alone and let
        # the caller flag it. Splitting it further would break a sentence.
        if unit_tokens > maximum:
            if current:
                chunks.append("\n\n".join(current))
                current, current_tokens = [], 0
            chunks.append(unit)
            continue

        if current_tokens + unit_tokens > maximum and current:
            chunks.append("\n\n".join(current))
            current, current_tokens = [unit], unit_tokens
        else:
            current.append(unit)
            current_tokens += unit_tokens
            if current_tokens >= target:
                chunks.append("\n\n".join(current))
                current, current_tokens = [], 0

    if current:
        chunks.append("\n\n".join(current))
    return chunks


def _apply_overlap(chunks: list[str], overlap_tokens: int) -> list[str]:
    """Carry the last sentence of each chunk into the next one."""
    if len(chunks) < 2 or overlap_tokens <= 0:
        return chunks

    out = [chunks[0]]
    for prev, cur in zip(chunks, chunks[1:]):
        tail = split_sentences(prev)
        carried: list[str] = []
        budget = overlap_tokens
        for sentence in reversed(tail):
            cost = estimate_tokens(sentence)
            if cost > budget:
                break
            carried.insert(0, sentence)
            budget -= cost
        out.append((" ".join(carried) + "\n\n" + cur).strip() if carried else cur)
    return out


def chunk_section(section: Section) -> list[tuple[str, list[str]]]:
    """Split one section into [(chunk_text, warnings)].

    Returns warnings alongside each chunk so oversized atomic units surface in
    the build manifest instead of silently degrading answers.
    """
    target = settings.chunk_target_tokens
    maximum = settings.chunk_max_tokens
    body = section.content.strip()

    if not body:
        return []

    total_tokens = estimate_tokens(body)

    # Atomic kinds: never split, even when oversized.
    if section.kind in ("faq", "definition", "table"):
        warnings = []
        if total_tokens > maximum:
            warnings.append(
                f"{section.kind} section kept whole at {total_tokens} tokens "
                f"(over {maximum}); splitting would break its structure"
            )
        return [(body, warnings)]

    # Fits in one chunk.
    if total_tokens <= maximum:
        return [(body, [])]

    # Prose: paragraphs first, fall back to sentences for huge paragraphs.
    units = split_paragraphs(body)
    if any(estimate_tokens(u) > maximum for u in units):
        expanded: list[str] = []
        for u in units:
            expanded.extend(split_sentences(u) if estimate_tokens(u) > maximum else [u])
        units = expanded

    chunks = _pack(units, target, maximum)
    chunks = _apply_overlap(chunks, settings.chunk_overlap_tokens)

    out: list[tuple[str, list[str]]] = []
    for c in chunks:
        warn = []
        if estimate_tokens(c) > maximum:
            warn.append(f"chunk exceeds max at {estimate_tokens(c)} tokens")
        out.append((c, warn))
    return out
