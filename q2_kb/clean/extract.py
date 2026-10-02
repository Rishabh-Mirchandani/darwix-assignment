"""HTML -> structured sections.

Two extractors run over every page and their outputs are merged:

1. **Trafilatura** for the main editorial body. It is trained to drop nav,
   headers, footers and promo rails, which is the bulk of an insurer's page.
2. **BeautifulSoup**, targeted, for the structures trafilatura flattens or
   drops: FAQ accordions (the question/answer pairing is semantic and must
   survive) and comparison tables (where a flattened cell sequence becomes
   meaningless -- "Platinum 50000 Gold 25000" is not a retrievable fact).

The unit of output is a *section*: one heading plus the prose beneath it.
Sections -- not pages -- are what later gets chunked, because a heading is the
natural boundary for a policy rule and it doubles as the record title.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import trafilatura
from bs4 import BeautifulSoup, Tag

# Repeated chrome that survives trafilatura on insurance sites. Matched
# case-insensitively against a whole line, so it cannot eat real prose.
BOILERPLATE_LINES = {
    "buy now", "get quote", "get a quote", "calculate premium", "renew now",
    "download brochure", "download policy wording", "view all", "read more",
    "know more", "learn more", "apply now", "share this", "back to top",
    "home", "contact us", "about us", "careers", "sitemap", "privacy policy",
    "terms and conditions", "disclaimer", "follow us", "connect with us",
    "customer login", "agent login", "pay premium", "track application",
    "find a hospital", "locate branch", "faqs", "related articles",
    "you may also like", "recommended for you", "trending", "load more",
}

# Marketing superlatives that carry no policy information. Dropped only when
# they constitute the entire line.
MARKETING_ONLY = re.compile(
    r"^(india'?s\s+)?(no\.?\s*1|best|top|leading|trusted|award[- ]winning|"
    r"\#1)[\w\s,'-]*$",
    re.I,
)

IRDAI_DISCLAIMER = re.compile(
    r"(irdai\s+registration|cin\s*[:#]|uin\s*[:#]|insurance is the subject matter)",
    re.I,
)


@dataclass
class Section:
    """A heading plus the text under it, with provenance back to the page."""

    heading: str
    content: str
    kind: str = "prose"          # prose | faq | definition | table
    heading_level: int = 2
    source_url: str = ""
    anchor: str | None = None    # fragment id, when the DOM gives us one

    @property
    def token_estimate(self) -> int:
        # Deliberately crude: ~0.75 words/token is close enough for chunk
        # sizing and avoids pulling a tokenizer into the ingest path.
        return int(len(self.content.split()) / 0.75)


@dataclass
class PageExtract:
    url: str
    title: str
    sections: list[Section] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _is_boilerplate(line: str) -> bool:
    stripped = line.strip().lower().rstrip(":.! ")
    if not stripped:
        return True
    if stripped in BOILERPLATE_LINES:
        return True
    if MARKETING_ONLY.match(stripped):
        return True
    # Single words and stray punctuation are nav remnants.
    if len(stripped) < 3:
        return True
    return False


def clean_text_block(text: str) -> str:
    """Drop boilerplate lines and collapse whitespace."""
    kept = [ln.strip() for ln in text.splitlines() if not _is_boilerplate(ln)]
    joined = "\n".join(kept)
    joined = re.sub(r"\n{3,}", "\n\n", joined)
    joined = re.sub(r"[ \t]{2,}", " ", joined)
    return joined.strip()


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60]


# ---------------- structured extractors ----------------


def _classify_accordion(label: str) -> str:
    """An accordion label ending in '?' is a true FAQ; otherwise it names a
    benefit or feature. Both are atomic, but they answer different intents, so
    they are tagged separately and carry different categories downstream."""
    return "faq" if label.rstrip().endswith("?") else "definition"


def extract_accordions(soup: BeautifulSoup, url: str) -> list[Section]:
    """Pull heading/body pairs out of accordion widgets.

    These are the highest-value records in the corpus. Two kinds live in the
    same markup: genuine FAQ entries ("What documents do I need?") and
    structured benefit blocks ("Alternative Treatments" -> the AYUSH rule).
    Both are already written as a self-contained statement, which is exactly
    the shape a voice agent needs, so neither is left to prose flattening.

    Three strategies, most-specific first. Note the case-insensitive `i` flag
    on the attribute selectors: this site renders Mantine components with a
    capital 'A' (`mantine-Accordion-item`), and a case-sensitive
    `[class*="accordion"]` silently matches nothing -- which is precisely the
    bug that made an earlier build extract zero FAQ sections from 958
    available items.
    """
    sections: list[Section] = []
    seen: set[str] = set()

    def add(label: str, body: str) -> None:
        label = re.sub(r"\s+", " ", label).strip()
        body = re.sub(r"\s+", " ", body).strip()
        if not label or len(body) < 25 or label in seen:
            return
        # A panel that merely repeats its label carries no information.
        if body.lower().startswith(label.lower()) and len(body) < len(label) * 1.4:
            return
        seen.add(label)
        sections.append(
            Section(
                heading=label,
                content=body,
                kind=_classify_accordion(label),
                source_url=url,
                anchor=_slug(label),
            )
        )

    # Strategy A: schema.org FAQPage markup is unambiguous when present.
    for q in soup.select('[itemtype*="Question" i]'):
        name = q.select_one('[itemprop="name"]')
        answer = q.select_one('[itemprop="text"], [itemprop="acceptedAnswer"]')
        if name and answer:
            add(name.get_text(" ", strip=True), answer.get_text(" ", strip=True))

    # Strategy B: Mantine accordions -- the pattern this site actually uses.
    for item in soup.select('[class*="Accordion-item" i]'):
        if not isinstance(item, Tag):
            continue
        label_el = item.select_one('[class*="Accordion-label" i]')
        panel_el = item.select_one(
            '[class*="Accordion-content" i], [class*="Accordion-panel" i]'
        )
        if label_el and panel_el:
            add(label_el.get_text(" ", strip=True), panel_el.get_text(" ", strip=True))

    # Strategy C: generic accordion/collapse markup, as a fallback.
    for node in soup.select('[class*="accordion" i], [class*="faq" i], [class*="collapse" i]'):
        if not isinstance(node, Tag):
            continue
        head = node.select_one(
            'h2, h3, h4, h5, summary, [class*="title" i], [class*="question" i], '
            '[class*="label" i]'
        )
        if not head:
            continue
        label = head.get_text(" ", strip=True)
        full = node.get_text(" ", strip=True)
        body = full.replace(label, "", 1).strip()
        # Guard against matching a whole page wrapper named "faq-section".
        if len(body) > 4000:
            continue
        add(label, body)

    return sections


def extract_tables(soup: BeautifulSoup, url: str) -> list[Section]:
    """Linearise tables into readable sentences.

    A table rendered as bare cells loses the column context that makes it
    answerable. "Room rent: Single private AC room" is retrievable;
    "Single private AC room" next to eleven other cells is not.
    """
    sections: list[Section] = []
    for idx, table in enumerate(soup.find_all("table")):
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue

        header_cells = [
            c.get_text(" ", strip=True) for c in rows[0].find_all(["th", "td"])
        ]
        if not any(header_cells):
            continue

        lines: list[str] = []
        for row in rows[1:]:
            cells = [c.get_text(" ", strip=True) for c in row.find_all(["td", "th"])]
            if not any(cells):
                continue
            label = cells[0]
            pairs = [
                f"{header_cells[i]}: {cells[i]}"
                for i in range(1, min(len(cells), len(header_cells)))
                if cells[i]
            ]
            lines.append(f"{label} -- " + "; ".join(pairs) if pairs else label)

        if not lines:
            continue

        # Nearest preceding heading gives the table a name.
        prev = table.find_previous(["h1", "h2", "h3", "h4"])
        heading = prev.get_text(" ", strip=True) if prev else f"Comparison table {idx + 1}"
        sections.append(
            Section(
                heading=heading,
                content="\n".join(lines),
                kind="table",
                source_url=url,
                anchor=_slug(heading),
            )
        )
    return sections


def extract_prose(html: str, url: str) -> tuple[str, list[Section], list[str]]:
    """Trafilatura pass -> (page_title, sections, warnings)."""
    warnings: list[str] = []
    extracted = trafilatura.extract(
        html,
        include_comments=False,
        include_tables=False,     # handled by extract_tables for structure
        include_formatting=True,  # keeps '##' markers we split on
        favor_precision=True,     # prefer dropping chrome over keeping it
        url=url,
    )
    if not extracted:
        warnings.append("trafilatura returned no main content")
        return "", [], warnings

    meta = trafilatura.extract_metadata(html)
    title = (meta.title if meta and meta.title else "").strip()

    sections: list[Section] = []
    current_heading = title or "Overview"
    current_level = 1
    buffer: list[str] = []

    def flush() -> None:
        body = clean_text_block("\n".join(buffer))
        if len(body) > 80:  # below this it is a stray label, not content
            sections.append(
                Section(
                    heading=current_heading,
                    content=body,
                    kind="prose",
                    heading_level=current_level,
                    source_url=url,
                    anchor=_slug(current_heading),
                )
            )

    for line in extracted.splitlines():
        m = re.match(r"^(#{1,6})\s+(.*)$", line.strip())
        if m:
            flush()
            buffer = []
            current_level = len(m.group(1))
            current_heading = m.group(2).strip()
        else:
            buffer.append(line)
    flush()

    if not sections:
        warnings.append("no sections survived cleaning")
    return title, sections, warnings


def extract_page(html: str, url: str) -> PageExtract:
    """Full extraction for one raw HTML snapshot."""
    soup = BeautifulSoup(html, "lxml")

    # Remove obviously non-content nodes before either extractor sees them.
    for tag in soup(["script", "style", "noscript", "nav", "header", "footer", "form"]):
        tag.decompose()

    title, prose, warnings = extract_prose(html, url)
    accordions = extract_accordions(soup, url)
    tables = extract_tables(soup, url)

    page = PageExtract(url=url, title=title, warnings=warnings)
    page.sections = prose + accordions + tables

    # Flag obvious source errors for the manifest.
    body_len = sum(len(s.content) for s in page.sections)
    if body_len < 400:
        page.warnings.append(f"very little content extracted ({body_len} chars)")
    if IRDAI_DISCLAIMER.search(html) and not page.sections:
        page.warnings.append("page appears to be disclaimer-only")

    return page
