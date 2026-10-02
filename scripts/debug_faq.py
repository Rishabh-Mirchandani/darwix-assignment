"""Inspect the DOM of a dedicated FAQ page to find the real accordion pattern."""
from __future__ import annotations

import sys
from collections import Counter

from bs4 import BeautifulSoup

sys.path.insert(0, ".")
from shared.config import settings  # noqa: E402


def main() -> None:
    needle = sys.argv[1] if len(sys.argv) > 1 else "insurance-faq"
    matches = sorted(settings.raw_dir.glob(f"*{needle}*.html"))
    if not matches:
        print(f"no raw snapshot matching '{needle}'")
        print("available:", [p.name[:60] for p in list(settings.raw_dir.glob('*.html'))[:10]])
        return

    path = matches[0]
    print(f"inspecting: {path.name}\n")
    soup = BeautifulSoup(path.read_text(encoding="utf-8", errors="ignore"), "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    # 1. schema.org FAQ markup?
    print("--- schema.org Question nodes ---")
    print("  count:", len(soup.select('[itemtype*="Question"]')))
    print("  faqpage:", len(soup.select('[itemtype*="FAQPage"]')))

    # 2. What classes actually exist that look accordion-ish?
    print("\n--- classes containing accordion/faq/collapse/toggle ---")
    cls_counter: Counter[str] = Counter()
    for el in soup.find_all(class_=True):
        for c in el.get("class", []):
            lc = c.lower()
            if any(k in lc for k in ("accordion", "faq", "collapse", "toggle", "panel", "question")):
                cls_counter[c] += 1
    for c, n in cls_counter.most_common(25):
        print(f"  {n:>4}  .{c}")

    # 3. <details>/<summary> native accordions
    print("\n--- native <details> ---")
    print("  count:", len(soup.find_all("details")))

    # 4. Where do question marks actually live?
    print("\n--- tags whose text ends in '?' (likely questions) ---")
    tag_counter: Counter[str] = Counter()
    samples: dict[str, list[str]] = {}
    for el in soup.find_all(True):
        if not el.find(True):  # leaf-ish nodes only
            txt = el.get_text(" ", strip=True)
            if txt.endswith("?") and 15 < len(txt) < 200:
                key = f"{el.name}.{'.'.join(el.get('class', []))[:40]}"
                tag_counter[key] += 1
                samples.setdefault(key, []).append(txt)
    for k, n in tag_counter.most_common(12):
        print(f"  {n:>4}  {k}")
        for s in samples[k][:2]:
            print(f"          {s[:95]}")


if __name__ == "__main__":
    main()
