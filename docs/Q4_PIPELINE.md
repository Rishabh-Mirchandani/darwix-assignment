# Q4 — Live insights and nudges

Covers the brief's required items: streaming method, signal design, nudge
logic, latency report with component breakdown, false-positive controls, and
the required test coverage.

Reproduce: `python q4_live_nudges/run_pipeline.py`
Dashboard: `python -m uvicorn q4_live_nudges.server:app --port 8003`

---

## 1. Streaming method

The brief is explicit that *"a completed recording analyzed only after upload
does not qualify"*, so the system is built around a constraint: **nothing may
look ahead.**

```
per-turn audio ──► paced to wall clock ──► Whisper ──► Tier 1 (regex)
                                                   └─► Tier 2 (LLM, conditional)
                                          ──► nudge gate ──► WebSocket ──► panel
```

Audio is fed at wall-clock speed by `replay_realtime()`, which sleeps until
each segment's true start time. The measured **realtime factor is 1.01** —
145.5s of wall time for 143.6s of audio. Running faster than 1.0 would be
cheating; the number is reported precisely so that is checkable.

A `--speed` flag exists for iteration, but every figure here comes from a 1.0
run.

**Speaker separation is exact, not inferred.** Groq's Whisper endpoint does not
diarise, so rather than guess speakers from a mixed track, the pipeline consumes
the *per-turn* files the voice server already writes (`NNN_caller.webm`,
`NNN_agent.mp3`). This matters because half the compliance rules apply only to
the agent — attributing an over-promise to the wrong speaker inverts the signal.
**Limitation:** a genuinely mixed single-channel recording would need a
diarisation model.

---

## 2. Signal design — two tiers

| | Tier 1 | Tier 2 |
|---|---|---|
| Method | compiled regex + checklist | LLM (`qwen3.8-27b`, JSON mode) |
| Latency p50 | **0.1 ms** | **147 ms** |
| Runs on | every utterance | customer turns **only when Tier 1 found nothing** |
| Handles | compliance, explicit phrases | frustration vs terseness, intent, missed openings |

**Why compliance is deterministic rather than LLM.** A required disclosure
either was or was not said. That is a rule, not a judgement call. An LLM is
slower, costs tokens, and is *less* reliable here because it will occasionally
decide a paraphrase counts. The checklist also runs across the whole call — the
signal is the *absence* of a phrase by the time a trigger topic arises, which a
per-utterance classifier cannot express.

**Why Tier 2 is conditional.** Paying 147ms to re-confirm a keyword match is
waste, and running an LLM on every turn would both exhaust the rate limit and
generate exactly the low-value alert spam the brief penalises. On the demo call
Tier 2 fired 7 times across 17 turns.

### Signals detected

| signal | tier | priority |
|---|---|---|
| `compliance_gap` (missing disclosure) | 1 | CRITICAL |
| `risky_statement` (agent over-promise) | 1 | CRITICAL |
| `rising_frustration` | 1 + 2 | HIGH |
| `payment_difficulty` | 1 + 2 | HIGH |
| `missed_cross_sell` | 1 + 2 | MEDIUM |
| `buying_signal` | 1 + 2 | MEDIUM |
| `competitor_mention` | 1 | MEDIUM |
| `callback_needed` | 1 | MEDIUM |
| `topic_shift` | 2 | LOW |

### Disclosure checklist

Two kinds, with different firing rules:

- **Deadline-based** (identity): required unconditionally within 20s of call
  open. Fires the moment the deadline passes.
- **Trigger-based** (waiting period, premium caveat): armed when the
  conversation raises the topic, fires on the agent's *next* turn if still
  unsaid. The one-turn grace matters — nudging an agent who was already about
  to say it is how a coaching panel becomes noise.

---

## 3. Nudge logic and suppression

Detecting a signal is the easy half. Deciding which signals deserve to
interrupt a human mid-conversation is the half the brief grades.

An agent on a live call absorbs roughly one nudge per 20 seconds. Past that the
panel becomes wallpaper and they stop looking — which makes the system *worse*
than nothing, because it costs attention and returns none. Every control below
exists to spend a strict attention budget.

| # | control | setting |
|---|---|---|
| 1 | Confidence floor, per type | 0.55 (compliance) → 0.85 (topic shift) |
| 2 | Duplicate suppression | by **topic key**, not text |
| 3 | Cooldown, per topic | 25s (compliance) → 120s (topic shift) |
| 4 | Global rate limit | **4 / minute** |
| 5 | Priority pre-emption | CRITICAL bypasses the rate limit |
| 6 | Expiry | 25s (LOW) → 60s (CRITICAL) |
| 7 | Stale displacement | a CRITICAL on screen >22s can be replaced |

**Confidence floors are asymmetric on purpose.** A missed disclosure is a
regulatory problem; a missed cross-sell is a slow Tuesday. Compliance therefore
fires at a *lower* confidence than a sales hint.

**Deduplication keys on signal type, not wording** — "customer mentioned a
second car" and "customer mentioned another vehicle" are one nudge.

---

## 4. Latency report

Measured end to end on the demo call, at 1.0× speed:

| stage | demo call p50 | demo call p95 | real call p50 | real call p95 |
|---|---|---|---|---|
| ASR (`whisper-large-v3-turbo`) | 271.4 ms | 386.0 ms | 299.5 ms | 369.2 ms |
| Signal — Tier 1 | **0.1 ms** | 0.1 ms | **0.1 ms** | 0.3 ms |
| Signal — Tier 2 (LLM) | 567.9 ms | — | n/a (0 calls) | — |
| Nudge gating | <0.1 ms | <0.1 ms | <0.1 ms | <0.1 ms |
| Delivery (WebSocket) | <0.1 ms | <0.1 ms | <0.1 ms | <0.1 ms |
| **End to end** | **309 ms** | **1105 ms** | **300 ms** | **369 ms** |

Realtime factor 1.01 and 0.99 respectively.

**The LLM tier is the whole variance.** Tier 2 measured 146.9 ms p50 on
`qwen/qwen3.8-27b`; after that model's 200k tokens-per-day cap was exhausted
mid-build and the pipeline moved to `openai/gpt-oss-120b`, the same stage
measures **567.9 ms** — and end-to-end p95 moved from 403 ms to 1105 ms with no
other change. The real human call triggered no Tier 2 calls and sits at p95
**369 ms**, which is what the pipeline costs with the LLM out of the path.

That is the argument for the two-tier split stated numerically: Tier 1 is
0.1 ms and model-independent, so every signal moved into it is a signal whose
latency cannot regress when a model changes underneath you.

### Replaying a real Q1 call

The brief permits reusing a Q1/Q3 recording. The live human call replays with:

```bash
python q4_live_nudges/run_pipeline.py --call call_3e9ee8e2c0d0
```

It produced **zero nudges**, correctly: both required disclosures were
satisfied (`identity` at 0 s, `waiting_period` in direct response to the
diabetes question) and the customer gave no frustration, cross-sell or hardship
cue. Silence on a well-run call is the result the suppression design is for.

**ASR dominates at ~90% of the budget.** That is the right place for it to sit —
it is the only stage that cannot be made faster without changing provider or
model. Tier 1 at 0.1ms is effectively free, which is the whole argument for
putting compliance there.

---

## 5. False-positive analysis

Ground truth was written **before** the pipeline ran
(`data/audio/q4_demo_call/ground_truth.json`): 17 turns, 8 labelled as
should-produce-a-signal, 9 as should-stay-silent. Labelling after seeing output
is how you accidentally grade yourself generously.

| metric | value |
|---|---|
| Expected signals | 8 |
| Detected | 7 |
| **Precision** | **1.00** |
| Recall | 0.875 |
| False positives | **0** |
| Nudges emitted | 7 |

### The nine silent turns included three deliberate traps

| turn | trap | fired? |
|---|---|---|
| "No, I don't have a second car, it's just the one." | **negated** cross-sell — the phrase is present, the answer is no | no |
| "And I'm not frustrated, I just want to understand the numbers." | explicit negation of frustration | no |
| "Sorry, the line is a bit bad. Could you say that again?" | audio trouble a naive sentiment model reads as anger | no |
| "Hmm. Right. I see. Okay." | contentless filler | no |

These pass because of an explicit negation veto with a 34-character lookbehind.

> **Bug worth recording:** the negation veto originally suppressed the *most
> important* detections. *"Don't worry, your claim will definitely be
> approved"* contains a negation, so the veto hid the exact over-promise it
> should flag. The veto is now never applied to agent risk patterns, where a
> reassuring frame makes the statement **worse**, not safer.

> **Second bug:** `\bdiabet\b` can never match "diabetes" — there is no word
> boundary between `t` and `e`. The waiting-period disclosure rule was dead for
> its single most common trigger word. Both were caught only because the test
> suite asserts on **silence** as well as detection.

### The one miss, and why it stays

`buying_signal` ("How do I sign up?") was **detected and then suppressed** by
the 4/min rate limit — not a detection failure. The demo call packs 8 signals
into 145 seconds, far denser than a real call, so the cap binding is the control
working correctly under deliberately adversarial input.

Raising the cap to score 8/8 would mean removing the mechanism the brief asks
for. It is left in place and reported instead.

Commercially this is still the worst suppression to take: a buying signal is
the highest-value moment on the call. A production system should let
`buying_signal` pre-empt the rate limit the way CRITICAL does — a one-line
change, deliberately not made here so the measured behaviour matches the
described design.

---

## 6. Required test coverage

| requirement | where | result |
|---|---|---|
| Missed cross-sell | turn 06, "My wife also needs cover" | detected, MEDIUM |
| Skipped disclosure | turn 05, pre-existing condition answered without waiting period | detected, CRITICAL |
| Risky statement | turn 13, "your claim will definitely be approved" | detected, CRITICAL |
| Rising frustration | turn 12, "third time I've called" | detected, HIGH |
| Noisy / ambiguous | turns 08–11 | **all silent** |

Plus 25 unit tests (`python q4_live_nudges/test_signals.py`) covering true
positives, true negatives, the disclosure checklist, and every suppression rule
including rate limiting and CRITICAL pre-emption. **25/25 passing.**

---

## 7. Limits at 10× and with noisy audio

**10× concurrent calls** breaks outside this code first:

- Groq free tier is 8,000 tokens/min and 200,000/day — ten concurrent calls
  exhaust the per-minute budget in a handful of turns. (This cap was hit during
  the build.)
- ASR is a per-utterance network round trip; ten calls means ten concurrent
  requests against the same quota.
- Tier 1 costs nothing and scales trivially. **Tier 2 is the scaling cost**, so
  the ratio between them is the tuning knob.

**10× corpus** is irrelevant here — Q4 holds no index.

**Noisy audio** degrades Tier 1 **non-linearly**: the regexes match exact words,
so one ASR error on "second car" silently loses the cross-sell. Tier 2 is more
robust to transcription noise, which argues for shifting the balance toward it
when audio quality is poor — at a latency and quota cost.

All measurements here used synthesised speech: clean, 24kHz, no packet loss.
Real telephony is 8kHz μ-law. Expect materially worse WER, and therefore worse
Tier 1 recall, in production.

Mitigation already present: suppressed signals are counted and reported, so a
sudden drop in detections is visible rather than silent.
