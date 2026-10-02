# Q3 — Native-Language Voice Bots: evidence

Two markets, two calls each, all four passing.

- **Transcripts:** [`q3_localized/calls/`](../q3_localized/calls/)
- **Audio recordings:** `data/audio/q3_<label>/` - every turn rendered with the
  market's **native** neural voice (`fil-PH-BlessicaNeural` /
  `fil-PH-AngeloNeural`, `id-ID-GadisNeural` / `id-ID-ArdiNeural`), agent and
  caller on different voices.

The conversations were run as scripted text first, so the dialogue logic could
be tested deterministically and re-run after every prompt change; the audio is
rendered from those exact transcripts (`q3_localized/render_call_audio.py`).
That split is deliberate: speech is a transport concern, and testing dialogue
through ASR would only add transcription noise to a test of conversation
behaviour. The recordings double as the evidence for the native-TTS
requirement.

| call | market | requirement covered | language consistency |
|---|---|---|---|
| `ph_01_premium_reminder` | PH | cooperative, mixed EN/finance terms, lapse objection | 100% |
| `ph_02_objection_escalation` | PH | sector objection, out-of-scope, human escalation | 100% |
| `id_01_installment_reminder` | ID | cooperative, colloquial register, payment difficulty | 100% |
| `id_02_javanese_accent` | ID | **regional accent**, objection, escalation | 100% |

---

## 1. ASR configuration and measured behaviour

**Provider / model:** Groq, `whisper-large-v3-turbo`.

**Why turbo rather than `large-v3`** — this was measured, not assumed. Both
models were given identical synthesised Taglish containing an English loanword:

| spoken | `whisper-large-v3-turbo` | `whisper-large-v3` |
|---|---|---|
| "…reminder na **due na** ang inyong premium sa ika-15" | "…reminder na **due na** ang inyong premium sa **ika-labing lima**" | "…reminder na **duna** ang inyong premium…" |

`large-v3` collapsed the English loanword *"due na"* into the non-word
*"duna"*. Code-switching fidelity is the entire point of this question, so the
model that preserves it wins. Turbo is also ~20% faster (222ms vs 286ms).

Note `ika-15` → `ika-labing lima`: turbo expanded the numeral into its spoken
Filipino form. That is **correct** Tagalog, not an error.

**Languages tested, with language hints:**

| market | hint | sample | result | latency |
|---|---|---|---|---|
| India | auto | "What is the waiting period for pre-existing diseases?" | exact | 264–411ms |
| Philippines | `tl` | "Magandang araw po! Ito po ang reminder na due na ang inyong premium sa ika-15." | near-exact (numeral expanded) | 222ms |
| Indonesia | `id` | "Selamat siang, Bapak. Cicilan Anda akan jatuh tempo pada tanggal 15." | **exact** | 230ms |

The Indonesian transcription preserved `cicilan` and `jatuh tempo` exactly —
the domain terms the brief names.

**Code-switching behaviour.** Language hints are set per market but the India
bot runs on auto-detect, because Indian callers code-switch unpredictably.
Forcing `tl` on the PH bot was the right call: without a hint, Whisper tends to
resolve Taglish toward English and drops the Tagalog grammar frame.

**Observed errors / limits.** All ASR evidence here is on *synthesised* speech,
which is cleaner than telephony audio — no background noise, no clipping, no
8kHz codec. Real-world WER will be materially worse, particularly for the
Javanese-inflected register, where Whisper's training data is thin.

---

## 2. Localisation, not translation — Philippines

Three examples where a literal translation would have been wrong.

### 2.1 Finance nouns stay in English

A translator renders "policy" as *patakaran* and "beneficiary" as
*tagapagmana*. Filipino agents never do — those words sound like a government
form. The bot keeps the English noun inside a Tagalog grammar frame:

> **Produced:** "May **30 days po** kayong **grace period** from the **due
> date**, active pa rin po ang **policy** ninyo within that period."

Five English finance terms, Tagalog syntax, `po` throughout. The test asserts
on this directly — `patakaran`, `gantimpala` and `tagapagmana` appearing in a
reply is a **failure condition** in `q3_localized/test_calls.py`.

### 2.2 `po` is structural, not decoration

Tagalog encodes respect grammatically. Dropping `po` is not neutral — it reads
as rude to a customer. The prompt makes it mandatory and the test measures its
presence across every reply (100% in both PH calls). A translation pipeline
drops it entirely, because English has no equivalent to carry over.

### 2.3 Lapse anxiety is the market's real objection

The dominant worry on a Philippine bancassurance call is not price — it is
*"baka mag-lapse na po yung akin?"* ("has mine already lapsed?"). The flow is
built around that, with grace-period content as the first thing the KB must
answer. A generic insurance script translated into Tagalog would lead with
benefits and never address it.

> **Caller:** "Teka po, baka mag-lapse na po yung akin? Nahuli po kasi ako ng bayad."
> **Bot:** "Huwag po muna kayong mag-alala… May 30 days po kayong grace period
> from the due date, so active pa rin po ang policy ninyo within that period."

---

## 3. Localisation, not translation — Indonesia

### 3.1 Finance terms stay Indonesian; tech terms stay English

The direction reverses versus PH. `cicilan`, `angsuran`, `denda`, `tenor`,
`jatuh tempo`, `keringanan` must stay Indonesian — but `transfer`,
`virtual account`, `auto-debit` and `customer service` are the words
Indonesians actually use and should **not** be forced into Indonesian.

> **Produced:** "Untuk keterlambatan ada **denda harian** yang dihitung dari
> nilai **angsuran** yang tertunggak, mulai hari pertama setelah **jatuh tempo**."

The test fails the call if `installment`, `late fee` or `due date` appear in
English.

### 3.2 Register mirrors the caller, formal → colloquial

Indonesian has a wide formality range and staying stiff signals a machine. The
caller in `id_01` opens colloquially — *"Udah terima kok remindernya kemarin"*,
*"Ada keringanan **gak**…"* — and the bot relaxes to match while keeping
`Bapak`. A translation layer has no concept of register and stays uniformly
formal.

### 3.3 Collections framing is regulated, and indirect

Indonesian collections calls are warm and indirect; blunt debt language causes
hang-ups and is regulated conduct. The prompt forbids threatening *tarik unit*
(repossession), blacklisting or legal action, and frames the call as
*mengingatkan* (reminding) rather than *menagih* (demanding). On payment
hardship the bot must never invent a waiver:

> **Caller:** "Nah itu dia, bulan ini agak berat. Gaji belum turun."
> **Bot:** "Baik, Pak, saya mengerti kondisinya. Kira-kira kapan Bapak
> berencana membayar…"

Then, asked about tenor extension, it refused rather than promising relief it
cannot authorise:

> **Bot:** "Mohon maaf Pak, untuk opsi perpanjangan tenor saya belum memiliki
> informasinya. Saya sambungkan ke rekan saya…"

---

## 4. Regional accent — Javanese (call `id_02`)

The brief requires at least one regional accent outside standard Jakarta
speech. The caller speaks Javanese-inflected Indonesian throughout:

| caller said | standard Indonesian | meaning |
|---|---|---|
| `Nggih, leres` | *Ya, benar* | yes, correct |
| `Mboten, dereng nampi` | *Tidak, belum terima* | no, not yet received |
| `Kulo piyambak` | *Saya sendiri* | myself |
| `Monggo dijelaske malih` | *Silakan dijelaskan lagi* | please explain again |
| `kulo pengin ngendikan kaliyan petugase` | *saya ingin bicara dengan petugas* | I want to speak to staff |

**Observed behaviour — all three requirements met:**

1. **No unexpected English switch.** Every reply stayed in Indonesian. This is
   the specific failure the brief calls out, and it is the most common one: a
   model that cannot parse the input often falls back to English.
2. **No correction of the caller.** The bot never rephrased the caller's
   Javanese into standard Indonesian or signalled incomprehension.
3. **Sector vocabulary retained** under accent pressure — `denda`, `angsuran`,
   `jatuh tempo` all survived.

It also correctly understood `Monggo dijelaske malih` as a request to
re-explain, and re-explained in simpler terms rather than repeating verbatim.

**Deliberate limitation:** the bot replies in standard Indonesian rather than
imitating Javanese. This is a design decision, not a gap — a poor imitation of
a regional register reads as mockery, which is far worse than being politely
standard. The prompt permits mirroring at most one marker (`nggih`, `monggo`)
for rapport.

---

## 5. TTS

| market | voice | status |
|---|---|---|
| India | `en-IN-NeerjaNeural` | native |
| Philippines | `fil-PH-BlessicaNeural` / `fil-PH-AngeloNeural` | **native Filipino** |
| Indonesia | `id-ID-GadisNeural` / `id-ID-ArdiNeural` | **native Indonesian** |

All verified present and synthesising (322 voices enumerated). No compromise
was needed on voice nativeness.

**Compromise that was needed:** `edge-tts` is an unofficial endpoint. Version
7.0.0 fails with a 403 against Microsoft's `Sec-MS-GEC` token check; ≥7.2.8 is
required and is pinned. Fine for a prototype, not for production.

---

## 6. Retrieval design for non-English markets

Records carry English `content` (embedded and searched) and local-language
`answer_text` (spoken). The agent is instructed to **search in English, answer
in the local language**.

This exists because `bge-small-en-v1.5` is English-only; embedding Tagalog or
Indonesian with it degrades retrieval silently. The alternative — a multilingual
embedding model — was rejected because the larger multilingual models do not fit
comfortably in 8GB alongside the reranker.

**Market is a hard partition, not a ranking preference.** An Indian
waiting-period rule is simply *wrong* for a Philippine life policy, so
cross-market records are made unreachable rather than down-ranked. Verified:

```
query  : "pre-existing disease waiting period IRDAI"   market=ph
result : grounded=false, confidence -10.88
```

**Limitation:** a caller's exact local phrasing never reaches the index
directly; it passes through the model's English query rewrite first.

---

## 7. Market comparison

| dimension | Philippines | Indonesia |
|---|---|---|
| Sector | Life / bancassurance | Multifinance (vehicle) |
| Flow | Premium reminder + renewal | Instalment reminder + collections |
| Code-switching | Heavy — English nouns in Tagalog frame | Light — Indonesian terms, English tech words |
| Politeness | `po`/`opo`, grammatically mandatory | `Bapak`/`Ibu` + softeners, register-mirrored |
| Primary objection | Lapse anxiety | Penalty size and affordability |
| Regulatory risk | Mis-stating lapse/reinstatement | Collections conduct (threats are prohibited) |
| Escalation trigger | Account-specific, bereavement | Hardship, anger, account-specific |

The direction of code-switching **inverts** between the two markets: Filipino
agents pull English nouns *in*; Indonesian agents keep finance terms local and
only borrow technology words. A single "localisation layer" applied to both
would get one of them wrong.

---

## 8. Known gaps

1. **No native-speaker review.** The strongest limitation. Both scripts were
   written carefully and validated structurally (register, `po` usage, term
   retention, accent handling), but neither has been signed off by a native
   speaker. Before any real deployment this is a blocker.
2. **KB content is synthetic.** The 24 PH/ID records encode publicly documented
   market mechanics but are hand-authored and labelled as illustrative in every
   record's `source`. They are **not** any named insurer's terms.
3. **ASR evidence is on synthesised speech**, which understates real-world WER —
   especially for the Javanese register.
4. **Compliance not legally reviewed.** The PH bot follows Insurance Code grace
   periods and the ID bot follows OJK-aligned collections conduct as publicly
   described, but neither has had a compliance sign-off.
5. **Model substitution mid-evaluation.** `ph_01` ran on `qwen/qwen3.8-27b`;
   the other three ran on `openai/gpt-oss-120b` after the free tier's 200k
   tokens-per-day cap was exhausted. qwen produced noticeably more natural
   Taglish; gpt-oss is slightly more formal and translated-sounding. Both pass
   the structural assertions, but a production system would standardise on one
   model and re-validate.
