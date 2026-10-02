"""Print what the source rules would actually crawl, grouped by category.

Run before a full crawl. The point is to catch rules that are too broad -- a
pattern like "/contact" happily matches 150 near-identical branch-address
pages, which would swamp the index with records no caller ever asks about.
"""
from __future__ import annotations

import sys
from collections import Counter
from xml.etree import ElementTree

import httpx

sys.path.insert(0, ".")
from shared.config import settings           # noqa: E402
from q2_kb.ingest.sources import SITEMAP, classify  # noqa: E402

NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}


def main() -> None:
    resp = httpx.get(
        SITEMAP, headers={"User-Agent": settings.user_agent}, timeout=30,
        follow_redirects=True,
    )
    root = ElementTree.fromstring(resp.content)

    by_rule: Counter[str] = Counter()
    by_category: Counter[str] = Counter()
    samples: dict[str, list[str]] = {}

    total = 0
    for el in root.findall(".//sm:url", NS):
        loc_el = el.find("sm:loc", NS)
        if loc_el is None or not loc_el.text:
            continue
        total += 1
        rule = classify(loc_el.text.strip())
        if not rule:
            continue
        by_rule[rule.name] += 1
        by_category[rule.category] += 1
        samples.setdefault(rule.name, []).append(loc_el.text.strip())

    print(f"sitemap URLs: {total}")
    print(f"in scope:     {sum(by_rule.values())}\n")

    print(f"{'rule':<26}{'category':<22}count")
    print("-" * 60)
    for rule_name, count in by_rule.most_common():
        cat = next(
            (c for c in by_category if samples[rule_name]), ""
        )
        print(f"{rule_name:<26}{'':<22}{count}")

    print("\nby category:")
    for cat, count in by_category.most_common():
        print(f"  {cat:<24}{count}")

    print("\nsamples per rule:")
    for rule_name, urls in samples.items():
        print(f"\n[{rule_name}] {len(urls)} urls")
        for u in urls[:4]:
            print(f"   {u}")
        if len(urls) > 4:
            print(f"   ... and {len(urls) - 4} more")


if __name__ == "__main__":
    main()
