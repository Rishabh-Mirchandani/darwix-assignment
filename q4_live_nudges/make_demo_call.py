"""Synthesise a two-speaker demo call that exercises every Q4 requirement.

The brief's required coverage is specific: a missed cross-sell, a skipped
disclosure or risky statement, rising frustration, and a noisy/ambiguous
stretch where nudges should NOT fire. A real recorded call may or may not
contain all four; a purpose-built one contains all four by construction and is
reproducible, which makes the false-positive analysis meaningful rather than
anecdotal.

Each turn is written to its own file with the speaker in the filename, matching
the layout the Q1 voice server produces. The pipeline therefore consumes this
exactly as it would a real recorded call, with no special-casing.

Agent and customer use different voices so ASR is doing genuine work.

The `expect` field on each turn is the ground truth for the false-positive
analysis in `analyse_nudges.py`. Writing it here, before running the pipeline,
is deliberate -- labelling after seeing the output is how you accidentally
grade yourself generously.
"""
from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import edge_tts

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from shared.config import settings  # noqa: E402

AGENT_VOICE = "en-IN-NeerjaNeural"
CUSTOMER_VOICE = "en-IN-PrabhatNeural"
OUT_DIR = settings.audio_dir / "q4_demo_call"


@dataclass
class Turn:
    speaker: str          # agent | customer
    text: str
    # Ground truth: which signal (if any) SHOULD be detected here.
    expect: str | None = None
    why: str = ""


SCRIPT: list[Turn] = [
    Turn("agent",
         "Good afternoon, am I speaking with Mister Sharma?",
         expect=None,
         why="Neutral open. Note: no company identification yet."),

    Turn("customer",
         "Yes, speaking.",
         expect=None, why="Neutral."),

    # --- compliance: identity disclosure is now overdue ---
    Turn("agent",
         "I'm calling about the health insurance enquiry you submitted "
         "last week. Is this a good time?",
         expect="compliance_gap",
         why="Agent still has not named themselves or the company. The "
             "identity disclosure deadline lapses here."),

    Turn("customer",
         "Sure, go ahead.",
         expect=None, why="Neutral."),

    # --- compliance: pre-existing condition without waiting period ---
    Turn("customer",
         "I should mention I have diabetes, type two, diagnosed three years ago.",
         expect=None,
         why="Arms the waiting-period disclosure requirement but is not itself "
             "a signal."),

    Turn("agent",
         "That's absolutely fine, diabetes is very common and we cover it.",
         expect="compliance_gap",
         why="REQUIRED: agent answered a pre-existing condition question "
             "without disclosing the waiting period. This is the brief's "
             "'skipped disclosure' case."),

    # --- missed cross-sell ---
    Turn("customer",
         "Okay good. My wife also needs cover, she's not insured at all right now.",
         expect="missed_cross_sell",
         why="REQUIRED: explicit second-life opportunity. The brief's "
             "'missed cross-sell' case."),

    Turn("agent",
         "Right, noted. So for your own policy, the premium works out to "
         "about eighteen thousand rupees a year.",
         expect="compliance_gap",
         why="Quotes a figure without saying it is indicative or subject to "
             "underwriting. Also ignores the cross-sell just offered."),

    # --- ambiguous / noisy: must NOT fire ---
    Turn("customer",
         "Hmm. Right. I see. Okay.",
         expect=None,
         why="NOISE CASE: filler with no content. Must produce no nudge."),

    Turn("customer",
         "Sorry, the line is a bit bad. Could you say that again?",
         expect=None,
         why="NOISE CASE: audio trouble, not frustration. A naive sentiment "
             "detector fires here; it must not."),

    Turn("customer",
         "No, I don't have a second car, it's just the one.",
         expect=None,
         why="NOISE CASE: negated cross-sell. The phrase 'second car' appears "
             "but the answer is no. Must not fire."),

    Turn("customer",
         "And I'm not frustrated, I just want to understand the numbers.",
         expect=None,
         why="NOISE CASE: explicit negation of frustration. Must not fire."),

    # --- rising frustration (genuine) ---
    Turn("customer",
         "Although honestly, this is the third time I've called about this "
         "and nobody has given me a straight answer.",
         expect="rising_frustration",
         why="REQUIRED: genuine escalating frustration with concrete evidence. "
             "The brief's 'rising frustration' case."),

    Turn("agent",
         "I understand. Let me just reassure you, your claim will definitely "
         "be approved, there's nothing to worry about.",
         expect="risky_statement",
         why="REQUIRED: agent guarantees a claim outcome. Compliance exposure, "
             "and the hardest case because the surrounding tone is reassuring."),

    # --- payment difficulty ---
    Turn("customer",
         "The thing is, I lost my job in March so money is quite tight at the moment.",
         expect="payment_difficulty",
         why="Genuine hardship disclosure; should route to a support option."),

    # --- buying signal ---
    Turn("customer",
         "But alright, that sounds reasonable. How do I sign up?",
         expect="buying_signal",
         why="Clear intent to proceed."),

    Turn("agent",
         "I'll email you the proposal form today.",
         expect=None, why="Neutral close."),
]


async def synth(text: str, voice: str, path: Path) -> None:
    await edge_tts.Communicate(text, voice).save(str(path))


async def main() -> None:
    if OUT_DIR.exists():
        for old in OUT_DIR.glob("*"):
            old.unlink()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    manifest: list[dict] = []
    print(f"synthesising {len(SCRIPT)} turns -> {OUT_DIR}\n")

    for i, turn in enumerate(SCRIPT):
        voice = AGENT_VOICE if turn.speaker == "agent" else CUSTOMER_VOICE
        suffix = "agent" if turn.speaker == "agent" else "caller"
        path = OUT_DIR / f"{i:03d}_{suffix}.mp3"
        await synth(turn.text, voice, path)
        size = path.stat().st_size
        manifest.append({
            "index": i,
            "file": path.name,
            "speaker": turn.speaker,
            "text": turn.text,
            "expect": turn.expect,
            "why": turn.why,
            "bytes": size,
        })
        flag = turn.expect or "-"
        print(f"  {i:02d} {turn.speaker:<9} {size/1024:>6.1f} KB  "
              f"[{flag}]  {turn.text[:52]}")

    man_path = OUT_DIR / "ground_truth.json"
    man_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    expected = [m for m in manifest if m["expect"]]
    noise = [m for m in manifest if not m["expect"]]
    print(f"\nturns: {len(manifest)}")
    print(f"  should produce a signal : {len(expected)}")
    print(f"  should stay silent      : {len(noise)}")
    print(f"\nwrote {man_path}")


if __name__ == "__main__":
    asyncio.run(main())
