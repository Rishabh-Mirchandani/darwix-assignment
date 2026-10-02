"""Browser voice client server: the "web calling interface" Q1 asks for.

Pipeline per caller turn:

    browser mic -> VAD (client) -> webm/opus chunk -> WebSocket
      -> Groq whisper-large-v3-turbo  (ASR)
      -> VoiceAgent.say()             (LLM + KB tool calls)
      -> edge-tts                     (TTS)
      -> mp3 back over the same socket -> browser plays it

Every stage is timestamped and the breakdown is pushed to the browser with each
reply, so the latency numbers in the write-up come from the same path a caller
experiences rather than from a separate benchmark.

Audio is captured to data/audio/<call_id>/ and the transcript to
q1_voice_agent/calls/, which together are the recorded-call evidence the brief
requires.
"""
from __future__ import annotations

import asyncio
import base64
import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import edge_tts
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from groq import Groq

from shared.config import settings
from q1_voice_agent.agent import VoiceAgent
from q1_voice_agent.tools import retrieval_healthy

STATIC_DIR = Path(__file__).parent.parent / "static"
CALLS_DIR = Path(__file__).parent.parent / "calls"
AUDIO_ROOT = settings.audio_dir

app = FastAPI(title="Darwix Assessment - Voice Agent")
groq_client = Groq(api_key=settings.groq_api_key)


# ---------------------------------------------------------------------------
# Market configuration
#
# One server, several markets. Q1 is the English/India bot; Q3 adds the
# Philippines and Indonesia bots by pointing at a different prompt, ASR
# language hint and voice. Keeping them in one table makes the difference
# between markets auditable -- it is configuration, not three forked servers.
# ---------------------------------------------------------------------------

MARKETS: dict[str, dict] = {
    "in": {
        "label": "India - health insurance lead qualification",
        "prompt": "system.md",
        "voice": "en-IN-NeerjaNeural",
        "asr_language": None,       # auto-detect; callers may code-switch
        "greeting_hint": "English",
    },
}


def market_config(market: str) -> dict:
    return MARKETS.get(market, MARKETS["in"])


# ---------------------------------------------------------------------------
# Stage helpers
# ---------------------------------------------------------------------------


def transcribe(audio_bytes: bytes, language: str | None) -> tuple[str, float]:
    """Groq Whisper. Returns (text, latency_ms)."""
    t0 = time.perf_counter()
    kwargs = {"language": language} if language else {}
    resp = groq_client.audio.transcriptions.create(
        file=("turn.webm", audio_bytes),
        model=settings.groq_asr_model,
        response_format="json",
        **kwargs,
    )
    return resp.text.strip(), (time.perf_counter() - t0) * 1000


async def synthesise(text: str, voice: str) -> tuple[bytes, float]:
    """edge-tts. Returns (mp3_bytes, latency_ms).

    Collected into memory rather than streamed because the browser plays a
    single Audio element per turn; streaming would only help for replies long
    enough that the agent prompt already forbids them.
    """
    t0 = time.perf_counter()
    chunks: list[bytes] = []
    communicate = edge_tts.Communicate(text, voice)
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            chunks.append(chunk["data"])
    return b"".join(chunks), (time.perf_counter() - t0) * 1000


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "retrieval_api": retrieval_healthy(),
        "asr_model": settings.groq_asr_model,
        "chat_model": settings.groq_chat_model,
        "markets": list(MARKETS),
    }


@app.websocket("/ws/call")
async def call(ws: WebSocket) -> None:
    await ws.accept()

    market = ws.query_params.get("market", "in")
    cfg = market_config(market)

    if not retrieval_healthy():
        await ws.send_json({
            "type": "error",
            "message": "Retrieval API is not running on port 8001. "
                       "The agent cannot ground answers; refusing to start.",
        })
        await ws.close()
        return

    agent = VoiceAgent(
        system_prompt_file=cfg["prompt"],
        language=cfg.get("asr_language") or "en",
    )
    call_dir = AUDIO_ROOT / f"call_{agent.state.call_id}"
    call_dir.mkdir(parents=True, exist_ok=True)
    turn_index = 0

    await ws.send_json({
        "type": "connected",
        "call_id": agent.state.call_id,
        "market": market,
        "label": cfg["label"],
    })

    # --- agent speaks first, as on a real outbound call ---
    try:
        t0 = time.perf_counter()
        opening = await asyncio.to_thread(agent.greet)
        llm_ms = (time.perf_counter() - t0) * 1000
        audio, tts_ms = await synthesise(opening, cfg["voice"])
        (call_dir / "000_agent.mp3").write_bytes(audio)

        await ws.send_json({
            "type": "agent",
            "text": opening,
            "audio": base64.b64encode(audio).decode(),
            "timings": {"llm_ms": round(llm_ms), "tts_ms": round(tts_ms)},
        })
    except Exception as exc:
        await ws.send_json({"type": "error", "message": f"greeting failed: {exc}"})
        await ws.close()
        return

    # --- turn loop ---
    try:
        while True:
            message = await ws.receive()

            if message.get("type") == "websocket.disconnect":
                break

            # Control messages arrive as text, audio as bytes.
            if "text" in message and message["text"]:
                payload = json.loads(message["text"])
                if payload.get("type") == "hangup":
                    break
                continue

            audio_in = message.get("bytes")
            if not audio_in or len(audio_in) < 2000:
                # Too short to be speech; usually a VAD false trigger.
                await ws.send_json({"type": "ignored", "reason": "clip too short"})
                continue

            turn_index += 1
            turn_start = time.perf_counter()
            (call_dir / f"{turn_index:03d}_caller.webm").write_bytes(audio_in)

            # 1. ASR
            try:
                text, asr_ms = await asyncio.to_thread(
                    transcribe, audio_in, cfg["asr_language"]
                )
            except Exception as exc:
                await ws.send_json({"type": "error", "message": f"ASR failed: {exc}"})
                continue

            if not text:
                await ws.send_json({"type": "ignored", "reason": "no speech detected"})
                continue

            await ws.send_json({"type": "caller", "text": text,
                                "timings": {"asr_ms": round(asr_ms)}})

            # 2. Agent (LLM + tool calls)
            t0 = time.perf_counter()
            reply = await asyncio.to_thread(agent.say, text)
            llm_ms = (time.perf_counter() - t0) * 1000

            # 3. TTS
            try:
                audio_out, tts_ms = await synthesise(reply, cfg["voice"])
            except Exception as exc:
                await ws.send_json({
                    "type": "agent", "text": reply, "audio": None,
                    "error": f"TTS failed: {exc}",
                })
                continue

            (call_dir / f"{turn_index:03d}_agent.mp3").write_bytes(audio_out)
            total_ms = (time.perf_counter() - turn_start) * 1000

            last = agent.transcript[-1]
            await ws.send_json({
                "type": "agent",
                "text": reply,
                "audio": base64.b64encode(audio_out).decode(),
                "tools": last.tool_calls,
                "timings": {
                    "asr_ms": round(asr_ms),
                    "llm_ms": round(llm_ms),
                    "tts_ms": round(tts_ms),
                    "total_ms": round(total_ms),
                },
            })

    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001
        try:
            await ws.send_json({"type": "error", "message": str(exc)})
        except Exception:
            pass
    finally:
        label = f"web_{market}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        try:
            path = agent.save_transcript(CALLS_DIR, label)
            print(f"[voice] call {agent.state.call_id} saved -> {path}")
            print(f"[voice] audio -> {call_dir}")
        except Exception as exc:
            print(f"[voice] failed to save transcript: {exc}")


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8002, log_level="info")
