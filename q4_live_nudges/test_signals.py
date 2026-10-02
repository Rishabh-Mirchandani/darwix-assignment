"""Unit tests for signal detection and nudge suppression.

Two things are being proven here, and the second matters more:

1. The detectors fire on the cases the brief requires (missed cross-sell,
   skipped disclosure, rising frustration).
2. They do NOT fire on the near-misses -- negated phrases, hypotheticals and
   repeated mentions. The brief's test coverage includes "a noisy or ambiguous
   call where unnecessary nudges should be avoided", and the only way to show
   that is to assert on silence.

Runs with no network and no API key, so it executes in milliseconds and can be
run on every change.
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")
from q4_live_nudges.pipeline.nudges import NudgeManager          # noqa: E402
from q4_live_nudges.pipeline.signals import (                     # noqa: E402
    SignalType, Tier1Detector, Utterance,
)

PASS, FAIL = [], []


def check(name: str, condition: bool, detail: str = "") -> None:
    (PASS if condition else FAIL).append(name)
    mark = "[+]" if condition else "[-]"
    print(f"{mark} {name}" + (f"  -- {detail}" if detail and not condition else ""))


def types_of(signals) -> set[SignalType]:
    return {s.type for s in signals}


# ---------------------------------------------------------------------------
print("\n--- Tier 1: TRUE POSITIVES (must fire) ---")
# ---------------------------------------------------------------------------

cases_fire = [
    ("second vehicle", "customer",
     "Actually we just bought a second car last month.",
     SignalType.MISSED_CROSS_SELL),
    ("spouse also needs cover", "customer",
     "My wife also needs cover, she's not on anything.",
     SignalType.MISSED_CROSS_SELL),
    ("explicit frustration", "customer",
     "This is the third time I've called about this, it's ridiculous.",
     SignalType.RISING_FRUSTRATION),
    ("escalation demand", "customer",
     "I want to speak to your manager.",
     SignalType.RISING_FRUSTRATION),
    ("cannot afford", "customer",
     "Honestly I can't afford that right now.",
     SignalType.PAYMENT_DIFFICULTY),
    ("job loss", "customer",
     "I lost my job in March so money is tight.",
     SignalType.PAYMENT_DIFFICULTY),
    ("buying signal", "customer",
     "That sounds good, how do I sign up?",
     SignalType.BUYING_SIGNAL),
    ("callback", "customer",
     "I'm driving right now, can you call me back later?",
     SignalType.CALLBACK_NEEDED),
    ("competitor", "customer",
     "Star Health quoted me something cheaper.",
     SignalType.COMPETITOR_MENTION),
    ("agent guarantees claim", "agent",
     "Don't worry, your claim will definitely be approved.",
     SignalType.RISKY_STATEMENT),
    ("agent overclaims cover", "agent",
     "There are no exclusions at all on this plan.",
     SignalType.RISKY_STATEMENT),
    ("collections threat", "agent",
     "If you don't pay we will tarik unit next week.",
     SignalType.RISKY_STATEMENT),
]

for name, speaker, text, expected in cases_fire:
    det = Tier1Detector()
    sig = det.detect(Utterance(speaker, text, 10.0, 12.0))
    check(name, expected in types_of(sig), f"got {types_of(sig)}")


# ---------------------------------------------------------------------------
print("\n--- Tier 1: TRUE NEGATIVES (must stay silent) ---")
# ---------------------------------------------------------------------------

cases_silent = [
    ("negated second car", "customer",
     "No, we don't have a second car.",
     SignalType.MISSED_CROSS_SELL),
    ("negated frustration", "customer",
     "I'm not frustrated, I just want to understand it.",
     SignalType.RISING_FRUSTRATION),
    ("affordability as a question", "customer",
     "I can afford that, that's fine.",
     SignalType.PAYMENT_DIFFICULTY),
    ("no spouse", "customer",
     "My wife doesn't need cover, she's covered at work.",
     SignalType.MISSED_CROSS_SELL),
]

for name, speaker, text, must_not in cases_silent:
    det = Tier1Detector()
    sig = det.detect(Utterance(speaker, text, 10.0, 12.0))
    check(name, must_not not in types_of(sig), f"wrongly fired {must_not}")


# ---------------------------------------------------------------------------
print("\n--- Compliance checklist ---")
# ---------------------------------------------------------------------------

det = Tier1Detector()
det.detect(Utterance("agent", "Hi, this is Meera from Niva Bupa.", 0.0, 3.0))
det.detect(Utterance("customer", "I have diabetes, is that covered?", 4.0, 7.0))
after = det.detect(Utterance("agent", "Yes, diabetes is a common condition.", 8.0, 11.0))
check(
    "missing waiting-period disclosure fires",
    SignalType.COMPLIANCE_GAP in types_of(after),
    f"got {types_of(after)}",
)

det2 = Tier1Detector()
det2.detect(Utterance("agent", "Hi, this is Meera from Niva Bupa.", 0.0, 3.0))
det2.detect(Utterance("customer", "I have diabetes, is that covered?", 4.0, 7.0))
ok = det2.detect(Utterance(
    "agent", "Yes, though there's a waiting period before it's covered.", 8.0, 12.0
))
check(
    "disclosure made -> no gap",
    SignalType.COMPLIANCE_GAP not in types_of(ok),
    f"got {types_of(ok)}",
)

det3 = Tier1Detector()
det3.detect(Utterance("agent", "Hi there.", 0.0, 2.0))
late = det3.detect(Utterance("agent", "So about your cover...", 50.0, 53.0))
check(
    "missing identity disclosure fires after deadline",
    SignalType.COMPLIANCE_GAP in types_of(late),
    f"got {types_of(late)}",
)


# ---------------------------------------------------------------------------
print("\n--- Nudge suppression ---")
# ---------------------------------------------------------------------------

clock = {"t": 1000.0}
mgr = NudgeManager(clock=lambda: clock["t"])
det = Tier1Detector()

s1 = det.detect(Utterance("customer", "We just bought a second car.", 10.0, 13.0))
n1, r1 = mgr.consider(s1[0])
check("first cross-sell nudge emitted", n1 is not None, r1)

# Same topic, different words, immediately after.
det_b = Tier1Detector()
s2 = det_b.detect(Utterance("customer", "My other vehicle also needs cover.", 15.0, 18.0))
n2, r2 = mgr.consider(s2[0])
check("duplicate topic suppressed", n2 is None and "duplicate" in r2, r2)

# After expiry, still inside cooldown.
clock["t"] += 40.0
det_c = Tier1Detector()
s3 = det_c.detect(Utterance("customer", "The second bike too.", 55.0, 57.0))
n3, r3 = mgr.consider(s3[0])
check("cooldown blocks re-fire", n3 is None and "cooldown" in r3, r3)

# Past cooldown -> allowed again.
clock["t"] += 60.0
det_d = Tier1Detector()
s4 = det_d.detect(Utterance("customer", "And my husband also needs cover.", 120.0, 123.0))
n4, r4 = mgr.consider(s4[0])
check("re-fires after cooldown", n4 is not None, r4)

# Rate limit.
mgr2 = NudgeManager(clock=lambda: clock["t"])
emitted = 0
for i, text in enumerate([
    "We just bought a second car.",
    "I can't afford that.",
    "Star Health quoted me cheaper.",
    "That sounds good, how do I sign up?",
    "Call me back later please.",
]):
    d = Tier1Detector()
    sigs = d.detect(Utterance("customer", text, 10.0 + i, 12.0 + i))
    if sigs:
        n, _ = mgr2.consider(sigs[0])
        if n:
            emitted += 1
check("rate limit caps emissions at 4/min", emitted <= 4, f"emitted {emitted}")

# CRITICAL pre-empts the rate limit.
d = Tier1Detector()
crit = d.detect(Utterance("agent", "Your claim will definitely be approved.", 30.0, 33.0))
n5, r5 = mgr2.consider(crit[0])
check("CRITICAL bypasses rate limit", n5 is not None, r5)

print("\n--- suppression stats ---")
for k, v in mgr2.stats.to_dict().items():
    print(f"  {k:<32}{v}")

print("\n" + "=" * 60)
print(f"{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    for f in FAIL:
        print(f"  FAILED: {f}")
raise SystemExit(1 if FAIL else 0)
