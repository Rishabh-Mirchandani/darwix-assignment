# Video walkthrough script

Target: **10–12 minutes**. The brief requires coverage of system overview + live
demo, architecture and key decisions, KB/retrieval design and voice-agent flow,
multilingual handling, live nudge generation, and error/fallback cases plus
limitations.

Before recording:

```bash
python scripts/preflight.py      # must print ALL CLEAR
```

Chrome or Edge. Headphones. Close other tabs (the mic VAD is energy-based).

---

## 0 · Opening (45s)

> "Four systems, built end to end on a zero-cost stack — no paid APIs, no trial
> credits. The knowledge base is scraped from a real insurer, the voice agent
> can only answer from it, and I'll show it refusing a question it can't ground."

State the constraint up front — it reframes every tradeoff that follows:

> "This runs on a 4-core laptop with 8GB and no GPU, against a free tier capped
> at 8,000 tokens a minute. Most of the architecture follows from that."

---

## 1 · Live call — the core demo (3 min) 🔴 **most important**

Open **http://127.0.0.1:8002**. Start call. Let it greet you.

Run the six turns, but the two that matter are back-to-back:

| say | point at |
|---|---|
| "Yes, now's fine." | — |
| "It's for me and my wife, we're in Pune." | one question per turn |
| "She's 38, I'm 41." | — |
| **"What's the waiting period if I have diabetes?"** | **right panel: `search_knowledge_base → grounded`** |
| **"And when's my next premium due?"** | **`not in KB` → it refuses and offers a human** |
| "Okay, thanks." | lead saved |

Say out loud on turn 5:

> "It just searched, got nothing above threshold, and said so. It didn't say
> 'typically 30 days' — which is what a model does when it's guessing. That
> refusal is enforced in retrieval, not in the prompt: when the gate fails, the
> API returns a null answer and zero citations, so there's literally nothing to
> paraphrase."

Point at the latency panel: ASR / agent / TTS per turn.

---

## 2 · The decision worth the most marks (2 min)

Open `README.md` to the cross-encoder table.

> "My first refusal gate used cosine similarity. It failed three of four
> refusals. 'What's my policy balance' scored 0.716 — higher than several real
> questions — because cosine measures topic *proximity*, not whether a passage
> answers the question. Every out-of-scope question that was merely
> insurance-shaped got through."

| signal | answerable min | unanswerable max | margin | refusals |
|---|---|---|---|---|
| cosine | 0.767 | 0.725 | +0.04 | 1/4 |
| cross-encoder | 2.99 | −0.68 | **+3.68** | **4/4** |

> "Swapping to a cross-encoder took it to four out of four. It costs 319
> milliseconds. For a bot that quotes eligibility rules, a mis-sell is worse
> than a third of a second."

Then the counter-intuitive one:

> "There's no vector database. Exact cosine over 2,108 records measures 0.4
> milliseconds — an ANN index would add a dependency tree to beat 0.4ms. That
> stops being true around 10⁵ vectors, and the storage format is deliberately
> boring so the swap is mechanical."

---

## 3 · Knowledge base (1.5 min)

Show `data/processed/build_report.json` or just quote it:

> "3,622 sitemap URLs → 105 in scope → 100 fetched. 2,769 chunks → 2,084 after
> removing 628 exact and 22 near-duplicates. Every drop is logged with a reason."

Two findings worth naming — they show the data was actually inspected:

> "167 of the in-scope URLs were near-identical branch-address pages. Left in,
> they'd have been 67% of the corpus and would have starved every category the
> bot actually needs. And five sitemap URLs 404, including a hyphenation typo —
> plus two live test pages sitting in production. Those are reported, not
> silently dropped."

Mention PII briefly: Verhoeff-validated Aadhaar, so sum-insured figures and
toll-free numbers survive — a naive regex redacts exactly the content the bot
needs to quote.

---

## 4 · Multilingual (2 min)

Open `docs/Q3_LOCALISATION.md`. Do **not** read it — land three points.

**(a) Direction of code-switching inverts between markets:**

> "Filipino agents pull English nouns *in* — premium, policy, lapse — inside
> Tagalog grammar. Indonesian agents keep finance terms local — cicilan, denda,
> jatuh tempo — and only borrow technology words. A single localisation layer
> gets one of them wrong."

Show the produced Taglish:

> "May 30 days po kayong grace period from the due date, active pa rin po ang
> policy ninyo within that period."

**(b) The ASR choice was measured, not assumed:**

> "On Taglish, whisper-large-v3-turbo transcribed the loanword 'due na'
> correctly. large-v3 collapsed it to 'duna'. Code-switching fidelity is the
> whole point, so turbo wins — and it's 20% faster."

**(c) Regional accent** — show the `id_02` transcript:

> "The caller speaks Javanese-inflected Indonesian — nggih, mboten, monggo. The
> bot stayed in Indonesian, never switched to English, never corrected them. It
> replies in standard Indonesian rather than imitating the accent, deliberately
> — a bad imitation reads as mockery."

---

## 5 · Live nudges (2 min)

Open **http://127.0.0.1:8003**. Click **Start live replay**. Let it run ~40s so
nudges appear *while audio is still playing*.

> "This is replaying at wall-clock speed — realtime factor 1.01. Nothing looks
> ahead. End-to-end, audio to nudge, is 266 milliseconds at p50."

Point at a CRITICAL nudge:

> "That's a compliance gap — the agent discussed a pre-existing condition
> without disclosing the waiting period. That check is a regex, not an LLM:
> 0.1 milliseconds, and more reliable than a model for 'was this phrase said'.
> The LLM tier only runs when the deterministic tier finds nothing."

Then the part most people skip — **precision**:

> "Precision is 1.0. Nine turns in this call are deliberate traps: a *negated*
> second vehicle, someone explicitly saying 'I'm not frustrated', and a
> line-quality complaint a naive sentiment model reads as anger. None of them
> fired. Suppressing noise is harder than detecting signal."

Show a `suppressed` line in the feed and explain the rate limit.

---

## 6 · Failures and limits (1.5 min)

Open `docs/PRODUCTION.md` — the "things that went wrong" table. Pick two:

> "Zero FAQ sections were extracted from 958 available, because
> `[class*=accordion]` is case-sensitive CSS and the site renders
> `mantine-Accordion-item`. The build reported success the whole time."

> "And `\bdiabet\b` can never match 'diabetes' — there's no word boundary
> between t and e. That killed the waiting-period compliance rule for its most
> common trigger word. Both were caught by tests that assert on silence, not by
> reading the code."

Then limits, stated plainly:

> "Q3's knowledge base is synthetic and labelled as such in every record — I
> wasn't going to fabricate citations to a real insurer. Neither language has
> native-speaker review, which for production is a blocker. Q4's speaker
> separation is given by per-turn files, not inferred. And three of the four
> Q3 calls ran on a substituted model after I exhausted the 200k-per-day free
> cap mid-build."

---

## 7 · Close (30s)

> "Four working systems, every number measured on this laptop rather than
> estimated. The decisions I'd defend hardest are the three that came from
> measurement and surprised me: no vector database, a cross-encoder instead of
> cosine, and deterministic rules beating an LLM for compliance."

---

## Fallbacks if something fails on camera

| failure | do this |
|---|---|
| 429 on a live call | "That's the free tier's 8k/min ceiling." Show a saved transcript in `q1_voice_agent/calls/` instead. |
| Mic/VAD misbehaving | `python q1_voice_agent/test_calls.py --only 01` — same behaviour, scripted. |
| Dashboard empty | `python q4_live_nudges/run_pipeline.py` — same pipeline, CLI output. |
| Any service dead | `python scripts/preflight.py` restarts everything. |

Never re-record from the top for a 429 — narrating the constraint is better
evidence than hiding it.
