"""Count Mantine accordion items across every raw snapshot.

Sizes the FAQ-extraction fix before it is written: if the pattern only appears
on the 8 dedicated FAQ pages it is worth little, but if product and coverage
pages carry it too, it is the highest-value content in the corpus.
"""
from __future__ import annotations

import sys
from collections import Counter

from bs4 import BeautifulSoup

sys.path.insert(0, ".")
from shared.config import settings  # noqa: E402


def main() -> None:
    files = sorted(settings.raw_dir.glob("*.html"))
    per_file: Counter[str] = Counter()
    total_items = 0
    pages_with = 0

    for path in files:
        soup = BeautifulSoup(path.read_text(encoding="utf-8", errors="ignore"), "lxml")
        items = soup.select(".mantine-Accordion-item")
        if items:
            pages_with += 1
            total_items += len(items)
            per_file[path.name[:70]] = len(items)

    print(f"raw snapshots scanned : {len(files)}")
    print(f"pages with accordions : {pages_with}")
    print(f"total accordion items : {total_items}\n")

    print("top pages by accordion count:")
    for name, n in per_file.most_common(15):
        print(f"  {n:>3}  {name}")

    # Verify the control/panel pairing actually yields Q&A text.
    print("\n--- sample extraction from the richest page ---")
    if per_file:
        richest = per_file.most_common(1)[0][0]
        match = next(p for p in files if p.name[:70] == richest)
        soup = BeautifulSoup(match.read_text(encoding="utf-8", errors="ignore"), "lxml")
        for item in soup.select(".mantine-Accordion-item")[:3]:
            ctrl = item.select_one(".mantine-Accordion-label")
            panel = item.select_one(".mantine-Accordion-content, .mantine-Accordion-panel")
            q = ctrl.get_text(" ", strip=True) if ctrl else "(no label)"
            a = panel.get_text(" ", strip=True) if panel else "(no panel)"
            print(f"\n  Q: {q[:110]}")
            print(f"  A: {a[:220]}")


if __name__ == "__main__":
    main()
