"""Live nudge dashboard: WebSocket server + static page.

The brief allows a dashboard, WebSocket, webhook, polling API or CLI. This
provides the first two, because a coaching panel is the form the product
actually takes -- an agent cannot read a JSON feed mid-call -- and because
watching nudges appear while audio is still playing is the clearest possible
demonstration that analysis is happening *during* the call rather than after.

The same `LiveCallPipeline` backs this and the CLI runner, so the dashboard
cannot drift from the measured path.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI, WebSocket, WebSocketDisconnect       # noqa: E402
from fastapi.responses import FileResponse                        # noqa: E402

from shared.config import settings                                # noqa: E402
from q4_live_nudges.pipeline.stream import (                      # noqa: E402
    LiveCallPipeline, PipelineEvent, load_call_segments,
)

STATIC_DIR = Path(__file__).parent / "static"
DEMO_DIR = settings.audio_dir / "q4_demo_call"

app = FastAPI(title="Darwix Assessment - Live Call Nudges")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "dashboard.html")


@app.get("/calls")
def list_calls() -> dict:
    """Recorded calls available to replay."""
    root = settings.audio_dir
    calls = []
    for d in sorted(root.iterdir()) if root.exists() else []:
        if not d.is_dir():
            continue
        audio = [p for p in d.iterdir()
                 if p.suffix in (".mp3", ".webm", ".wav", ".m4a")]
        if audio:
            calls.append({
                "id": d.name,
                "segments": len(audio),
                "has_ground_truth": (d / "ground_truth.json").exists(),
            })
    return {"calls": calls, "default": DEMO_DIR.name}


@app.websocket("/ws/live")
async def live(ws: WebSocket) -> None:
    await ws.accept()

    call_id = ws.query_params.get("call", DEMO_DIR.name)
    speed = float(ws.query_params.get("speed", "1.0"))
    use_tier2 = ws.query_params.get("tier2", "1") != "0"

    call_dir = settings.audio_dir / call_id
    if not call_dir.exists():
        await ws.send_json({"type": "error",
                            "message": f"no call audio at {call_dir}"})
        await ws.close()
        return

    segments = load_call_segments(call_dir)
    if not segments:
        await ws.send_json({"type": "error", "message": "no audio segments"})
        await ws.close()
        return

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[dict] = asyncio.Queue()

    def emit(ev: PipelineEvent) -> None:
        # The pipeline runs stages in worker threads, so hand events back to
        # the event loop rather than touching the socket from off-loop.
        loop.call_soon_threadsafe(
            queue.put_nowait, {"type": ev.kind, **ev.payload}
        )

    pipeline = LiveCallPipeline(emit=emit, use_tier2=use_tier2)

    await ws.send_json({
        "type": "started",
        "call": call_id,
        "segments": len(segments),
        "audio_seconds": round(sum(s.duration_s for s in segments), 1),
        "speed": speed,
        "tier2": use_tier2,
    })

    async def pump() -> None:
        while True:
            event = await queue.get()
            await ws.send_json(event)

    pump_task = asyncio.create_task(pump())
    try:
        await pipeline.run(segments, speed=speed)
        # Let the queue drain before closing.
        await asyncio.sleep(0.4)
        await ws.send_json({"type": "finished", "report": pipeline.report()})
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001
        try:
            await ws.send_json({"type": "error", "message": str(exc)})
        except Exception:
            pass
    finally:
        pump_task.cancel()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8003, log_level="warning")
