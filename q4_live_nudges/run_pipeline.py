"""Run the live pipeline over a recorded call and score it against ground truth.

Default source is the synthesised demo call, which carries a pre-written
`ground_truth.json` labelling which turns should and should not produce a
signal. Scoring against labels written *before* the run is what makes the
false-positive number meaningful.

Plays at wall-clock speed by default. `--speed` exists for iteration only;
every number quoted in the write-up comes from a 1.0 run.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.config import settings                                  # noqa: E402
from q4_live_nudges.pipeline.stream import (                        # noqa: E402
    LiveCallPipeline, PipelineEvent, load_call_segments,
)

DEMO_DIR = settings.audio_dir / "q4_demo_call"
REPORTS = Path(__file__).parent / "reports"


def print_event(ev: PipelineEvent) -> None:
    if ev.kind == "transcript":
        p = ev.payload
        who = "AGENT   " if p["speaker"] == "agent" else "CUSTOMER"
        print(f"  [{p['start_s']:>6.1f}s] {who} {p['text'][:78]}")
        print(f"            (asr {p['asr_ms']:.0f}ms)")
    elif ev.kind == "nudge":
        p = ev.payload
        t = p["timings"]
        print(f"      >>> NUDGE [{p['priority']}] {p['text']}")
        print(f"          signal={p['signal_type']} conf={p['confidence']} "
              f"tier={p['tier']} e2e={t['end_to_end_ms']}ms")
    elif ev.kind == "suppressed":
        print(f"      ... suppressed {ev.payload['signal']}: {ev.payload['reason']}")


def score(pipeline: LiveCallPipeline, truth_path: Path) -> dict:
    """Compare emitted nudges against the pre-written labels."""
    truth = json.loads(truth_path.read_text(encoding="utf-8"))

    expected_turns = [t for t in truth if t["expect"]]
    silent_turns = [t for t in truth if not t["expect"]]

    fired_types = [n.signal_type.value for n in pipeline.nudges.history]
    expected_types = [t["expect"] for t in expected_turns]

    # A detection counts as a hit if that signal type fired at any point. This
    # is intentionally lenient on exact turn alignment: ASR chunk boundaries and
    # the one-turn disclosure grace period both shift a signal by a turn, and
    # penalising that would measure alignment rather than detection.
    hits, misses = [], []
    remaining = list(fired_types)
    for exp in expected_types:
        if exp in remaining:
            remaining.remove(exp)
            hits.append(exp)
        else:
            misses.append(exp)

    # Anything emitted that no labelled turn asked for.
    false_positives = remaining

    return {
        "expected_signals": len(expected_types),
        "detected": len(hits),
        "missed": misses,
        "false_positives": false_positives,
        "recall": round(len(hits) / len(expected_types), 3) if expected_types else None,
        "precision": (
            round(len(hits) / len(fired_types), 3) if fired_types else None
        ),
        "silent_turns_in_script": len(silent_turns),
        "total_nudges_emitted": len(fired_types),
    }


async def main_async(args: argparse.Namespace) -> int:
    # Accept either a path or a bare directory name under data/audio, so
    # `--call call_3e9ee8e2c0d0` works the same way the dashboard's call
    # selector does.
    if args.call:
        call_dir = Path(args.call)
        if not call_dir.exists():
            call_dir = settings.audio_dir / args.call
    else:
        call_dir = DEMO_DIR
    if not call_dir.exists():
        print(f"No call audio at {call_dir}.\n"
              f"Generate it with: python q4_live_nudges/make_demo_call.py")
        return 2

    segments = load_call_segments(call_dir)
    if not segments:
        print(f"No audio files in {call_dir}")
        return 2

    total_audio = sum(s.duration_s for s in segments)
    print(f"call      : {call_dir}")
    print(f"segments  : {len(segments)}")
    print(f"audio     : ~{total_audio:.0f}s  (replay speed {args.speed}x)")
    print(f"tier 2 LLM: {'on' if not args.no_tier2 else 'off'}")
    print("-" * 80)

    pipeline = LiveCallPipeline(
        emit=print_event,
        use_tier2=not args.no_tier2,
    )

    wall0 = time.perf_counter()
    await pipeline.run(segments, speed=args.speed)
    wall = time.perf_counter() - wall0

    report = pipeline.report()
    report["replay"] = {
        "audio_seconds": round(total_audio, 1),
        "wall_seconds": round(wall, 1),
        "speed": args.speed,
        "realtime_factor": round(wall / total_audio, 2) if total_audio else None,
    }

    truth_path = call_dir / "ground_truth.json"
    if truth_path.exists():
        report["accuracy"] = score(pipeline, truth_path)

    print("\n" + "=" * 80)
    print("LATENCY (ms)")
    print("=" * 80)
    lat = report["latency_ms"]
    print(f"  {'stage':<22}{'p50':>10}{'p95':>10}")
    print(f"  {'-'*42}")
    print(f"  {'ASR':<22}{lat['asr']['p50']:>10}{lat['asr']['p95']:>10}")
    print(f"  {'signal (tier1)':<22}{lat['signal_tier1']['p50']:>10}"
          f"{lat['signal_tier1']['p95']:>10}")
    print(f"  {'signal (tier2 LLM)':<22}{lat['signal_tier2_llm']['p50']:>10}"
          f"{lat['signal_tier2_llm']['p95']:>10}"
          f"   ({lat['signal_tier2_llm']['calls']} calls)")
    print(f"  {'END TO END':<22}{lat['end_to_end']['p50']:>10}"
          f"{lat['end_to_end']['p95']:>10}   max {lat['end_to_end']['max']}")

    print("\n" + "=" * 80)
    print("NUDGE CONTROL")
    print("=" * 80)
    for k, v in report["nudges"].items():
        print(f"  {k:<32}{v}")

    if "accuracy" in report:
        a = report["accuracy"]
        print("\n" + "=" * 80)
        print("ACCURACY vs GROUND TRUTH")
        print("=" * 80)
        print(f"  expected signals        {a['expected_signals']}")
        print(f"  detected                {a['detected']}")
        print(f"  recall                  {a['recall']}")
        print(f"  precision               {a['precision']}")
        print(f"  nudges emitted          {a['total_nudges_emitted']}")
        if a["missed"]:
            print(f"  MISSED                  {', '.join(a['missed'])}")
        if a["false_positives"]:
            print(f"  FALSE POSITIVES         {', '.join(a['false_positives'])}")
        if not a["missed"] and not a["false_positives"]:
            print("  -> every expected signal fired; nothing spurious")

    print(f"\n  replay realtime factor  {report['replay']['realtime_factor']} "
          f"(1.0 = exactly real time)")

    REPORTS.mkdir(parents=True, exist_ok=True)
    # Per-call filename. A single shared filename meant replaying a second call
    # silently overwrote the first run's report -- including the figures the
    # write-up quotes and scripts/verify_claims.py checks against.
    name = ("pipeline_report.json" if call_dir.name == DEMO_DIR.name
            else f"pipeline_report_{call_dir.name}.json")
    out = REPORTS / name
    out.write_text(json.dumps({
        "report": report,
        "transcript": pipeline.transcript,
        "nudges": [n.to_dict() for n in pipeline.nudges.history],
        "suppressed": pipeline.suppressed,
    }, indent=2), encoding="utf-8")
    print(f"\nwrote {out}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--call", help="call audio directory")
    ap.add_argument("--speed", type=float, default=1.0,
                    help="replay speed; 1.0 = real time (use 1.0 for reported numbers)")
    ap.add_argument("--no-tier2", action="store_true",
                    help="disable the LLM tier (ablation)")
    return asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
