"""Is it safe to start a demo call right now?

The free tier allows 8,000 tokens per minute and a voice turn costs ~1,700, so
roughly four turns fit in a minute. If a test run has just consumed the window,
the next call stalls mid-turn while the pacer waits it out -- which is ruinous
on a recording.

This reports the remaining per-minute allowance and says plainly whether to
start now or wait.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.config import settings  # noqa: E402

TOKENS_PER_TURN = 1700


def main() -> int:
    from groq import Groq

    client = Groq(api_key=settings.groq_api_key, max_retries=0)
    try:
        raw = client.chat.completions.with_raw_response.create(
            model=settings.groq_chat_model,
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=5,
        )
    except Exception as exc:
        print(f"MODEL UNAVAILABLE: {str(exc)[:160]}")
        print("\nFall back by editing shared/config.py:")
        print('  groq_chat_model = "openai/gpt-oss-120b"')
        return 2

    h = raw.headers
    left = int(h.get("x-ratelimit-remaining-tokens", 0))
    limit = int(h.get("x-ratelimit-limit-tokens", 8000))
    reset = h.get("x-ratelimit-reset-tokens", "?")
    turns = left // TOKENS_PER_TURN

    print(f"model            {settings.groq_chat_model}")
    print(f"tokens this min  {left} / {limit}   (resets in {reset})")
    print(f"turns available  ~{turns}  (at ~{TOKENS_PER_TURN} tokens/turn)")
    print()

    if turns >= 5:
        print("START NOW - enough budget for a full demo call.")
        return 0
    if turns >= 3:
        print("MARGINAL - enough for a short call. Keep it to the two key turns:")
        print("  'What's the waiting period if I have diabetes?'  (grounded)")
        print("  'When is my next premium due?'                   (refuses)")
        return 0

    print("WAIT ~60s - the window is nearly spent; a call started now will")
    print("stall mid-turn while the pacer waits it out.")
    print("\nRe-run this script until it says START NOW.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
