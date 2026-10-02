"""Pick the chat models empirically instead of by reputation.

Two different jobs with different constraints:

  Q1/Q3 conversation -> quality and instruction-following matter; a voice turn
                        can absorb ~1s.
  Q4 nudge generation -> latency dominates. A nudge that lands after the agent
                        has moved on is worthless, so time-to-first-token is
                        the metric, not benchmark scores.

Also probes multilingual competence, because Q3 needs Taglish and Bahasa
Indonesia and a model that is strong in English can be poor at both.
"""
from __future__ import annotations

import statistics
import sys
import time

sys.path.insert(0, ".")
from shared.config import settings  # noqa: E402

GROQ_CHAT = ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b"]
GEMINI_CANDIDATES = ["gemini-3.8-flash", "gemini-flash-latest"]

NUDGE_SYSTEM = (
    "You are a real-time sales coach. Read the call snippet and output ONE "
    "short coaching nudge of at most 12 words, or the single word NONE. "
    "No preamble."
)
NUDGE_USER = (
    "Agent: So the premium works out to about 18,000 a year.\n"
    "Customer: That's quite a lot. My wife also needs cover, and honestly "
    "I'm not sure we can afford both right now."
)

MULTILINGUAL = (
    "Reply in natural Taglish (Tagalog-English code-switching) as a Filipino "
    "insurance agent. One sentence, remind the client their premium is due "
    "on the 15th. Do not translate literally."
)


def bench_groq(model: str, rounds: int = 3) -> dict | None:
    from groq import Groq

    client = Groq(api_key=settings.groq_api_key)
    latencies: list[float] = []
    ttfts: list[float] = []
    reply = ""

    for _ in range(rounds):
        try:
            t0 = time.perf_counter()
            stream = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": NUDGE_SYSTEM},
                    {"role": "user", "content": NUDGE_USER},
                ],
                max_tokens=40,
                temperature=0,
                stream=True,
            )
            ttft = None
            chunks: list[str] = []
            for chunk in stream:
                delta = chunk.choices[0].delta.content
                if delta:
                    if ttft is None:
                        ttft = (time.perf_counter() - t0) * 1000
                    chunks.append(delta)
            total = (time.perf_counter() - t0) * 1000
            latencies.append(total)
            if ttft is not None:
                ttfts.append(ttft)
            reply = "".join(chunks).strip()
        except Exception as exc:
            print(f"    ERROR {model}: {type(exc).__name__}: {str(exc)[:140]}")
            return None

    return {
        "model": model,
        "ttft_ms": statistics.mean(ttfts) if ttfts else -1,
        "total_ms": statistics.mean(latencies),
        "reply": reply,
    }


def probe_multilingual(model: str) -> str:
    from groq import Groq

    client = Groq(api_key=settings.groq_api_key)
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": MULTILINGUAL}],
            max_tokens=80,
            temperature=0.3,
        )
        return resp.choices[0].message.content.strip().replace("\n", " ")
    except Exception as exc:
        return f"ERROR: {type(exc).__name__}: {str(exc)[:80]}"


def bench_gemini() -> None:
    from google import genai

    client = genai.Client(api_key=settings.google_api_key)
    for name in GEMINI_CANDIDATES:
        try:
            t0 = time.perf_counter()
            resp = client.models.generate_content(
                model=name,
                contents=f"{NUDGE_SYSTEM}\n\n{NUDGE_USER}",
            )
            dt = (time.perf_counter() - t0) * 1000
            text = (resp.text or "").strip().replace("\n", " ")
            print(f"  [+] {name:<24} {dt:>7.0f}ms  {text[:70]!r}")
        except Exception as exc:
            print(f"  [-] {name:<24} {type(exc).__name__}: {str(exc)[:110]}")


def main() -> None:
    print("=" * 78)
    print("GROQ CHAT MODELS - nudge task (streamed, 3 rounds)")
    print("=" * 78)
    rows = []
    for m in GROQ_CHAT:
        print(f"\n  benchmarking {m} ...", flush=True)
        r = bench_groq(m)
        if r:
            rows.append(r)
            print(f"    TTFT {r['ttft_ms']:>7.0f}ms   total {r['total_ms']:>7.0f}ms")
            print(f"    reply: {r['reply'][:100]!r}")

    if rows:
        print("\n  --- ranked by time-to-first-token (what Q4 cares about) ---")
        for r in sorted(rows, key=lambda x: x["ttft_ms"]):
            print(f"    {r['model']:<26} TTFT {r['ttft_ms']:>7.0f}ms  "
                  f"total {r['total_ms']:>7.0f}ms")

    print("\n" + "=" * 78)
    print("MULTILINGUAL PROBE - Taglish (needed for Q3)")
    print("=" * 78)
    for m in GROQ_CHAT:
        print(f"\n  {m}:")
        print(f"    {probe_multilingual(m)[:230]}")

    print("\n" + "=" * 78)
    print("GEMINI")
    print("=" * 78)
    bench_gemini()


if __name__ == "__main__":
    main()
