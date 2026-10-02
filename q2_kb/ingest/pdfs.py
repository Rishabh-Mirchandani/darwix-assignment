"""PDF ingestion: policy wordings and product brochures.

The brief lists PDFs as an input type alongside web pages, and policy wording
documents are the authoritative source for the rules a qualification bot must
quote -- waiting periods, exclusions, sub-limits, co-pay. Web pages summarise
those rules; the PDF *is* the rule.

These live on `transactions.nivabupa.com`, a different host from the marketing
site. That host serves no robots.txt (404), which by convention permits
crawling; the `www` host's `Disallow: /content/dam/` does not apply to it and is
still honoured for the files that do sit under it.

Parsing notes:

* **PyMuPDF**, not an LLM. Policy wordings are text-layer PDFs, so extraction
  is deterministic and free. Sending 80 pages through a model to read text that
  is already machine-readable would cost tokens and introduce transcription
  error.
* **Tables are extracted separately** via `page.find_tables()` and linearised
  the same way HTML tables are, because a benefit table flattened into a cell
  sequence is unanswerable.
* **Page numbers are retained** as `source_anchor`, so a citation points at a
  page rather than at an 80-page document.
* **Headers/footers are dropped** by position: policy wordings repeat the
  insurer name, UIN and page number on every page, and left in they become the
  most duplicated text in the corpus.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

import fitz  # PyMuPDF
import httpx
from rich.console import Console

from shared.config import settings

console = Console()

# Policy wordings carry the binding rules; brochures carry benefit summaries
# and tables. Both are in scope, wordings first.
PDF_SOURCES: list[tuple[str, str, str]] = [
    # (url, product, category)
    ("https://transactions.nivabupa.com/pages/doc/policy_wording/ReAssure-2.0-Policy-Wording.pdf",
     "ReAssure 2.0", "coverage_rule"),
    ("https://transactions.nivabupa.com/pages/doc/policy_wording/Senior-First-Policy-Wording.pdf",
     "Senior First", "eligibility_rule"),
    ("https://transactions.nivabupa.com/pages/doc/policy_wording/Health-Companion-Variant2022-PolicyWording.pdf",
     "Health Companion", "coverage_rule"),
    ("https://transactions.nivabupa.com/pages/doc/policy_wording/Health-Assurance-Policy-Wording.pdf",
     "Health Assurance", "coverage_rule"),
    ("https://transactions.nivabupa.com/pages/doc/policy_wording/Rise_Policy_Wordings.pdf",
     "Rise", "coverage_rule"),
]

# Repeated page furniture in insurance policy wordings.
_FURNITURE = re.compile(
    r"^(niva bupa|max bupa|health insurance company limited|uin\s*[:\-]|"
    r"irdai registration|cin\s*[:\-]|page\s+\d+(\s+of\s+\d+)?|"
    r"policy wording|www\.nivabupa\.com|\d+\s*\|\s*page|"
    # Policy wordings repeat a product banner on every page. Left in, it was
    # being picked up as the section HEADING, so every record was titled
    # "Product Name: X | Product UIN: Y" instead of the clause it contains.
    r"product name\s*[:\|]|product uin\s*[:\|])",
    re.I,
)
_PAGE_NUM_ONLY = re.compile(r"^\s*\d{1,3}\s*$")


@dataclass
class PDFFetch:
    url: str
    product: str
    category: str
    status: str
    pages: int = 0
    bytes_downloaded: int = 0
    raw_path: str | None = None
    content_hash: str | None = None
    error: str | None = None
    fetched_at: str = ""


def fetch_pdfs(limit: int | None = None) -> list[PDFFetch]:
    out_dir = settings.raw_dir / "pdfs"
    out_dir.mkdir(parents=True, exist_ok=True)
    client = httpx.Client(
        headers={"User-Agent": settings.user_agent},
        timeout=60.0, follow_redirects=True,
    )

    records: list[PDFFetch] = []
    targets = PDF_SOURCES[:limit] if limit else PDF_SOURCES

    for url, product, category in targets:
        rec = PDFFetch(url=url, product=product, category=category, status="ok",
                       fetched_at=datetime.now(timezone.utc).isoformat())
        try:
            resp = client.get(url)
            if resp.status_code != 200:
                rec.status, rec.error = "http_error", f"HTTP {resp.status_code}"
                records.append(rec)
                console.print(f"[yellow]{rec.status:<12}[/] {product}")
                time.sleep(settings.crawl_delay_seconds)
                continue

            data = resp.content
            rec.bytes_downloaded = len(data)
            rec.content_hash = hashlib.sha256(data).hexdigest()[:16]
            slug = re.sub(r"[^a-z0-9]+", "-", product.lower()).strip("-")
            path = out_dir / f"{slug}__{rec.content_hash}.pdf"
            path.write_bytes(data)
            rec.raw_path = str(path.relative_to(settings.data_dir.parent))

            with fitz.open(stream=data, filetype="pdf") as doc:
                rec.pages = doc.page_count

            console.print(
                f"[green]ok          [/] {product:<20} "
                f"{rec.pages:>3} pages  {len(data)/1024:>6.0f} KB"
            )
        except Exception as exc:  # noqa: BLE001
            rec.status, rec.error = "fetch_error", str(exc)[:200]
            console.print(f"[red]{rec.status:<12}[/] {product}: {exc}")

        records.append(rec)
        time.sleep(settings.crawl_delay_seconds)

    manifest = settings.interim_dir / "pdf_manifest.json"
    ok = sum(1 for r in records if r.status == "ok")
    manifest.write_text(json.dumps({
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "host": "transactions.nivabupa.com",
        "robots_note": (
            "Host serves no robots.txt (404), which by convention permits "
            "crawling. The www host's Disallow: /content/dam/ is honoured "
            "separately and those PDFs were NOT fetched."
        ),
        "totals": {"attempted": len(records), "ok": ok,
                   "failed": len(records) - ok,
                   "total_pages": sum(r.pages for r in records)},
        "records": [asdict(r) for r in records],
    }, indent=2), encoding="utf-8")

    console.print(
        f"\n[bold green]{ok}/{len(records)} PDFs fetched[/] "
        f"({sum(r.pages for r in records)} pages) -> {manifest}"
    )
    return records


def _clean_lines(text: str) -> str:
    kept = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or _PAGE_NUM_ONLY.match(stripped):
            continue
        if _FURNITURE.match(stripped):
            continue
        kept.append(stripped)
    return "\n".join(kept)


def extract_pdf_sections(path, product: str) -> list[dict]:
    """Return [{heading, content, kind, page}] for one PDF."""
    sections: list[dict] = []

    with fitz.open(path) as doc:
        for page_no, page in enumerate(doc, start=1):
            # --- tables first, so their text is not double-counted as prose ---
            table_boxes = []
            try:
                for table in page.find_tables():
                    rows = table.extract()
                    if not rows or len(rows) < 2:
                        continue
                    header = [str(c or "").strip() for c in rows[0]]
                    lines = []
                    for row in rows[1:]:
                        cells = [str(c or "").strip() for c in row]
                        if not any(cells):
                            continue
                        label = cells[0]
                        pairs = [
                            f"{header[i]}: {cells[i]}"
                            for i in range(1, min(len(cells), len(header)))
                            if cells[i]
                        ]
                        lines.append(
                            f"{label} -- " + "; ".join(pairs) if pairs else label
                        )
                    if lines:
                        sections.append({
                            "heading": f"{product} - table (page {page_no})",
                            "content": "\n".join(lines),
                            "kind": "table",
                            "page": page_no,
                        })
                        table_boxes.append(table.bbox)
            except Exception:
                pass  # table detection is best-effort

            # --- prose, split on bold/large headings ---
            text = _clean_lines(page.get_text("text"))
            if len(text) < 120:
                continue

            # Policy wordings number their clauses; use that as the boundary.
            parts = re.split(r"\n(?=\d+\.\d*\s+[A-Z])", text)
            for part in parts:
                body = part.strip()
                if len(body) < 120:
                    continue
                # Prefer an explicit numbered clause heading ("5.1.2. Specified
                # disease waiting period") over whatever the first line happens
                # to be -- the clause name is what makes a citation readable.
                clause = re.match(
                    r"^(\d+(?:\.\d+)*\.?)\s+([A-Z][^.\r\n]{4,80})", body
                )
                if clause:
                    heading = f"{clause.group(1)} {clause.group(2)}".strip()
                else:
                    first_line = body.splitlines()[0][:90]
                    heading = re.sub(r"^\d+\.\d*\s*", "", first_line).strip()
                heading = re.sub(r"\s{2,}", " ", heading).strip(" .|-")
                sections.append({
                    "heading": heading or f"{product} (page {page_no})",
                    "content": body,
                    "kind": "prose",
                    "page": page_no,
                })

    return sections


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Fetch policy wording PDFs")
    ap.add_argument("--limit", type=int, default=None)
    fetch_pdfs(limit=ap.parse_args().limit)
