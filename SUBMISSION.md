# Submission package — requirement → evidence map

Every requirement in the brief, and where it is satisfied. Intended to be read
alongside `README.md`.

**Verify the whole system in one command:** `python scripts/preflight.py`
(expect `ALL CLEAR`).

---

## Q1 — Knowledge-grounded voice agent

| Requirement | Evidence |
|---|---|
| Configure the voice platform, add script and business rules | `q1_voice_agent/prompts/system.md`, `q1_voice_agent/server/voice_server.py` |
| **Connect the Q2 knowledge base; don't hardcode FAQs/policies in the prompt** | Agent calls `search_knowledge_base` over HTTP to `:8001`. The prompt contains flow and refusal rules **only** — zero insurance facts. Verify by reading it. |
| Conversation flow, qualification logic | Five-field qualification, one question per turn — `prompts/system.md` |
| Grounded objection handling | Scenario `02_objection`: 4 searches, 4 grounded |
| Unsupported-question fallback | Returns `answer: null` + zero citations when the gate fails (`q2_kb/retrieval/api.py`) |
| Human escalation | `request_human_handoff` tool; fires in 4 of 6 scenarios |
| **Callable number or web calling interface** | Browser call UI at `http://127.0.0.1:8002` (mic → VAD → ASR → agent → TTS over one WebSocket) |
| **At least 3 test calls, transcripts and results** | **6 scripted scenarios** (all passing) + **1 live human browser call**. Transcripts `q1_voice_agent/calls/`, **audio `data/audio/q1_*/` and `data/audio/call_3e9ee8e2c0d0/`** |
| Cooperative customer | `01_cooperative` — 5/5 fields, lead saved |
| Objection | `02_objection` — three objections, all grounded |
| Incomplete / conflicting details | `03_conflicting_details` — caught the 35 vs 45 conflict and asked |
| Out-of-scope question | `04_out_of_scope` — four unanswerable questions, all refused |
| Human-assistance request | `05_human_request` — escalated immediately |
| *(extra)* Pressure after refusal | `06_mixed_pressure` — held the line when pushed |
| **Bot states when information is unavailable** | See the two quoted exchanges in `README.md` |
| Optional business action | **Lead creation** — SQLite (`data/leads.db`) + mock CRM summary; also callback scheduling and escalation webhook-style handoff |

---

## Q2 — Production-ready knowledge base

| Requirement | Evidence |
|---|---|
| Explain website extraction and **document parsing** | `docs/DESIGN.md` §1 — sitemap crawl **plus 5 policy-wording PDFs, 159 pages, 358 records** via PyMuPDF |
| Remove nav/headers/footers/repeated/irrelevant content | `q2_kb/clean/extract.py`; `docs/DESIGN.md` §2 |
| **Handle extraction failures and flag source errors** | `data/interim/crawl_manifest.json` + `pdf_manifest.json` — every fetch logged. **5 sitemap 404s** (incl. a hyphenation typo) and **2 live test pages** found and reported |
| Remove duplicate / near-duplicate | **628 exact + 22 near** removed (SHA-256, then MinHash LSH) |
| Standardise headings, dates, terminology, categories | `q2_kb/clean/normalize.py` — 20 canonical terms, ~60 variants; Indian lakh/crore parsing; ISO dates |
| **Identify and protect PII** | `q2_kb/clean/pii.py` — **Verhoeff-validated** Aadhaar, PAN holder-type check, mobile series, corporate-email allowlist |
| Document schema and sample records | `docs/DESIGN.md` §3 (full annotated record) |
| Chunking strategy and metadata structure | `docs/DESIGN.md` §4 — voice-sized, atomic FAQ/table |
| Product/policy taxonomy and source tracking | `docs/DESIGN.md` §5 — 6 categories from site IA |
| Versioning, embedding/indexing, retrieval/ranking, citation | `docs/DESIGN.md` §6–9 |
| **At least 5 retrieval queries, with verdicts** | **20 queries** — `docs/RETRIEVAL_EVAL.md`, `data/processed/retrieval_eval.json` |
| Each: question, retrieved record, source, relevance, verdict | All present per query in `docs/RETRIEVAL_EVAL.md` |
| Product / policy / qualification / FAQ / objection coverage | Q01–Q03 product, Q04–Q09 policy, Q10–Q11 qualification, Q12–Q14 FAQ/claims, Q15–Q16 objection, Q17–Q20 out-of-scope |
| **Connect the KB to the voice bot** | HTTP tool call, demonstrable live in the browser UI |

**Result: 20/20** — 16/16 answerable, **4/4 refusals**.

---

## Q3 — Native-language voice bots

| Requirement | Evidence |
|---|---|
| Philippines: life insurance / bancassurance | `q3_localized/prompts/system_ph.md` |
| PH: English, Filipino/Tagalog, natural Taglish | `docs/Q3_LOCALISATION.md` §2 |
| PH terms used naturally (premium, policy, beneficiary, rider, lapse, coverage) | Kept in English inside Tagalog grammar — asserted in tests |
| Indonesia: multifinance / consumer finance | `q3_localized/prompts/system_id.md` |
| ID: formal + colloquial, English loanwords, **regional accent** | `id_01` colloquial; `id_02` **Javanese** (`nggih`/`mboten`/`monggo`) |
| ID terms (cicilan, tenor, denda, DP, jatuh tempo, angsuran, pembiayaan) | Asserted — English equivalents are a test failure |
| **Language-specific ASR, tested per market** | `docs/Q3_LOCALISATION.md` §1 — provider, model, per-market latency |
| Report code-switching behaviour and observed errors | §1 — turbo kept "due na", large-v3 produced "duna" |
| Regional-accent performance | §4 — full behaviour analysis |
| Localised scripts, FAQs, objections, rules, politeness | §2–3 |
| **≥3 adaptation examples per market** | §2 (PH ×3), §3 (ID ×3) |
| Native TTS, document compromises | §5 — `fil-PH` and `id-ID` neural voices; edge-tts caveat stated |
| Fallback stays in the customer's language | Asserted: ≥80% language markers per reply; **100% achieved** |
| **2 recorded calls per market + transcripts** | 4 transcripts in `q3_localized/calls/`; **audio in `data/audio/q3_*/`** (52 files, native voices) |
| Comparison between markets | §7 |
| Known native-speaker / compliance gaps | §8 — stated plainly |

---

## Q4 — Live insights and nudges

| Requirement | Evidence |
|---|---|
| **Analysis during the call, not after** | Realtime factor **1.01**; `replay_realtime()` paces to wall clock |
| Streaming input | `q4_live_nudges/pipeline/stream.py` |
| Streaming transcription, agent/customer separation | Groq Whisper; speakers exact from per-turn files |
| Report transcription latency per chunk | ASR p50 **247ms**, p95 387ms |
| Signal extraction (intent, compliance, sentiment, buying, missed opp., callback) | 9 signal types, `pipeline/signals.py` |
| Nudge generation + delivery surface | **WebSocket dashboard** at `:8003`, plus CLI |
| **End-to-end latency with P50/P95 and components** | `docs/Q4_PIPELINE.md` §4 — **p50 275ms / p95 403ms** |
| Nudge control (confidence, dedupe, cooldown, grouping, priority, expiry) | **7 controls** — `docs/Q4_PIPELINE.md` §3 |
| **False-positive analysis** | §5 — **precision 1.00**, ground truth written before the run |
| Missed cross-sell | turn 06 — detected |
| Skipped disclosure / risky statement | turns 05 and 13 — both detected, CRITICAL |
| Rising frustration | turn 12 — detected |
| **Noisy/ambiguous: no unnecessary nudges** | turns 08–11 — **all silent**, incl. 3 traps |
| ≥1 compliance + ≥1 missed-opportunity example | 3 compliance, 1 cross-sell |
| Repository, setup, streaming method, signal design, nudge logic | `docs/Q4_PIPELINE.md` |
| **Limitations at 10× and with noisy audio** | §7 and `docs/PRODUCTION.md` §1–2 |

---

## Final submission package

| Required | Status |
|---|---|
| GitHub repo with README | `README.md` — setup, architecture, all results |
| Environment variable template | `.env.example` — **placeholders only, verified no key material** |
| Architecture diagram | `README.md` (system) + `docs/DESIGN.md` (retrieval) |
| Setup instructions | `README.md` → Quick start |
| Sample inputs | `data/raw/`, `data/audio/q4_demo_call/` |
| Test results | `docs/RETRIEVAL_EVAL.md`, `*/calls/index.json`, `q4_live_nudges/reports/` |
| Recorded calls + transcripts | Q1 **live human call** (`data/audio/call_3e9ee8e2c0d0/`, both sides, 13 files); Q3 4 calls (`data/audio/q3_*/`); Q4 demo call (`data/audio/q4_demo_call/`) |
| **No credentials committed** | `.env` gitignored; scan script in `README.md` |
| Known limitations | `docs/PRODUCTION.md` §3 |
| Production-improvement plan | `docs/PRODUCTION.md` §4 |
| Video walkthrough | Script: `docs/DEMO_SCRIPT.md` |

---

## Reproduce everything

```bash
python scripts/preflight.py            # services + end-to-end behaviour
python -m q2_kb.eval.run_eval          # Q2: 20/20
python q1_voice_agent/test_calls.py    # Q1: 6/6
python q3_localized/test_calls.py      # Q3: 4 calls
python q4_live_nudges/test_signals.py  # Q4: 25/25
python q4_live_nudges/run_pipeline.py  # Q4: live replay + latency
python scripts/bench_retrieval.py      # reranker ablation
python scripts/calibrate_reranker.py   # cosine vs cross-encoder margin
```

---

## Honest notes

Listed here rather than left to be discovered:

1. **Q3 knowledge content is synthetic** and labelled as illustrative in every
   record's `source`. The India KB is real and fully cited.
2. **No native-speaker review** of the Taglish or Indonesian.
3. **Three of four Q3 calls ran on a substituted model** (`gpt-oss-120b`) after
   the free tier's 200k tokens/day cap was exhausted mid-build. `ph_01` ran on
   `qwen3.8-27b`, which produced noticeably more natural Taglish.
4. **Q4 speaker separation is given by file naming**, not inferred.
5. **All ASR evidence is on synthesised speech**, which understates real
   telephony WER.
6. **No streaming TTS** — the top remaining latency win (~2s/turn), documented
   in `docs/PRODUCTION.md` §4.
