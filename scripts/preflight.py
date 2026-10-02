"""Pre-flight check: start every service and prove the whole chain works.

Run this before a demo or a recording. It is deliberately paranoid, because the
expensive failure is discovering a dead port halfway through a take.

Checks, in dependency order:
  1. .env keys present
  2. index built and loadable
  3. Groq reachable, and the configured chat model has budget left
  4. retrieval API up, answering AND refusing correctly
  5. voice server up, with the retrieval API visible to it
  6. nudge dashboard up with replayable audio

Starts anything that is down, then re-checks. Exits non-zero if the demo would
not work.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PY = ROOT / ".venv" / "Scripts" / "python.exe"
if not PY.exists():                      # non-Windows fallback
    PY = ROOT / ".venv" / "bin" / "python"

SERVICES = [
    (8001, "retrieval API", "q2_kb.retrieval.api:app", 35),
    (8002, "voice server", "q1_voice_agent.server.voice_server:app", 15),
    (8003, "nudge dashboard", "q4_live_nudges.server:app", 10),
]

OK, BAD = "[ OK ]", "[FAIL]"
problems: list[str] = []


def say(status: str, msg: str, detail: str = "") -> None:
    print(f"{status} {msg}" + (f"\n       {detail}" if detail else ""), flush=True)
    if status == BAD:
        problems.append(msg)


def port_up(port: int) -> bool:
    try:
        httpx.get(f"http://127.0.0.1:{port}/health", timeout=3.0)
        return True
    except Exception:
        try:
            httpx.get(f"http://127.0.0.1:{port}/", timeout=3.0)
            return True
        except Exception:
            return False


def start(port: int, name: str, target: str, wait_s: int) -> None:
    print(f"       starting {name} on :{port} ...", flush=True)
    kwargs = {"cwd": str(ROOT), "stdout": subprocess.DEVNULL,
              "stderr": subprocess.DEVNULL}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
    subprocess.Popen(
        [str(PY), "-m", "uvicorn", target, "--host", "127.0.0.1",
         "--port", str(port), "--log-level", "warning"],
        **kwargs,
    )
    for _ in range(wait_s):
        time.sleep(1)
        if port_up(port):
            return


def main() -> int:
    print("=" * 66)
    print("PRE-FLIGHT")
    print("=" * 66)

    # --- 1. keys ---
    from shared.config import settings

    if settings.groq_api_key:
        say(OK, f"GROQ_API_KEY present ({len(settings.groq_api_key)} chars)")
    else:
        say(BAD, "GROQ_API_KEY missing", "copy .env.example to .env and fill it")
        return 1

    # --- 2. index ---
    index_file = settings.index_dir / "embeddings.npy"
    if index_file.exists():
        import numpy as np

        shape = np.load(index_file, mmap_mode="r").shape
        say(OK, f"index present: {shape[0]} records x {shape[1]} dims")
    else:
        say(BAD, "no index", "run: python -m q2_kb.index.build_index")
        return 1

    # --- 3. model budget ---
    try:
        from groq import Groq

        client = Groq(api_key=settings.groq_api_key, max_retries=0)
        raw = client.chat.completions.with_raw_response.create(
            model=settings.groq_chat_model,
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=5,
        )
        left = raw.headers.get("x-ratelimit-remaining-tokens", "?")
        say(OK, f"chat model reachable: {settings.groq_chat_model}",
            f"tokens left this minute: {left}")
    except Exception as exc:
        detail = str(exc)[:150]
        say(BAD, f"chat model {settings.groq_chat_model} unavailable", detail)
        print("       -> fall back by editing shared/config.py:")
        print("          groq_chat_model = \"openai/gpt-oss-120b\"")

    # --- 4-6. services ---
    for port, name, target, wait_s in SERVICES:
        if port_up(port):
            say(OK, f"{name} already up on :{port}")
        else:
            start(port, name, target, wait_s)
            if port_up(port):
                say(OK, f"{name} started on :{port}")
            else:
                say(BAD, f"{name} failed to start on :{port}")

    # --- end-to-end behaviour, not just liveness ---
    print("-" * 66)
    try:
        grounded = httpx.post(
            "http://127.0.0.1:8001/search",
            json={"query": "waiting period for pre-existing diseases",
                  "market": "in", "top_n": 2},
            timeout=30.0,
        ).json()
        if grounded["grounded"] and grounded["citations"]:
            say(OK, "retrieval answers a known question",
                f"confidence {grounded['confidence']:.2f}, "
                f"{len(grounded['citations'])} citations")
        else:
            say(BAD, "retrieval failed to answer a known question")

        refused = httpx.post(
            "http://127.0.0.1:8001/search",
            json={"query": "what is my current policy balance",
                  "market": "in", "top_n": 2},
            timeout=30.0,
        ).json()
        if not refused["grounded"] and refused["answer"] is None:
            say(OK, "refusal gate fires on an out-of-scope question",
                f"score {refused['confidence']:.2f} below "
                f"{refused['threshold']:.2f}")
        else:
            say(BAD, "refusal gate did NOT fire — demo would show a hallucination")
    except Exception as exc:
        say(BAD, "retrieval behaviour check failed", str(exc)[:120])

    try:
        vh = httpx.get("http://127.0.0.1:8002/health", timeout=10.0).json()
        if vh.get("retrieval_api"):
            say(OK, "voice server can see the retrieval API")
        else:
            say(BAD, "voice server cannot reach retrieval API",
                "the agent will refuse to start a call")
    except Exception as exc:
        say(BAD, "voice server health check failed", str(exc)[:120])

    try:
        calls = httpx.get("http://127.0.0.1:8003/calls", timeout=10.0).json()
        n = len(calls.get("calls", []))
        if n:
            say(OK, f"nudge dashboard has {n} replayable call(s)")
        else:
            say(BAD, "no call audio to replay",
                "run: python q4_live_nudges/make_demo_call.py")
    except Exception as exc:
        say(BAD, "nudge dashboard check failed", str(exc)[:120])

    # --- summary ---
    print("=" * 66)
    if problems:
        print(f"{len(problems)} PROBLEM(S) — fix before recording:")
        for p in problems:
            print(f"  - {p}")
        return 1

    print("ALL CLEAR. Demo URLs:")
    print("  voice agent      http://127.0.0.1:8002")
    print("  nudge dashboard  http://127.0.0.1:8003")
    print("  retrieval API    http://127.0.0.1:8001/docs")
    print("\nUse Chrome or Edge, and headphones.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
