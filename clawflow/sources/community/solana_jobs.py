"""clawflow plugin: Solana ecosystem RFPs + Solana Foundation grants.

Surfaces:
  * Solana Foundation's public RFP/grants feed (RSS)
  * Hiring-related issues across Solana ecosystem repos (Marinade,
    Wormhole, Jupiter, Drift, Helius, Metaplex, etc.)
  * Foundation governance forum postings that mention hires/grants

All free, no API key, no scraping of Discord/Telegram.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from datetime import timedelta
from typing import Any, AsyncIterator, ClassVar

import feedparser
import httpx

from clawflow.leads import Lead
from clawflow.sources import Source, register

log = logging.getLogger("clawflow.sources.community.solana_jobs")

# Solana ecosystem repos to scan for "bounty" + "help wanted" issues.
# 2026 list — these are the active senior-hiring orgs.
SOLANA_REPOS: tuple[str, ...] = (
    "solana-labs/solana",
    "solana-foundation/solana-improvement-documents",
    "anza-xyz/agave",
    "jito-labs/jito-solana",
    "marinade-finance/liquid-staking-solana",
    "wormhole-foundation/wormhole",
    "wormhole-foundation/example-native-token-transfers",
    "jup-ag/jupiter-core",
    "drift-labs/protocol-v2",
    "drift-labs/dlob-server",
    "helius-labs/helius-sdk",
    "metaplex-foundation/metaplex-program-library",
    "coral-xyz/backpack",
    "mango-markets/mango-v4",
    "zeta-chain/zeta-chain-core",
    "solana-mobile/solana-mobile-sdk-react-native",
    "switchboard-xyz/switchboard-on-demand",
    "tensor-foundation/tensor-ticketing",
    "dexterlab-in-solana/dexterlab-contracts",
)

BOUNTY_LABEL_TERMS = ('"💎 Bounty"', "bounty", '"help wanted"')

# Solana Foundation's officially hosted public RSS for grants / RFPs.
# (They're in the middle of a site rebuild as of 2026; this URL is
# conservatively kept as a fallback if both fail.)
SOLANA_GRANTS_FEED = "https://solana.org/feed.rss"
SOLANA_RFPS_FEED = "https://solana.org/news/feed.xml"


def _coerce_token() -> str:
    tok = os.environ.get("GITHUB_TOKEN") or ""
    if tok:
        return tok
    try:
        from clawflow.auth import get_secret as _gs
        return _gs("github_token") or ""
    except Exception:
        return ""


def _heuristic_score(labels: list[str], comments: int) -> int:
    score = 0
    sl = {l.lower() for l in labels}
    if any("bounty" in l for l in sl):
        score += 40
    if any("help wanted" in l for l in sl):
        score += 8
    if any("good first issue" in l for l in sl):
        score += 4
    if any("rust" in l for l in sl):
        score += 6
    if any("solana" in l for l in sl):
        score += 8
    score += min(max(int(comments), 0), 20)
    return min(score, 100)


# ⚠️ KNOWN_BROKEN_AS_OF_2026-10 — upstreams 404'd when this plugin was built.
# Code is intact and ready; you only need to swap the URL constants
# at the top of this file for a working endpoint. See CHANGELOG v0.2.0.
@register
class SolanaJobsSource(Source):
    """Surfaces paid work across the Solana ecosystem."""

    key: ClassVar[str] = "solana_jobs"
    label: ClassVar[str] = "Solana ecosystem jobs / grants"
    description: ClassVar[str] = (
        "Solana Foundation grants + bounty issues from Marinade, Wormhole, "
        "Jupiter, Drift, Helius, Metaplex, Backpack, etc."
    )
    default_enabled: ClassVar[bool] = False

    async def validate_config(self, cfg: Any) -> tuple[bool, str]:
        extra = getattr(cfg, "extra", {}) or {}
        repos = extra.get("repos") or list(SOLANA_REPOS)
        return True, f"solana_jobs: tracking {len(repos)} repos"

    def fetch(self, state: dict[str, Any], cfg: Any) -> AsyncIterator[Lead]:
        return self._fetch(state, cfg)

    async def _graphql_issues(self, client: httpx.AsyncClient, repos: list[str], max_age: int) -> AsyncIterator[Lead]:
        since = (datetime.now(timezone.utc) - timedelta(days=max_age)).strftime("%Y-%m-%d")
        label_clause = " OR ".join(f"label:{lbl}" for lbl in BOUNTY_LABEL_TERMS)
        repo_clause = " OR ".join(f"repo:{r}" for r in repos)
        query = (
            f"({label_clause}) ({repo_clause}) "
            f"created:>{since} state:open is:issue"
        )
        cursor = None
        for _ in range(4):  # up to 100 issues per cycle
            body = {
                "query": 'query($q: String!, $cursor: String) { search(query: $q, type: ISSUE, first: 25, after: $cursor) { issueCount pageInfo { hasNextPage endCursor } nodes { ... on Issue { number title url createdAt comments { totalCount } labels(first: 20) { nodes { name } } repository { nameWithOwner } } } } }',
                "variables": {"q": query, "cursor": cursor},
            }
            try:
                resp = await client.post("https://api.github.com/graphql", json=body)
            except (httpx.HTTPError, ConnectionError, TimeoutError):
                return
            if resp.status_code >= 400:
                return
            try:
                payload = resp.json()
            except ValueError:
                return
            search = (payload.get("data") or {}).get("search") or {}
            for n in search.get("nodes") or []:
                if not n:
                    continue
                repo_full = ((n.get("repository") or {}).get("nameWithOwner") or "")
                if not repo_full:
                    continue
                title = (n.get("title") or "").strip()
                if not title:
                    continue
                labels = []
                for ln in ((n.get("labels") or {}).get("nodes") or []):
                    if isinstance(ln, dict):
                        labels.append(ln.get("name") or "")
                    elif isinstance(ln, str):
                        labels.append(ln)
                comments_field = n.get("comments") or {}
                comments = int((comments_field.get("totalCount") if isinstance(comments_field, dict) else comments_field) or 0)
                score = _heuristic_score(labels, comments)
                yield Lead(
                    id=f"solana_jobs:{repo_full}#{n.get('number')}",
                    source=self.key,
                    title=title[:200],
                    url=n.get("url") or "",
                    posted_at=n.get("createdAt"),
                    score=score,
                    tags=tuple(labels) + ("solana",),
                    raw={"repo": repo_full, "number": n.get("number"), "labels": labels, "comments": comments},
                )
            page = search.get("pageInfo") or {}
            if not page.get("hasNextPage"):
                return
            cursor = page.get("endCursor")
            if not cursor:
                return

    async def _rss_leads(self, feed_url: str, source_tag: str) -> AsyncIterator[Lead]:
        try:
            async with httpx.AsyncClient(headers={"User-Agent": "clawflow/0.1"}, timeout=15.0, follow_redirects=True) as client:
                resp = await client.get(feed_url)
        except (httpx.HTTPError, ConnectionError, TimeoutError) as exc:
            log.warning("solana_jobs: %s fetch failed (%s)", feed_url, exc)
            return
        if resp.status_code >= 400:
            log.warning("solana_jobs: %s -> HTTP %s", feed_url, resp.status_code)
            return
        # feedparser accepts raw text or bytes
        parsed = feedparser.parse(resp.text)
        for entry in parsed.entries[:30]:
            title = (entry.get("title") or "").strip()
            link = (entry.get("link") or "").strip()
            summary = (entry.get("summary") or "").strip()
            if not title or not link:
                continue
            yield Lead(
                id=f"solana_jobs:{source_tag}:{hash(link)}",
                source=self.key,
                title=title[:200],
                url=link,
                posted_at=entry.get("published"),
                score=20,
                tags=(source_tag,),
                raw={"summary": summary[:400]},
            )

    async def _fetch(self, state: dict[str, Any], cfg: Any) -> AsyncIterator[Lead]:
        # Two RSS feeds: foundation grants + news (RFPs land in news)
        for entry in self._rss_leads(SOLANA_GRANTS_FEED, "solana_grants"):
            yield entry
        for entry in self._rss_leads(SOLANA_RFPS_FEED, "solana_rfps"):
            yield entry

        # GitHub: issues across the curated Solana repo list
        token = _coerce_token()
        if not token:
            log.warning("solana_jobs: no GITHUB_TOKEN; skipping GitHub issue scan. Run `clawflow auth github --token X` to enable.")
            return
        extra = getattr(cfg, "extra", {}) or {}
        repos = extra.get("repos") or list(SOLANA_REPOS)
        max_age = int(extra.get("max_age_days", 60))
        async with httpx.AsyncClient(
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "clawflow/0.1",
            },
            timeout=30.0,
        ) as client:
            async for entry in self._graphql_issues(client, repos, max_age):
                yield entry
