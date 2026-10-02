"""Sitemap-driven, robots-respecting crawler.

Design notes (these answer the brief's "explain website extraction"):

1. URL discovery comes from the sitemap, not from following links. A link
   spider on an insurance site drowns in nav/footer permutations and pulls the
   same page under a dozen paths; the sitemap is the publisher's own canonical
   list.
2. robots.txt is parsed and enforced per-URL, not just read once for show.
3. Every fetch -- success or failure -- lands in a manifest. The brief asks us
   to "handle extraction failures and flag obvious source errors", which means
   failures have to be visible artifacts, not swallowed exceptions.
4. Raw HTML is written to disk before any parsing. Parsing is then a pure
   function of the raw snapshot, so cleaning can be re-run and diffed without
   re-hitting the network.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.robotparser
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from xml.etree import ElementTree

import httpx
from rich.console import Console
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from shared.config import settings
from q2_kb.ingest.sources import BASE, SITEMAP, classify

console = Console()

SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
URL_XPATH = ".//sm:url"


@dataclass
class FetchRecord:
    """One row of the crawl manifest -- the audit trail for provenance."""

    url: str
    status: str                 # ok | http_error | timeout | robots_blocked | too_small
    http_code: int | None = None
    bytes_downloaded: int = 0
    content_hash: str | None = None
    raw_path: str | None = None
    category: str | None = None
    source_rule: str | None = None
    lastmod: str | None = None
    error: str | None = None
    fetched_at: str = ""


class Crawler:
    def __init__(self) -> None:
        self.robots = urllib.robotparser.RobotFileParser()
        self.records: list[FetchRecord] = []
        self.client = httpx.Client(
            headers={"User-Agent": settings.user_agent},
            timeout=settings.crawl_timeout_seconds,
            follow_redirects=True,
        )

    # ---------------- robots ----------------

    def load_robots(self) -> None:
        url = f"{BASE}/robots.txt"
        try:
            resp = self.client.get(url)
            resp.raise_for_status()
            self.robots.parse(resp.text.splitlines())
            console.print(f"[green]robots.txt loaded[/] ({len(resp.text)} bytes)")
        except Exception as exc:  # noqa: BLE001 - fail loud rather than crawl blind
            raise RuntimeError(
                f"Could not read robots.txt at {url}: {exc}. Refusing to crawl."
            ) from exc

    def allowed(self, url: str) -> bool:
        return self.robots.can_fetch(settings.user_agent, url)

    # ---------------- discovery ----------------

    def discover(self) -> list[tuple[str, str | None]]:
        """Return [(url, lastmod)] from the sitemap, filtered by source rules."""
        resp = self.client.get(SITEMAP)
        resp.raise_for_status()
        root = ElementTree.fromstring(resp.content)
        all_urls = root.findall(URL_XPATH, SITEMAP_NS)

        found: list[tuple[str, str | None]] = []
        for url_el in all_urls:
            loc_el = url_el.find("sm:loc", SITEMAP_NS)
            if loc_el is None or not loc_el.text:
                continue
            loc = loc_el.text.strip()
            mod_el = url_el.find("sm:lastmod", SITEMAP_NS)
            lastmod = mod_el.text.strip() if mod_el is not None and mod_el.text else None
            if classify(loc):
                found.append((loc, lastmod))

        seen: set[str] = set()
        unique = [(u, m) for u, m in found if not (u in seen or seen.add(u))]
        console.print(
            f"[cyan]sitemap:[/] {len(all_urls)} URLs -> {len(unique)} in scope"
        )
        return unique

    # ---------------- fetching ----------------

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.TransportError)),
        reraise=True,
    )
    def _get(self, url: str) -> httpx.Response:
        return self.client.get(url)

    def fetch(self, url: str, lastmod: str | None) -> FetchRecord:
        rule = classify(url)
        rec = FetchRecord(
            url=url,
            status="ok",
            category=rule.category if rule else None,
            source_rule=rule.name if rule else None,
            lastmod=lastmod,
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )

        if not self.allowed(url):
            rec.status = "robots_blocked"
            rec.error = "Disallowed by robots.txt"
            return rec

        try:
            resp = self._get(url)
        except httpx.TimeoutException as exc:
            rec.status, rec.error = "timeout", str(exc)
            return rec
        except Exception as exc:  # noqa: BLE001
            rec.status, rec.error = "http_error", str(exc)
            return rec

        rec.http_code = resp.status_code
        rec.bytes_downloaded = len(resp.content)

        if resp.status_code != 200:
            rec.status = "http_error"
            rec.error = f"HTTP {resp.status_code}"
            return rec

        # A page that is mostly chrome is a source error worth flagging, not a
        # silent empty record downstream.
        if len(resp.content) < 2000:
            rec.status = "too_small"
            rec.error = f"Only {len(resp.content)} bytes; likely an error page"
            return rec

        content_hash = hashlib.sha256(resp.content).hexdigest()[:16]
        slug = re.sub(r"[^a-z0-9]+", "-", url.replace(BASE, "").lower()).strip("-")[:90]
        raw_path = settings.raw_dir / f"{slug or 'index'}__{content_hash}.html"
        raw_path.write_bytes(resp.content)

        rec.content_hash = content_hash
        rec.raw_path = str(raw_path.relative_to(settings.data_dir.parent))
        return rec

    # ---------------- orchestration ----------------

    def run(self, limit: int | None = None) -> list[FetchRecord]:
        self.load_robots()
        targets = self.discover()
        cap = limit or settings.crawl_max_pages
        if cap:
            targets = targets[:cap]

        for i, (url, lastmod) in enumerate(targets, 1):
            rec = self.fetch(url, lastmod)
            self.records.append(rec)
            tag = "green" if rec.status == "ok" else "yellow"
            console.print(
                f"[{tag}]{rec.status:<14}[/] {i:>3}/{len(targets)}  "
                f"{url.replace(BASE, '')[:72]}"
            )
            time.sleep(settings.crawl_delay_seconds)  # politeness

        self.write_manifest()
        return self.records

    def write_manifest(self) -> None:
        path = settings.interim_dir / "crawl_manifest.json"
        ok = sum(1 for r in self.records if r.status == "ok")
        statuses = {r.status for r in self.records if r.status != "ok"}
        payload = {
            "crawled_at": datetime.now(timezone.utc).isoformat(),
            "source": BASE,
            "user_agent": settings.user_agent,
            "crawl_delay_seconds": settings.crawl_delay_seconds,
            "totals": {
                "attempted": len(self.records),
                "ok": ok,
                "failed": len(self.records) - ok,
            },
            "failures_by_status": {
                s: sum(1 for r in self.records if r.status == s) for s in statuses
            },
            "records": [asdict(r) for r in self.records],
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        console.print(
            f"\n[bold green]crawl complete[/] {ok}/{len(self.records)} ok "
            f"-> manifest at {path}"
        )


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Crawl source site into data/raw")
    parser.add_argument("--limit", type=int, default=None, help="max pages to fetch")
    args = parser.parse_args()
    Crawler().run(limit=args.limit)


if __name__ == "__main__":
    main()
