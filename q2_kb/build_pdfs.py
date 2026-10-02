"""Build KB records from the fetched policy-wording PDFs.

Runs the *same* clean/normalise/PII/chunk pipeline as the web build, so PDF and
web records are interchangeable at retrieval time and differ only in
`source_type` and the citation shape (page number instead of anchor).

Policy wordings are the authoritative source for the rules a qualification bot
quotes. Where a web page says "a waiting period applies", the wording states the
months, the exceptions and the conditions -- so these records are given the
same confidence floors but tend to score higher on specific rule queries.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from rich.console import Console
from rich.table import Table

from shared.config import settings
from q2_kb.clean import pii as pii_mod
from q2_kb.clean.dedupe import deduplicate
from q2_kb.clean.normalize import (
    extract_amounts, find_terms, is_low_information,
    normalize_content, normalize_heading,
)
from q2_kb.index.chunk import estimate_tokens
from q2_kb.index.schema import KBRecord, make_record_id
from q2_kb.ingest.pdfs import extract_pdf_sections

console = Console()

# Policy wordings are long; a clause split mid-sentence is worse here than
# anywhere else because the exception usually follows the rule.
MAX_PDF_CHUNK_TOKENS = 420


def _split_long(text: str, limit: int) -> list[str]:
    """Split on sentence boundaries only, never mid-clause."""
    if estimate_tokens(text) <= limit:
        return [text]
    import re

    sentences = re.split(r"(?<=[.;])\s+(?=[A-Z(])", text)
    out, cur, cur_tok = [], [], 0
    for s in sentences:
        t = estimate_tokens(s)
        if cur and cur_tok + t > limit:
            out.append(" ".join(cur))
            cur, cur_tok = [s], t
        else:
            cur.append(s)
            cur_tok += t
    if cur:
        out.append(" ".join(cur))
    return out


def build() -> list[KBRecord]:
    manifest_path = settings.interim_dir / "pdf_manifest.json"
    if not manifest_path.exists():
        raise SystemExit("No pdf_manifest.json. Run: python -m q2_kb.ingest.pdfs")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = [r for r in manifest["records"] if r["status"] == "ok"]

    all_records: list[KBRecord] = []
    stats = Counter()
    warnings: list[dict] = []

    for entry in entries:
        path = Path(settings.data_dir.parent) / entry["raw_path"]
        if not path.exists():
            warnings.append({"product": entry["product"], "warning": "file missing"})
            continue

        sections = extract_pdf_sections(path, entry["product"])
        stats["sections"] += len(sections)
        console.print(
            f"  {entry['product']:<20} {entry['pages']:>3} pages -> "
            f"{len(sections):>3} sections"
        )

        for sec in sections:
            heading = normalize_heading(sec["heading"])
            body = normalize_content(sec["content"])
            if not heading or not body:
                continue

            for idx, chunk in enumerate(_split_long(body, MAX_PDF_CHUNK_TOKENS)):
                redacted, pii_types = pii_mod.redact(chunk)
                tokens = estimate_tokens(redacted)

                low, reason = is_low_information(redacted, tokens)
                if low:
                    stats["dropped_low_info"] += 1
                    continue

                title = f"{entry['product']}: {heading}"[:160]
                rec = KBRecord(
                    record_id=make_record_id(
                        f"pdf_{entry['category']}", title, idx
                    ),
                    title=title,
                    content=redacted,
                    answer_text=redacted,
                    category=entry["category"],       # type: ignore[arg-type]
                    market="in",
                    section_kind=sec["kind"],          # type: ignore[arg-type]
                    source=f"{entry['product']} policy wording / PDF page {sec['page']}",
                    source_url=entry["url"],
                    source_anchor=f"page={sec['page']}",
                    source_type="pdf",
                    content_hash=entry["content_hash"],
                    version="1.0",
                    pii=bool(pii_types),
                    pii_types=pii_types,
                    canonical_terms=find_terms(redacted),
                    amounts=extract_amounts(redacted),
                    token_estimate=tokens,
                )
                all_records.append(rec)
                stats["chunks"] += 1
                if pii_types:
                    stats["with_pii"] += 1

    # Dedupe across PDFs: policy wordings share large boilerplate clauses
    # (grievance redressal, definitions) almost verbatim between products.
    console.print(f"\n  deduplicating {len(all_records)} chunks...")
    result = deduplicate([r.content for r in all_records])
    kept = [all_records[i] for i in result.kept]
    stats["exact_dupes"] = result.exact_dupes
    stats["near_dupes"] = result.near_dupes

    seen: Counter[str] = Counter()
    for r in kept:
        seen[r.record_id] += 1
        if seen[r.record_id] > 1:
            r.record_id = f"{r.record_id}_{seen[r.record_id]:02d}"

    out = settings.processed_dir / "pdf_records.jsonl"
    with out.open("w", encoding="utf-8") as fh:
        for r in kept:
            fh.write(r.model_dump_json() + "\n")

    report = settings.processed_dir / "pdf_build_report.json"
    report.write_text(json.dumps({
        "pdfs": len(entries),
        "pages": sum(e["pages"] for e in entries),
        "sections": stats["sections"],
        "chunks_produced": stats["chunks"],
        "dropped_low_information": stats["dropped_low_info"],
        "exact_duplicates": stats["exact_dupes"],
        "near_duplicates": stats["near_dupes"],
        "records_final": len(kept),
        "records_with_pii": stats["with_pii"],
        "by_category": dict(Counter(r.category for r in kept).most_common()),
        "by_section_kind": dict(Counter(r.section_kind for r in kept).most_common()),
        "warnings": warnings,
    }, indent=2), encoding="utf-8")

    t = Table(title="PDF build", header_style="bold")
    t.add_column("metric"); t.add_column("value", justify="right")
    t.add_row("PDFs", str(len(entries)))
    t.add_row("pages", str(sum(e["pages"] for e in entries)))
    t.add_row("sections extracted", str(stats["sections"]))
    t.add_row("chunks produced", str(stats["chunks"]))
    t.add_row("  low-information dropped", str(stats["dropped_low_info"]))
    t.add_row("  exact duplicates", str(stats["exact_dupes"]))
    t.add_row("  near duplicates", str(stats["near_dupes"]))
    t.add_row("[bold]final records[/]", f"[bold]{len(kept)}[/]")
    t.add_row("records with PII", str(stats["with_pii"]))
    console.print()
    console.print(t)
    console.print(f"\n[green]wrote[/] {out}\n[green]wrote[/] {report}")
    return kept


if __name__ == "__main__":
    build()
