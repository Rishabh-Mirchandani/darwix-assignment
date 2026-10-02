"""Knowledge-base record schema.

The brief specifies a minimum shape (record_id, title, content, category,
source, version, pii). We keep every one of those names so the submission maps
onto the spec literally, and add fields that a *voice* agent needs:

* `answer_text`   -- a speakable form. Web copy is written for scanning, not
                     speaking; bullet fragments and "View all" read terribly
                     aloud. Retrieval matches on `content`, the bot speaks
                     `answer_text`.
* `canonical_terms` -- resolved terminology (see clean/normalize.py), indexed
                     alongside the body so "PED" and "pre-existing disease"
                     retrieve the same record.
* `amounts`       -- parsed rupee values, enabling numeric filters.
* `source_url` + `source_anchor` + `content_hash` -- citation and change
                     detection. A citation that cannot be clicked is not a
                     citation.
* `confidence_floor` -- per-category retrieval threshold. An eligibility rule
                     answered loosely is a compliance problem; a marketing
                     blurb answered loosely is not. One global threshold cannot
                     express that.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

Category = Literal[
    "product_plan",
    "coverage_rule",
    "eligibility_rule",
    "claims_process",
    "faq",
    "service_policy",
    "objection_handling",
    "exclusion",
]

# Confidence floors, expressed on the CROSS-ENCODER logit scale, not cosine.
#
# This scale was chosen from measurement, not taste. `scripts/calibrate_reranker.py`
# scored the 20-query eval set both ways:
#
#   signal          answerable (n=16)      unanswerable (n=4)     margin
#   cosine          min 0.767              max 0.725              +0.042
#   cross-encoder   min 2.994              max -0.681             +3.675
#
# Cosine measures topic proximity, so "what is my policy balance" scores 0.716
# purely for being insurance-shaped -- overlapping real questions and making any
# cosine threshold a coin flip. The cross-encoder reads (query, passage) jointly
# and scores whether the passage actually answers the question, which separates
# the populations by a wide margin.
#
# Base floor is 1.0: ~2.0 of headroom below the lowest true positive and ~1.7
# above the highest true negative. Deliberately not the 1.156 midpoint -- with
# only four negatives, tuning to the midpoint would be overfitting.
DEFAULT_CONFIDENCE_FLOOR = 1.0

CATEGORY_CONFIDENCE_FLOOR: dict[str, float] = {
    # A wrong eligibility or exclusion answer is a mis-selling risk, so these
    # demand clearer evidence before the bot will speak.
    "eligibility_rule": 2.0,
    "exclusion": 2.0,
    "claims_process": 1.5,
    "coverage_rule": 1.2,
    "product_plan": 1.0,
    "faq": 1.0,
    "service_policy": 1.0,
    "objection_handling": 1.0,
}


class KBRecord(BaseModel):
    """One retrievable, citable unit of knowledge."""

    # --- spec-mandated fields ---
    record_id: str
    title: str
    content: str
    category: Category
    source: str
    version: str = "1.0"
    pii: bool = False
    # Which market this record serves. Retrieval filters on it so the
    # Philippines bot can never ground an answer in Indian policy text --
    # a cross-market leak would be a factually wrong answer delivered
    # with full confidence.
    market: Literal["in", "ph", "id"] = "in"

    # --- provenance / citation ---
    source_url: str
    source_anchor: str | None = None
    source_type: Literal["website", "pdf"] = "website"
    content_hash: str = ""
    last_modified: str | None = None
    ingested_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    # --- retrieval aids ---
    answer_text: str = ""
    # How the text was structured on the page. `category` says what the record
    # is about; `section_kind` says what shape it has. A caller asking "do you
    # cover AYUSH?" is best served by a `definition` or `faq` block, which is
    # already a self-contained statement, over a `prose` chunk that assumes
    # surrounding page context.
    section_kind: Literal["prose", "faq", "definition", "table"] = "prose"
    canonical_terms: list[str] = Field(default_factory=list)
    amounts: list[int] = Field(default_factory=list)
    pii_types: list[str] = Field(default_factory=list)
    chunk_index: int = 0
    chunk_total: int = 1
    token_estimate: int = 0

    # --- quality flags surfaced in the manifest ---
    warnings: list[str] = Field(default_factory=list)

    @property
    def confidence_floor(self) -> float:
        return CATEGORY_CONFIDENCE_FLOOR.get(
            self.category, DEFAULT_CONFIDENCE_FLOOR
        )

    def citation(self) -> str:
        """Human-readable citation used in retrieval output and call logs."""
        anchor = f"#{self.source_anchor}" if self.source_anchor else ""
        return f"{self.title} - {self.source_url}{anchor} (v{self.version})"

    def embedding_text(self) -> str:
        """What actually gets embedded.

        Title and canonical terms are prepended to the body because a bare
        chunk often lacks the words the caller will use. A chunk reading "36
        months from policy inception" is unretrievable until the heading
        "Pre-existing disease waiting period" sits in front of it.
        """
        parts = [self.title]
        if self.canonical_terms:
            parts.append("Terms: " + ", ".join(self.canonical_terms))
        parts.append(self.content)
        return "\n".join(parts)

    def to_metadata(self) -> dict[str, str | int | float | bool]:
        """Flat, scalar-only metadata.

        Lists are joined to strings so the record round-trips through JSON
        and any future columnar/vector store without a schema migration.
        """
        return {
            "record_id": self.record_id,
            "title": self.title,
            "category": self.category,
            "market": self.market,
            "section_kind": self.section_kind,
            "source": self.source,
            "source_url": self.source_url,
            "source_anchor": self.source_anchor or "",
            "source_type": self.source_type,
            "version": self.version,
            "pii": self.pii,
            "pii_types": ",".join(self.pii_types),
            "canonical_terms": ",".join(self.canonical_terms),
            "amounts": ",".join(str(a) for a in self.amounts),
            "content_hash": self.content_hash,
            "last_modified": self.last_modified or "",
            "chunk_index": self.chunk_index,
            "chunk_total": self.chunk_total,
            "confidence_floor": self.confidence_floor,
            "answer_text": self.answer_text,
        }


def make_record_id(category: str, title: str, chunk_index: int) -> str:
    """Stable, readable id: kb_<category>_<slug-hash>_<nn>.

    Deterministic so a re-crawl of unchanged content produces identical ids,
    which is what makes versioning and incremental updates possible.
    """
    digest = hashlib.sha1(f"{category}:{title}".encode()).hexdigest()[:8]
    return f"kb_{category}_{digest}_{chunk_index:02d}"
