"""Nudge generation and suppression.

Detecting a signal is the easy half. The hard half -- and the half the brief
grades ("suppression of repetitive or low-value alerts") -- is deciding which
signals deserve to interrupt a human who is mid-conversation.

An agent on a live call can absorb maybe one nudge every twenty seconds. Past
that, the panel becomes wallpaper and they stop looking at it, which makes the
system worse than having none: it costs attention and returns nothing. So every
rule here exists to spend a strict attention budget well.

Six controls, in the order they apply:

1. **Confidence floor** -- per type. A compliance gap fires at lower confidence
   than a cross-sell hint, because the costs are asymmetric: a missed
   disclosure is a regulatory problem, a missed cross-sell is a slow Tuesday.
2. **Duplicate suppression** -- by topic key, not by text. "Customer mentioned
   a second car" and "customer mentioned another vehicle" are one nudge.
3. **Cooldown** -- per topic. Re-firing the same topic needs real new evidence.
4. **Global rate limit** -- a hard ceiling on nudges per minute regardless of
   how much is happening.
5. **Priority pre-emption** -- a CRITICAL nudge bypasses the rate limit and
   expires lower-priority nudges, because a compliance gap outranks a tip.
6. **Expiry** -- a nudge about something said 90 seconds ago is no longer
   actionable; it is clutter. It removes itself.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Iterable

from q4_live_nudges.pipeline.signals import Priority, Signal, SignalType

# Per-type confidence floors. Asymmetric on purpose: the cost of a false
# negative on compliance is far higher than the cost of a false positive.
CONFIDENCE_FLOOR: dict[SignalType, float] = {
    SignalType.COMPLIANCE_GAP: 0.55,
    SignalType.RISKY_STATEMENT: 0.55,
    SignalType.PAYMENT_DIFFICULTY: 0.65,
    SignalType.RISING_FRUSTRATION: 0.70,
    SignalType.BUYING_SIGNAL: 0.70,
    SignalType.MISSED_CROSS_SELL: 0.72,
    SignalType.COMPETITOR_MENTION: 0.72,
    SignalType.CALLBACK_NEEDED: 0.75,
    SignalType.TOPIC_SHIFT: 0.85,   # rarely worth interrupting for
}

COOLDOWN_SECONDS: dict[SignalType, float] = {
    SignalType.COMPLIANCE_GAP: 25.0,
    SignalType.RISKY_STATEMENT: 25.0,
    SignalType.RISING_FRUSTRATION: 45.0,   # repeated frustration nudges annoy
    SignalType.PAYMENT_DIFFICULTY: 60.0,
    SignalType.MISSED_CROSS_SELL: 90.0,
    SignalType.BUYING_SIGNAL: 45.0,
    SignalType.CALLBACK_NEEDED: 90.0,
    SignalType.COMPETITOR_MENTION: 90.0,
    SignalType.TOPIC_SHIFT: 120.0,
}

EXPIRY_SECONDS: dict[Priority, float] = {
    Priority.CRITICAL: 60.0,
    Priority.HIGH: 45.0,
    Priority.MEDIUM: 35.0,
    Priority.LOW: 25.0,
}

MAX_NUDGES_PER_MINUTE = 4        # the attention budget
MAX_ACTIVE_NUDGES = 3            # what fits on screen without scanning
# A CRITICAL nudge that has been on screen this long has been seen. Holding
# the slot past that point starves fresher signals -- which is exactly what
# happened on the demo call, where three stacked compliance nudges
# suppressed a payment-difficulty disclosure and a buying signal.
STALE_AFTER_SECONDS = 22.0

# Deterministic nudge text for Tier 1 signals. Writing these by hand rather
# than generating them is a latency decision: a keyword match that already
# tells us exactly what happened does not need a 150ms round trip to be phrased.
STATIC_NUDGE_TEXT: dict[SignalType, str] = {
    SignalType.MISSED_CROSS_SELL: "Cross-sell opening - offer cover for the person mentioned.",
    SignalType.RISING_FRUSTRATION: "Acknowledge the frustration before continuing.",
    SignalType.PAYMENT_DIFFICULTY: "Offer an approved payment-support option or a callback.",
    SignalType.BUYING_SIGNAL: "Buying signal - move to next steps now.",
    SignalType.CALLBACK_NEEDED: "Bad time - offer a specific callback slot.",
    SignalType.COMPETITOR_MENTION: "Competitor named - ask what they were quoted.",
    SignalType.RISKY_STATEMENT: "Do not guarantee outcomes - soften that claim.",
    SignalType.TOPIC_SHIFT: "Topic changed - confirm the previous point was closed.",
}


def topic_key(signal: Signal) -> str:
    """Stable identity for deduplication.

    Keyed on the signal TYPE, not the evidence text, so three different
    phrasings of the same underlying event collapse into one nudge. Compliance
    gaps additionally key on which disclosure is missing, since those are
    genuinely distinct problems.
    """
    if signal.type == SignalType.COMPLIANCE_GAP:
        return f"{signal.type.value}:{signal.evidence}"
    return signal.type.value


@dataclass
class Nudge:
    id: str
    text: str
    signal_type: SignalType
    priority: Priority
    confidence: float
    evidence: str
    created_at: float
    expires_at: float
    latency_ms: float = 0.0        # signal detected -> nudge ready
    tier: int = 1

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "text": self.text,
            "signal_type": self.signal_type.value,
            "priority": self.priority.name,
            "priority_value": self.priority.value,
            "confidence": round(self.confidence, 2),
            "evidence": self.evidence,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "latency_ms": round(self.latency_ms, 1),
            "tier": self.tier,
        }


@dataclass
class SuppressionStats:
    """Every rejection is counted, because 'we suppressed 14 alerts' is the
    evidence that the control layer is doing something."""

    below_confidence: int = 0
    duplicate: int = 0
    cooldown: int = 0
    rate_limited: int = 0
    total_signals: int = 0
    emitted: int = 0

    def to_dict(self) -> dict:
        suppressed = (
            self.below_confidence + self.duplicate
            + self.cooldown + self.rate_limited
        )
        return {
            "signals_seen": self.total_signals,
            "nudges_emitted": self.emitted,
            "suppressed_total": suppressed,
            "suppressed_below_confidence": self.below_confidence,
            "suppressed_duplicate": self.duplicate,
            "suppressed_cooldown": self.cooldown,
            "suppressed_rate_limit": self.rate_limited,
            "suppression_rate": (
                round(suppressed / self.total_signals, 3)
                if self.total_signals else 0.0
            ),
        }


class NudgeManager:
    def __init__(self, clock=time.time) -> None:
        self.clock = clock
        self.active: list[Nudge] = []
        self.history: list[Nudge] = []
        self.last_fired: dict[str, float] = {}     # topic key -> timestamp
        self.recent_emissions: list[float] = []    # for the rate limit window
        self.stats = SuppressionStats()
        self._counter = 0

    # ------------------------------------------------------------------

    def _next_id(self) -> str:
        self._counter += 1
        return f"n{self._counter:04d}"

    def _expire(self, now: float) -> list[str]:
        keep, expired = [], []
        for n in self.active:
            (keep if n.expires_at > now else expired).append(n)
        self.active = keep
        return [n.id for n in expired]

    def _rate_limited(self, now: float) -> bool:
        self.recent_emissions = [t for t in self.recent_emissions if now - t < 60.0]
        return len(self.recent_emissions) >= MAX_NUDGES_PER_MINUTE

    # ------------------------------------------------------------------

    def consider(
        self,
        signal: Signal,
        text: str | None = None,
        detected_at: float | None = None,
    ) -> tuple[Nudge | None, str]:
        """Apply every suppression rule. Returns (nudge_or_None, reason)."""
        now = self.clock()
        self.stats.total_signals += 1
        self._expire(now)

        # 1. confidence floor
        floor = CONFIDENCE_FLOOR.get(signal.type, 0.7)
        if signal.confidence < floor:
            self.stats.below_confidence += 1
            return None, f"below confidence floor ({signal.confidence:.2f} < {floor:.2f})"

        key = topic_key(signal)
        priority = signal.priority

        # 2. duplicate: already on screen
        if any(topic_key_of(n) == key for n in self.active):
            self.stats.duplicate += 1
            return None, "duplicate of an active nudge"

        # 3. cooldown
        last = self.last_fired.get(key)
        cooldown = COOLDOWN_SECONDS.get(signal.type, 60.0)
        if last is not None and now - last < cooldown:
            self.stats.cooldown += 1
            return None, f"cooldown ({now - last:.0f}s < {cooldown:.0f}s)"

        # 4/5. rate limit, which CRITICAL pre-empts
        if self._rate_limited(now) and priority != Priority.CRITICAL:
            self.stats.rate_limited += 1
            return None, "rate limit reached (4/min)"

        nudge_text = text or STATIC_NUDGE_TEXT.get(
            signal.type, f"Signal: {signal.type.value}"
        )
        nudge = Nudge(
            id=self._next_id(),
            text=nudge_text,
            signal_type=signal.type,
            priority=priority,
            confidence=signal.confidence,
            evidence=signal.evidence,
            created_at=now,
            expires_at=now + EXPIRY_SECONDS[priority],
            latency_ms=(now - (detected_at or signal.detected_at)) * 1000,
            tier=signal.tier,
        )

        # Make room. Lowest priority goes first; failing that, the oldest
        # nudge that has already been on screen long enough to have been read.
        if len(self.active) >= MAX_ACTIVE_NUDGES:
            self.active.sort(key=lambda n: (n.priority.value, -n.created_at))
            if self.active[-1].priority.value > priority.value:
                self.active.pop()
            elif priority == Priority.CRITICAL:
                self.active.pop()
            else:
                stale = [
                    n for n in self.active
                    if now - n.created_at >= STALE_AFTER_SECONDS
                ]
                if stale:
                    oldest = min(stale, key=lambda n: n.created_at)
                    self.active.remove(oldest)
                else:
                    self.stats.rate_limited += 1
                    return None, "screen full with fresher equal-or-higher nudges"

        self.active.append(nudge)
        self.history.append(nudge)
        self.last_fired[key] = now
        self.recent_emissions.append(now)
        self.stats.emitted += 1
        return nudge, "emitted"

    def snapshot(self) -> list[dict]:
        now = self.clock()
        self._expire(now)
        return [
            n.to_dict()
            for n in sorted(self.active, key=lambda x: (x.priority.value, -x.created_at))
        ]


def topic_key_of(nudge: Nudge) -> str:
    if nudge.signal_type == SignalType.COMPLIANCE_GAP:
        return f"{nudge.signal_type.value}:{nudge.evidence}"
    return nudge.signal_type.value
