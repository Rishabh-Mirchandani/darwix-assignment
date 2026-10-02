"""PII detection and redaction.

Scope note: this KB is built from a public marketing/policy website, so the
expected PII yield is low. That is exactly why the detector has to be
*validating* rather than greedy -- on public insurance pages, a naive
12-digit regex fires on sum-insured figures, claim statistics and phone
numbers far more often than on real Aadhaar numbers. Every false positive
redacts legitimate policy content and silently degrades retrieval.

So each detector pairs a pattern with a cheap structural check:

  * Aadhaar  -> Verhoeff checksum (the official scheme). Rejects ~90% of
                random 12-digit strings.
  * PAN      -> positional format AAAAA9999A plus a valid 4th-character
                holder-type code.
  * Phone    -> Indian mobile series must start 6-9; rejects years, amounts
                and pincodes.
  * Email    -> RFC-ish pattern, then a corporate-contact allowlist so that
                publishing `customer.care@` does not get flagged as personal.

The output feeds two fields on every KB record: `pii` (bool) and
`pii_types` (list), so a downstream consumer can filter or route without
re-scanning text.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

# --- Verhoeff checksum tables (used by UIDAI for Aadhaar) -------------------

_D = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    [1, 2, 3, 4, 0, 6, 7, 8, 9, 5],
    [2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
    [3, 4, 0, 1, 2, 8, 9, 5, 6, 7],
    [4, 0, 1, 2, 3, 9, 5, 6, 7, 8],
    [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
    [6, 5, 9, 8, 7, 1, 0, 4, 3, 2],
    [7, 6, 5, 9, 8, 2, 1, 0, 4, 3],
    [8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
    [9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
]
_P = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    [1, 5, 7, 6, 2, 8, 3, 0, 9, 4],
    [5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
    [8, 9, 1, 6, 0, 4, 3, 5, 2, 7],
    [9, 4, 5, 3, 1, 2, 6, 8, 7, 0],
    [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
    [2, 7, 9, 3, 8, 0, 6, 4, 1, 5],
    [7, 0, 4, 6, 9, 1, 3, 2, 5, 8],
]


def verhoeff_valid(number: str) -> bool:
    """True if `number` (digits only) passes the Verhoeff checksum."""
    if not number.isdigit():
        return False
    c = 0
    for i, digit in enumerate(reversed(number)):
        c = _D[c][_P[i % 8][int(digit)]]
    return c == 0


# --- Patterns --------------------------------------------------------------

AADHAAR_RE = re.compile(r"\b([2-9]\d{3})[\s-]?(\d{4})[\s-]?(\d{4})\b")
PAN_RE = re.compile(r"\b([A-Z]{5})(\d{4})([A-Z])\b")
PHONE_RE = re.compile(r"(?:\+?91[\s-]?)?\b([6-9]\d{9})\b")
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
POLICY_RE = re.compile(r"\b(?:policy\s*(?:no|number|#)\s*[:.\-]?\s*)([A-Z0-9/\-]{8,20})\b", re.I)
DOB_RE = re.compile(r"\b(0?[1-9]|[12]\d|3[01])[/\-](0?[1-9]|1[0-2])[/\-](19|20)\d{2}\b")

# PAN 4th char encodes holder type; anything else is not a real PAN.
_PAN_HOLDER_TYPES = set("ABCFGHLJPTK")

# Published corporate contacts are not personal data -- redacting them would
# strip exactly the escalation details the voice agent needs to hand out.
_GENERIC_EMAIL_LOCALPARTS = {
    "info", "support", "care", "customercare", "customer.care", "contact",
    "help", "helpdesk", "service", "services", "sales", "enquiry", "enquiries",
    "grievance", "grievances", "nodal", "claims", "claim", "feedback",
    "admin", "office", "hello", "noreply", "no-reply",
}

# Likewise, a published toll-free/landline contact is business information.
_KNOWN_CONTACT_PREFIXES = ("1800", "1860", "022", "011", "044", "080")

# Institutional domains. An address at the insurer, the regulator or the
# Insurance Ombudsman is a published business contact, not personal data.
#
# This was added after the PDF ingest flagged 47 records as containing PII:
# every one was an Ombudsman office address (bimalokpal.<city>@cioins.co.in)
# listed in the grievance-redressal annexe of a policy wording. Redacting those
# removes precisely the escalation contacts the agent needs to hand out -- a
# false positive here is not neutral, it deletes useful content.
_BUSINESS_EMAIL_DOMAINS = {
    "nivabupa.com", "maxbupa.com",
    "cioins.co.in",            # Council for Insurance Ombudsmen
    "irdai.gov.in", "irda.gov.in",
    "policyholder.gov.in",
    "gov.in", "nic.in",
}

# Local-part prefixes used by institutional mailboxes.
_GENERIC_EMAIL_PREFIXES = ("bimalokpal", "ombudsman", "grievance", "nodal",
                           "customer", "corporate", "compliance")


@dataclass
class PIIFinding:
    pii_type: str
    raw: str
    start: int
    end: int
    replacement: str


def _aadhaar_findings(text: str) -> Iterable[PIIFinding]:
    for m in AADHAAR_RE.finditer(text):
        digits = "".join(m.groups())
        if not verhoeff_valid(digits):
            continue  # almost certainly a sum insured / stat, not an Aadhaar
        yield PIIFinding("aadhaar", m.group(0), m.start(), m.end(), "[AADHAAR_REDACTED]")


def _pan_findings(text: str) -> Iterable[PIIFinding]:
    for m in PAN_RE.finditer(text):
        if m.group(1)[3] not in _PAN_HOLDER_TYPES:
            continue
        yield PIIFinding("pan", m.group(0), m.start(), m.end(), "[PAN_REDACTED]")


def _phone_findings(text: str) -> Iterable[PIIFinding]:
    for m in PHONE_RE.finditer(text):
        number = m.group(1)
        if number.startswith(_KNOWN_CONTACT_PREFIXES):
            continue
        # Reject matches embedded in longer digit runs (IDs, amounts).
        before = text[max(0, m.start() - 1): m.start()]
        after = text[m.end(): m.end() + 1]
        if before.isdigit() or after.isdigit():
            continue
        yield PIIFinding("phone", m.group(0), m.start(), m.end(), "[PHONE_REDACTED]")


def _email_findings(text: str) -> Iterable[PIIFinding]:
    for m in EMAIL_RE.finditer(text):
        address = m.group(0).lower()
        local, _, domain = address.partition("@")

        if local in _GENERIC_EMAIL_LOCALPARTS:
            continue
        if local.startswith(_GENERIC_EMAIL_PREFIXES):
            continue
        # Match the domain or any parent of it, so sub.cioins.co.in is covered.
        parts = domain.split(".")
        if any(
            ".".join(parts[i:]) in _BUSINESS_EMAIL_DOMAINS
            for i in range(len(parts))
        ):
            continue

        yield PIIFinding("email", m.group(0), m.start(), m.end(), "[EMAIL_REDACTED]")


def _policy_findings(text: str) -> Iterable[PIIFinding]:
    for m in POLICY_RE.finditer(text):
        yield PIIFinding(
            "policy_number", m.group(1), m.start(1), m.end(1), "[POLICY_NO_REDACTED]"
        )


def _dob_findings(text: str) -> Iterable[PIIFinding]:
    for m in DOB_RE.finditer(text):
        yield PIIFinding("date_of_birth", m.group(0), m.start(), m.end(), "[DOB_REDACTED]")


_DETECTORS = (
    _aadhaar_findings,
    _pan_findings,
    _phone_findings,
    _email_findings,
    _policy_findings,
    _dob_findings,
)


def detect(text: str) -> list[PIIFinding]:
    """Return all validated PII findings, sorted by position."""
    findings: list[PIIFinding] = []
    for detector in _DETECTORS:
        findings.extend(detector(text))
    return sorted(findings, key=lambda f: f.start)


def redact(text: str) -> tuple[str, list[str]]:
    """Return (redacted_text, sorted_unique_pii_types).

    Replacement runs right-to-left so earlier offsets stay valid.
    """
    findings = detect(text)
    if not findings:
        return text, []

    out = text
    for f in sorted(findings, key=lambda f: f.start, reverse=True):
        out = out[: f.start] + f.replacement + out[f.end:]
    return out, sorted({f.pii_type for f in findings})


def scan(text: str) -> tuple[bool, list[str]]:
    """Cheap check used when we only need the flags, not the redacted text."""
    types = sorted({f.pii_type for f in detect(text)})
    return bool(types), types
