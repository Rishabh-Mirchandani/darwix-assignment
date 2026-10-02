"""Fair re-test of Gemini: streamed, warmed, multiple rounds.

The first benchmark showed 3.4-5.6s, which would disqualify it from the voice
path. That measurement was non-streamed and included a cold call, so this
re-runs it the way a voice agent would actually use it -- streaming, after a
warm-up -- before any architectural decision is made on the number.
"""
from __future__ import annotations

import statistics
import sys
import time

sys.path.insert(0, ".")
from shared.config import settings  # noqa: E402

PROMPT = (
    "You are a health insurance agent. The customer said: 'That's quite a lot, "
    "I'm not sure I can afford it.' Reply in one short sentence."
)
CANDIDATES = ["gemini-flash-latest", "gemini-3.8-flash"]
ROUNDS = 3


def bench(model: str) -> None:
    from google import genai

    client = genai.Client(api_key=settings.google_api_key)

    # Warm-up (not timed): establishes the connection and any session setup.
    try:
        client.models.generate_content(model=model, contents="hi")
    except Exception as exc:
        print(f"  [-] {model}: warm-up failed {type(exc).__name__}: {str(exc)[:90]}")
        return

    ttfts: list[float] = []
    totals: list[float] = []
    last = ""

    for _ in range(ROUNDS):
        try:
            t0 = time.perf_counter()
            ttft = None
            parts: list[str] = []
            for chunk in client.models.generate_content_stream(
                model=model, contents=PROMPT
            ):
                if chunk.text:
                    if ttft is None:
                        ttft = (time.perf_counter() - t0) * 1000
                    parts.append(chunk.text)
            totals.append((time.perf_counter() - t0) * 1000)
            if ttft is not None:
                ttfts.append(ttft)
            last = "".join(parts).strip().replace("\n", " ")
        except Exception as exc:
            print(f"  [-] {model}: {type(exc).__name__}: {str(exc)[:110]}")
            return

    print(f"  [+] {model}")
    print(f"        TTFT  mean {statistics.mean(ttfts):>7.0f}ms   "
          f"min {min(ttfts):>7.0f}ms" if ttfts else "        (no TTFT)")
    print(f"        TOTAL mean {statistics.mean(totals):>7.0f}ms   "
          f"min {min(totals):>7.0f}ms")
    print(f"        reply: {last[:100]!r}")


def bench_groq_same_task() -> None:
    from groq import Groq

    client = Groq(api_key=settings.groq_api_key)
    model = "qwen/qwen3.8-27b"
    client.chat.completions.create(
        model=model, messages=[{"role": "user", "content": "hi"}], max_tokens=5
    )

    ttfts: list[float] = []
    totals: list[float] = []
    last = ""
    for _ in range(ROUNDS):
        t0 = time.perf_counter()
        ttft = None
        parts: list[str] = []
        for chunk in client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": PROMPT}],
            max_tokens=120,
            stream=True,
        ):
            d = chunk.choices[0].delta.content
            if d:
                if ttft is None:
                    ttft = (time.perf_counter() - t0) * 1000
                parts.append(d)
        totals.append((time.perf_counter() - t0) * 1000)
        if ttft is not None:
            ttfts.append(ttft)
        last = "".join(parts).strip().replace("\n", " ")

    print(f"  [+] groq {model}")
    print(f"        TTFT  mean {statistics.mean(ttfts):>7.0f}ms   "
          f"min {min(ttfts):>7.0f}ms")
    print(f"        TOTAL mean {statistics.mean(totals):>7.0f}ms   "
          f"min {min(totals):>7.0f}ms")
    print(f"        reply: {last[:100]!r}")


def main() -> None:
    print("GEMINI (streamed, warmed, same task)")
    for m in CANDIDATES:
        bench(m)
    print("\nGROQ (same task, for comparison)")
    bench_groq_same_task()


if __name__ == "__main__":
    main()
