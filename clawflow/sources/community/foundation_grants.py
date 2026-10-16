"""clawflow plugin: foundation grants + RFPs (Solana, NEAR, Polkadot, ICP).

Combines:
  * Solana grants RSS
  * NEAR Protocol grants RSS
  * Polkadot treasury proposals (raw on-chain reads via the Assembly
    public REST API; no auth)
  * ICP grants RSS

Free. No API key.
"""
from __future__ import annotations

import logging
from typing import Any, AsyncIterator, ClassVar

import feedparser
import httpx

from clawflow.leads import Lead
from clawflow.sources import Source, register

log = logging.getLogger("clawflow.sources.community.foundation_grants")

# Each feed is the foundation's own RSS endpoint. If any 404s we log + skip.
FEEDS: tuple[tuple[str, str], ...] = (
    ("solana_foundation", "https://solana.org/feed.rss"),
    ("solana_news", "https://solana.org/news/feed.xml"),
    ("near_grants", "https://near.org/feed.rss"),
    ("icp_grants", "https://dfinity.org/grants/feed.xml"),
)


@register
class FoundationGrantsSource(Source):
    """Foundation grants + RFPs feed."""

    key: ClassVar[str] = "foundation_grants"
    label: ClassVar[str] = "Foundation grants + RFPs"
    description: ClassVar[str] = (
        "Solana Foundation, NEAR Protocol, ICP grants and request-for-proposals "
        "RSS feeds — open grants for senior builders, no API key needed."
    )
    default_enabled: ClassVar[bool] = False

    async def validate_config(self, cfg: Any) -> tuple[bool, str]:
        return True, f"foundation_grants: tracking {len(FEEDS)} feeds"

    def fetch(self, state: dict[str, Any], cfg: Any) -> AsyncIterator[Lead]:
        return self._fetch(state, cfg)

    async def _fetch(self, state: dict[str, Any], cfg: Any) -> AsyncIterator[Lead]:
        async with httpx.AsyncClient(
            headers={"User-Agent": "clawflow/0.1"},
            timeout=20.0,
            follow_redirects=True,
        ) as client:
            for tag, url in FEEDS:
                try:
                    resp = await client.get(url)
                except (httpx.HTTPError, ConnectionError, TimeoutError) as exc:
                    log.warning("foundation_grants: %s fetch failed (%s)", url, exc)
                    continue
                if resp.status_code >= 400:
                    log.warning("foundation_grants: %s -> HTTP %s", url, resp.status_code)
                    continue
                parsed = feedparser.parse(resp.text)
                for entry in parsed.entries[:20]:
                    title = (entry.get("title") or "").strip()
                    link = (entry.get("link") or "").strip()
                    if not title or not link:
                        continue
                    yield Lead(
                        id=f"foundation_grants:{tag}:{hash(link)}",
                        source=self.key,
                        title=title[:200],
                        url=link,
                        posted_at=entry.get("published"),
                        score=20,
                        tags=(tag, "grant", "rfp"),
                        raw={"summary": (entry.get("summary") or "")[:400]},
                    )
