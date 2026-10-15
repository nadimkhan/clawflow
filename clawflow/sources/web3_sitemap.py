"""Web3 job-board sources.

Two sub-sources live in this module because they share helpers and config:

* :class:`Remote3RSSSource` — parses the Remote3 RSS feed and yields entries
  whose text matches a web3/solidity/rust keyword.
* :class:`CryptoJobsSitemapSource` — fetches CryptoJobsList's ``sitemap.xml``,
  follows the first N ``/jobs/`` URLs, and parses the h1 title + apply link
  + first 200 chars of description from each page. Hard rate-limited to
  ~1 request/second so we don't get IP-banned.

Both sources persist a seen-set on the ``state`` dict so a re-run skips
items we've already yielded. The orchestrator owns the on-disk state file;
this module just reads/writes keys on the in-memory ``state`` dict.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Any, AsyncIterator, ClassVar
from urllib.parse import urljoin, urlparse

import feedparser  # type: ignore[import-untyped]
import httpx

from clawflow.leads import Lead
from clawflow.sources import Source, register

# Primary feed URL plus a couple of fallbacks in case the canonical endpoint
# moved (the orchestrator validates the live URL; these are static guesses).
REMOTE3_PRIMARY = "https://www.remote3.com/rss"
REMOTE3_FALLBACKS: tuple[str, ...] = (
    "https://www.remote3.com/rss.xml",
    "https://www.remote3.com/feed",
    "https://www.remote3.com/feed.xml",
)

# CryptoJobsList split their sitemap in 2026: the root sitemap.xml is now
# just a small index pointing at 9 per-section sitemaps. The job listings
# live in /sitemap-jobs.xml directly. Point at it to skip the index dance.
CRYPTOJOBS_SITEMAP = "https://cryptojobslist.com/sitemap-jobs.xml"

# Keywords we filter on. Matching is OR + case-insensitive on a haystack built
# from the title, summary, and any RSS tags.
TAG_KEYWORDS: tuple[str, ...] = (
    "solidity",
    "smart contract",
    "rust",
    "web3",
    "blockchain",
)

log = logging.getLogger("clawflow.sources.web3_sitemap")


def _parse_dt(value: str | None) -> datetime | None:
    """Parse an ISO-8601 timestamp; return None on failure."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _matches_tag(text: str) -> bool:
    """Return True iff any TAG_KEYWORDS appears in ``text`` (case-insensitive)."""
    lower = text.lower()
    return any(kw in lower for kw in TAG_KEYWORDS)


async def _fetch_text(client: httpx.AsyncClient, url: str) -> str | None:
    """GET ``url`` and return its text; log + return None on any failure."""
    try:
        resp = await client.get(url, timeout=20.0, follow_redirects=True)
    except (httpx.HTTPError, ConnectionError, TimeoutError) as exc:
        log.warning("web3_sitemap: GET %s failed (%s)", url, exc)
        return None
    if resp.status_code >= 400:
        log.warning("web3_sitemap: GET %s -> %s", url, resp.status_code)
        return None
    return resp.text


@register
class Remote3RSSSource(Source):
    """Remote3 RSS feed, filtered to web3/solidity/rust keywords."""

    key: ClassVar[str] = "remote3_rss"
    label: ClassVar[str] = "Remote3 RSS"
    description: ClassVar[str] = (
        "Web3 job board RSS feed from remote3.com (filtered by web3 keywords)."
    )
    default_enabled: ClassVar[bool] = True

    async def validate_config(self, cfg: Any) -> tuple[bool, str]:
        return True, "remote3_rss: no config required"

    async def _resolve_feed_url(self, client: httpx.AsyncClient) -> str | None:
        """Try primary URL then fallbacks; return the first one that responds."""
        for url in (REMOTE3_PRIMARY, *REMOTE3_FALLBACKS):
            try:
                resp = await client.get(url, timeout=10.0, follow_redirects=True)
            except (httpx.HTTPError, ConnectionError, TimeoutError):
                continue
            if resp.status_code < 400 and resp.text:
                return str(resp.url)
        return None

    def fetch(self, state: dict[str, Any], cfg: Any) -> AsyncIterator[Lead]:
        """Async generator entry point."""
        return self._fetch(state, cfg)

    async def _fetch(self, state: dict[str, Any], cfg: Any) -> AsyncIterator[Lead]:
        async with httpx.AsyncClient(
            headers={"User-Agent": "clawflow/0.1"},
            timeout=20.0,
        ) as client:
            feed_url = await self._resolve_feed_url(client)
            if not feed_url:
                log.warning("remote3_rss: could not resolve feed URL; skipping")
                return
            text = await _fetch_text(client, feed_url)
            if not text:
                return
            parsed = feedparser.parse(text)
            for entry in parsed.entries:
                try:
                    title = (entry.get("title") or "").strip()
                    link = entry.get("link") or ""
                    if not (title and link):
                        continue
                    summary = (
                        entry.get("summary") or entry.get("description") or ""
                    )[:500]
                    tags = [
                        t.get("term", "")
                        for t in (entry.get("tags") or [])
                        if isinstance(t, dict)
                    ]
                    haystack = " ".join([title, summary, *tags])
                    if not _matches_tag(haystack):
                        continue
                    posted = _parse_dt(
                        entry.get("published") or entry.get("updated")
                    )
                    yield Lead(
                        id=f"remote3:{link}",
                        source=self.key,
                        title=title[:200],
                        url=link,
                        posted_at=posted,
                        score=60,
                        tags=tuple(t for t in tags if t),
                        raw={
                            "summary": summary,
                            "feed_url": feed_url,
                        },
                    )
                except Exception as exc:
                    log.warning("remote3_rss: skipped entry (%s)", exc)
                    continue


@register
class CryptoJobsSitemapSource(Source):
    """Crawl CryptoJobsList's sitemap and parse each ``/jobs/`` page."""

    key: ClassVar[str] = "cryptojobs_sitemap"
    label: ClassVar[str] = "CryptoJobsList sitemap"
    description: ClassVar[str] = (
        "Crawl cryptojobslist.com sitemap.xml, parse each /jobs/ page (h1 + apply)."
    )
    default_enabled: ClassVar[bool] = True

    def __init__(self, rate_seconds: float = 1.05) -> None:
        """``rate_seconds`` defaults to just over 1 req/sec to respect the host."""
        self._rate_seconds = rate_seconds

    async def validate_config(self, cfg: Any) -> tuple[bool, str]:
        max_pages = int(getattr(cfg, "max_pages", 50) or 50)
        return True, f"cryptojobs_sitemap: max_pages={max_pages} rate={self._rate_seconds}s"

    async def _slow_get(self, client: httpx.AsyncClient, url: str) -> str | None:
        """Sleep then GET — the rate-limit gate."""
        await asyncio.sleep(self._rate_seconds)
        return await _fetch_text(client, url)

    def fetch(self, state: dict[str, Any], cfg: Any) -> AsyncIterator[Lead]:
        """Async generator entry point."""
        return self._fetch(state, cfg)

    async def _fetch(self, state: dict[str, Any], cfg: Any) -> AsyncIterator[Lead]:
        max_pages = int(getattr(cfg, "max_pages", 50) or 50)
        keywords = tuple(getattr(cfg, "keywords", None) or TAG_KEYWORDS)
        seen: set[str] = set(state.get("cryptojobs_seen", []) or [])
        try:
            async with httpx.AsyncClient(
                headers={"User-Agent": "clawflow/0.1"},
                timeout=20.0,
            ) as client:
                sitemap_text = await self._slow_get(client, CRYPTOJOBS_SITEMAP)
                if not sitemap_text:
                    return
                urls = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", sitemap_text)
                job_urls = [u for u in urls if "/jobs/" in u][:max_pages]
                log.info(
                    "cryptojobs_sitemap: %d job URLs in sitemap (cap %d)",
                    len(job_urls),
                    max_pages,
                )
                for url in job_urls:
                    if url in seen:
                        continue
                    try:
                        html = await self._slow_get(client, url)
                    except Exception as exc:
                        log.warning("cryptojobs_sitemap: GET %s failed (%s)", url, exc)
                        continue
                    if not html:
                        continue
                    lead = self._html_to_lead(url, html, keywords)
                    if lead is not None:
                        seen.add(url)
                        yield lead
        except Exception as exc:
            log.error("cryptojobs_sitemap: fatal (%s); bailing", exc)
            return
        finally:
            # Persist a bounded seen-set so the state file doesn't grow forever.
            state["cryptojobs_seen"] = sorted(seen)[-1000:]

    def _html_to_lead(
        self,
        url: str,
        html: str,
        keywords: tuple[str, ...],
    ) -> Lead | None:
        """Parse a single job page into a :class:`Lead` (or None if it doesn't match)."""
        try:
            title_match = re.search(
                r"<h1[^>]*>(.*?)</h1>", html, re.IGNORECASE | re.DOTALL
            )
            raw_title = title_match.group(1) if title_match else ""
            title = re.sub(r"<[^>]+>", "", raw_title)
            title = re.sub(r"\s+", " ", title).strip()
            if not title:
                return None

            apply_match = re.search(
                r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>\s*apply\s*</a>',
                html,
                re.IGNORECASE,
            )
            apply_url = urljoin(url, apply_match.group(1)) if apply_match else url

            desc_match = re.search(
                r'<meta\s+name=["\']description["\']\s+content=["\']([^"\']+)["\']',
                html,
                re.IGNORECASE,
            )
            desc = (desc_match.group(1) if desc_match else "")[:200]

            lower = (title + " " + desc).lower()
            if not any(kw.lower() in lower for kw in keywords):
                return None

            return Lead(
                id=f"cryptojobs:{urlparse(url).path}",
                source=self.key,
                title=title[:200],
                url=apply_url or url,
                posted_at=None,
                score=55,
                tags=("cryptojobs",),
                raw={"page_url": url, "description": desc, "apply_url": apply_url},
            )
        except Exception as exc:
            log.warning("cryptojobs_sitemap: parse %s failed (%s)", url, exc)
            return None