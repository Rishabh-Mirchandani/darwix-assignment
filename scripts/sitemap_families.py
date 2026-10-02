"""Group every sitemap URL by its first path segment.

Used once, to design the source rules from the site's real information
architecture instead of guessing path fragments.
"""
from __future__ import annotations

import sys
from collections import Counter, defaultdict
from xml.etree import ElementTree

import httpx

sys.path.insert(0, ".")
from shared.config import settings           # noqa: E402
from q2_kb.ingest.sources import SITEMAP     # noqa: E402

NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}


def main() -> None:
    resp = httpx.get(
        SITEMAP, headers={"User-Agent": settings.user_agent}, timeout=30,
        follow_redirects=True,
    )
    root = ElementTree.fromstring(resp.content)

    families: Counter[str] = Counter()
    examples: dict[str, list[str]] = defaultdict(list)

    for el in root.findall(".//sm:url", NS):
        loc_el = el.find("sm:loc", NS)
        if loc_el is None or not loc_el.text:
            continue
        path = loc_el.text.strip().replace("https://www.nivabupa.com", "")
        seg = path.strip("/").split("/")[0] or "(root)"
        families[seg] += 1
        if len(examples[seg]) < 3:
            examples[seg].append(path)

    print(f"{'first path segment':<46}{'count'}")
    print("-" * 58)
    for seg, count in families.most_common(40):
        print(f"{seg:<46}{count}")

    print("\n\nexamples for the larger families:")
    for seg, count in families.most_common(22):
        print(f"\n[{seg}]  n={count}")
        for ex in examples[seg]:
            print(f"   {ex}")


if __name__ == "__main__":
    main()
