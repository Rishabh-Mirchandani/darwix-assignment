# Call audio

Every recording in this repo, what it is, and which ones are the evidence.

## Q1 — live human browser calls

Recorded through the browser client at `:8002`. Each turn is saved as a
separate file (`NNN_caller.webm` from the microphone, `NNN_agent.mp3` from TTS),
which is what gives Q4 exact speaker labels without a diarisation model.

| directory | turns | result | use |
|---|---|---|---|
| **`call_3e9ee8e2c0d0/`** | 6 | **1 KB search, grounded, escalated** | **← the Q1 evidence call** |
| `call_5ded02f305e8/` | 9 | no searches; model asked 5 questions in one turn | superseded |
| `call_fb2a2fde63a3/` | 9 | no searches; `save_lead` rejected by schema validation | superseded |
| `call_c0cb6e91635b/` | 4 | short attempt | superseded |
| `call_2cb38fdf7acf/` | 2 | short attempt | superseded |
| `call_1c1294a98a64/` | 2 | short attempt | superseded |

The superseded calls are kept deliberately rather than deleted. Two of them are
the evidence for bugs documented in `docs/PRODUCTION.md`:

- `call_fb2a2fde63a3` is where `save_lead` was rejected because the model
  emitted `null` for optional fields the schema declared as `"string"` — the
  whole lead was discarded. Fixed by making optional fields nullable.
- `call_5ded02f305e8` is where `openai/gpt-oss-120b` emitted five questions in
  a single turn. Fixed by enforcing one question per turn in code
  (`enforce_voice_style`) rather than relying on prompt adherence.

Transcripts for all of these are in `q1_voice_agent/calls/web_in_*.json`.

## Q3 — localised calls, native voices

Rendered from the scripted transcripts by `q3_localized/render_call_audio.py`.
Agent and caller use different native voices so turn structure is audible.

| directory | market | voices | turns |
|---|---|---|---|
| `q3_ph_01_premium_reminder/` | PH | `fil-PH-Blessica` / `fil-PH-Angelo` | 13 |
| `q3_ph_02_objection_escalation/` | PH | `fil-PH-Blessica` / `fil-PH-Angelo` | 11 |
| `q3_id_01_installment_reminder/` | ID | `id-ID-Gadis` / `id-ID-Ardi` | 13 |
| `q3_id_02_javanese_accent/` | ID | `id-ID-Gadis` / `id-ID-Ardi` | 11 |

Each carries a `transcript.json` with the per-turn text, speaker and voice.

## Q4 — pipeline input

| directory | what |
|---|---|
| `q4_demo_call/` | 17-turn synthetic call, purpose-built so every required Q4 scenario occurs. Ships with `ground_truth.json` — the signal labels, **written before** the pipeline was run, which is what makes the precision figure meaningful. |

`call_3e9ee8e2c0d0/` can also be replayed through the Q4 pipeline — the brief
permits reusing a Q1 recording:

```bash
python q4_live_nudges/run_pipeline.py --call call_3e9ee8e2c0d0
```

## Loose files

`tts_en.mp3`, `tts_ph.mp3`, `tts_id.mp3`, `provider_check_en.mp3` — TTS and ASR
verification samples from `scripts/verify_providers.py`. These are the clips
behind the ASR comparison in `docs/Q3_LOCALISATION.md` §1.
