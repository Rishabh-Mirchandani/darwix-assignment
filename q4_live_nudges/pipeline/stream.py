"""Real-time streaming pipeline: audio -> ASR -> signals -> nudges.

The brief is explicit that "a completed recording analyzed only after upload
does not qualify", so the constraint this module is built around is that
nothing may look ahead. Audio is fed in wall-clock time, each chunk is
transcribed as it arrives, and a nudge must be emitted before the call moves
on. If this ran faster than real time it would be cheating.

**Speaker separation.** Groq's Whisper endpoint does not diarise. Rather than
guess speakers from a mixed track, the replay source consumes the *per-turn*
audio files the Q1/Q3 voice server already writes (`NNN_caller.webm`,
`NNN_agent.mp3`). Speaker labels are therefore exact rather than inferred,
which matters because half the compliance rules only apply to the agent --
attributing an over-promise to the wrong speaker would invert the signal. For
a genuinely mixed single-channel recording this would need a diarisation model,
and that limitation is recorded in the write-up rather than papered over.

**Latency accounting.** Every stage stamps a monotonic clock, so the report can
attribute delay to ASR, signal extraction, the LLM or delivery rather than
quoting one end-to-end number that hides where the time went.
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator, Callable

from groq import Groq

from shared.config import settings
from q4_live_nudges.pipeline.nudges import NudgeManager
from q4_live_nudges.pipeline.signals import (
    Signal, SignalType, Tier1Detector, Utterance,
)


@dataclass
class AudioSegment:
    """One piece of call audio with a known speaker and wall-clock position."""

    speaker: str          # agent | customer
    data: bytes
    suffix: str           # file extension, so Whisper gets the right container
    start_s: float        # seconds into the call
    duration_s: float


@dataclass
class StageTiming:
    """Per-utterance latency breakdown, in milliseconds."""

    audio_received: float = 0.0
    asr_ms: float = 0.0
    tier1_ms: float = 0.0
    tier2_ms: float = 0.0
    nudge_ms: float = 0.0
    delivery_ms: float = 0.0

    @property
    def end_to_end_ms(self) -> float:
        return (
            self.asr_ms + self.tier1_ms + self.tier2_ms
            + self.nudge_ms + self.delivery_ms
        )

    def to_dict(self) -> dict:
        return {
            "asr_ms": round(self.asr_ms, 1),
            "tier1_ms": round(self.tier1_ms, 2),
            "tier2_ms": round(self.tier2_ms, 1),
            "nudge_ms": round(self.nudge_ms, 2),
            "delivery_ms": round(self.delivery_ms, 2),
            "end_to_end_ms": round(self.end_to_end_ms, 1),
        }


# ---------------------------------------------------------------------------
# Audio sources
# ---------------------------------------------------------------------------


def load_call_segments(call_dir: Path) -> list[AudioSegment]:
    """Read a recorded call directory into ordered, speaker-labelled segments.

    Expects the layout the voice server writes:
        000_agent.mp3, 001_caller.webm, 001_agent.mp3, ...
    """
    candidates = [
        p for p in call_dir.iterdir()
        if p.suffix in (".mp3", ".webm", ".wav", ".m4a", ".ogg")
    ]

    def order_key(path: Path) -> tuple[int, int, str]:
        """Conversation order, not alphabetical order.

        The voice server writes `NNN_caller.webm` then `NNN_agent.mp3` for the
        same turn index, because the caller speaks and the agent replies. Plain
        `sorted()` puts "agent" before "caller" within an index, which replays
        every agent reply BEFORE the utterance it answers.

        That is not only confusing to read: the compliance checklist is
        speaker-ordered, so a disclosure the agent made in response to a
        question would appear to precede it, and gaps get misattributed.

        Turn 000 is the agent's opening greeting and has no caller file, so it
        still sorts first.
        """
        stem = path.stem
        index_part, _, speaker_part = stem.partition("_")
        try:
            index = int(index_part)
        except ValueError:
            index = 0
        # Within a turn: caller (0) precedes agent (1).
        speaker_rank = 0 if "caller" in speaker_part else 1
        return (index, speaker_rank, stem)

    files = sorted(candidates, key=order_key)
    segments: list[AudioSegment] = []
    cursor = 0.0
    for path in files:
        speaker = "customer" if "caller" in path.name else "agent"
        data = path.read_bytes()
        # Duration is estimated from file size: these are short conversational
        # turns at a known bitrate, and an exact decode would pull in ffmpeg for
        # a number only used to pace the replay.
        duration = max(1.0, len(data) / 4000.0)
        segments.append(AudioSegment(speaker, data, path.suffix, cursor, duration))
        cursor += duration + 0.4   # small inter-turn gap
    return segments


async def replay_realtime(
    segments: list[AudioSegment],
    speed: float = 1.0,
) -> AsyncIterator[AudioSegment]:
    """Yield segments paced to wall-clock time.

    `speed` exists only so the test suite can run faster than real time; the
    demo and every reported latency number use 1.0.
    """
    start = time.perf_counter()
    for seg in segments:
        target = seg.start_s / speed
        now = time.perf_counter() - start
        if target > now:
            await asyncio.sleep(target - now)
        yield seg


# ---------------------------------------------------------------------------
# ASR
# ---------------------------------------------------------------------------


class StreamingASR:
    def __init__(self) -> None:
        self.client = Groq(api_key=settings.groq_api_key)
        self.model = settings.groq_asr_model
        self.latencies: list[float] = []

    def transcribe(self, seg: AudioSegment, language: str | None = None) -> tuple[str, float]:
        t0 = time.perf_counter()
        kwargs = {"language": language} if language else {}
        try:
            resp = self.client.audio.transcriptions.create(
                file=(f"chunk{seg.suffix}", seg.data),
                model=self.model,
                response_format="json",
                **kwargs,
            )
            text = resp.text.strip()
        except Exception as exc:  # noqa: BLE001
            text = ""
            print(f"[asr] error: {type(exc).__name__}: {str(exc)[:120]}")
        ms = (time.perf_counter() - t0) * 1000
        self.latencies.append(ms)
        return text, ms


# ---------------------------------------------------------------------------
# Tier 2: LLM signal extraction
# ---------------------------------------------------------------------------

TIER2_SYSTEM = (
    "You analyse a live sales call in real time. Given the recent transcript, "
    "identify at most ONE signal the agent should act on RIGHT NOW.\n"
    "Signal types: rising_frustration, buying_signal, missed_cross_sell, "
    "payment_difficulty, callback_needed, competitor_mention.\n"
    'Reply with JSON only: {"signal": "<type>", "confidence": 0.0-1.0, '
    '"nudge": "<max 12 words, imperative>"} '
    'or {"signal": "none"} if nothing is actionable.\n'
    "Be conservative. Most turns warrant no nudge. Do not invent urgency."
)


class Tier2Detector:
    """LLM pass for signals that need reading comprehension.

    Only runs when Tier 1 found nothing, and only on customer turns. Paying
    ~150ms to re-confirm a keyword match would be waste, and running it on
    every turn would both blow the rate limit and generate exactly the
    low-value alert spam the brief penalises.
    """

    def __init__(self) -> None:
        self.client = Groq(api_key=settings.groq_api_key)
        self.model = settings.groq_nudge_model
        self.latencies: list[float] = []
        self.calls = 0

    def detect(self, window: list[Utterance]) -> tuple[Signal | None, str, float]:
        if not window:
            return None, "", 0.0

        transcript = "\n".join(
            f"{u.speaker}: {u.text}" for u in window[-6:]
        )
        t0 = time.perf_counter()
        try:
            self.calls += 1
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": TIER2_SYSTEM},
                    {"role": "user", "content": transcript},
                ],
                max_tokens=90,
                temperature=0.0,
                response_format={"type": "json_object"},
            )
            raw = resp.choices[0].message.content or "{}"
            data = json.loads(raw)
        except Exception as exc:  # noqa: BLE001
            ms = (time.perf_counter() - t0) * 1000
            self.latencies.append(ms)
            print(f"[tier2] error: {type(exc).__name__}: {str(exc)[:100]}")
            return None, "", ms

        ms = (time.perf_counter() - t0) * 1000
        self.latencies.append(ms)

        name = (data.get("signal") or "none").strip().lower()
        if name in ("none", "", None):
            return None, "", ms
        try:
            sig_type = SignalType(name)
        except ValueError:
            return None, "", ms

        signal = Signal(
            type=sig_type,
            confidence=float(data.get("confidence", 0.6)),
            evidence=window[-1].text[:80],
            speaker=window[-1].speaker,
            tier=2,
        )
        return signal, (data.get("nudge") or "").strip(), ms


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


@dataclass
class PipelineEvent:
    kind: str                     # transcript | nudge | suppressed | stats
    payload: dict
    at: float = field(default_factory=time.time)


class LiveCallPipeline:
    def __init__(
        self,
        emit: Callable[[PipelineEvent], None] | None = None,
        use_tier2: bool = True,
        language: str | None = None,
    ) -> None:
        self.asr = StreamingASR()
        self.tier1 = Tier1Detector()
        self.tier2 = Tier2Detector() if use_tier2 else None
        self.nudges = NudgeManager()
        self.emit = emit or (lambda e: None)
        self.language = language

        self.window: list[Utterance] = []
        self.timings: list[StageTiming] = []
        self.suppressed: list[dict] = []
        self.transcript: list[dict] = []

    async def process(self, seg: AudioSegment) -> None:
        timing = StageTiming(audio_received=time.perf_counter())

        # --- ASR ---
        text, timing.asr_ms = await asyncio.to_thread(
            self.asr.transcribe, seg, self.language
        )
        if not text:
            return

        utt = Utterance(
            speaker=seg.speaker,
            text=text,
            start_s=seg.start_s,
            end_s=seg.start_s + seg.duration_s,
        )
        self.window.append(utt)
        self.transcript.append({
            "speaker": utt.speaker,
            "text": utt.text,
            "start_s": round(utt.start_s, 2),
            "asr_ms": round(timing.asr_ms, 1),
        })
        self.emit(PipelineEvent("transcript", {
            "speaker": utt.speaker,
            "text": utt.text,
            "start_s": round(utt.start_s, 2),
            "asr_ms": round(timing.asr_ms, 1),
        }))

        # --- Tier 1 (deterministic) ---
        t0 = time.perf_counter()
        signals = self.tier1.detect(utt)
        timing.tier1_ms = (time.perf_counter() - t0) * 1000

        nudge_texts: dict[int, str] = {}

        # --- Tier 2 (LLM) only when Tier 1 found nothing ---
        if not signals and self.tier2 and utt.speaker == "customer":
            sig, text_out, timing.tier2_ms = await asyncio.to_thread(
                self.tier2.detect, self.window
            )
            if sig:
                signals = [sig]
                if text_out:
                    nudge_texts[id(sig)] = text_out

        # --- Nudge gating ---
        t0 = time.perf_counter()
        for sig in signals:
            custom = nudge_texts.get(id(sig)) or sig.detail or None
            nudge, reason = self.nudges.consider(sig, text=custom)
            if nudge:
                timing.nudge_ms = (time.perf_counter() - t0) * 1000
                d0 = time.perf_counter()
                payload = nudge.to_dict()
                payload["timings"] = timing.to_dict()
                payload["trigger_text"] = utt.text[:120]
                self.emit(PipelineEvent("nudge", payload))
                timing.delivery_ms = (time.perf_counter() - d0) * 1000
            else:
                self.suppressed.append({
                    "signal": sig.type.value,
                    "confidence": round(sig.confidence, 2),
                    "reason": reason,
                    "evidence": sig.evidence,
                    "at_s": round(utt.start_s, 2),
                })
                self.emit(PipelineEvent("suppressed", {
                    "signal": sig.type.value,
                    "reason": reason,
                }))

        if timing.nudge_ms == 0.0:
            timing.nudge_ms = (time.perf_counter() - t0) * 1000

        self.timings.append(timing)

    async def run(self, segments: list[AudioSegment], speed: float = 1.0) -> None:
        async for seg in replay_realtime(segments, speed=speed):
            await self.process(seg)
        self.emit(PipelineEvent("stats", self.report()))

    # ------------------------------------------------------------------

    def report(self) -> dict:
        def pct(values: list[float], p: float) -> float:
            if not values:
                return 0.0
            s = sorted(values)
            idx = max(0, min(len(s) - 1, int(len(s) * p) - 1))
            return round(s[idx], 1)

        e2e = [t.end_to_end_ms for t in self.timings]
        asr = [t.asr_ms for t in self.timings]
        t1 = [t.tier1_ms for t in self.timings]
        t2 = [t.tier2_ms for t in self.timings if t.tier2_ms > 0]

        return {
            "utterances": len(self.timings),
            "latency_ms": {
                "end_to_end": {"p50": pct(e2e, 0.5), "p95": pct(e2e, 0.95),
                               "max": round(max(e2e), 1) if e2e else 0},
                "asr": {"p50": pct(asr, 0.5), "p95": pct(asr, 0.95)},
                "signal_tier1": {"p50": pct(t1, 0.5), "p95": pct(t1, 0.95)},
                "signal_tier2_llm": {
                    "p50": pct(t2, 0.5), "p95": pct(t2, 0.95),
                    "calls": len(t2),
                },
            },
            "nudges": self.nudges.stats.to_dict(),
            "suppressed_detail": self.suppressed,
        }
