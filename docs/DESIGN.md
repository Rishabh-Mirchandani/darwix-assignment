# Q2 — Knowledge base design

Covers the items the brief names explicitly: extraction and parsing, cleaning,
schema and sample records, chunking and metadata, taxonomy and source tracking,
versioning, embedding/indexing, retrieval/ranking, and citation.

---

## 1. Website extraction and document parsing

**Discovery is sitemap-driven, not a link spider.** A spider on an insurance
site drowns in nav/footer permutations and reaches the same page under a dozen
paths. The sitemap is the publisher's own canonical list: 3,622 URLs.

**Scope is an explicit allowlist**, not everything. `scripts/sitemap_families.py`
grouped all URLs by path family first, and the rules in
`q2_kb/ingest/sources.py` were written from what that showed:

| family | count | decision |
|---|---|---|
| `disease-articles` | 1,600 | excluded — medical explainers, not policy |
| `health-wellness-articles` | 1,091 | excluded — lifestyle |
| `corporate-insurance-articles` | 236 | excluded — B2B |
| `contact-us` | 180 | **167 excluded** — near-identical branch addresses |
| `health-insurance-articles` | 68 | filtered — real guides mixed with SEO spam |
| `health-insurance` | 51 | **included** — core policy mechanics |
| `family-health-insurance-plans` | 21 | **included** — product pages |
| `insurance-faq` | 6 | **included** — highest-value content |
| `help-centre` | 6 | **included** |

Result: 3,622 → **105 in scope**, balanced across six categories.

Without the branch-address exclusion, 67% of the corpus would have been office
addresses — they would have dominated retrieval and starved the categories a
qualification bot actually needs.

**robots.txt is enforced per-URL**, not read once for appearance. The source was
chosen partly because its robots.txt grants AI crawlers explicit access
(`User-agent: ClaudeBot / Allow: /`). Crawl delay 1.5s.

**Raw HTML is written to disk before parsing**, so cleaning is a pure function
of a snapshot and can be re-run and diffed without re-hitting the network.

### Two extractors, merged

1. **Trafilatura** for the editorial body (`favor_precision=True` — prefer
   dropping chrome over keeping it).
2. **BeautifulSoup, targeted**, for what trafilatura flattens:
   - **Accordions** (FAQ + benefit blocks)
   - **Tables**, linearised into sentences. A table rendered as bare cells
     loses the column context that makes it answerable: `Room rent: Single
     private AC room` is retrievable, `Single private AC room` next to eleven
     other cells is not.

> **Bug worth recording:** the accordion extractor originally returned **zero**
> sections from **958 available items**. The selector `[class*="accordion"]` is
> case-sensitive CSS and the site renders `mantine-Accordion-item`. The build
> reported success throughout. Fixed with the `i` flag; it recovered 332 FAQ
> and 346 definition sections — the single largest quality gain in the project.

---

### Document parsing: policy-wording PDFs

Web pages *summarise* policy rules; the policy wording **is** the rule. Five
wordings were ingested (159 pages) from `transactions.nivabupa.com`,
a different host from the marketing site. That host serves no robots.txt (404),
which by convention permits crawling; the `www` host's `Disallow: /content/dam/`
is honoured separately and those PDFs were deliberately **not** fetched.

| | |
|---|---|
| PDFs / pages | 5 / 159 |
| sections extracted | 302 |
| chunks produced | 411 |
| exact / near duplicates removed | 19 / 34 |
| **final records** | **358** |

Parsing decisions:

* **PyMuPDF, not an LLM.** These are text-layer PDFs, so extraction is
  deterministic and free. Sending 159 pages through a model to read text that is
  already machine-readable would cost tokens and add transcription error.
* **Tables extracted separately** via `page.find_tables()` and linearised the
  same way HTML tables are. 146 table records in the final index come from here.
* **Page numbers retained** as `source_anchor`, so a citation points at a page
  rather than at an 80-page document.
* **Repeated page furniture dropped.** Wordings repeat the insurer name, UIN and
  page number on every page. An early version also picked that banner up as the
  section *heading*, so every record was titled "Product Name: X | Product UIN:
  Y"; headings now prefer the numbered clause ("5.1.2 Specified disease waiting
  period"), which is what makes a citation readable.
* **Near-duplicate rate is high between wordings** (34 removed) because
  definitions and grievance-redressal clauses are near-identical across products
  — exactly the case MinHash is for.

One retrieval effect worth noting: on a specific rule query the PDF now wins.
"what is the specific disease waiting period under ReAssure" returns clause
5.1.2 with its exclusion code, which no marketing page states.

## 2. Cleaning

| step | approach | measured outcome |
|---|---|---|
| Boilerplate | line-level allowlist + marketing-only regex | nav/CTA removed |
| CTA inside prose | sentence-level regex | stops "Get the right coverage instantly" mid-answer |
| Markdown artifacts | strip emphasis, repair glued colons | 229 records repaired |
| Low-information stubs | pointer/dangling-colon detection | 35 dropped, each with a reason |
| Exact duplicates | SHA-256 over normalised text | **628 removed** |
| Near duplicates | MinHash + LSH, 3-shingles, Jaccard 0.82 | **22 removed** |
| PII | validating detectors (below) | 2 records flagged |

Deduplication is a **recall** fix, not a storage saving: insurance sites repeat
benefit copy across every plan page, and without it the top-4 slots fill with
four phrasings of one fact while genuinely relevant records get pushed out.
Duplicates are merged, not blindly deleted — the longest body wins as the most
complete statement of the rule.

### PII: validate, don't just match

The corpus is public marketing content, so the expected PII yield is low —
which is exactly why the detectors must be *validating*. A naive 12-digit regex
fires on sum-insured figures far more often than on real Aadhaar numbers, and
every false positive redacts legitimate policy content.

| type | validation beyond the pattern |
|---|---|
| Aadhaar | **Verhoeff checksum** (the official UIDAI scheme) |
| PAN | positional format + valid 4th-char holder-type code |
| Phone | Indian mobile series must start 6–9; toll-free prefixes allowlisted |
| Email | corporate-contact allowlist (`customer.care@` is business info) |

Verified on realistic text: `10000000` (sum insured), `1800 500 8888` and
`customer.care@...` all survive; a Verhoeff-valid Aadhaar, PAN, mobile, DOB and
policy number are all redacted.

### Terminology standardisation

Not cosmetic — it decides whether lexical retrieval works. "Pre-existing
disease", "PED" and "pre existing condition" are one concept; BM25 only matches
the literal string. 20 canonical terms with ~60 variants are resolved and
indexed alongside the body.

Amounts are parsed through Indian notation (`5 lakh`, `10,00,000`, `1 crore` →
integers) and dates normalised to ISO.

---

## 3. Schema and a sample record

Spec-mandated fields are kept under the exact names the brief uses, plus fields
a *voice* consumer needs.

```json
{
  "record_id": "kb_product_plan_83e5df34_00",
  "title": "Waiting Periods",
  "content": "1. Pre-existing disease waiting period of 36 months since inception of the policy and continuous renewal 2. Initial waiting period of 30 days unless the treatment needed is the result of an accident 3. Specific waiting period of 24 months for some listed illnesses...",
  "answer_text": "...",
  "category": "product_plan",
  "market": "in",
  "section_kind": "definition",
  "source": "product_plans / website section",
  "source_url": "https://www.nivabupa.com/family-health-insurance-plans/money-saver.html",
  "source_anchor": "waiting-periods",
  "version": "1.0",
  "pii": false,
  "pii_types": [],
  "canonical_terms": ["pre-existing disease", "waiting period"],
  "amounts": [],
  "content_hash": "2a0f660c9d4eff68",
  "last_modified": "2026-07-17",
  "ingested_at": "2026-10-02T06:12:44Z",
  "chunk_index": 0,
  "chunk_total": 1,
  "token_estimate": 118
}
```

Non-obvious fields and why they exist:

| field | reason |
|---|---|
| `answer_text` | Web copy is written for scanning, not speaking. Retrieval matches `content`; the bot speaks `answer_text`. For PH/ID this is the **local-language** form while `content` stays English. |
| `canonical_terms` | Indexed with the body so "PED" and "pre-existing disease" retrieve the same record. |
| `section_kind` | `category` says what a record is *about*; `section_kind` says what *shape* it has. A caller asking "do you cover AYUSH?" is better served by a self-contained `definition` block than a `prose` chunk that assumes page context. |
| `market` | A **hard partition**. An Indian waiting-period rule is simply wrong for a Philippine life policy, so cross-market records are unreachable, not down-ranked. |
| `confidence_floor` | Derived per category — an eligibility error is a mis-sell, a marketing blurb is not. One global threshold cannot express that. |

Record IDs are deterministic — `kb_<category>_<sha1(category:title)[:8]>_<nn>` —
so an unchanged re-crawl produces identical IDs. That is what makes versioning
and incremental updates possible at all.

---

## 4. Chunking

The consumer is a **voice** agent, which inverts the usual tradeoffs.

- **Upper bound comes from speech, not context window.** A chunk read aloud
  must fit ~15–20 seconds or the caller interrupts: ~380 tokens, far below a
  text RAG system's budget. Target 220, max 380, overlap 40.
- **Chunks must be self-contained.** In chat a user scrolls back; on a call they
  cannot. Splitting "36 months" from "for pre-existing diseases" is not merely
  unhelpful — the agent would state a waiting period without saying what it
  applies to.
- **FAQ, definition and table sections are atomic** and never split, even when
  oversized (flagged instead). A truncated table is worse than a long one.
- Prose splits on paragraph, then sentence — never mid-sentence. Overlap
  carries one trailing sentence forward.

Resulting distribution (2,084 India records):

| est. tokens | count |
|---|---|
| 0–100 | 1,452 |
| 101–200 | 437 |
| 201–300 | 150 |
| 301–380 | 35 |
| 380+ | 10 (atomic, flagged) |

---

## 5. Taxonomy and source tracking

Six categories, assigned at **crawl time from the site's own information
architecture** via the URL rule that matched — not guessed by an LLM afterwards.

| category | records | role in the call |
|---|---|---|
| `coverage_rule` | 1,119 | what is and isn't covered |
| `product_plan` | 542 | named plans to pitch |
| `eligibility_rule` | 293 | drives qualification |
| `claims_process` | 76 | post-sale questions |
| `service_policy` | 41 | contact, escalation |
| `faq` | 13 | direct Q&A |

Every record carries `source_url`, `source_anchor`, `content_hash` and
`last_modified`, so any spoken answer is traceable to a clickable source.

---

## 6. Versioning

- `version` on each record (currently `1.0`).
- `content_hash` (SHA-256 of raw page bytes) detects source change.
- `last_modified` from the sitemap.
- `ingested_at` timestamp.
- Deterministic IDs make a diff-based refresh straightforward.

**Not built:** incremental re-crawl. The machinery is in place; the scheduler is
not.

---

## 7. Embedding and indexing

`bge-small-en-v1.5` (384-dim) via **fastembed/ONNX** — no PyTorch, ~130MB,
chosen for an 8GB machine with no GPU.

`embedding_text()` prepends title and canonical terms to the body, because a
bare chunk often lacks the words the caller uses: "36 months from policy
inception" is unretrievable until "Pre-existing disease waiting period" sits in
front of it.

**There is no vector database, deliberately.** Exact cosine over a
2,466 × 384 float32 matrix is one BLAS matmul — **measured at 0.4ms**. An ANN
index exists to avoid an O(N) scan, but at this N the scan is already faster
than the index lookup's overhead, and ANN adds recall error to tune away.

Honest limit: this stops being right somewhere around 10⁵–10⁶ vectors. The
storage format (one `.npy` + one `.jsonl`) is deliberately boring so swapping in
HNSW is mechanical.

---

## 8. Retrieval and ranking

Three stages, and **ranking is separated from confidence** — the central design
decision.

```
query → BM25 ─┐
              ├→ RRF fusion → cross-encoder rerank → confidence gate
       dense ─┘
```

**Hybrid, because insurance queries are bimodal.** "Is PED covered" is exact
jargon (BM25 wins); "what if I was already sick when I signed up" is pure
paraphrase (dense wins). Running one loses a whole class of query. The eval set
proves it with the same fact asked three ways: 0.891 / 0.779 / 0.767 — all
correct.

**Ranking uses Reciprocal Rank Fusion** (k=60), which combines two rankings
without requiring their scores to share a scale. This avoids the common bug of
min-max normalising BM25 per query, which makes the best result score 1.0
*every time* — including when it is bad.

**Confidence uses the cross-encoder score, never cosine.** The first version
used cosine on the reasoning that it is absolute and therefore thresholdable.
Measurement killed that:

| signal | answerable (min) | unanswerable (max) | margin | refusals correct |
|---|---|---|---|---|
| cosine | 0.767 | 0.725 | **+0.04** | 1/4 |
| cross-encoder | 2.99 | −0.68 | **+3.68** | **4/4** |

Cosine measures topic *proximity*, not answerability — so "what is my policy
balance" scored 0.716 purely for being insurance-shaped. The floor is set at
1.0 rather than the 1.156 midpoint, because tuning to a midpoint on four
negatives is overfitting.

**Per-category floors:** eligibility and exclusions 2.0, claims 1.5, coverage
1.2, everything else 1.0.

Cost: **+311ms** (p50 25ms without the reranker, 336ms with it). Worth it — a mis-sell is worse than a third
of a second.

---

## 9. Citation and the refusal contract

Every grounded answer returns `record_id`, `title`, `source_url` and `version`.
`KBRecord.citation()` renders `Title — URL#anchor (v1.0)`.

**When the gate fails, the API returns `answer: null` and an empty citation
list.** The retrieved text is withheld entirely — the model cannot paraphrase
what it was never given. Low-confidence results appear under `results` for
debugging only.

This matters more than it sounds: a prompt instruction to "say you don't know"
is **not a control**, because the model never sees that the evidence was weak.
Enforcing it in retrieval is what makes the behaviour reliable rather than
aspirational.

---

## 10. Data quality findings

Reported rather than silently dropped:

- **5 of 105 sitemap URLs return 404** (4.8%), including a hyphenation typo:
  `/25lakh-health-insurance-plans.html` 404s while `/25-lakh-...` works.
- **Two live test pages in the production sitemap**:
  `test-campaign-Page.html`, `aspire-test-lead-form.html`.
- **201 records carried markdown artifacts** into text destined to be read
  aloud; repaired.
- **35 chunks were pointer-only stubs** ("Some of them are listed below:") —
  dropped, because such a record can still win on title similarity and then
  give the caller nothing.

Full manifest: `data/interim/crawl_manifest.json`,
`data/processed/build_report.json`.
