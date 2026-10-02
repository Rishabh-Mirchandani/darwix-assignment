"""Read Groq's rate-limit headers and measure the real free-tier ceiling.

The scripted call test showed turn latency jumping from ~400ms to a suspiciously
consistent ~30s after the second turn. That pattern is backoff, not compute --
the SDK retries 429s transparently, so throttling shows up as latency rather
than as an error. This script makes the limits explicit so the agent can be
designed against them instead of around a mystery.
"""
from __future__ import annotations

import sys
import time

sys.path.insert(0, ".")
from shared.config import settings  # noqa: E402

INTERESTING = [
    "x-ratelimit-limit-requests",
    "x-ratelimit-remaining-requests",
    "x-ratelimit-reset-requests",
    "x-ratelimit-limit-tokens",
    "x-ratelimit-remaining-tokens",
    "x-ratelimit-reset-tokens",
    "retry-after",
]


def main() -> None:
    from groq import Groq

    # max_retries=0 so a 429 surfaces immediately instead of being hidden
    # behind the SDK's transparent backoff.
    client = Groq(api_key=settings.groq_api_key, max_retries=0)

    print("=== headers on a small request ===")
    raw = client.chat.completions.with_raw_response.create(
        model=settings.groq_chat_model,
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=5,
    )
    for k in INTERESTING:
        v = raw.headers.get(k)
        if v:
            print(f"  {k:<36} {v}")

    # How large is the system prompt we resend every single turn?
    from pathlib import Path

    prompt = Path("q1_voice_agent/prompts/system.md").read_text(encoding="utf-8")
    approx_tokens = len(prompt.split()) / 0.75
    print(f"\nsystem prompt: {len(prompt)} chars, ~{approx_tokens:.0f} tokens")
    print("(resent on EVERY turn, so it multiplies against the TPM ceiling)")

    print("\n=== burst test: 6 sequential turn-sized requests ===")
    big = prompt + "\n\nCaller: what is the waiting period?"
    for i in range(6):
        t0 = time.perf_counter()
        try:
            raw = client.chat.completions.with_raw_response.create(
                model=settings.groq_chat_model,
                messages=[{"role": "user", "content": big}],
                max_tokens=120,
            )
            dt = (time.perf_counter() - t0) * 1000
            rem_t = raw.headers.get("x-ratelimit-remaining-tokens", "?")
            rem_r = raw.headers.get("x-ratelimit-remaining-requests", "?")
            print(f"  {i + 1}: {dt:>8.0f} ms   tokens_left={rem_t:<10} "
                  f"requests_left={rem_r}")
        except Exception as exc:
            dt = (time.perf_counter() - t0) * 1000
            name = type(exc).__name__
            detail = str(exc)[:150]
            print(f"  {i + 1}: {dt:>8.0f} ms   {name}: {detail}")
            ra = getattr(getattr(exc, "response", None), "headers", {})
            if ra:
                for k in INTERESTING:
                    if ra.get(k):
                        print(f"       {k} = {ra.get(k)}")
            break


if __name__ == "__main__":
    main()
