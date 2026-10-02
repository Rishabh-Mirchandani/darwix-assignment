# Limitations, scale, and what I'd change next

Written to be read by someone deciding whether this is worth taking further —
so the failure modes come first, not last.

---

## 1. What breaks at 10× — and what doesn't

"10× scale" has two different meanings here and they fail in different places.

### 10× the corpus (2k → 20k records)

| component | verdict |
|---|---|
| NumPy exact search | **Fine.** 0.4ms → ~4ms. Still far below the reranker. |
| BM25 (`rank_bm25`, pure Python) | **First thing to break.** Already 11ms at 2k; it is O(N) in Python and will dominate. Replace with a Tantivy/Lucene index. |
| Cross-encoder rerank | Unchanged — it scores a fixed 8 candidates, independent of corpus size. |
| Build time | ~8 min → ~80 min single-threaded. Needs batching or a GPU box. |

Exact search only stops being right around **10⁵–10⁶ vectors**. The storage
format (one `.npy` + one `.jsonl`) is deliberately boring so swapping in HNSW
is mechanical.

### 10× concurrent calls (1 → 10 simultaneous)

This is the real limit, and it is **not** in my code:

- **Groq free tier: 8,000 tokens/min and 200,000 tokens/day.** One call uses
  ~1,200 tokens/turn. Ten concurrent calls exhaust the per-minute budget in
  roughly six turns *total*. I hit the daily cap during this build and had to
  switch Q3 to a second model mid-evaluation.
- **The reranker is CPU-bound and single-threaded** at ~361ms. Ten concurrent
  requests on 4 cores queue badly. It needs either a GPU, batching across
  requests, or a smaller model.
- **`edge-tts`** is an unofficial endpoint with no SLA and no rate-limit
  contract. It will fail unpredictably under load.

**Fix order:** paid LLM tier → batch/GPU the reranker → licensed TTS →
replace BM25. The first alone unblocks most of it.

---

## 2. Noisy audio

Everything measured here used **synthesised speech**: clean, 24kHz, no
background noise, no codec artifacts, perfect turn boundaries. Real telephony
is 8kHz μ-law with packet loss. Expect:

- **Materially higher WER**, worst on the Javanese-inflected register where
  Whisper's training data is thinnest.
- **The browser VAD will fail first.** It is energy-based, so in a noisy room
  it either cuts callers off mid-sentence or never triggers end-of-turn. A
  trained VAD (Silero) is the fix; it was excluded here only because it pulls
  in PyTorch, which does not fit the 8GB constraint.
- **Q4 signal quality degrades non-linearly.** The Tier-1 regexes match exact
  words; one ASR error on "second car" and the cross-sell is silently missed.
  Tier-2 (LLM) is more robust to transcription noise, which is an argument for
  shifting the balance toward it when audio quality is poor.

Mitigation already in place: Q4 counts and reports suppressed signals, so a
sudden drop in detections is visible rather than silent.

---

## 3. Known weaknesses, honestly

1. **Q3 content is synthetic and un-reviewed by native speakers.** The single
   biggest gap. Structurally validated, not linguistically signed off.
2. **Speaker separation in Q4 is given, not inferred** — it comes from
   per-turn audio files. A mixed single-channel recording needs diarisation.
3. **The confidence threshold is calibrated on 20 queries.** The cross-encoder
   separates the two populations by +3.68, which is a wide margin, but 4
   negatives is a small sample. I deliberately set the floor at 1.0 rather than
   the 1.156 midpoint to avoid overfitting to it.
4. **No streaming TTS.** The agent's reply is synthesised whole, adding
   1.5–3.7s before audio starts. `edge-tts` supports chunked streaming; wiring
   it would cut perceived latency substantially. This is the highest-value
   remaining latency win and it was a time casualty.
5. **The agent occasionally repeats a question.** Mitigated with an explicit
   prompt rule after it was observed asking for a name three times; it still
   happens occasionally on `gpt-oss`, which follows the rule less reliably than
   `qwen`.
6. **No authentication on any service.** All three bind to 127.0.0.1 and assume
   a trusted local environment.
7. **PII detection is rule-based.** Verhoeff-validated Aadhaar, PAN format,
   Indian mobile series and email are covered; names and addresses are not. A
   production system needs NER.

---

## 4. What I'd do next, in order

1. **Streaming TTS** — biggest perceived-latency win available, ~2s per turn.
2. **Paid LLM tier** — removes the pacer, the daily cap, and the mid-build
   model substitution.
3. **Native-speaker review of Q3** — blocker for any real deployment.
4. **Expand the eval set to ~100 queries** with more negatives, and re-derive
   the thresholds from that rather than from 20.
5. **Trained VAD + telephony-grade audio testing** before claiming any
   real-world latency number.
6. **Incremental re-crawl.** The crawler is idempotent and record IDs are
   deterministic (`sha1(category:title)`), so a diff-based refresh is
   straightforward — but it is not built.
7. **Observability.** Latency is measured per run; nothing is persisted to a
   time-series store, so there is no way to see drift.
8. **Keep `scripts/verify_claims.py` in CI.** It asserts every figure quoted in
   the documentation against the artifact that produced it, and it has already
   caught two cases of the write-up drifting from reality after a re-run. A
   submission whose numbers no longer match its own outputs is worse than one
   with fewer numbers.

---

## 5. Things that went wrong during the build

Kept because they are the useful part of the record.

| what | why it mattered |
|---|---|
| **Cosine similarity failed as a refusal gate** (3/4 refusals leaked) | Cosine measures topic proximity, not answerability. Only visible because the eval set contained negatives. Fixed with a cross-encoder: margin +0.04 → +3.68. |
| **Zero FAQ sections extracted from 958 available** | `[class*="accordion"]` is case-sensitive CSS; the site renders `mantine-Accordion-item`. Silent — the build reported success. |
| **`\bdiabet\b` never matches "diabetes"** | No word boundary between `t` and `e`. The waiting-period compliance rule was dead for its most common trigger word. |
| **Negation veto suppressed the most important detections** | *"Don't worry, your claim will definitely be approved"* contains a negation, which made the veto hide the exact over-promise it should flag. |
| **Turn latency p50 of 29.7 seconds** | Groq 429s retried transparently by the SDK, surfacing as a silent stall rather than an error. Fixed by compressing the prompt and pacing proactively: → 310ms. |
| **Gemini dropped from the critical path** | Measured 3.4–5.6s per turn, then returned `503 high demand` mid-benchmark. Not viable for voice. |
| **`gpt-oss` returned empty strings** | Reasoning models emit reasoning tokens first; a 40-token cap was exhausted before any visible output. Not a bug — a misread of the model. |
| **Daily token cap hit mid-Q3** | 200k TPD exhausted; three of four Q3 calls ran on a substituted model. Disclosed in `docs/Q3_LOCALISATION.md`. |
| **`save_lead` rejected, whole lead lost** | Found on a live call. `gpt-oss` emits `null` for optional fields it has no value for; the schema declared them `"string"`, so the provider rejected the entire tool call and eight valid fields were discarded with three unknown ones. Never surfaced on `qwen`, which omits unknown keys instead - a latent schema bug one model's habits were hiding. Fixed by making optional fields nullable, plus a retry-without-tools path so a malformed call costs a tool, not the caller's turn. |
| **Five questions in one turn** | `gpt-oss-120b` ignored the one-question-per-turn rule that `qwen` obeyed, producing a turn the caller could only answer the last part of. Fixed in code (`enforce_voice_style`) rather than by re-prompting: turn structure is a hard constraint for voice, and instruction-following varies by model. |
| **Replay ordered agent before caller** | `sorted()` put `001_agent.mp3` before `001_caller.webm` - alphabetical, not conversational - so every agent reply replayed before the utterance it answered. Worse than cosmetic: the compliance checklist is speaker-ordered, so disclosures could be misattributed to the wrong turn. |
| **Second pipeline run overwrote the first** | Both runs wrote `pipeline_report.json`, so replaying a real call silently destroyed the demo-call figures the write-up quotes. Caught by `scripts/verify_claims.py`, which is why that script exists. Fixed with per-call report filenames. |
| **Stale transcript rendered to audio** | The Q3 index selected transcripts by filename, which is a random call-id hash, so it picked a pre-fix run still containing the repeated-question bug. Now selects by modification time. |

Every one of these was caught by a test or a measurement rather than by
reading the code.
