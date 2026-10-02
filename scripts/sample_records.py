"""Sample built KB records so chunk quality can be eyeballed, not assumed.

    python scripts/sample_records.py                 # short records (the risk)
    python scripts/sample_records.py --kind faq
    python scripts/sample_records.py --max-tokens 40
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter

sys.path.insert(0, ".")
from shared.config import settings  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", default=None, help="prose | faq | definition | table")
    ap.add_argument("--category", default=None)
    ap.add_argument("--min-tokens", type=int, default=0)
    ap.add_argument("--max-tokens", type=int, default=100)
    ap.add_argument("-n", type=int, default=15)
    ap.add_argument("--seed", type=int, default=11)
    args = ap.parse_args()

    path = settings.processed_dir / "kb_records.jsonl"
    rows = [json.loads(line) for line in path.open(encoding="utf-8")]

    pool = [
        r for r in rows
        if args.min_tokens <= r["token_estimate"] <= args.max_tokens
        and (args.kind is None or r["section_kind"] == args.kind)
        and (args.category is None or r["category"] == args.category)
    ]

    print(f"total records   : {len(rows)}")
    print(f"matching filter : {len(pool)}\n")

    if pool:
        print("kind distribution within filter:")
        for k, n in Counter(r["section_kind"] for r in pool).most_common():
            print(f"   {k:<12}{n}")
        print()

    random.seed(args.seed)
    for r in random.sample(pool, min(args.n, len(pool))):
        print("-" * 78)
        print(f"[{r['section_kind']}/{r['category']}] {r['token_estimate']} tok  "
              f"{r['record_id']}")
        print(f"TITLE   : {r['title'][:100]}")
        print(f"CONTENT : {r['content'][:300]}")


if __name__ == "__main__":
    main()
