"""Dump every sitemap URL under the given first-path-segments.

    python scripts/dump_family.py insurance-faq help-centre health-insurance
"""
from __future__ import annotations

import sys
from collections import defaultdict
from xml.etree import ElementTree

import httpx

sys.path.insert(0, ".")
from shared.config import settings           # noqa: E402
from q2_kb.ingest.sources import SITEMAP     # noqa: E402

NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}


def main() -> None:
    wanted = set(sys.argv[1:])
    if not wanted:
        print(__doc__)
        return

    resp = httpx.get(
        SITEMAP, headers={"User-Agent": settings.user_agent}, timeout=30,
        follow_redirects=True,
    )
    root = ElementTree.fromstring(resp.content)

    grouped: dict[str, list[str]] = defaultdict(list)
    for el in root.findall(".//sm:url", NS):
        loc_el = el.find("sm:loc", NS)
        if loc_el is None or not loc_el.text:
            continue
        path = loc_el.text.strip().replace("https://www.nivabupa.com", "")
        seg = path.strip("/").split("/")[0] or "(root)"
        if seg in wanted:
            grouped[seg].append(path)

    for seg in sorted(grouped):
        urls = grouped[seg]
        print(f"\n===== {seg}  (n={len(urls)}) =====")
        for u in sorted(urls):
            print(f"  {u}")


if __name__ == "__main__":
    main()
