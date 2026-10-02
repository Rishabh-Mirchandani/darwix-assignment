"""Source registry for the knowledge base.

The crawl is driven by an explicit allowlist rather than an open-ended spider.
That keeps us inside sections that carry policy content and makes provenance
auditable -- every record traces to a seed rule a human approved.

Source: Niva Bupa (standalone health insurer, India). Chosen because its
robots.txt grants AI crawlers explicit access (`User-agent: ClaudeBot /
Allow: /`), pages are server-rendered, and the site carries the mixed content
the brief asks for: product pages, policy rules, FAQs, tables and PDFs.

These rules were not guessed. `scripts/sitemap_families.py` grouped all 3,622
sitemap URLs by path family first; the counts below come from that pass, and
the exclusions exist because of what it found:

  disease-articles             1600   medical explainers, not policy -> excluded
  health-wellness-articles     1091   lifestyle content              -> excluded
  corporate-insurance-articles  236   B2B, out of scope for this bot -> excluded
  contact-us                    180   167 branch-address templates   -> excluded
  health-insurance-articles      68   real guides mixed with SEO spam -> filtered
  health-insurance               51   core policy concepts           -> INCLUDED
  family-health-insurance-plans  21   product pages                  -> INCLUDED
  insurance-faq                   6   dedicated FAQ pages            -> INCLUDED
  help-centre                     6   process + explainer content    -> INCLUDED

Without the branch-address exclusion, 67% of the corpus would have been 180
near-identical office-address pages, which would dominate retrieval and starve
the categories a qualification bot actually needs.
"""
from __future__ import annotations

from dataclasses import dataclass

BASE = "https://www.nivabupa.com"
SITEMAP = f"{BASE}/sitemap.xml"


@dataclass(frozen=True)
class SourceRule:
    """Maps a URL pattern to a taxonomy category.

    `category` becomes the KB record's category, so the taxonomy comes from the
    site's own information architecture rather than being guessed by an LLM
    after the fact.
    """

    name: str
    url_contains: tuple[str, ...]
    category: str
    priority: int = 1  # lower = crawled first when the page budget bites


RULES: list[SourceRule] = [
    # Dedicated FAQ pages: highest value for a voice agent, since the copy is
    # already phrased as answers to questions customers actually ask.
    SourceRule(
        name="faq_pages",
        url_contains=(
            "/insurance-faq/",
            "/help-centre/help-faq",
            "/help-centre/insurance-explained",
        ),
        category="faq",
        priority=0,
    ),
    # Named products the bot must be able to pitch and compare.
    SourceRule(
        name="product_plans",
        url_contains=(
            "/family-health-insurance-plans/",
            "/corporate-group-health-insurance/",
        ),
        category="product_plan",
        priority=0,
    ),
    # Curated subsets of the articles family must be matched BEFORE the broad
    # /health-insurance/ rule, otherwise everything collapses into one
    # category. Ordering here is load-bearing, not cosmetic.
    SourceRule(
        name="claims_process",
        url_contains=(
            "claim-rejection", "claims-procedure", "claim-status",
            "non-payable", "how-to-check-tpa",
        ),
        category="claims_process",
        priority=0,
    ),
    SourceRule(
        name="eligibility_and_renewal",
        url_contains=(
            "age-limit", "policy-renewal", "portability", "migration",
            "exclusion-list", "new-irdai-rules", "free-look", "grace-period",
            "pre-existing-diseases", "maternity-insurance-waiting-period",
        ),
        category="eligibility_rule",
        priority=0,
    ),
    SourceRule(
        name="service_policy",
        url_contains=(
            "customer-care-number", "/help-centre/helppurchasepolicy",
            "insurance-ombudsman", "grievance",
        ),
        category="service_policy",
        priority=1,
    ),
    # Policy mechanics: co-pay, deductibles, sub-limits, cashless, TPA.
    # Broadest rule, so it runs last.
    SourceRule(
        name="coverage_rules",
        url_contains=("/health-insurance/",),
        category="coverage_rule",
        priority=2,
    ),
]


# ---------------------------------------------------------------------------
# Exclusions
# ---------------------------------------------------------------------------

# Structural: robots.txt Disallow entries plus transactional/duplicate paths.
EXCLUDE_PATTERNS: tuple[str, ...] = (
    "/content/nivabupa/in/en/bank/",
    "/content/dam/",
    "/v1/",
    "/press-release",
    "/web-story",
    "/author-profile",
    "/sitemap",
    "?",                       # query strings -> duplicate content
    "/login",
    "/payment",
    "/add-review",
    # 167 near-identical branch pages. One office address adds nothing a
    # qualification bot can use, and 167 of them would swamp the index.
    "/contact-us/branch-address-",
)

# Content noise found *inside* otherwise-useful families. The articles section
# mixes genuine policy guides with SEO filler and agent-recruitment material,
# none of which a customer-facing bot should ever retrieve.
NOISE_PATTERNS: tuple[str, ...] = (
    # SEO filler unrelated to insurance
    "earn-money", "ghar-baithe", "part-time", "side-income", "remote-careers",
    "mahilaon", "jobs-in-hindi", "work-from-home", "dual-citizenship",
    # Agent recruitment / licensing exams: B2B, not customer-facing
    "ic38", "irda-exam", "irda-certificate", "irda-licence",
    "irda-insurance-agent", "agent-portal", "agents-commission",
    "agent-commission", "become-an-agent", "insurance-agent",
    # Non-English variants: the Q1 bot is English, and Q3 covers Filipino and
    # Bahasa Indonesia, not Indian regional languages. Including these would
    # add retrieval noise serving no configured market.
    "-in-hindi", "-in-tamil", "-in-telugu", "-in-marathi", "-in-bengali",
    # Hospital/city directories: lists of names, no policy content
    "best-hospitals-in",
    # Test/staging pages left live in the production sitemap
    "test-campaign", "test-lead-form", "aspire-test",
)

# Recorded explicitly so the data-quality report can cite them as *detected*
# source errors rather than silently dropped URLs.
KNOWN_SOURCE_ERRORS: tuple[str, ...] = (
    "/health-insurance-plans/test-campaign-Page.html",
    "/health-insurance-plans/aspire-test-lead-form.html",
)


def is_noise(url: str) -> bool:
    low = url.lower()
    return any(p in low for p in NOISE_PATTERNS)


def classify(url: str) -> SourceRule | None:
    """Return the first matching rule, or None if the URL is out of scope."""
    low = url.lower()
    if any(p in low for p in EXCLUDE_PATTERNS):
        return None
    if is_noise(low):
        return None
    for rule in sorted(RULES, key=lambda r: r.priority):
        if any(frag in low for frag in rule.url_contains):
            return rule
    return None
