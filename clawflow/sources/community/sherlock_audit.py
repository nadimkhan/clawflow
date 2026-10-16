"""clawflow plugin: Web3 smart-contract bug-bounty programs.

Surfaces active bug-bounty programs posted on Immunefi.com, the
largest public smart-contract bug-bounty platform. Each "Lead" is
one bounty program the user could pursue as a security researcher.

The previous version of this plugin pointed at Sherlock.xyz's public
API (`https://app.sherlock.xyz/api/public/contests`) which 404'd
throughout 2026. That endpoint was retired; the platform moved to a
private API. We switch to Immunefi which still serves a fully
public, server-rendered directory at `/explore/`.

Free. No API key.
"""
from __future__ import annotations

import logging
import re
from typing import Any, AsyncIterator, ClassVar

import httpx

from clawflow.leads import Lead
from clawflow.sources import Source, register

log = logging.getLogger("clawflow.sources.community.audit_bounties")

# Immunefi's public directory. The page is server-rendered (SvelteKit
# SSR), so the program slugs are visible in the static HTML.
IMMUNEFI_EXPLORE = "https://immunefi.com/explore/"

# Seniority / role filter: senior AI+blockchain engineers usually want
# high-impact protocols, not random ERC-20 games. We bump the score
# for well-known protocols.
SENIOR_PROTOCOL_BONUS: dict[str, int] = {
    "ethereum": 20, "cosmos": 15, "polkadot": 15, "solana": 15,
    "near": 12, "sui": 12, "aptos": 10,
    "aave": 18, "uniswap": 18, "curve": 15, "compound": 15,
    "makerdao": 18, "lido": 18, "rocketpool": 15,
    "chainlink": 18, "thegraph": 12, "filecoin": 15,
    "optimism": 12, "arbitrum": 12, "polygon": 10,
    "ens": 10, "1inch": 10, "morpho": 12, "eigenlayer": 15,
    "celestia": 12, "monad": 12, "berachain": 10,
    "wormhole": 15, "layerzero": 15, "stargate": 12,
    "pancakeswap": 8, "sushiswap": 8, "yearn": 12,
    "balancer": 10, "frax": 10, "gmx": 10,
}


def _score_program(slug: str) -> int:
    base = 30  # base for being on Immunefi at all
    bonus = SENIOR_PROTOCOL_BONUS.get(slug.lower(), 0)
    return min(base + bonus, 100)


@register
class AuditBountiesSource(Source):
    """Public smart-contract bug-bounty programs from Immunefi."""

    key: ClassVar[str] = "audit_bounties"
    label: ClassVar[str] = "Web3 audit / bug-bounty programs"
    description: ClassVar[str] = (
        "Public bug-bounty programs on Immunefi (the largest smart-contract "
        "security platform) — surface Aave, Uniswap, ENS, Wormhole, Lido, "
        "and ~30 other senior-tier protocol bounty programs."
    )
    default_enabled: ClassVar[bool] = False

    async def validate_config(self, cfg: Any) -> tuple[bool, str]:
        return True, "audit_bounties: tracking immunefi.com/explore/"

    def fetch(self, state: dict[str, Any], cfg: Any) -> AsyncIterator[Lead]:
        return self._fetch(state, cfg)

    async def _fetch(self, state: dict[str, Any], cfg: Any) -> AsyncIterator[Lead]:
        ua = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        )
        try:
            async with httpx.AsyncClient(
                headers={
                    "User-Agent": ua,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                },
                timeout=20.0,
                follow_redirects=True,
            ) as client:
                resp = await client.get(IMMUNEFI_EXPLORE)
        except (httpx.HTTPError, ConnectionError, TimeoutError) as exc:
            log.warning("audit_bounties: fetch failed (%s)", exc)
            return
        if resp.status_code >= 400:
            log.warning("audit_bounties: explore HTTP %s", resp.status_code)
            return

        # Find every unique bug-bounty program URL.
        # Pattern: /bug-bounty/<slug>/information/  (sometimes without /information/)
        hrefs = re.findall(r'href="([^"]+)"', resp.text)
        seen: set[str] = set()
        for href in hrefs:
            m = re.search(r'/bug-bounty/([a-z0-9][a-z0-9-]+)/?', href)
            if not m:
                continue
            slug = m.group(1)
            # Skip generic paths
            if slug in {"program", "index", "home", "get-started", "projects"}:
                continue
            if slug in seen:
                continue
            seen.add(slug)
            # Title-case the slug
            title = slug.replace("-", " ").title()
            url = f"https://immunefi.com/bug-bounty/{slug}/information/"
            score = _score_program(slug)
            yield Lead(
                id=f"audit_bounties:{slug}",
                source=self.key,
                title=f"{title} bug bounty",
                url=url,
                posted_at=None,
                score=score,
                tags=("audit", "smart-contract", "bug-bounty", slug),
                raw={"program_slug": slug, "platform": "immunefi"},
            )
        log.info("audit_bounties: yielded %d program leads", len(seen))
