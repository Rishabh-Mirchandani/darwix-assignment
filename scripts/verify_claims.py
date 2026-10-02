"""Check the numbers quoted in the docs against the artifacts that produced them.

Documentation drifts the moment a pipeline is re-run. This asserts the headline
figures in README.md and docs/ still match what is actually on disk, so a stale
number is caught here rather than by a reviewer.

Run before committing or recording.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

failures: list[str] = []
checks = 0


def check(label: str, claimed, actual, tol: float = 0.0) -> None:
    global checks
    checks += 1
    if isinstance(claimed, (int, float)) and isinstance(actual, (int, float)):
        ok = abs(claimed - actual) <= tol
    else:
        ok = claimed == actual
    mark = "[ OK ]" if ok else "[FAIL]"
    print(f"{mark} {label:<46} doc={claimed}  actual={actual}")
    if not ok:
        failures.append(f"{label}: doc says {claimed}, artifact says {actual}")


def docs_text() -> str:
    parts = [(ROOT / "README.md").read_text(encoding="utf-8")]
    for p in sorted((ROOT / "docs").glob("*.md")):
        parts.append(p.read_text(encoding="utf-8"))
    parts.append((ROOT / "SUBMISSION.md").read_text(encoding="utf-8"))
    return "\n".join(parts)


def main() -> int:
    text = docs_text()

    # --- index ---
    matrix = np.load(ROOT / "data/index/embeddings.npy", mmap_mode="r")
    records = [
        json.loads(l)
        for l in (ROOT / "data/index/records.jsonl").open(encoding="utf-8")
    ]
    check("index rows == record count", matrix.shape[0], len(records))
    check("embedding dim is 384", int(matrix.shape[1]), 384)

    by_market = Counter(r["market"] for r in records)
    check("total records (docs say 2,466)", 2466, len(records))
    check("india records (2,442 = 2,084 web + 358 PDF)", 2442, by_market["in"])

    by_source = Counter(r["source_type"] for r in records)
    check("web-sourced records (2,108)", 2108, by_source["website"])
    check("PDF-sourced records (358)", 358, by_source["pdf"])
    check("ph records", 12, by_market["ph"])
    check("id records", 12, by_market["id"])

    # --- build ---
    build = json.loads(
        (ROOT / "data/processed/build_report.json").read_text(encoding="utf-8")
    )["stats"]
    check("exact duplicates removed (628)", 628, build["exact_duplicates"])
    check("near duplicates removed (22)", 22, build["near_duplicates"])
    check("chunks produced (2,769)", 2769, build["chunks_produced"])
    check("pages parsed (100)", 100, build["pages_parsed"])

    # --- crawl ---
    crawl = json.loads(
        (ROOT / "data/interim/crawl_manifest.json").read_text(encoding="utf-8")
    )
    check("pages attempted (105)", 105, crawl["totals"]["attempted"])
    check("pages ok (100)", 100, crawl["totals"]["ok"])
    check("pages failed (5)", 5, crawl["totals"]["failed"])

    # --- retrieval eval ---
    ev = json.loads(
        (ROOT / "data/processed/retrieval_eval.json").read_text(encoding="utf-8")
    )
    verdicts = Counter(r["verdict"] for r in ev)
    check("eval total queries (20)", 20, len(ev))
    check("eval all correct (20/20)", 20, verdicts["correct"])
    answerable = [r for r in ev if r["expected"] == "answer"]
    refusals = [r for r in ev if r["expected"] == "refuse"]
    check("answerable correct (16/16)", 16,
          sum(1 for r in answerable if r["verdict"] == "correct"))
    check("refusals correct (4/4)", 4,
          sum(1 for r in refusals if r["verdict"] == "correct"))

    # --- Q4 ---
    q4 = json.loads(
        (ROOT / "q4_live_nudges/reports/pipeline_report.json").read_text(encoding="utf-8")
    )["report"]
    e2e = q4["latency_ms"]["end_to_end"]
    doc_p50 = re.search(r"\*\*p50 (\d+)ms / p95 (\d+)ms\*\* end-to-end", text)
    if doc_p50:
        check("Q4 e2e p50 matches report", float(doc_p50.group(1)), e2e["p50"], tol=1.0)
        check("Q4 e2e p95 matches report", float(doc_p50.group(2)), e2e["p95"], tol=1.0)
    check("Q4 precision 1.0", 1.0, q4["accuracy"]["precision"])
    check("Q4 realtime factor ~1.0", 1.0, q4["replay"]["realtime_factor"], tol=0.15)

    # --- call artifacts ---
    q1 = json.loads(
        (ROOT / "q1_voice_agent/calls/index.json").read_text(encoding="utf-8")
    )
    check("Q1 scenarios (6)", 6, len(q1))
    check("Q1 all passed", True, all(r["passed"] for r in q1))

    q3 = json.loads(
        (ROOT / "q3_localized/calls/index.json").read_text(encoding="utf-8")
    )
    check("Q3 calls (4)", 4, len(q3))
    check("Q3 two per market", True,
          Counter(r["market"] for r in q3) == {"ph": 2, "id": 2})
    check("Q3 all >=80% language consistency", True,
          all(r["language_consistency"] >= 0.8 for r in q3))

    # --- secrets ---
    key_re = re.compile(r"gsk_[A-Za-z0-9]{20}|AIzaSy[A-Za-z0-9_-]{20}")
    leaked = []
    for p in ROOT.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(ROOT).as_posix()
        if rel.startswith((".git/", ".venv/")) or rel == ".env":
            continue
        try:
            if key_re.search(p.read_text(encoding="utf-8", errors="ignore")):
                leaked.append(rel)
        except Exception:
            pass
    check("no API keys in committable files", [], leaked)

    print("\n" + "=" * 68)
    if failures:
        print(f"{len(failures)} MISMATCH(ES) of {checks} checks:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"all {checks} documented figures match the artifacts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
