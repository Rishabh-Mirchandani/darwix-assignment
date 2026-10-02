"""End-to-end test of the voice path, driven by synthesised caller speech.

The scripted tests in `test_calls.py` exercise dialogue logic with text. This
exercises the thing text cannot: real audio in, real audio out, over the actual
WebSocket the browser uses.

Caller turns are synthesised with a *different* TTS voice from the agent's, so
the ASR is transcribing genuine audio rather than replaying a string. That makes
this a true integration test of ASR -> agent -> KB -> TTS, and it produces the
recorded-call artifacts the brief asks for without needing a person at a mic.

What it cannot test: microphone capture and the browser VAD. Those are verified
by hand in the recorded demo.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import statistics
import sys
import time
from pathlib import Path

import edge_tts
import websockets

sys.path.insert(0, ".")
from shared.config import settings  # noqa: E402

WS_URL = "ws://127.0.0.1:8002/ws/call?market=in"
OUT_DIR = settings.audio_dir / "e2e"

# A different voice from the agent's, so ASR does the real work.
CALLER_VOICE = "en-IN-PrabhatNeural"

SCRIPT = [
    "Yes, now is a good time.",
    "It's for me and my wife.",
    "I'm forty two, she's thirty nine.",
    "What is the waiting period for pre-existing conditions?",
    "When is my next premium due?",
    "Alright, can I speak to a person about that?",
]


async def synth_caller(text: str, path: Path) -> None:
    await edge_tts.Communicate(text, CALLER_VOICE).save(str(path))


async def run(script: list[str]) -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print("synthesising caller audio...", flush=True)
    clips: list[tuple[str, bytes]] = []
    for i, line in enumerate(script):
        p = OUT_DIR / f"caller_{i:02d}.mp3"
        await synth_caller(line, p)
        clips.append((line, p.read_bytes()))
        print(f"  {i}: {p.stat().st_size / 1024:5.1f} KB  {line[:52]}")

    totals: list[float] = []
    grounded_count = 0
    refused_count = 0
    turn_rows: list[dict] = []

    print(f"\nconnecting to {WS_URL}", flush=True)
    async with websockets.connect(WS_URL, max_size=16 * 1024 * 1024) as ws:

        async def recv_json(timeout: float = 180.0) -> dict:
            raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
            return json.loads(raw)

        msg = await recv_json()
        assert msg["type"] == "connected", msg
        call_id = msg["call_id"]
        print(f"connected: call_id={call_id}  market={msg['market']}\n")

        # The agent opens the call.
        msg = await recv_json()
        if msg["type"] != "agent":
            print(f"unexpected opening message: {msg}")
            return 1
        print(f"AGENT : {msg['text']}")
        if msg.get("audio"):
            (OUT_DIR / "agent_open.mp3").write_bytes(base64.b64decode(msg["audio"]))

        for i, (expected, audio) in enumerate(clips):
            print(f"\nCALLER: {expected}")
            t0 = time.perf_counter()
            await ws.send(audio)

            # First a transcript echo, then the agent reply.
            transcript = None
            reply = None
            while reply is None:
                msg = await recv_json()
                if msg["type"] == "caller":
                    transcript = msg["text"]
                    print(f"  ASR -> {transcript!r}  ({msg['timings']['asr_ms']}ms)")
                elif msg["type"] == "agent":
                    reply = msg
                elif msg["type"] == "ignored":
                    print(f"  IGNORED: {msg['reason']}")
                    break
                elif msg["type"] == "error":
                    print(f"  ERROR: {msg['message']}")
                    return 1

            if reply is None:
                continue

            wall = (time.perf_counter() - t0) * 1000
            t = reply.get("timings", {})
            print(f"AGENT : {reply['text']}")
            for tool in reply.get("tools", []):
                flag = ("grounded" if tool.get("grounded") is True
                        else "NOT IN KB" if tool.get("grounded") is False else "ok")
                print(f"  tool -> {tool['tool']} [{flag}] {tool['latency_ms']}ms")
                if tool.get("grounded") is True:
                    grounded_count += 1
                elif tool.get("grounded") is False:
                    refused_count += 1

            print(f"  timing -> asr {t.get('asr_ms')}ms | agent {t.get('llm_ms')}ms "
                  f"| tts {t.get('tts_ms')}ms | server {t.get('total_ms')}ms "
                  f"| wall {wall:.0f}ms")

            if reply.get("audio"):
                (OUT_DIR / f"agent_{i:02d}.mp3").write_bytes(
                    base64.b64decode(reply["audio"])
                )
            if t.get("total_ms"):
                totals.append(t["total_ms"])

            turn_rows.append({
                "caller_said": expected,
                "asr_heard": transcript,
                "agent_said": reply["text"],
                "timings": t,
                "wall_ms": round(wall),
                "tools": reply.get("tools", []),
            })

        await ws.send(json.dumps({"type": "hangup"}))

    print("\n" + "=" * 72)
    print("E2E VOICE SUMMARY")
    print("=" * 72)
    print(f"turns completed    : {len(turn_rows)}/{len(script)}")
    print(f"grounded answers   : {grounded_count}")
    print(f"KB refusals        : {refused_count}")
    if totals:
        s = sorted(totals)
        print(f"server turn latency: p50={s[len(s)//2]:.0f}ms  "
              f"p95={s[int(len(s)*0.95)-1]:.0f}ms  max={s[-1]:.0f}ms")

    # ASR fidelity: how closely did the transcript match what was spoken?
    print("\nASR fidelity:")
    for row in turn_rows:
        said = row["caller_said"].lower().rstrip(".?!")
        heard = (row["asr_heard"] or "").lower().rstrip(".?!")
        mark = "exact" if said == heard else "differs"
        print(f"  [{mark}] {row['caller_said'][:46]}")
        if mark == "differs":
            print(f"           heard: {row['asr_heard']}")

    report = OUT_DIR / "e2e_report.json"
    report.write_text(json.dumps({
        "turns": turn_rows,
        "grounded": grounded_count,
        "refusals": refused_count,
        "latency_ms": {
            "p50": statistics.median(totals) if totals else None,
            "values": totals,
        },
    }, indent=2), encoding="utf-8")
    print(f"\nwrote {report}")
    print(f"audio -> {OUT_DIR}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--turns", type=int, default=len(SCRIPT),
                    help="limit turns (useful under rate limits)")
    args = ap.parse_args()
    return asyncio.run(run(SCRIPT[: args.turns]))


if __name__ == "__main__":
    raise SystemExit(main())
