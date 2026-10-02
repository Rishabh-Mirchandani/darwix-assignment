"""Terminology, amount, date and heading standardisation.

The brief asks us to "standardize headings, dates, terminology, categories and
form fields". On Indian health-insurance content that is not cosmetic -- it
directly decides whether retrieval works:

* **Terminology.** The same concept appears as "pre-existing disease", "PED"
  and "pre existing condition" across three pages. A caller asks about "PED".
  Lexical search (BM25) finds only the literal match, so without a canonical
  form one phrasing silently retrieves nothing. We therefore keep the original
  surface text *and* append a canonical alias line that the index can match on.

* **Amounts.** "Rs. 5 lakh", "INR 5,00,000", "₹5L" and "500000" are the same
  number written four ways. Indian grouping (5,00,000) also breaks naive
  comma-stripping. We parse to an integer and restate it unambiguously, so a
  bot quoting a sum insured cannot mangle it.

* **Dates.** DD/MM/YYYY and "15th March 2024" both become ISO, so version and
  effective-date metadata is sortable.
"""
from __future__ import annotations

import re
from datetime import date

# ---------------------------------------------------------------------------
# Terminology: canonical form -> surface variants seen in the wild
# ---------------------------------------------------------------------------

TERM_CANON: dict[str, tuple[str, ...]] = {
    "pre-existing disease": (
        "pre existing disease", "pre-existing condition", "pre existing condition",
        "ped", "pre-existing ailment", "preexisting disease",
    ),
    "sum insured": (
        "sum assured", "coverage amount", "cover amount", "si", "insured sum",
        "health cover amount",
    ),
    "waiting period": (
        "cooling period", "cooling-off period", "wait period", "initial waiting",
    ),
    "co-payment": ("co pay", "copay", "co-pay", "copayment", "cost sharing"),
    "no claim bonus": ("ncb", "cumulative bonus", "no-claim bonus", "bonus for no claim"),
    "cashless treatment": (
        "cashless facility", "cashless hospitalisation", "cashless hospitalization",
        "cashless claim", "cashless settlement",
    ),
    "room rent limit": (
        "room rent capping", "room rent cap", "room category limit",
        "room rent sub-limit", "room rent sublimit",
    ),
    "network hospital": ("empanelled hospital", "tie-up hospital", "partner hospital"),
    "third party administrator": ("tpa", "third-party administrator"),
    "day care procedure": (
        "day care treatment", "daycare procedure", "daycare treatment",
        "day-care procedure",
    ),
    "pre-hospitalisation expenses": (
        "pre hospitalization expenses", "pre-hospitalization expenses",
        "pre hospitalisation cost",
    ),
    "post-hospitalisation expenses": (
        "post hospitalization expenses", "post-hospitalization expenses",
        "post hospitalisation cost",
    ),
    "restoration benefit": ("refill benefit", "recharge benefit", "reinstatement benefit"),
    "free look period": ("free-look period", "look-in period", "review period"),
    "portability": ("policy portability", "port policy", "insurer transfer"),
    "grace period": ("renewal grace period", "premium grace period"),
    "domiciliary hospitalisation": (
        "domiciliary treatment", "home hospitalisation", "home treatment cover",
    ),
    "critical illness": ("ci cover", "critical illness cover", "major illness cover"),
    "maternity benefit": ("maternity cover", "maternity expenses", "delivery cover"),
    "annual health check-up": (
        "health checkup", "health check up", "preventive health check-up",
        "annual checkup",
    ),
}

# Reverse index built once: variant -> canonical
_VARIANT_TO_CANON: dict[str, str] = {}
for _canon, _variants in TERM_CANON.items():
    _VARIANT_TO_CANON[_canon] = _canon
    for _v in _variants:
        _VARIANT_TO_CANON[_v] = _canon

# Longest-first so "pre-existing condition" wins over "condition".
_TERM_RE = re.compile(
    r"\b(" + "|".join(
        re.escape(v) for v in sorted(_VARIANT_TO_CANON, key=len, reverse=True)
    ) + r")\b",
    re.I,
)


def find_terms(text: str) -> list[str]:
    """Canonical insurance terms present in `text`, deduplicated."""
    found = {_VARIANT_TO_CANON[m.group(1).lower()] for m in _TERM_RE.finditer(text)}
    return sorted(found)


# ---------------------------------------------------------------------------
# Amounts
# ---------------------------------------------------------------------------

_AMOUNT_RE = re.compile(
    r"(?:(?:rs\.?|inr|₹)\s*)?"
    r"(\d{1,3}(?:,\d{2,3})*(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"\s*(lakhs?|lacs?|crores?|cr|l|k)?\b",
    re.I,
)

_MULTIPLIER = {
    "l": 100_000, "lakh": 100_000, "lakhs": 100_000, "lac": 100_000, "lacs": 100_000,
    "cr": 10_000_000, "crore": 10_000_000, "crores": 10_000_000,
    "k": 1_000,
}


def parse_amount(raw: str, unit: str | None) -> int | None:
    """Parse an Indian-notation amount into rupees."""
    try:
        value = float(raw.replace(",", ""))
    except ValueError:
        return None
    if unit:
        value *= _MULTIPLIER.get(unit.lower(), 1)
    return int(round(value))


def format_amount(rupees: int) -> str:
    """Restate an amount unambiguously, with the Indian unit people speak."""
    if rupees >= 10_000_000 and rupees % 10_000_000 == 0:
        return f"INR {rupees:,} ({rupees // 10_000_000} crore)"
    if rupees >= 100_000 and rupees % 100_000 == 0:
        return f"INR {rupees:,} ({rupees // 100_000} lakh)"
    return f"INR {rupees:,}"


def extract_amounts(text: str) -> list[int]:
    """All monetary amounts in the text, as integers. Used as chunk metadata
    so 'plans with 10 lakh cover' can filter numerically."""
    out: list[int] = []
    for m in _AMOUNT_RE.finditer(text):
        # Require a currency marker or an explicit unit; a bare "30" is a day
        # count or an age, not money.
        has_currency = bool(re.match(r"(rs\.?|inr|₹)", m.group(0), re.I))
        if not (has_currency or m.group(2)):
            continue
        amount = parse_amount(m.group(1), m.group(2))
        if amount and amount >= 1000:
            out.append(amount)
    return sorted(set(out))


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------

_MONTHS = {
    m: i for i, m in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun",
         "jul", "aug", "sep", "oct", "nov", "dec"], start=1
    )
}

_DATE_NUMERIC = re.compile(r"\b(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})\b")
_DATE_WORDY = re.compile(
    r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,9})\.?,?\s+(\d{4})\b"
)


def to_iso(text: str) -> str:
    """Rewrite recognisable dates to ISO (YYYY-MM-DD) in place."""

    def _numeric(m: re.Match[str]) -> str:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if mo > 12:            # the two are swapped; assume DD/MM
            d, mo = mo, d
        try:
            return date(y, mo, d).isoformat()
        except ValueError:
            return m.group(0)

    def _wordy(m: re.Match[str]) -> str:
        mo = _MONTHS.get(m.group(2)[:3].lower())
        if not mo:
            return m.group(0)
        try:
            return date(int(m.group(3)), mo, int(m.group(1))).isoformat()
        except ValueError:
            return m.group(0)

    text = _DATE_NUMERIC.sub(_numeric, text)
    return _DATE_WORDY.sub(_wordy, text)


# ---------------------------------------------------------------------------
# Headings
# ---------------------------------------------------------------------------

_HEADING_NOISE = re.compile(
    r"^\s*(?:\d+[.)]\s*)?(.*?)\s*(?:\||-|–)\s*(?:niva\s*bupa|max\s*bupa).*$", re.I
)


def normalize_heading(heading: str) -> str:
    """Title-ise a heading and strip the site-name suffix SEO titles carry."""
    cleaned = _HEADING_NOISE.sub(r"\1", heading).strip(" -|–:")
    cleaned = re.sub(r"\s+", " ", cleaned)
    # Headings are spoken aloud by the voice agent, and TTS renders a bare
    # currency glyph inconsistently across voices. Normalise it the same way
    # record bodies are.
    cleaned = re.sub(r"₹\s*", "INR ", cleaned)
    cleaned = re.sub(r"\bRs\.?\s*", "INR ", cleaned)
    if cleaned.isupper() and len(cleaned) > 4:
        cleaned = cleaned.title()
    return cleaned.strip()


# Sales calls-to-action that trafilatura keeps because they are full sentences
# inside the content flow. They survive line-level boilerplate filtering, then
# surface mid-answer on a call ("...assessed based on policy exclusions. Get
# right coverage, right premium and the right protection instantly."), which
# reads as the bot pitching in the middle of a factual reply.
_CTA_SENTENCES = re.compile(
    r"(?:^|(?<=[.!?]))\s*"
    r"(?:[^.!?]*\b(?:"
    r"get (?:the )?right coverage|buy now|get a? ?quote|call us (?:now|today)|"
    r"talk to (?:our|an) (?:expert|advisor)|instantly|hassle[- ]free experience|"
    r"compare (?:plans|and buy)|secure your family today|protect your family today"
    r")\b[^.!?]*[.!?])",
    re.I,
)

# Content that only points at something that did not survive extraction.
_POINTER_ONLY = re.compile(
    r"\b(listed below|given below|mentioned below|as follows|the table below|"
    r"below are|following are|are listed here|some of them are)\b",
    re.I,
)


def strip_cta(text: str) -> str:
    """Remove sales CTA sentences from inside a content block."""
    cleaned = _CTA_SENTENCES.sub(" ", text)
    return re.sub(r"\s{2,}", " ", cleaned).strip()


def is_low_information(text: str, token_estimate: int) -> tuple[bool, str]:
    """True when a chunk is a stub rather than a retrievable fact.

    Returns (verdict, reason) so the build report can show *why* a record was
    dropped instead of silently shrinking the corpus.
    """
    stripped = text.strip()
    if not stripped:
        return True, "empty after cleaning"
    if token_estimate < 8:
        return True, f"too short ({token_estimate} tokens)"
    # A short block that merely announces a list whose items were lost.
    if token_estimate < 30 and _POINTER_ONLY.search(stripped):
        return True, "pointer to content that did not survive extraction"
    if token_estimate < 30 and stripped.endswith(":"):
        return True, "dangling lead-in (ends with colon, no body)"
    # Mostly punctuation or digits -> a stray table fragment.
    alpha = sum(c.isalpha() for c in stripped)
    if alpha < len(stripped) * 0.5:
        return True, "less than half alphabetic characters"
    return False, ""


# Markdown emphasis survives trafilatura's `include_formatting=True`, which we
# keep because the heading markers are what section splitting relies on. The
# emphasis markers are useless downstream and actively harmful for a voice
# agent: the record is read aloud, and the source frequently renders
# "**Label:**Text" with no space, so stripping has to restore the space too.
_MD_EMPHASIS = re.compile(r"\*\*|__|(?<![\w:])\*(?!\s)|(?<!\s)\*(?![\w*])")
_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_GLUED_COLON = re.compile(r":(?=[A-Z])")


def strip_markdown(text: str) -> str:
    """Remove emphasis/link syntax and repair glued punctuation."""
    text = _MD_LINK.sub(r"\1", text)          # keep link text, drop the URL
    text = _MD_EMPHASIS.sub("", text)
    text = _GLUED_COLON.sub(": ", text)       # "Diseases:Covered" -> "Diseases: Covered"
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text


def normalize_content(text: str) -> str:
    """Date normalisation plus whitespace hygiene, applied to record bodies."""
    text = strip_cta(text)
    text = strip_markdown(text)
    text = to_iso(text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Normalise the rupee symbol spacing so amounts read cleanly aloud.
    text = re.sub(r"₹\s*", "INR ", text)
    text = re.sub(r"\bRs\.?\s*", "INR ", text)
    return text.strip()
