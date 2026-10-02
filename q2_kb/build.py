"""Build pipeline: raw HTML snapshots -> clean, deduplicated KB records.

Stages, in order:

    raw HTML
      -> extract      (trafilatura prose + targeted FAQ/table extraction)
      -> normalise    (terminology, amounts, dates, headings)
      -> PII scan     (validated detectors; redact before anything is stored)
      -> chunk        (voice-sized, semantic boundaries)
      -> dedupe       (exact SHA-256, then MinHash LSH near-duplicate)
      -> records.jsonl + build_report.json

Everything is written to disk at each stage so a reviewer can inspect
intermediate state, and so re-running a later stage never requires re-crawling.

The build report is a deliverable in its own right: it carries the counts the
assessment asks about (what was dropped, why, how much PII was found, which
pages produced nothing) rather than leaving those as numbers only I ever saw.
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from rich.console import Console
from rich.table import Table

from shared.config import settings
from q2_kb.clean import pii as pii_mod
from q2_kb.clean.dedupe import deduplicate
from q2_kb.clean.extract import Section, extract_page
from q2_kb.clean.normalize import (
    extract_amounts,
    find_terms,
    is_low_information,
    normalize_content,
    normalize_heading,
)
from q2_kb.index.chunk import chunk_section, estimate_tokens
from q2_kb.index.schema import KBRecord, make_record_id

console = Console()


@dataclass
class BuildStats:
    pages_total: int = 0
    pages_parsed: int = 0
    pages_empty: int = 0
    sections_total: int = 0
    sections_by_kind: dict[str, int] = field(default_factory=dict)
    chunks_produced: int = 0        # before any filtering
    chunks_before_dedupe: int = 0   # after low-information filter
    dropped_low_information: int = 0
    drop_reasons: dict[str, int] = field(default_factory=dict)
    records_final: int = 0
    exact_duplicates: int = 0
    near_duplicates: int = 0
    records_with_pii: int = 0
    pii_type_counts: dict[str, int] = field(default_factory=dict)
    page_warnings: list[dict[str, str]] = field(default_factory=list)
    chunk_warnings: list[dict[str, str]] = field(default_factory=list)
    categories: dict[str, int] = field(default_factory=dict)
    token_histogram: dict[str, int] = field(default_factory=dict)


def _load_manifest() -> list[dict]:
    path = settings.interim_dir / "crawl_manifest.json"
    if not path.exists():
        raise SystemExit(
            f"No crawl manifest at {path}. Run: python -m q2_kb.ingest.crawl"
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    return [r for r in data["records"] if r["status"] == "ok" and r.get("raw_path")]


def _section_to_records(
    section: Section,
    category: str,
    source_rule: str,
    lastmod: str | None,
    content_hash: str,
    stats: BuildStats,
) -> list[KBRecord]:
    """Normalise, redact and chunk one section into KB records."""
    heading = normalize_heading(section.heading)
    body = normalize_content(section.content)
    if not heading or not body:
        return []

    chunks = chunk_section(Section(
        heading=heading, content=body, kind=section.kind,
        heading_level=section.heading_level, source_url=section.source_url,
        anchor=section.anchor,
    ))

    records: list[KBRecord] = []
    for idx, (chunk_text, warnings) in enumerate(chunks):
        # Redact before the text is stored anywhere, not after.
        redacted, pii_types = pii_mod.redact(chunk_text)

        # Drop stubs before they reach the index. A record that only says
        # "Some of them are listed below:" can still win a similarity match on
        # its title and then give the caller nothing.
        stats.chunks_produced += 1
        tokens = estimate_tokens(redacted)
        low_info, reason = is_low_information(redacted, tokens)
        if low_info:
            stats.dropped_low_information += 1
            stats.drop_reasons[reason] = stats.drop_reasons.get(reason, 0) + 1
            continue

        record = KBRecord(
            record_id=make_record_id(category, heading, idx),
            title=heading,
            content=redacted,
            category=category,  # type: ignore[arg-type]
            source=f"{source_rule} / website section",
            source_url=section.source_url,
            source_anchor=section.anchor,
            source_type="website",
            content_hash=content_hash,
            last_modified=lastmod,
            pii=bool(pii_types),
            pii_types=pii_types,
            canonical_terms=find_terms(redacted),
            amounts=extract_amounts(redacted),
            section_kind=section.kind,  # type: ignore[arg-type]
            chunk_index=idx,
            chunk_total=len(chunks),
            token_estimate=tokens,
            warnings=warnings,
        )
        # answer_text starts as the chunk itself; the speakable rewrite is a
        # separate, optional LLM pass (see scripts/make_answer_text.py) so the
        # build stays deterministic and runs without any API key.
        record.answer_text = redacted
        records.append(record)

        if warnings:
            stats.chunk_warnings.append(
                {"record_id": record.record_id, "warning": "; ".join(warnings)}
            )
        if pii_types:
            stats.records_with_pii += 1
            for t in pii_types:
                stats.pii_type_counts[t] = stats.pii_type_counts.get(t, 0) + 1

    return records


def build() -> list[KBRecord]:
    manifest = _load_manifest()
    stats = BuildStats(pages_total=len(manifest))
    all_records: list[KBRecord] = []

    console.print(f"[cyan]building from {len(manifest)} crawled pages[/]\n")

    for entry in manifest:
        raw_path = Path(settings.data_dir.parent) / entry["raw_path"]
        if not raw_path.exists():
            stats.page_warnings.append(
                {"url": entry["url"], "warning": "raw snapshot missing"}
            )
            continue

        html = raw_path.read_text(encoding="utf-8", errors="ignore")
        page = extract_page(html, entry["url"])

        for w in page.warnings:
            stats.page_warnings.append({"url": entry["url"], "warning": w})

        if not page.sections:
            stats.pages_empty += 1
            continue

        stats.pages_parsed += 1
        stats.sections_total += len(page.sections)
        for s in page.sections:
            stats.sections_by_kind[s.kind] = stats.sections_by_kind.get(s.kind, 0) + 1

        for section in page.sections:
            all_records.extend(
                _section_to_records(
                    section=section,
                    category=entry["category"],
                    source_rule=entry["source_rule"],
                    lastmod=entry.get("lastmod"),
                    content_hash=entry["content_hash"],
                    stats=stats,
                )
            )

    stats.chunks_before_dedupe = len(all_records)
    console.print(f"  extracted [bold]{len(all_records)}[/] chunks, deduplicating...")

    # ---- dedupe across the whole corpus, not per page ----
    result = deduplicate([r.content for r in all_records])
    stats.exact_duplicates = result.exact_dupes
    stats.near_duplicates = result.near_dupes

    kept = [all_records[i] for i in result.kept]

    # Record ids must stay unique after dedupe collapses same-titled sections.
    seen_ids: Counter[str] = Counter()
    for rec in kept:
        seen_ids[rec.record_id] += 1
        if seen_ids[rec.record_id] > 1:
            rec.record_id = f"{rec.record_id}_{seen_ids[rec.record_id]:02d}"

    stats.records_final = len(kept)
    stats.categories = dict(Counter(r.category for r in kept).most_common())

    buckets = Counter()
    for r in kept:
        t = r.token_estimate
        bucket = (
            "0-100" if t <= 100 else
            "101-200" if t <= 200 else
            "201-300" if t <= 300 else
            "301-380" if t <= 380 else "380+"
        )
        buckets[bucket] += 1
    stats.token_histogram = dict(buckets)

    _write_outputs(kept, stats)
    _print_summary(stats)
    return kept


def _write_outputs(records: list[KBRecord], stats: BuildStats) -> None:
    records_path = settings.processed_dir / "kb_records.jsonl"
    with records_path.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(rec.model_dump_json() + "\n")

    report_path = settings.processed_dir / "build_report.json"
    report = {
        "built_at": datetime.now(timezone.utc).isoformat(),
        "config": {
            "chunk_target_tokens": settings.chunk_target_tokens,
            "chunk_max_tokens": settings.chunk_max_tokens,
            "chunk_overlap_tokens": settings.chunk_overlap_tokens,
        },
        "stats": asdict(stats),
    }
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    console.print(
        f"\n[green]wrote[/] {records_path}\n[green]wrote[/] {report_path}"
    )


def _print_summary(s: BuildStats) -> None:
    table = Table(title="KB build summary", show_header=True, header_style="bold")
    table.add_column("metric")
    table.add_column("value", justify="right")

    table.add_row("pages crawled", str(s.pages_total))
    table.add_row("pages parsed", str(s.pages_parsed))
    table.add_row("pages yielding nothing", str(s.pages_empty))
    table.add_row("sections extracted", str(s.sections_total))
    for kind, n in sorted(s.sections_by_kind.items()):
        table.add_row(f"  sections ({kind})", str(n))
    table.add_row("chunks produced", str(s.chunks_produced))
    table.add_row("  low-information dropped", str(s.dropped_low_information))
    table.add_row("chunks entering dedupe", str(s.chunks_before_dedupe))
    table.add_row("  exact duplicates removed", str(s.exact_duplicates))
    table.add_row("  near duplicates removed", str(s.near_duplicates))
    table.add_row("[bold]final records[/]", f"[bold]{s.records_final}[/]")
    table.add_row("records containing PII", str(s.records_with_pii))
    table.add_row("page warnings", str(len(s.page_warnings)))
    table.add_row("chunk warnings", str(len(s.chunk_warnings)))
    console.print()
    console.print(table)

    cat = Table(title="records by category", header_style="bold")
    cat.add_column("category")
    cat.add_column("count", justify="right")
    for k, v in s.categories.items():
        cat.add_row(k, str(v))
    console.print(cat)

    tok = Table(title="chunk size distribution (est. tokens)", header_style="bold")
    tok.add_column("bucket")
    tok.add_column("count", justify="right")
    for k in ["0-100", "101-200", "201-300", "301-380", "380+"]:
        if k in s.token_histogram:
            tok.add_row(k, str(s.token_histogram[k]))
    console.print(tok)


if __name__ == "__main__":
    build()
