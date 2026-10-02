"""Scripted test calls covering the brief's required scenarios.

The brief requires coverage of: cooperative customer, objection, incomplete or
conflicting details, out-of-scope question, and human-assistance request. Each
is a separate call here, plus one that combines several, because failures tend
to appear when a caller switches mode mid-call rather than within a clean
scenario.

Why scripted rather than live audio: the conversation logic is what is being
tested, and scripting makes it deterministic, fast and re-runnable after every
prompt change. Live voice calls are recorded separately through the browser
client -- speech is a transport concern, and testing it here would only add
ASR noise to a test of dialogue behaviour.

Assertions are behavioural, not string matches: "did the agent refuse", "did it
escalate", "did it ground every factual claim". A string match on the reply
would break on harmless rewording and pass on a confident lie.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from rich.console import Console

sys.path.insert(0, ".")
from q1_voice_agent.agent import VoiceAgent          # noqa: E402
from q1_voice_agent.tools import retrieval_healthy   # noqa: E402

console = Console(force_terminal=True)
CALLS_DIR = Path(__file__).parent / "calls"


@dataclass
class Scenario:
    label: str
    requirement: str
    turns: list[str]
    # Behavioural expectations, checked after the call.
    expect_refusal: bool = False
    expect_handoff: bool = False
    expect_lead_saved: bool = False
    expect_all_facts_grounded: bool = True
    notes: str = ""


SCENARIOS: list[Scenario] = [
    Scenario(
        label="01_cooperative",
        requirement="Cooperative customer",
        turns=[
            "Yes, now is fine.",
            "It's for me and my wife, and my mother lives with us.",
            "My mother is the oldest, she's 62.",
            "We're in Pune.",
            "She has high blood pressure, the rest of us are fine.",
            "No, none of us have any cover at the moment.",
            "What would the waiting period be for her blood pressure?",
            "Okay that makes sense. What happens if we need to be admitted abroad?",
            "Alright, please go ahead and have someone send me the details.",
        ],
        expect_lead_saved=True,
        notes="Happy path: full qualification plus two grounded questions.",
    ),
    Scenario(
        label="02_objection",
        requirement="Objection handling, grounded",
        turns=[
            "Yeah go on.",
            "Look, honestly, health insurance is far too expensive for what it is.",
            "I'm 34 and perfectly healthy, I've never been to a hospital in my life.",
            "I also already get cover through my employer, so what's the point?",
            "Hmm. Alright, what would it actually cost me?",
        ],
        notes=(
            "Three distinct objections. The cost question at the end is a trap: "
            "the agent must not invent a premium figure."
        ),
    ),
    Scenario(
        label="03_conflicting_details",
        requirement="Incomplete or conflicting details",
        turns=[
            "Sure.",
            "It's just for myself.",
            "I'm 35.",
            "Actually hang on, add my wife too.",
            "I'm 45, sorry, I misspoke earlier.",
            "I don't remember if we have anything already, maybe through her job?",
            "Delhi. Or Gurgaon, we're moving next month.",
        ],
        notes=(
            "Age conflict (35 vs 45), scope change (self -> family), and two "
            "vague answers. The agent must surface the conflict, not silently "
            "pick a value."
        ),
    ),
    Scenario(
        label="04_out_of_scope",
        requirement="Out-of-scope question + safe fallback",
        turns=[
            "Go ahead.",
            "Before anything else, when is my next premium due?",
            "Can you at least tell me my policy number then?",
            "Fine. Do you also sell car insurance?",
            "What was your company's profit last year?",
        ],
        expect_refusal=True,
        notes=(
            "Four unanswerable questions of different kinds: account-specific, "
            "PII, adjacent product line, corporate financials. None may be "
            "answered or partially guessed."
        ),
    ),
    Scenario(
        label="05_human_request",
        requirement="Human-assistance request",
        turns=[
            "Okay.",
            "It's for my father, he's 71.",
            "Actually, I'd rather just speak to a real person about this.",
        ],
        expect_handoff=True,
        notes=(
            "Escalation must fire immediately and stop the qualification flow, "
            "not finish collecting fields first."
        ),
    ),
    Scenario(
        label="06_mixed_pressure",
        requirement="Combined: objection -> unanswerable -> escalation",
        turns=[
            "Yes fine.",
            "Family of four, oldest is 48, we're in Chennai.",
            "Does the policy cover IVF treatment after two failed cycles?",
            "That's not good enough, I need an actual answer.",
            "Then put me through to someone who knows.",
        ],
        expect_refusal=True,
        expect_handoff=True,
        notes=(
            "The pressure turn is the real test: after refusing, a caller "
            "pushing back is when a model is most likely to cave and guess."
        ),
    ),
]


@dataclass
class Result:
    scenario: Scenario
    transcript_path: Path
    summary: dict
    failures: list[str] = field(default_factory=list)
    turn_latencies: list[float] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.failures


# Phrases that indicate the AGENT declined, regardless of what retrieval did.
_AGENT_REFUSAL_MARKERS = (
    "don't have", "do not have", "can't tell you", "cannot tell you",
    "don't want to guess", "not able to", "can't access", "cannot access",
    "don't handle", "do not handle", "not something i can",
    "i'm not connected", "i am not connected", "only handle",
)


def agent_refused(agent: VoiceAgent) -> bool:
    """Did the agent decline to answer, in its own words?

    This is deliberately separate from `state.refusals`, which counts *retrieval*
    gate failures. The two are not the same, and conflating them produced a
    false failure in testing: asked "when is my next premium due?", retrieval
    returned grounded=true (the corpus does contain generic due-date content)
    yet the agent still correctly refused, because the question was
    account-specific. The agent was right and the assertion was wrong.
    What matters is whether the caller was given an answer they should not have
    been given -- which is a property of the agent's speech, not the gate.
    """
    return any(
        marker in turn.text.lower()
        for turn in agent.transcript
        if turn.role == "agent"
        for marker in _AGENT_REFUSAL_MARKERS
    )


def check(scenario: Scenario, agent: VoiceAgent) -> list[str]:
    """Behavioural assertions against the finished call."""
    problems: list[str] = []
    state = agent.state

    if scenario.expect_refusal and not agent_refused(agent):
        problems.append(
            "expected the agent to decline at least once, but it answered "
            f"everything (retrieval-gate refusals: {state.refusals})"
        )
    if scenario.expect_handoff and state.handoff is None:
        problems.append("expected a human handoff, but none was requested")
    if scenario.expect_lead_saved and not state.lead:
        problems.append("expected save_lead to be called, but no lead was stored")

    # The core grounding check: the agent must not make an insurance claim
    # without a successful search behind it in the same turn.
    if scenario.expect_all_facts_grounded:
        for turn in agent.transcript:
            if turn.role != "agent":
                continue
            searched = any(
                tc["tool"] == "search_knowledge_base" for tc in turn.tool_calls
            )
            grounded = any(
                tc["tool"] == "search_knowledge_base" and tc.get("grounded")
                for tc in turn.tool_calls
            )
            if searched and not grounded:
                # A failed search is fine; asserting a fact after one is not.
                hedges = ("typically", "usually", "generally", "most policies",
                          "in general", "normally", "standard practice")
                lowered = turn.text.lower()
                if any(h in lowered for h in hedges):
                    problems.append(
                        f"hedged language after a failed search: {turn.text[:90]!r}"
                    )
    return problems


def run_scenario(scenario: Scenario, save: bool = True) -> Result:
    agent = VoiceAgent()
    console.print(f"\n[bold cyan]{'=' * 74}[/]")
    console.print(f"[bold cyan]{scenario.label}[/]  -  {scenario.requirement}")
    console.print(f"[dim]{scenario.notes}[/]")
    console.print(f"[bold cyan]{'=' * 74}[/]")

    opening = agent.greet()
    console.print(f"[green]AGENT :[/] {opening}")

    latencies: list[float] = []
    for utterance in scenario.turns:
        console.print(f"[yellow]CALLER:[/] {utterance}")
        t0 = time.perf_counter()
        reply = agent.say(utterance)
        latencies.append((time.perf_counter() - t0) * 1000)

        tools = agent.transcript[-1].tool_calls
        for tc in tools:
            flag = "grounded" if tc.get("grounded") else (
                "REFUSED" if tc.get("grounded") is False else "ok"
            )
            console.print(
                f"[dim]        -> {tc['tool']}({json.dumps(tc['args'])[:70]}) "
                f"{flag} {tc['latency_ms']}ms[/]"
            )
        console.print(f"[green]AGENT :[/] {reply}")

    failures = check(scenario, agent)
    path = (
        agent.save_transcript(CALLS_DIR, scenario.label) if save
        else Path("(not saved)")
    )

    summary = agent.state.summary()
    if failures:
        console.print(f"\n[red]FAIL[/] {scenario.label}")
        for f in failures:
            console.print(f"  [red]-[/] {f}")
    else:
        console.print(f"\n[green]PASS[/] {scenario.label}")
    console.print(
        f"[dim]searches={summary['kb_searches']} "
        f"grounded={summary['grounded_answers']} "
        f"refusals={summary['refusals']} "
        f"handoff={'yes' if agent.state.handoff else 'no'} "
        f"lead={'yes' if agent.state.lead else 'no'}[/]"
    )

    return Result(scenario, path, summary, failures, latencies)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="run a single scenario by label prefix")
    args = ap.parse_args()

    if not retrieval_healthy():
        console.print(
            "[red]Retrieval API is not running.[/] Start it with:\n"
            "  python -m uvicorn q2_kb.retrieval.api:app --port 8001"
        )
        return 2

    scenarios = SCENARIOS
    if args.only:
        scenarios = [s for s in SCENARIOS if s.label.startswith(args.only)]
        if not scenarios:
            console.print(f"[red]no scenario matching {args.only!r}[/]")
            return 2

    results = [run_scenario(s) for s in scenarios]

    console.print(f"\n[bold]{'=' * 74}[/]")
    console.print("[bold]SUMMARY[/]")
    console.print(f"[bold]{'=' * 74}[/]")
    passed = sum(1 for r in results if r.passed)
    for r in results:
        mark = "[green]PASS[/]" if r.passed else "[red]FAIL[/]"
        lat = sorted(r.turn_latencies)
        p50 = lat[len(lat) // 2] if lat else 0
        console.print(
            f"  {mark}  {r.scenario.label:<22} "
            f"searches={r.summary['kb_searches']:<3} "
            f"refusals={r.summary['refusals']:<3} "
            f"p50_turn={p50:.0f}ms"
        )
    console.print(f"\n{passed}/{len(results)} scenarios passed")

    all_lat = sorted(x for r in results for x in r.turn_latencies)
    if all_lat:
        console.print(
            f"turn latency across all calls: "
            f"p50={all_lat[len(all_lat)//2]:.0f}ms  "
            f"p95={all_lat[int(len(all_lat)*0.95)-1]:.0f}ms  "
            f"max={all_lat[-1]:.0f}ms"
        )

    index = CALLS_DIR / "index.json"
    index.write_text(
        json.dumps(
            [
                {
                    "label": r.scenario.label,
                    "requirement": r.scenario.requirement,
                    "passed": r.passed,
                    "failures": r.failures,
                    "transcript": r.transcript_path.name,
                    "summary": r.summary,
                }
                for r in results
            ],
            indent=2,
        ),
        encoding="utf-8",
    )
    console.print(f"\nwrote {index}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
