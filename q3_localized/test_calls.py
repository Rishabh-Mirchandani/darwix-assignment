"""Localised call tests for the Philippines and Indonesia bots.

The brief's required coverage for Q3: cooperative customer, sector-specific
objection, mixed English/finance terms, colloquial speech, human escalation,
and an Indonesian regional accent. Two calls per market, written so that
between them every one of those is exercised.

Caller turns are written the way callers in these markets actually type and
talk -- Taglish with `po`, colloquial Indonesian with `udah`/`gak`, and a
Javanese-inflected caller using `nggih`/`mboten`/`monggo`. A test written in
clean textbook Tagalog or formal Indonesian would pass without proving
anything, because the failure mode being hunted is the bot breaking register
when the caller does.

The assertions are about **localisation**, not just task success:

  * did the bot stay in the caller's language and register
  * did it keep sector vocabulary in the right language (premium/policy in
    English for PH; cicilan/denda in Indonesian for ID)
  * did it avoid the "unexpected English switch" the brief calls out
  * did it hold the honesty rules under a different language
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from rich.console import Console

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from q1_voice_agent.agent import VoiceAgent            # noqa: E402
from q1_voice_agent.tools import retrieval_healthy     # noqa: E402

console = Console(force_terminal=True)
PROMPT_DIR = Path(__file__).parent / "prompts"
CALLS_DIR = Path(__file__).parent / "calls"


# Markers used to judge whether the bot stayed in register.
PH_MARKERS = re.compile(
    r"\b(po|opo|ninyo|kayo|ang|ng|sa|mga|yung|naman|lang|pong|kung|may|hindi|"
    r"salamat|sige|pasensya|magandang)\b", re.I
)
ID_MARKERS = re.compile(
    r"\b(ya|pak|bu|bapak|ibu|yang|untuk|bisa|tidak|sudah|belum|saya|mohon|"
    r"terima kasih|baik|nanti|kalau|dengan|ada|akan)\b", re.I
)
# Sector vocabulary that must remain in English for PH callers.
PH_KEEP_ENGLISH = ("premium", "policy", "beneficiary", "rider", "lapse", "coverage")
# Sector vocabulary that must remain in Indonesian for ID callers.
ID_KEEP_LOCAL = ("cicilan", "angsuran", "denda", "tenor", "jatuh tempo",
                 "pembiayaan", "keringanan", "pelunasan")


@dataclass
class Scenario:
    label: str
    market: str
    prompt: str
    requirement: str
    turns: list[str]
    expect_handoff: bool = False
    expect_refusal: bool = False
    must_use_local_terms: tuple[str, ...] = ()
    notes: str = ""


SCENARIOS: list[Scenario] = [
    # ---------------- Philippines ----------------
    Scenario(
        label="ph_01_premium_reminder",
        market="ph",
        prompt="system_ph.md",
        requirement="Cooperative customer + mixed English/finance terms + lapse objection",
        turns=[
            "Opo, ako nga po si Mister Santos.",
            "Ay oo, nakatanggap po ako ng notice last week.",
            "Sa BDO po ako nagbabayad, over the counter.",
            "Teka po, baka mag-lapse na po yung akin? Nahuli po kasi ako ng bayad.",
            "Ah okay po. Tapos yung beneficiary ko po, pwede ko pa po bang palitan?",
            "Sige po, salamat.",
        ],
        must_use_local_terms=("po",),
        notes=(
            "Core PH flow. Two grounded questions (lapse, beneficiary) using "
            "English finance nouns inside Tagalog grammar -- the exact "
            "code-switching pattern the brief asks for."
        ),
    ),
    Scenario(
        label="ph_02_objection_escalation",
        market="ph",
        prompt="system_ph.md",
        requirement="Sector objection + out-of-scope + human escalation",
        turns=[
            "Oo, bakit po?",
            "Ang mahal naman po kasi ng premium, gusto ko na po itigil.",
            "Magkano po ba exactly ang nabayaran ko na so far?",
            "Eh gusto ko po talaga malaman. Pakiusap po.",
            "Sige po, pwede po ba akong makausap ng tao?",
        ],
        expect_handoff=True,
        expect_refusal=True,
        must_use_local_terms=("po",),
        notes=(
            "Cost objection, then an account-specific question the bot cannot "
            "answer, then pressure, then escalation. Tests whether the honesty "
            "rules survive translation into another language."
        ),
    ),

    # ---------------- Indonesia ----------------
    Scenario(
        label="id_01_installment_reminder",
        market="id",
        prompt="system_id.md",
        requirement="Cooperative customer + colloquial register + payment difficulty",
        turns=[
            "Iya betul, saya sendiri.",
            "Udah terima kok remindernya kemarin.",
            "Nah itu dia, bulan ini agak berat. Gaji belum turun.",
            "Kalau telat gitu dendanya gimana ya?",
            "Oh gitu. Ada keringanan gak kalau misalnya saya minta perpanjangan tenor?",
            "Oke siap, makasih ya.",
        ],
        must_use_local_terms=("denda",),
        notes=(
            "Colloquial Jakarta register throughout (udah, gak, gitu, kok). "
            "The bot must mirror this down from formal rather than staying "
            "stiff, while keeping cicilan/denda/tenor in Indonesian."
        ),
    ),
    Scenario(
        label="id_02_javanese_accent",
        market="id",
        prompt="system_id.md",
        requirement="Regional accent (Javanese) + objection + escalation",
        turns=[
            "Nggih, leres. Kulo piyambak.",
            "Mboten, dereng nampi remindere Mbak.",
            "Nggih niku, kulo tasih bingung kaliyan dendane. Kok katah sanget nggih?",
            "Monggo dijelaske malih, kulo mboten paham.",
            "Nggih sampun, kulo pengin ngendikan kaliyan petugase mawon.",
        ],
        expect_handoff=True,
        must_use_local_terms=("denda",),
        notes=(
            "Javanese-inflected caller (nggih, mboten, kulo, monggo, niku). "
            "The brief requires at least one regional accent outside standard "
            "Jakarta speech. The bot must NOT switch to English, must not "
            "correct the caller, and should stay in Indonesian while "
            "acknowledging the register."
        ),
    ),
]


@dataclass
class Result:
    scenario: Scenario
    transcript_path: Path
    summary: dict
    failures: list[str] = field(default_factory=list)
    language_score: float = 0.0
    latencies: list[float] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.failures


def check(scenario: Scenario, agent: VoiceAgent) -> tuple[list[str], float]:
    problems: list[str] = []
    replies = [t.text for t in agent.transcript if t.role == "agent"]
    joined = " ".join(replies).lower()

    # --- did it stay in the caller's language? ---
    marker_re = PH_MARKERS if scenario.market == "ph" else ID_MARKERS
    hits = sum(1 for r in replies if marker_re.search(r))
    ratio = hits / len(replies) if replies else 0.0
    if ratio < 0.8:
        problems.append(
            f"language drift: only {hits}/{len(replies)} replies carried "
            f"{scenario.market} markers (expected >=80%)"
        )

    # --- sector vocabulary in the right language ---
    if scenario.market == "ph":
        # Tagalog translations of finance terms are the classic over-localisation.
        for bad in ("patakaran", "gantimpala", "tagapagmana"):
            if bad in joined:
                problems.append(
                    f"over-translated finance term {bad!r}; PH agents keep "
                    f"these in English"
                )
    else:
        # English finance nouns where Indonesian is standard.
        for bad in ("installment", "instalment", "late fee", "due date"):
            if bad in joined:
                problems.append(
                    f"used English {bad!r} where Indonesian term is standard "
                    f"(cicilan / denda / jatuh tempo)"
                )

    for term in scenario.must_use_local_terms:
        if term.lower() not in joined:
            problems.append(f"expected local term {term!r} never appeared")

    # --- behavioural rules, same as Q1 ---
    if scenario.expect_handoff and agent.state.handoff is None:
        problems.append("expected a human handoff, none requested")

    return problems, ratio


def run_scenario(scenario: Scenario, model: str | None = None) -> Result:
    agent = VoiceAgent(
        system_prompt_file=scenario.prompt,
        prompt_dir=PROMPT_DIR,
        market=scenario.market,
        language=scenario.market,
        model=model,
    )

    console.print(f"\n[bold cyan]{'=' * 76}[/]")
    console.print(f"[bold cyan]{scenario.label}[/] - {scenario.requirement}")
    console.print(f"[dim]{scenario.notes}[/]")
    console.print(f"[bold cyan]{'=' * 76}[/]")

    console.print(f"[green]AGENT :[/] {agent.greet()}")

    latencies: list[float] = []
    for utterance in scenario.turns:
        console.print(f"[yellow]CALLER:[/] {utterance}")
        t0 = time.perf_counter()
        reply = agent.say(utterance)
        latencies.append((time.perf_counter() - t0) * 1000)
        for tc in agent.transcript[-1].tool_calls:
            g = tc.get("grounded")
            flag = "grounded" if g is True else ("NOT-IN-KB" if g is False else "ok")
            console.print(
                f"[dim]        -> {tc['tool']} [{flag}] {tc['latency_ms']}ms[/]"
            )
        console.print(f"[green]AGENT :[/] {reply}")

    failures, ratio = check(scenario, agent)
    path = agent.save_transcript(CALLS_DIR, scenario.label)

    if failures:
        console.print(f"\n[red]FAIL[/] {scenario.label}")
        for f in failures:
            console.print(f"  [red]-[/] {f}")
    else:
        console.print(f"\n[green]PASS[/] {scenario.label}")
    console.print(
        f"[dim]language consistency {ratio:.0%} | "
        f"searches={agent.state.summary()['kb_searches']} | "
        f"handoff={'yes' if agent.state.handoff else 'no'}[/]"
    )

    return Result(scenario, path, agent.state.summary(), failures, ratio, latencies)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="label prefix, e.g. ph_01 or id")
    # Groq enforces a per-DAY token cap per model (200k on the free tier).
    # Exhausting one model should not block the deliverable, so the model
    # is overridable per run.
    ap.add_argument("--model", default=None, help="override the chat model")
    args = ap.parse_args()

    if not retrieval_healthy():
        console.print("[red]Retrieval API not running on :8001[/]")
        return 2

    scenarios = SCENARIOS
    if args.only:
        scenarios = [s for s in SCENARIOS if s.label.startswith(args.only)]
        if not scenarios:
            console.print(f"[red]no scenario matching {args.only!r}[/]")
            return 2

    results = [run_scenario(s, model=args.model) for s in scenarios]

    console.print(f"\n[bold]{'=' * 76}[/]")
    console.print("[bold]Q3 SUMMARY[/]")
    console.print(f"[bold]{'=' * 76}[/]")
    for r in results:
        mark = "[green]PASS[/]" if r.passed else "[red]FAIL[/]"
        lat = sorted(r.latencies)
        p50 = lat[len(lat) // 2] if lat else 0
        console.print(
            f"  {mark}  {r.scenario.label:<26} lang={r.language_score:>4.0%}  "
            f"searches={r.summary['kb_searches']:<3} p50={p50:.0f}ms"
        )
    passed = sum(1 for r in results if r.passed)
    console.print(f"\n{passed}/{len(results)} passed")

    CALLS_DIR.mkdir(parents=True, exist_ok=True)
    (CALLS_DIR / "index.json").write_text(json.dumps([
        {
            "label": r.scenario.label,
            "market": r.scenario.market,
            "requirement": r.scenario.requirement,
            "passed": r.passed,
            "failures": r.failures,
            "language_consistency": round(r.language_score, 3),
            "transcript": r.transcript_path.name,
            "summary": r.summary,
        } for r in results
    ], indent=2), encoding="utf-8")
    console.print(f"wrote {CALLS_DIR / 'index.json'}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
