"""End-to-end provider check: Groq LLM, Groq Whisper ASR, Gemini, edge-tts.

Run after filling .env. Verifies not just that the keys authenticate, but that
the exact models the pipeline depends on are reachable on the free tier, and
that the Filipino / Indonesian neural voices Q3 needs actually exist.

Prints latency for each call, since those numbers feed the Q4 budget.
"""
from __future__ import annotations

import asyncio
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, ".")
from shared.config import settings  # noqa: E402

SCRATCH = Path("data/audio")
SCRATCH.mkdir(parents=True, exist_ok=True)

results: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str) -> None:
    results.append((name, ok, detail))
    flag = "[+]" if ok else "[-]"
    print(f"{flag} {name}: {detail}", flush=True)


# --------------------------------------------------------------------------
# 1. Groq - list models
# --------------------------------------------------------------------------
def check_groq_models() -> list[str]:
    try:
        from groq import Groq

        client = Groq(api_key=settings.groq_api_key)
        models = client.models.list()
        ids = sorted(m.id for m in models.data)
        record("groq.models.list", True, f"{len(ids)} models reachable")
        chat = [m for m in ids if "whisper" not in m and "guard" not in m]
        asr = [m for m in ids if "whisper" in m]
        print(f"    chat models : {', '.join(chat[:8])}")
        print(f"    ASR models  : {', '.join(asr)}")
        return ids
    except Exception as exc:
        record("groq.models.list", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()
        return []


# --------------------------------------------------------------------------
# 2. Groq - chat completion (the Q4 nudge model)
# --------------------------------------------------------------------------
def check_groq_chat(available: list[str]) -> None:
    target = settings.groq_nudge_model
    if available and target not in available:
        alt = next(
            (m for m in available
             if "llama" in m and "70b" in m and "whisper" not in m),
            next((m for m in available if "llama" in m), None),
        )
        record(
            "groq.chat.model_choice", False,
            f"configured '{target}' NOT in account model list; "
            f"closest available: {alt}",
        )
        if alt:
            target = alt

    try:
        from groq import Groq

        client = Groq(api_key=settings.groq_api_key)
        t0 = time.perf_counter()
        resp = client.chat.completions.create(
            model=target,
            messages=[
                {"role": "system",
                 "content": "Reply with exactly one word."},
                {"role": "user",
                 "content": "Say OK"},
            ],
            max_tokens=10,
            temperature=0,
        )
        dt = (time.perf_counter() - t0) * 1000
        text = resp.choices[0].message.content.strip()
        record("groq.chat", True, f"model={target} latency={dt:.0f}ms reply={text!r}")
    except Exception as exc:
        record("groq.chat", False, f"{type(exc).__name__}: {exc}")


# --------------------------------------------------------------------------
# 3. edge-tts - generate audio, including the Q3 market voices
# --------------------------------------------------------------------------
async def _tts(text: str, voice: str, out: Path) -> float:
    import edge_tts

    t0 = time.perf_counter()
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(str(out))
    return (time.perf_counter() - t0) * 1000


def check_tts() -> Path | None:
    import edge_tts

    # Confirm the market voices exist in the catalogue.
    try:
        voices = asyncio.run(edge_tts.list_voices())
        names = {v["ShortName"] for v in voices}
        wanted = {
            "en-IN-NeerjaNeural": "Q1 English (India)",
            "fil-PH-BlessicaNeural": "Q3 Philippines",
            "fil-PH-AngeloNeural": "Q3 Philippines (male)",
            "id-ID-GadisNeural": "Q3 Indonesia",
            "id-ID-ArdiNeural": "Q3 Indonesia (male)",
        }
        missing = [v for v in wanted if v not in names]
        if missing:
            record("edge_tts.voices", False, f"missing: {missing}")
        else:
            record(
                "edge_tts.voices", True,
                f"{len(names)} voices; all 5 target voices present",
            )
            for v, purpose in wanted.items():
                print(f"    {v:<26} {purpose}")
    except Exception as exc:
        record("edge_tts.voices", False, f"{type(exc).__name__}: {exc}")
        return None

    # Actually synthesise one clip -- used as ASR input below.
    out = SCRATCH / "provider_check_en.mp3"
    try:
        ms = asyncio.run(_tts(
            "What is the waiting period for pre-existing diseases "
            "under this health insurance policy?",
            "en-IN-NeerjaNeural",
            out,
        ))
        size = out.stat().st_size
        record("edge_tts.synthesis", True,
               f"{size/1024:.0f} KB in {ms:.0f}ms -> {out}")
        return out
    except Exception as exc:
        record("edge_tts.synthesis", False, f"{type(exc).__name__}: {exc}")
        return None


# --------------------------------------------------------------------------
# 4. Groq Whisper - transcribe what we just synthesised
# --------------------------------------------------------------------------
def check_groq_asr(audio: Path | None, available: list[str]) -> None:
    if audio is None or not audio.exists():
        record("groq.asr", False, "skipped - no audio produced")
        return

    target = settings.groq_asr_model
    if available and target not in available:
        alt = next((m for m in available if "whisper" in m), None)
        record("groq.asr.model_choice", False,
               f"configured '{target}' not listed; using {alt}")
        if alt:
            target = alt

    try:
        from groq import Groq

        client = Groq(api_key=settings.groq_api_key)
        t0 = time.perf_counter()
        with audio.open("rb") as fh:
            resp = client.audio.transcriptions.create(
                file=(audio.name, fh.read()),
                model=target,
                response_format="json",
            )
        dt = (time.perf_counter() - t0) * 1000
        record("groq.asr", True,
               f"model={target} latency={dt:.0f}ms text={resp.text.strip()!r}")
    except Exception as exc:
        record("groq.asr", False, f"{type(exc).__name__}: {exc}")


# --------------------------------------------------------------------------
# 5. Gemini - the conversational model
# --------------------------------------------------------------------------
def check_gemini() -> None:
    try:
        from google import genai

        client = genai.Client(api_key=settings.google_api_key)

        names = []
        try:
            names = [m.name for m in client.models.list()]
            flash = [n for n in names if "flash" in n][:6]
            record("gemini.models.list", True, f"{len(names)} models")
            print(f"    flash variants: {', '.join(flash)}")
        except Exception as exc:
            record("gemini.models.list", False, f"{type(exc).__name__}: {exc}")

        t0 = time.perf_counter()
        resp = client.models.generate_content(
            model=settings.gemini_model,
            contents="Reply with exactly one word: OK",
        )
        dt = (time.perf_counter() - t0) * 1000
        record("gemini.generate", True,
               f"model={settings.gemini_model} latency={dt:.0f}ms "
               f"reply={resp.text.strip()!r}")
    except Exception as exc:
        record("gemini.generate", False, f"{type(exc).__name__}: {exc}")


def main() -> int:
    print("=" * 70)
    print("PROVIDER VERIFICATION")
    print("=" * 70)

    if not settings.groq_api_key or not settings.google_api_key:
        print("Missing key(s) in .env")
        return 1

    print("\n--- Groq ---")
    available = check_groq_models()
    check_groq_chat(available)

    print("\n--- edge-tts ---")
    audio = check_tts()

    print("\n--- Groq Whisper ASR ---")
    check_groq_asr(audio, available)

    print("\n--- Gemini ---")
    check_gemini()

    print("\n" + "=" * 70)
    failed = [r for r in results if not r[1]]
    print(f"{len(results) - len(failed)}/{len(results)} checks passed")
    for name, _, detail in failed:
        print(f"  FAILED  {name}: {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
