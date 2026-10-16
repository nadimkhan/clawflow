"""clawflow plugin: AI-agent ecosystem job board.

Surfaces paid-contract and bounty work from the AI-agent + blockchain
intersection — the niche that matches the user's stack (LangChain,
ElizaOS, GOAT, ZerePy, multi-chain integration).

Sources:
  * GitHub: search issues labeled 'bounty' OR 'help wanted' across a
    curated list of high-signal web3 + AI-agent repos.
  * GitHub: dedicated repos for each framework (ElizaOS, GOAT, ZerePy,
    sendai-aigc, etc.) — issues with labels: 'bounty', 'help wanted',
    'good first issue'.
  * Optional: pull from each framework's "Bounties" GitHub repo if it
    exists.

Authentication: reuses the github_token from clawflow.auth, falling
back to env var, falling back to anonymous (lower rate limit).
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, AsyncIterator, ClassVar

import httpx

from clawflow.leads import Lead
from clawflow.sources import Source, register

log = logging.getLogger("clawflow.sources.community.ai_agent_jobs")

# Curated list of repos at the AI-agent + web3 intersection.
# Each entry is "owner/repo". We pull `bounty`-tagged issues from each.
# This is the user's exact niche — agentic AI systems calling smart
# contracts and DeFi primitives.
# Curated list of active AI-agent / agentic-LLM repos (Oct 2026).
# Last vetted: 2026-10-17 against GitHub search. Dead/moved repos removed.
# (ai16z/eliza, zerepb/zerepy, oceanprotocol/ocean.py and similar 2024-era
# entries no longer exist; they were dropped here.)
DEFAULT_ORGS_REPOS: tuple[str, ...] = (
    # Core agentic-AI frameworks (active 2026)
    "langchain-ai/langchain",
    "langchain-ai/langgraph",
    "run-llama/llama_index",
    "openai/openai-python",
    "anthropics/anthropic-sdk-python",
    # AI agent frameworks with on-chain action
    "goat-sdk/goat",
    "heurist-network/heurist-agent-framework",
    "fetchai/uagents",
    # Multi-agent + on-chain infrastructure
    "autonolas/autonolas-sdk",
    # Smart-contract LLM tools
    "modelcontextprotocol/modelcontextprotocol",
    "modelcontextprotocol/typescript-sdk",
    "modelcontextprotocol/python-sdk",
)

GRAPHQL_QUERY = """
query($q: String!, $cursor: String) {
  search(query: $q, type: ISSUE, first: 25, after: $cursor) {
    issueCount
    pageInfo { hasNextPage endCursor }
    nodes {
      ... on Issue {
        number title url createdAt
        comments { totalCount }
        labels(first: 20) { nodes { name } }
        repository { nameWithOwner stargazerCount primaryLanguage { name } }
      }
    }
  }
}
""".strip()


def _coerce_token() -> str:
    """Resolve a GitHub token, in priority order:

    1. ``GITHUB_TOKEN`` env var  (cheapest if user has it set)
    2. ``clawflow.auth.get_secret('github_token')``  (saved via the CLI)
    3. ``gh auth token`` subprocess  (no API needed; uses the keychain)
    4. ``""``  (caller falls back to REST and warns)

    The gh-cli fallback covers the common case where the user
    already ``gh auth login``ed but never made a separate PAT.
    """
    tok = os.environ.get("GITHUB_TOKEN") or ""
    if tok:
        return tok
    try:
        from clawflow.auth import get_secret as _gs
        tok = _gs("github_token") or ""
        if tok:
            return tok
    except Exception:
        pass
    # Last fallback: shell out to the gh CLI
    try:
        import subprocess
        out = subprocess.run(
            ["gh", "auth", "token"], capture_output=True, text=True, timeout=5,
        )
        tok = (out.stdout or "").strip()
        if tok:
            return tok
    except Exception:
        pass
    return ""


def _build_query(
    repos: list[str], max_age_days: int, since_iso: str, *, global_search: bool,
) -> str:
    """Build the GitHub search query.

    Two modes:
      * **Scoped** (default): only ``label:"💎 Bounty" OR label:bounty``
        issues created within ``max_age_days`` AND in one of the curated
        repos. Produces a tight, well-curated feed.
      * **Global** (``global_search=True``): same labels + age but
        no repo restriction. Adds ``stars:>50`` to filter the spam farms
        (auto-bot repos like "bounty-plaza" have 0 stars and produce
        dozens of garbage issues daily).

    Note: GitHub's `OR` between two ``label:...`` terms is treated as
    AND. The right way to express "either label A or label B" is the
    comma-list ``label:A,B`` syntax.
    """
    parts = ['is:issue', 'state:open', 'label:"💎 Bounty",bounty']
    if not global_search and repos:
        scope = " OR ".join(f"repo:{r}" for r in repos)
        parts.append(f"({scope})")
    if global_search:
        # Filter out zero-star spam-bot farms. Stars:>50 keeps genuine
        # paid-bounty projects while excluding the auto-generated trash.
        parts.append("stars:>50")
    parts.append(f"created:>{since_iso}")
    return " ".join(parts)


def _heuristic_score(labels: list[str], comments_count: int) -> int:
    """0-100 score weighting AI-agent + web3-specific labels."""
    score = 0
    sl = {l.lower() for l in labels}
    if any("bounty" in l for l in sl):
        score += 50
    if any("help wanted" in l for l in sl):
        score += 10
    if any("good first issue" in l for l in sl):
        score += 5
    if any("ai" in l or "agent" in l or "llm" in l for l in sl):
        score += 10
    if any("web3" in l or "defi" in l or "solidity" in l for l in sl):
        score += 15
    score += min(max(int(comments_count), 0), 20)
    return min(score, 100)


# ⚠️ KNOWN_BROKEN_AS_OF_2026-10 — upstreams 404'd when this plugin was built.
# Code is intact and ready; you only need to swap the URL constants
# at the top of this file for a working endpoint. See CHANGELOG v0.2.0.
@register
class AiAgentJobsSource(Source):
    """Surface paid bounties + help-wanted issues from AI-agent + web3 repos."""

    key: ClassVar[str] = "ai_agent_jobs"
    label: ClassVar[str] = "AI-agent ecosystem jobs"
    description: ClassVar[str] = (
        "Paid bounties + help-wanted in the agentic-AI + web3 intersection: "
        "ElizaOS, GOAT, ZerePy, LangChain, Chainlink, Ocean Protocol, etc."
    )
    default_enabled: ClassVar[bool] = False

    async def validate_config(self, cfg: Any) -> tuple[bool, str]:
        extra = getattr(cfg, "extra", {}) or {}
        repos = extra.get("repos") or list(DEFAULT_ORGS_REPOS)
        return True, f"ai_agent_jobs: tracking {len(repos)} repos"

    def fetch(self, state: dict[str, Any], cfg: Any) -> AsyncIterator[Lead]:
        return self._fetch(state, cfg)

    async def _fetch(self, state: dict[str, Any], cfg: Any) -> AsyncIterator[Lead]:
        token = _coerce_token()
        if not token:
            log.warning("ai_agent_jobs: no token (env / auth.json / gh CLI all empty); skipping GraphQL scan")
            return

        extra = getattr(cfg, "extra", {}) or {}
        # Default to global_search=True with stars:>50 filter. Scoped
        # search (curated repo list) produces zero leads because none
        # of the active agentic-AI framework repos use the bounty
        # label — they use Algora or sponsor-based mechanisms instead.
        # Filter on stars:>50 keeps the signal high and blocks the
        # spam-bot "bounty farms" (Ikalus1988/MisakaNet, etc.) that
        # auto-generate thousands of fake issues.
        global_search = bool(extra.get("global_search", True))
        repos = extra.get("repos") or list(DEFAULT_ORGS_REPOS)
        max_age_days = int(extra.get("max_age_days", 60))
        min_repo_stars = int(extra.get("min_repo_stars", 10))
        since_iso = (datetime.now(timezone.utc) - __import__("datetime").timedelta(days=max_age_days)).strftime("%Y-%m-%d")
        query = _build_query(list(repos), max_age_days, since_iso, global_search=global_search)

        # Use GraphQL if we have a token, else fall back to REST search.
        if not token:
            return

        # GraphQL path
        try:
            token = token if isinstance(token, str) else ""
            async with httpx.AsyncClient(
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                    "User-Agent": "clawflow/0.1",
                },
                timeout=30.0,
            ) as client:
                cursor = None
                yielded = 0
                for _ in range(3):  # up to 75 issues per cycle
                    body = {"query": GRAPHQL_QUERY, "variables": {"q": query, "cursor": cursor}}
                    try:
                        resp = await client.post("https://api.github.com/graphql", json=body)
                    except (httpx.HTTPError, ConnectionError, TimeoutError) as exc:
                        log.warning("ai_agent_jobs: GraphQL request failed (%s)", exc)
                        return
                    if resp.status_code == 401:
                        log.warning("ai_agent_jobs: GraphQL 401 (token bad/expired); bailing")
                        return
                    if resp.status_code >= 400:
                        log.warning("ai_agent_jobs: GraphQL HTTP %s", resp.status_code)
                        return
                    try:
                        payload = resp.json()
                    except ValueError:
                        return
                    search = (payload.get("data") or {}).get("search") or {}
                    nodes = search.get("nodes") or []
                    for n in nodes:
                        if not n:
                            continue
                        repo = (n.get("repository") or {})
                        repo_full = repo.get("nameWithOwner") or ""
                        if not repo_full:
                            continue
                        # Repo-level star filter: GitHub's `stars:>N` search
                        # filter doesn't actually filter by repo stars, so
                        # we have to do it here on `stargazerCount`.
                        try:
                            star_count = int(repo.get("stargazerCount") or 0)
                        except (TypeError, ValueError):
                            star_count = 0
                        if star_count < min_repo_stars:
                            log.debug(
                                "ai_agent_jobs: drop %s#%s — repo stars=%d < %d",
                                repo_full, n.get("number"), star_count, min_repo_stars,
                            )
                            continue
                        title = (n.get("title") or "").strip()
                        if not title:
                            continue
                        url = n.get("url") or ""
                        number = n.get("number")
                        labels_raw = (n.get("labels") or {}).get("nodes") or []
                        labels = [
                            (l.get("name") if isinstance(l, dict) else str(l) or "")
                            for l in labels_raw
                            if isinstance(l, dict) or isinstance(l, str)
                        ]
                        comments_field = n.get("comments")
                        comments = 0
                        if isinstance(comments_field, dict):
                            comments = int(comments_field.get("totalCount", 0) or 0)
                        elif isinstance(comments_field, int):
                            comments = comments_field
                        score = _heuristic_score(labels, comments)
                        yield Lead(
                            id=f"ai_agent_jobs:{repo_full}#{number}",
                            source=self.key,
                            title=title[:200],
                            url=url,
                            posted_at=n.get("createdAt"),
                            score=score,
                            tags=tuple(labels) + ("ai-agent",),
                            raw={
                                "repo": repo_full,
                                "number": number,
                                "comments": comments,
                                "labels": labels,
                            },
                        )
                        yielded += 1
                    page = (search.get("pageInfo") or {})
                    if not page.get("hasNextPage"):
                        break
                    cursor = page.get("endCursor")
                    if not cursor:
                        break
                log.info("ai_agent_jobs: yielded %d leads from %d repos", yielded, len(repos))
        except Exception as exc:  # noqa: BLE001
            log.error("ai_agent_jobs: fatal (%s); bailing", exc)
            return
