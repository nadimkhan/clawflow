"""clawflow plugin: DAO + foundation RFPs and hiring postings.

Two streams:
  * Snapshot's "Featured Jobs" board (https://snapshot.org/#/jobs) — JSON.
  * A small curated list of high-signal DAOs' direct hiring/careers pages.

All free. No auth. Snapshot.org is a hosted service; DAOs are scraped
from their public /careers or /jobs pages.
"""
from __future__ import annotations

import logging
import re
from typing import Any, AsyncIterator, ClassVar

import feedparser
import httpx

from clawflow.leads import Lead
from clawflow.sources import Source, register

log = logging.getLogger("clawflow.sources.community.dao_rfps")

# DAOs we curate. Each tuple: (display name, careers URL). Most DAO
# sites have a /jobs or /careers page; we scrape that.
DAO_CAREERS: tuple[tuple[str, str], ...] = (
    ("Snapshot", "https://snapshot.org/#/jobs"),
    ("Aave", "https://aave.com/careers"),
    ("Optimism", "https://optimism.io/careers"),
    ("Arbitrum", "https://arbitrum.io/careers"),
    ("Uniswap", "https://uniswap.org/careers"),
    ("ENS DAO", "https://www.ens.domains/careers"),
    ("Lens Protocol", "https://www.lens.xyz/careers"),
    ("Yearn Finance", "https://yearn.fi/v3/jobs"),
    ("Gitcoin", "https://www.gitcoin.co/careers"),
    ("Gnosis Chain", "https://www.gnosis.io/jobs"),
    ("Safe (Gnosis Safe)", "https://safe.global/careers"),
    ("BanklessDAO", "https://www.bankless.community/jobs"),
    ("Polygon Labs", "https://polygon.technology/careers"),
    ("1inch", "https://1inch.io/careers"),
    ("Curve DAO", "https://www.curve.space/jobs"),
    ("Balancer", "https://balancer.fi/jobs"),
)

# Marker words in scraped careers pages that suggest a *role*, not
# just a marketing blurb. Heuristic but useful.
ROLE_HINT_WORDS = (
    "engineer", "developer", "smart contract", "solidity", "rust",
    "ai engineer", "llm", "agent", "growth", "marketing",
    "researcher", "audit", "devrel", "protocol", "lead",
    "designer",
)


def _heuristic_score(haystack: str) -> int:
    """Score based on how many role-hint words we see in the page."""
    hay = haystack.lower()
    score = 0
    for w in ROLE_HINT_WORDS:
        if w in hay:
            score += 6
    score = min(10 + score, 80)  # floor of 10 just for being a known DAO
    return score


async def _scrape_dao(client: httpx.AsyncClient, name: str, url: str) -> str | None:
    try:
        resp = await client.get(url, follow_redirects=True)
    except (httpx.HTTPError, ConnectionError, TimeoutError) as exc:
        log.warning("dao_rfps: %s fetch failed (%s)", url, exc)
        return None
    if resp.status_code >= 400:
        return None
    return resp.text


@register
class DaoRfpsSource(Source):
    """DAO + foundation hiring / RFP postings."""

    key: ClassVar[str] = "dao_rfps"
    label: ClassVar[str] = "DAO & foundation RFPs"
    description: ClassVar[str] = (
        "Career/hiring pages of high-signal DAOs (Snapshot, Aave, Optimism, "
        "Arbitrum, Uniswap, ENS, Lens, Yearn, Gitcoin) — looks for engineer/agent roles."
    )
    default_enabled: ClassVar[bool] = False

    async def validate_config(self, cfg: Any) -> tuple[bool, str]:
        return True, f"dao_rfps: tracking {len(DAO_CAREERS)} DAOs"

    def fetch(self, state: dict[str, Any], cfg: Any) -> AsyncIterator[Lead]:
        return self._fetch(state, cfg)

    async def _fetch(self, state: dict[str, Any], cfg: Any) -> AsyncIterator[Lead]:
        seen: set[str] = set(state.get("dao_rfps_seen", []) or [])
        async with httpx.AsyncClient(
            headers={"User-Agent": "clawflow/0.1"},
            timeout=20.0,
            follow_redirects=True,
        ) as client:
            for name, url in DAO_CAREERS:
                html_text = await _scrape_dao(client, name, url)
                if not html_text:
                    continue
                # Save a brief digest; don't store full HTML in lead.
                # Pull a generous plain-text excerpt. Strip <style>/<script> blocks first
                # so the byte budget goes to readable content, not CSS.
                cleaned = re.sub(r"<style[^>]*>.*?</style>", " ",
                                 html_text, flags=re.DOTALL | re.IGNORECASE)
                cleaned = re.sub(r"<script[^>]*>.*?</script>", " ",
                                 cleaned, flags=re.DOTALL | re.IGNORECASE)
                cleaned = re.sub(r"@font-face\s*{[^}]*}", " ", cleaned)
                cleaned = re.sub(r"<[^>]+>", " ", cleaned)
                text = re.sub(r"\s+", " ", cleaned).strip()[:8000]
                score = _heuristic_score(text)
                dedup_key = f"dao_rfps:{name}"
                if dedup_key in seen:
                    # Still emit as a low-info "seen" record so user can verify it's fresh
                    continue
                seen.add(dedup_key)
                yield Lead(
                    id=dedup_key,
                    source=self.key,
                    title=f"{name} careers page",
                    url=url,
                    posted_at=None,
                    score=score,
                    tags=(name.lower().replace(" ", "-"), "dao", "hiring"),
                    raw={"page_excerpt": text},
                )
        # bounded set so the seen-list doesn't grow forever
        state["dao_rfps_seen"] = sorted(seen)[-200:]
