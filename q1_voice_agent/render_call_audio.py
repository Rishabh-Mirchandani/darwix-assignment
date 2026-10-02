"""Render saved Q1 scripted-call transcripts to audio.

The brief requires "two recorded calls per market". The Q3 conversations were
run as scripted text so the dialogue logic could be tested deterministically and
re-run after every prompt change -- but text transcripts are not recordings.

This renders those exact conversations to per-turn audio with the **native**
Filipino and Indonesian neural voices, which also produces the evidence for the
brief's native-TTS requirement. Agent and caller use different voices so the
turn structure is audible.

Costs no LLM tokens: the conversations already happened, this is TTS only.

Output layout matches the voice server's, so these calls can be replayed
through the Q4 pipeline exactly like a live one:

    data/audio/q1_<label>/000_agent.mp3
                          001_caller.mp3
                          ...
                          transcript.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import edge_tts

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from shared.config import settings  # noqa: E402

CALLS_DIR = Path(__file__).parent / "calls"

# Native voices per market. Agent and caller deliberately differ.
VOICES = {
    "ph": {"agent": "fil-PH-BlessicaNeural", "caller": "fil-PH-AngeloNeural"},
    "id": {"agent": "id-ID-GadisNeural", "caller": "id-ID-ArdiNeural"},
    "in": {"agent": "en-IN-NeerjaNeural", "caller": "en-IN-PrabhatNeural"},
}


async def synth(text: str, voice: str, path: Path) -> int:
    await edge_tts.Communicate(text, voice).save(str(path))
    return path.stat().st_size


async def render(transcript_path: Path, market: str) -> dict:
    data = json.loads(transcript_path.read_text(encoding="utf-8"))
    label = data.get("label") or transcript_path.stem.split("__")[0]
    voices = VOICES.get(market, VOICES["in"])

    out_dir = settings.audio_dir / f"q1_{label}"
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*"):
        old.unlink()

    print(f"\n{label}  (market={market})")
    print(f"  agent voice : {voices['agent']}")
    print(f"  caller voice: {voices['caller']}")

    turns = data["transcript"]
    total_bytes = 0
    rendered = []

    for i, turn in enumerate(turns):
        role = turn["role"]
        speaker = "agent" if role == "agent" else "caller"
        text = (turn.get("text") or "").strip()
        if not text:
            continue
        path = out_dir / f"{i:03d}_{speaker}.mp3"
        size = await synth(text, voices[speaker], path)
        total_bytes += size
        rendered.append({
            "index": i,
            "file": path.name,
            "speaker": speaker,
            "voice": voices[speaker],
            "text": text,
            "bytes": size,
        })
        print(f"    {i:02d} {speaker:<7} {size/1024:>6.1f} KB  {text[:52]}")

    (out_dir / "transcript.json").write_text(
        json.dumps({
            "label": label,
            "market": market,
            "voices": voices,
            "source_transcript": transcript_path.name,
            "model": data.get("model"),
            "summary": data.get("summary"),
            "turns": rendered,
        }, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"  -> {len(rendered)} turns, {total_bytes/1024:.0f} KB  {out_dir}")
    return {"label": label, "market": market, "turns": len(rendered),
            "kb": round(total_bytes / 1024), "dir": str(out_dir)}


async def main_async(args: argparse.Namespace) -> int:
    index_path = CALLS_DIR / "index.json"
    if not index_path.exists():
        print(f"no {index_path}; run q3_localized/test_calls.py first")
        return 2

    index = json.loads(index_path.read_text(encoding="utf-8"))
    results = []
    for row in index:
        if args.only and not row["label"].startswith(args.only):
            continue
        tpath = CALLS_DIR / row["transcript"]
        if not tpath.exists():
            print(f"  missing transcript: {tpath.name}")
            continue
        results.append(await render(tpath, row.get("market", "in")))

    print("\n" + "=" * 68)
    print(f"{len(results)} call(s) rendered to audio")
    for r in results:
        print(f"  {r['market']}  {r['label']:<30} {r['turns']:>2} turns  {r['kb']:>4} KB")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="label prefix, e.g. ph or id_02")
    return asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
