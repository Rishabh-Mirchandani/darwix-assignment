"""Conversation engine: the agent loop, tool dispatch and turn timing.

Deliberately transport-agnostic. The same class drives a scripted text test, a
browser voice session and the Q3 localised bots -- only the system prompt,
language and voice change. That matters for the assessment because the
conversation logic can be tested deterministically, in seconds, without
synthesising or transcribing a single second of audio.

Every turn records a latency breakdown. Those numbers are the Q1 evidence and
they feed directly into the Q4 budget.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from groq import Groq

from shared.config import settings
from q1_voice_agent.tools import TOOL_SCHEMAS, CallState, execute

PROMPT_DIR = Path(__file__).parent / "prompts"
MAX_TOOL_ROUNDS = 4          # guards against a tool-call loop on a live call
# Sized against Groq's 8k tokens/minute free-tier ceiling, not against the
# model's 131k context window. The context window is not the binding
# constraint here; the per-minute token budget is, and history is resent
# in full on every turn.
MAX_HISTORY_TURNS = 10


class TokenPacer:
    """Client-side pacing against Groq's free-tier tokens-per-minute ceiling.

    Without this, a burst of turns hits a 429 and the SDK silently retries with
    backoff -- which surfaces as a ~30s stall in the middle of a call, the worst
    possible failure mode for voice. Pacing proactively trades a short, known
    wait for an unpredictable long one, and it reports the wait so the latency
    report can separate "throttled" from "slow".

    This is purely a free-tier artifact. On a paid tier the limit is high enough
    that the pacer never fires, which is why it is a wrapper rather than being
    baked into the agent loop.
    """

    def __init__(self, tokens_per_minute: int = 8000, headroom: float = 0.95):
        self.budget = tokens_per_minute * headroom
        self.window: list[tuple[float, int]] = []  # (timestamp, tokens)
        self.total_waited_ms = 0.0
        self.throttle_events = 0

    def _spent_last_minute(self, now: float) -> int:
        self.window = [(t, n) for t, n in self.window if now - t < 60.0]
        return sum(n for _, n in self.window)

    def wait_if_needed(self, estimated_tokens: int) -> float:
        now = time.time()
        spent = self._spent_last_minute(now)
        if spent + estimated_tokens <= self.budget:
            return 0.0

        # Sleep only until ENOUGH budget frees, not until the oldest entry ages
        # out. The earlier version waited for the oldest request to expire
        # regardless of how little headroom was actually needed, which produced
        # 19-second pauses mid-call when a 3-second wait would have done.
        #
        # Entries expire in insertion order, so walk forward accumulating what
        # each expiry returns and stop at the first point that clears the
        # shortfall.
        needed = (spent + estimated_tokens) - self.budget
        freed = 0
        sleep_for = 0.0
        for ts, tokens in self.window:
            freed += tokens
            sleep_for = max(0.0, 60.0 - (now - ts))
            if freed >= needed:
                break

        sleep_for += 0.15  # small margin against clock skew at the boundary
        self.throttle_events += 1
        self.total_waited_ms += sleep_for * 1000
        time.sleep(sleep_for)
        return sleep_for * 1000

    def record(self, tokens: int) -> None:
        self.window.append((time.time(), tokens))


_QUESTION_SPLIT = re.compile(r"(?<=[?])\s+")
_FILLER_DOTS = re.compile(r"\.{3,}")


def enforce_voice_style(text: str) -> str:
    """Trim a reply to one spoken turn: at most one question.

    Prompt instructions are a request, not a guarantee, and adherence varies
    sharply by model. On a live call `openai/gpt-oss-120b` emitted five
    questions in a single turn -- "What's the age...Could you tell me the
    age...Which city...any pre-existing conditions...any health cover" -- which
    is unusable over audio: the caller answers only the last one, and the agent
    then re-asks the rest.

    So the constraint is enforced here rather than hoped for. Everything up to
    and including the FIRST question mark is kept; anything after it is
    dropped. A reply with no question is left alone apart from filler cleanup.

    This is deliberately a hard truncation rather than a re-prompt: a re-prompt
    costs another round trip and more tokens, and the first question is almost
    always the one the agent actually wanted to ask.
    """
    if not text:
        return text

    cleaned = _FILLER_DOTS.sub(" ", text)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()

    if "?" not in cleaned:
        return cleaned

    head, _, _ = cleaned.partition("?")
    return (head + "?").strip()


@dataclass
class Turn:
    role: str
    text: str
    at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    timings_ms: dict[str, float] = field(default_factory=dict)


class VoiceAgent:
    def __init__(
        self,
        system_prompt_file: str = "system.md",
        model: str | None = None,
        language: str = "en",
        temperature: float = 0.15,
        prompt_dir: Path | None = None,
        market: str = "in",
    ) -> None:
        self.client = Groq(api_key=settings.groq_api_key)
        self.model = model or settings.groq_chat_model
        self.language = language
        self.temperature = temperature

        self.market = market
        path = (prompt_dir or PROMPT_DIR) / system_prompt_file
        if not path.exists():
            raise FileNotFoundError(f"system prompt not found: {path}")
        self.system_prompt = path.read_text(encoding="utf-8")

        self.pacer = TokenPacer()
        self.state = CallState(market=market)
        self.messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt}
        ]
        self.transcript: list[Turn] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def greet(self) -> str:
        """Opening line. Generated, not hardcoded, so it matches the prompt's
        persona and language without a separate template per market."""
        self.messages.append({
            "role": "user",
            "content": "[CALL CONNECTED - the caller has picked up. Open the call.]",
        })
        return self._run_turn(record_user=False)

    def say(self, user_text: str) -> str:
        """Process one caller utterance and return what the agent should speak."""
        self.transcript.append(Turn(role="caller", text=user_text))
        self.messages.append({"role": "user", "content": user_text})
        return self._run_turn()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _trim_history(self) -> None:
        """Keep system + the most recent turns.

        Trimming is by message count rather than tokens because the cost of a
        slightly oversized prompt is far lower than the cost of a mid-call
        context error, and tool messages must stay adjacent to the assistant
        message that requested them.
        """
        if len(self.messages) <= MAX_HISTORY_TURNS:
            return
        head = self.messages[:1]
        tail = self.messages[-(MAX_HISTORY_TURNS - 1):]
        # Never start the tail on an orphaned tool result.
        while tail and tail[0].get("role") == "tool":
            tail = tail[1:]
        self.messages = head + tail

    def _run_turn(self, record_user: bool = True) -> str:
        """One caller utterance -> one spoken agent reply, resolving tools."""
        timings: dict[str, float] = {}
        tool_log: list[dict[str, Any]] = []
        turn_start = time.perf_counter()

        for round_index in range(MAX_TOOL_ROUNDS):
            self._trim_history()

            # Estimate this request's cost (~4 chars/token) and pace against
            # the free-tier ceiling before spending it.
            # +250 for the completion. Measured completions are 42 tokens
            # (qwen) to 156 (gpt-oss, which emits reasoning first); +600 was
            # guesswork that made the pacer throttle far earlier than the
            # real budget required.
            estimated = sum(
                len(str(m.get("content") or "")) for m in self.messages
            ) // 4 + 250
            waited = self.pacer.wait_if_needed(estimated)
            if waited:
                timings[f"throttle_wait_{round_index}"] = waited

            t0 = time.perf_counter()
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=self.messages,
                    tools=TOOL_SCHEMAS,
                    tool_choice="auto",
                    temperature=self.temperature,
                    max_tokens=400,
                )
            except Exception as exc:  # noqa: BLE001
                detail = str(exc)
                # A malformed tool call is rejected by the provider before any
                # content is returned, which drops the caller's turn entirely.
                # Observed on a live call: the model emitted null for three
                # optional fields and the whole lead was discarded. The schema
                # fix prevents the common case; this keeps the call alive for
                # the rest. A caller hearing silence is worse than a caller
                # hearing a reply that skipped a tool.
                if "tool_use_failed" in detail or "did not match schema" in detail:
                    timings[f"tool_schema_retry_{round_index}"] = 1.0
                    try:
                        response = self.client.chat.completions.create(
                            model=self.model,
                            messages=self.messages + [{
                                "role": "system",
                                "content": (
                                    "Your previous tool call was malformed. "
                                    "Reply to the caller in one short sentence "
                                    "without calling any tool."
                                ),
                            }],
                            temperature=self.temperature,
                            max_tokens=200,
                        )
                    except Exception:
                        raise exc from None
                else:
                    raise
            timings[f"llm_{round_index}"] = (time.perf_counter() - t0) * 1000

            usage = getattr(response, "usage", None)
            self.pacer.record(
                getattr(usage, "total_tokens", None) or estimated
            )

            choice = response.choices[0].message
            calls = choice.tool_calls or []

            # Assistant message must be appended exactly as returned, including
            # tool_calls, or the follow-up tool messages are rejected.
            self.messages.append({
                "role": "assistant",
                "content": choice.content or "",
                **({"tool_calls": [
                    {
                        "id": c.id,
                        "type": "function",
                        "function": {
                            "name": c.function.name,
                            "arguments": c.function.arguments,
                        },
                    } for c in calls
                ]} if calls else {}),
            })

            if not calls:
                reply = enforce_voice_style(choice.content or "")
                timings["turn_total"] = (time.perf_counter() - turn_start) * 1000
                self.transcript.append(
                    Turn(role="agent", text=reply,
                         tool_calls=tool_log, timings_ms=timings)
                )
                return reply

            # Execute every requested tool before looping back to the model.
            for call in calls:
                name = call.function.name
                try:
                    args = json.loads(call.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}

                t0 = time.perf_counter()
                result = execute(name, args, self.state)
                elapsed = (time.perf_counter() - t0) * 1000
                timings[f"tool_{name}_{round_index}"] = elapsed

                tool_log.append({
                    "tool": name,
                    "args": args,
                    "grounded": result.get("grounded"),
                    "confidence": result.get("confidence"),
                    "latency_ms": round(elapsed, 1),
                })

                self.messages.append({
                    "role": "tool",
                    "tool_call_id": call.id,
                    "name": name,
                    "content": json.dumps(result),
                })

        # Tool budget exhausted: fail safe rather than loop on a live call.
        fallback = (
            "I'm having trouble checking that right now. Let me have a "
            "colleague call you back on this."
        )
        timings["turn_total"] = (time.perf_counter() - turn_start) * 1000
        self.transcript.append(
            Turn(role="agent", text=fallback,
                 tool_calls=tool_log, timings_ms=timings)
        )
        return fallback

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------

    def transcript_rows(self) -> Iterator[dict[str, Any]]:
        for turn in self.transcript:
            yield {
                "role": turn.role,
                "text": turn.text,
                "at": turn.at,
                "tool_calls": turn.tool_calls,
                "timings_ms": {k: round(v, 1) for k, v in turn.timings_ms.items()},
            }

    def save_transcript(self, directory: Path, label: str) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{label}__{self.state.call_id}.json"
        payload = {
            "call_id": self.state.call_id,
            "label": label,
            "model": self.model,
            "language": self.language,
            "started_at": self.state.started_at,
            "summary": self.state.summary(),
            "kb_searches": self.state.searches,
            "transcript": list(self.transcript_rows()),
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path
