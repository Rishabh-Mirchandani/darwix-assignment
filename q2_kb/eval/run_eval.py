"""Run the retrieval evaluation and emit the submission table.

Produces three artifacts:

  data/processed/retrieval_eval.json  -- full results, machine readable
  docs/RETRIEVAL_EVAL.md              -- the table the brief asks for
  stdout                              -- a summary for the terminal

Verdicts are assigned automatically where the evidence is unambiguous and
flagged for human review otherwise:

  correct           -- behaviour matched `expect`, and (for answers) every
                       `must_mention` keyword appears in a retrieved record
  partially_correct -- answered when it should answer, but a required keyword
                       is missing from the top-N
  incorrect         -- answered when it should have refused, or refused when it
                       should have answered

Auto-grading is explicitly a screening pass, not a substitute for reading the
retrieved text. Every row carries the retrieved chunk so a reviewer can
override, and the markdown table is written for exactly that.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field

from rich.console import Console
from rich.table import Table

from shared.config import settings
from q2_kb.eval.queries import QUERIES, EvalQuery
from q2_kb.retrieval.hybrid import HybridRetriever, RetrievalResult

console = Console()


@dataclass
class EvalRow:
    qid: str
    question: str
    intent: str
    expected: str
    grounded: bool
    confidence: float
    threshold: float
    verdict: str
    explanation: str
    top_record_id: str = ""
    top_title: str = ""
    top_content: str = ""
    top_citation: str = ""
    top_cosine: float = 0.0
    missing_keywords: list[str] = field(default_factory=list)
    latency_ms: float = 0.0
    note: str = ""


def grade(q: EvalQuery, result: RetrievalResult) -> tuple[str, str, list[str]]:
    """Return (verdict, explanation, missing_keywords)."""
    if q.expect == "refuse":
        if not result.grounded:
            return (
                "correct",
                f"Correctly refused: {result.refusal_reason}",
                [],
            )
        return (
            "incorrect",
            f"Answered an out-of-scope question at confidence "
            f"{result.confidence:.3f} (floor {result.threshold:.3f}). "
            f"Top record: {result.results[0].record.title!r}",
            [],
        )

    # expect == "answer"
    if not result.grounded:
        return (
            "incorrect",
            f"Refused an answerable question: {result.refusal_reason}",
            list(q.must_mention),
        )

    haystack = " ".join(
        f"{r.record.title} {r.record.content}".lower() for r in result.results
    )
    missing = [kw for kw in q.must_mention if kw.lower() not in haystack]

    if not missing:
        return (
            "correct",
            f"Retrieved at cosine {result.confidence:.3f} (floor "
            f"{result.threshold:.3f}); all required concepts present in top-"
            f"{len(result.results)}.",
            [],
        )
    return (
        "partially_correct",
        f"Answered at cosine {result.confidence:.3f}, but top-"
        f"{len(result.results)} omits: {', '.join(missing)}.",
        missing,
    )


def run() -> list[EvalRow]:
    retriever = HybridRetriever()
    console.print(f"index loaded: [bold]{len(retriever.store)}[/] records\n")

    rows: list[EvalRow] = []
    for q in QUERIES:
        start = time.perf_counter()
        result = retriever.search(q.question)
        latency = (time.perf_counter() - start) * 1000

        verdict, explanation, missing = grade(q, result)
        top = result.results[0] if result.results else None

        rows.append(
            EvalRow(
                qid=q.qid,
                question=q.question,
                intent=q.intent,
                expected=q.expect,
                grounded=result.grounded,
                confidence=round(result.confidence, 4),
                threshold=round(result.threshold, 4),
                verdict=verdict,
                explanation=explanation,
                top_record_id=top.record.record_id if top else "",
                top_title=top.record.title if top else "",
                top_content=(top.record.content[:400] if top else ""),
                top_citation=top.record.citation() if top else "",
                top_cosine=round(top.vector_score, 4) if top else 0.0,
                missing_keywords=missing,
                latency_ms=round(latency, 1),
                note=q.note,
            )
        )

        colour = {
            "correct": "green",
            "partially_correct": "yellow",
            "incorrect": "red",
        }[verdict]
        console.print(
            f"[{colour}]{verdict:<18}[/] {q.qid}  conf={result.confidence:.3f} "
            f"thr={result.threshold:.3f}  {q.question[:56]}"
        )

    _write_outputs(rows)
    _summarise(rows)
    return rows


def _write_outputs(rows: list[EvalRow]) -> None:
    json_path = settings.processed_dir / "retrieval_eval.json"
    json_path.write_text(
        json.dumps([asdict(r) for r in rows], indent=2), encoding="utf-8"
    )

    md = ["# Q2 - Retrieval Evaluation", ""]
    md.append(
        "Auto-graded screening pass over the evaluation set in "
        "`q2_kb/eval/queries.py`. Every row includes the retrieved chunk so the "
        "verdict can be checked by reading rather than trusted."
    )
    md.append("")
    counts: dict[str, int] = {}
    for r in rows:
        counts[r.verdict] = counts.get(r.verdict, 0) + 1
    md.append("| verdict | count |")
    md.append("|---|---|")
    for v in ("correct", "partially_correct", "incorrect"):
        md.append(f"| {v} | {counts.get(v, 0)} |")
    md.append(f"| **total** | **{len(rows)}** |")
    md.append("")

    md.append("## Results")
    md.append("")
    for r in rows:
        md.append(f"### {r.qid} - {r.question}")
        md.append("")
        md.append(f"- **Intent:** {r.intent}  |  **Expected:** {r.expected}")
        md.append(
            f"- **Verdict:** `{r.verdict}`  |  **Confidence:** {r.confidence:.3f} "
            f"(floor {r.threshold:.3f})  |  **Latency:** {r.latency_ms} ms"
        )
        if r.grounded:
            md.append(f"- **Retrieved record:** `{r.top_record_id}` - {r.top_title}")
            md.append(f"- **Source:** {r.top_citation}")
            md.append("")
            md.append("  > " + r.top_content.replace("\n", "\n  > ")[:400])
        else:
            md.append("- **Retrieved:** nothing above threshold (refusal path)")
        md.append("")
        md.append(f"- **Relevance:** {r.explanation}")
        if r.note:
            md.append(f"- **Why this query is in the set:** {r.note}")
        md.append("")

    docs = settings.data_dir.parent / "docs"
    docs.mkdir(exist_ok=True)
    (docs / "RETRIEVAL_EVAL.md").write_text("\n".join(md), encoding="utf-8")
    console.print(f"\n[green]wrote[/] {json_path}")
    console.print(f"[green]wrote[/] {docs / 'RETRIEVAL_EVAL.md'}")


def _summarise(rows: list[EvalRow]) -> None:
    counts: dict[str, int] = {}
    for r in rows:
        counts[r.verdict] = counts.get(r.verdict, 0) + 1

    table = Table(title="retrieval eval", header_style="bold")
    table.add_column("verdict")
    table.add_column("count", justify="right")
    for v in ("correct", "partially_correct", "incorrect"):
        table.add_row(v, str(counts.get(v, 0)))
    table.add_row("[bold]total[/]", f"[bold]{len(rows)}[/]")
    console.print()
    console.print(table)

    answerable = [r for r in rows if r.expected == "answer"]
    refusals = [r for r in rows if r.expected == "refuse"]
    console.print(
        f"answerable: {sum(1 for r in answerable if r.verdict == 'correct')}"
        f"/{len(answerable)} fully correct"
    )
    console.print(
        f"refusals:   {sum(1 for r in refusals if r.verdict == 'correct')}"
        f"/{len(refusals)} correctly refused"
    )
    lat = sorted(r.latency_ms for r in rows)
    if lat:
        console.print(
            f"latency:    p50={lat[len(lat)//2]:.0f} ms  "
            f"p95={lat[int(len(lat)*0.95)-1]:.0f} ms"
        )


if __name__ == "__main__":
    run()
