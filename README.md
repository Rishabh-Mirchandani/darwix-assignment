# Darwix AI — AI Engineer Assessment

Four connected systems built on a **zero-cost stack** (no paid APIs, no trial
credits, no card). Every number in this README was measured on the machine
described under [Constraints](#constraints-that-shaped-the-build), not estimated.

| # | Deliverable | Status | Headline evidence |
|---|---|---|---|
| **Q1** | Knowledge-grounded voice agent | Working | 6/6 scripted scenarios pass; refuses and escalates under pressure |
| **Q2** | Production knowledge base | Working | **20/20** retrieval eval — 16/16 answerable, **4/4 refusals** |
| **Q3** | Native-language bots (PH + ID) | Working | 100% language consistency; genuine Taglish & Javanese handling |
| **Q4** | Live insights & nudges | Working | **p50 309ms / p95 1105ms** end-to-end, realtime factor **1.01**, precision **1.0** |

Q1 and Q2 are connected over HTTP: the voice agent calls the retrieval service
as a tool and **cannot answer without it**.

---

## Quick start

```bash
git clone <this repo> && cd darwix-assignment
python -m venv .venv
.venv\Scripts\activate          # Windows;  source .venv/bin/activate on Unix
pip install -r requirements.txt
python scripts/verify_env.py    # expect 20/20 imports OK

copy .env.example .env          # then paste the two free keys (see below)
python scripts/verify_providers.py
```

### Keys (both free, no credit card)

| Service | Used for | Where |
|---|---|---|
| **Groq** | ASR (Whisper) + all LLM inference | console.groq.com/keys |
| Google AI Studio | optional, batch only — not on any critical path | aistudio.google.com/apikey |

### Rebuild the knowledge base (optional — artifacts are committed)

```bash
python -m q2_kb.ingest.crawl        # ~3 min, polite 1.5s delay
python -m q2_kb.build               # clean, dedupe, PII, chunk
python -m q2_kb.index.build_index   # ~8 min on CPU
python q3_localized/market_kb.py
python -m q2_kb.index.add_records data/processed/market_records.jsonl
```

### Run it

```bash
# 1. Retrieval service — everything else depends on this
python -m uvicorn q2_kb.retrieval.api:app --port 8001

# 2. Voice agent (browser call UI)      -> http://127.0.0.1:8002
python -m uvicorn q1_voice_agent.server.voice_server:app --port 8002

# 3. Live nudge dashboard               -> http://127.0.0.1:8003
python -m uvicorn q4_live_nudges.server:app --port 8003
```

### Reproduce every result

```bash
python -m q2_kb.eval.run_eval          # Q2: 20/20
python q1_voice_agent/test_calls.py    # Q1: 6/6 scenarios
python q3_localized/test_calls.py      # Q3: 4 localised calls
python q4_live_nudges/test_signals.py  # Q4: 25/25 unit tests
python q4_live_nudges/run_pipeline.py  # Q4: live replay + latency report
python scripts/bench_retrieval.py      # retrieval latency, with/without reranker
```

---

## Architecture

```
                    ┌──────────────────────────────────────┐
                    │  Niva Bupa website  (robots.txt      │
                    │  explicitly allows AI crawlers)      │
                    └───────────────┬──────────────────────┘
                                    │ sitemap-driven, 1.5s delay
                                    ▼
   Q2  ┌─────────────────────────────────────────────────────────────┐
       │ crawl → extract → normalise → PII redact → chunk → dedupe    │
       │ 3,622 URLs → 105 in scope → 100 OK → 2,769 chunks → 2,084    │
       │                                                               │
       │  index: 2,466 × 384 float32  (2,108 web + 358 PDF + 24 PH/ID)       │
       │  NO vector DB — exact cosine is 0.4ms at this scale           │
       └───────────────┬─────────────────────────────────────────────┘
                       │
                       ▼  hybrid retrieval
       ┌───────────────────────────────────────────────┐
       │ BM25 ──┐                                       │
       │        ├─► RRF fusion ─► cross-encoder rerank  │
       │ dense ─┘                        │              │
       │                                 ▼              │
       │                        CONFIDENCE GATE         │
       │                   (per-category floors)        │
       └───────────────┬───────────────────────────────┘
                       │  POST /search  :8001
                       │  grounded=false → answer withheld entirely
         ┌─────────────┼─────────────┐
         ▼             ▼             ▼
   Q1 agent (in)  Q3 PH bot    Q3 ID bot          market= hard partition
         │             │             │
         └─────────────┴─────────────┘
                       │  recorded audio per turn
                       ▼
   Q4  ┌─────────────────────────────────────────────────────────────┐
       │ realtime replay → Whisper → Tier1 (regex, 0.1ms)             │
       │                            → Tier2 (LLM, 147ms, only if      │
       │                               Tier1 found nothing)           │
       │              → nudge gate (confidence/dedupe/cooldown/       │
       │                 rate-limit/priority/expiry) → WebSocket      │
       └─────────────────────────────────────────────────────────────┘
```

**Voice turn:** browser mic → energy VAD → webm → Whisper → agent (+KB tool) →
edge-tts → audio back, all over one WebSocket.

---

## The stack, and why

Every choice below was made against a measurement, and several reversed my
first instinct.

| Need | Chosen | Why — and what it beat |
|---|---|---|
| LLM | Groq `qwen/qwen3.8-27b` | **119ms** to first token. Gemini Flash measured **3.4–5.6s** then returned `503 high demand` — unusable for voice. `gpt-oss-20b/120b` returned **empty strings** (reasoning models: reasoning tokens consumed the output budget). |
| ASR | Groq `whisper-large-v3-turbo` | On synthesised Taglish, turbo transcribed the loanword *"due na"* correctly where `large-v3` collapsed it to *"duna"*. Code-switching fidelity is the point of Q3; turbo is also ~20% faster. |
| TTS | `edge-tts` ≥7.2.8 | Free, no key, genuine `fil-PH` and `id-ID` neural voices. **7.0.0 fails with a 403** on Microsoft's token check — the pin matters. |
| Embeddings | `bge-small-en-v1.5` via **fastembed/ONNX** | No PyTorch: ~2GB lighter and faster on 4 CPU cores. |
| Vector store | **NumPy, no database** | Exact cosine over 2,466×384 measures **0.4ms**. An ANN index would add a large dependency tree to beat 0.4 milliseconds. Honest limit: this stops being right somewhere around 10⁵–10⁶ vectors. |
| Relevance gate | `ms-marco-MiniLM-L-6-v2` cross-encoder | See below — this is the most important decision in the project. |

---

## The finding that shaped Q2

My first refusal gate used **cosine similarity**. It failed 3 of 4 refusals.
*"What is my current policy balance"* scored **0.716** — higher than several
legitimate questions — because cosine measures **topic proximity**, not whether
a passage answers the question. Every out-of-scope question that was merely
*insurance-shaped* sailed through.

Measured both signals on the same 20 queries (`scripts/calibrate_reranker.py`):

| signal | answerable (min) | unanswerable (max) | margin | refusals correct |
|---|---|---|---|---|
| cosine | 0.767 | 0.725 | **+0.04** | 1 / 4 |
| cross-encoder | 2.99 | −0.68 | **+3.68** | **4 / 4** |

Cost: **+311ms** (25ms without it, 336ms with it). Worth it — for a bot that quotes eligibility rules, a
mis-sell is worse than a third of a second. Full table in
[`docs/RETRIEVAL_EVAL.md`](docs/RETRIEVAL_EVAL.md).

**The gate is enforced in retrieval, not in the prompt.** When it fails, the
API returns `answer: null` and **zero citations** — the model cannot paraphrase
text it was never given. A prompt instruction is not a control, because the
model never sees that the evidence was weak.

---

## Results

### Q2 — retrieval, 20/20

16/16 answerable, 4/4 refused. The same fact asked three ways all retrieved
correctly, which is what justifies the hybrid: plain English *"waiting period
for pre-existing diseases"* (0.891), pure jargon *"PED waiting period"* (0.779),
and zero-overlap paraphrase *"I already have diabetes"* (0.767).

Steady-state latency (`scripts/bench_retrieval.py`):

| stage | mean |
|---|---|
| embed | 9.7 ms |
| vector search (2,466 records) | **0.4 ms** |
| BM25 | 9.1 ms |
| cross-encoder rerank | 363.9 ms |
| **total p50 / p95** | **336 / 787 ms** |

### Q1 — voice agent, 6/6 scenarios

Cooperative · objection · conflicting details · out-of-scope · human request ·
combined pressure. Transcripts in [`q1_voice_agent/calls/`](q1_voice_agent/calls/).

The two that matter most:

> **CALLER:** What happens if we need to be admitted abroad?
> → `search_knowledge_base` **[NOT-IN-KB]**
> **AGENT:** I don't have a clear answer on overseas hospitalization on this
> line. Let me get a colleague who can confirm that for you.

> **CALLER:** That's not good enough, I need an actual answer.
> **AGENT:** I understand, and I'm sorry I can't give you that answer on this
> line.

It held the line under direct pressure instead of inventing IVF terms.

#### Live browser call (human caller, recorded)

`q1_voice_agent/calls/web_in_20261002_083944__3e9ee8e2c0d0.json`

A real call through the browser client, not a scripted test:

> **Caller:** "What's the waiting period if I already have diabetes?"
> → `search_knowledge_base` **grounded**, confidence 5.23, 4 citations
> **Agent:** "For diabetes, the waiting period can be up to thirty-six months
> before related treatment is covered."

> **Caller:** "When is my next premium due?"
> → `request_human_handoff(reason="account_specific")`
> **Agent:** "A colleague will call you shortly to help with that."

Measured turn latency on that call:

| | p50 | p95 | max |
|---|---|---|---|
| Agent compute (ASR + LLM + KB + TTS) | **927 ms** | 1,435 ms | 4,318 ms |
| Wall clock, including free-tier pacing | 1,435 ms | — | 25,498 ms |

The two figures are reported separately on purpose. The slowest turn took 25.5s
of wall clock, of which **21.2s was the token pacer waiting out the 8k/min
ceiling** — the actual work was 4.3s. Quoting only the wall-clock number would
misattribute a billing limit to the architecture; quoting only the compute
number would hide what a caller actually experiences on a free tier.

### Q3 — localisation, not translation

Separate KB partition per market (`market=` is a **hard filter**, not a ranking
preference — an Indian waiting-period rule is simply wrong for a Philippine
life policy). An Indian question restricted to `market=ph` returns
`grounded: false` at **−10.88**.

Retrieval runs in English (`content`), delivery in local language
(`answer_text`) — so a small English embedding model can serve a non-English
voice bot. Details and limitations in [`docs/Q3_LOCALISATION.md`](docs/Q3_LOCALISATION.md).

### Q4 — live nudges

Two calls replayed, both at wall-clock speed:

| metric | demo call (adversarial) | real human call (clean) |
|---|---|---|
| utterances | 17 | 13 |
| ASR p50 | 271 ms | 300 ms |
| Tier 1 (deterministic) p50 | **0.1 ms** | **0.1 ms** |
| Tier 2 (LLM) p50 | 568 ms (7 calls) | — (0 calls) |
| **end-to-end p50 / p95** | **309 / 1105 ms** | **300 / 369 ms** |
| realtime factor | **1.01** | **0.99** |
| precision / recall | **1.00** / 0.875 | n/a - no signals expected |
| nudges emitted | 7 | **0** |

**Zero nudges on the real call is the correct result**, not a failure. The
checklist confirms why: the agent identified itself at 0s and disclosed the
waiting period when asked about diabetes, so both required disclosures were
satisfied and no customer-side signal occurred. A clean call should produce
silence - a system that manufactures alerts to look busy is the failure mode
the brief penalises.

The two runs also isolate the LLM's cost. Tier 2 p50 is **568ms on
`gpt-oss-120b`**, against **147ms measured earlier on `qwen3.8-27b`** before its
daily cap was exhausted; that single substitution moved end-to-end p95 from
403ms to 1105ms. The real call, which triggered no Tier 2 calls at all, stays
at p95 369ms - which is what the pipeline costs without an LLM in the path.

Unit tests: **25/25**.

All 9 deliberately ambiguous turns stayed silent — including three traps: a
**negated** second vehicle, an explicit *"I'm not frustrated"*, and a
line-quality complaint that a naive sentiment model reads as anger.

---

## Constraints that shaped the build

**Hardware:** Intel i5-1135G7, 4 cores, **8GB RAM, no NVIDIA GPU**. This is why
there is no PyTorch, no local Whisper, and no vector database — and why
`fastembed` (ONNX) and hosted inference were chosen over local models.

**Groq free tier: 8,000 tokens/minute.** The system prompt is resent on every
turn, so this binds hard. Mitigations: the prompt was compressed from 1,785 to
~865 tokens, tool schemas trimmed, history capped at 10 messages, and tool
results truncated before entering history — roughly halving per-turn cost to
**~1,199 tokens**. A `TokenPacer` then paces proactively, because without it a
429 surfaces as a silent **~30 second stall mid-call** (the SDK retries
transparently) — the worst possible failure mode for voice. Turn latency went
from **p50 29,700ms → 310ms** after these changes.

This ceiling is a free-tier artifact, not a design limit. On a paid tier the
pacer never fires.

---

## Known limitations

Stated plainly rather than discovered by a reviewer:

1. **Q3 content is synthetic.** The India KB is scraped from a real insurer with
   full URL citations. The 24 PH/ID records are hand-authored from publicly
   documented market mechanics and are **labelled as illustrative in every
   record's `source`**. Fabricating citations to a real insurer would be worse
   than having none.
2. **No native-speaker review.** The Taglish and Indonesian were written
   carefully and validated structurally (register, `po` usage, term retention),
   but neither has been signed off by a native speaker. For production this is
   a blocker, not a nice-to-have.
3. **Q4 diarisation is given, not inferred.** Speaker labels come from
   per-turn audio files. A genuinely mixed single-channel recording needs a
   diarisation model.
4. **Browser VAD is energy-based**, so it will cut off a caller who pauses
   mid-sentence in a noisy room. A trained VAD would be the upgrade.
5. **`edge-tts` is an unofficial endpoint.** Fine for a prototype; production
   needs a licensed TTS.
6. **The 404s are real.** 5 of 105 sitemap URLs returned 404 and 2 live test
   pages (`test-campaign-Page.html`) sit in the production sitemap — detected
   and reported rather than silently dropped.

See [`docs/PRODUCTION.md`](docs/PRODUCTION.md) for the scale-up plan and what
changes at 10×.

---

## Repository map

```
shared/config.py           every tunable, with the measurement behind it
q2_kb/
  ingest/                  robots-respecting sitemap crawler + source rules
  clean/                   extraction, PII (Verhoeff-validated), dedupe, normalise
  index/                   schema, chunking, embeddings, NumPy store
  retrieval/               hybrid search + confidence gate + FastAPI service
  eval/                    20-query evaluation set and runner
q1_voice_agent/            agent loop, tools, prompts, browser call UI, tests
q3_localized/              PH + ID prompts, market KB, localisation tests
q4_live_nudges/            signals, nudge gate, streaming pipeline, dashboard
scripts/                   verification, benchmarks, calibration, diagnostics
docs/                      evaluation results, design notes, production plan
```

Secrets are never committed — `.env` is gitignored and `.env.example` documents
every variable.
