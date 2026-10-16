"""clawflow plugin: Solana ecosystem jobs.

Surface paid engineering / engineering-lead / smart-contract work from
the Solana ecosystem. Two signals:

  1. **`jobs.solana.com`** — the official Solana Foundation job board,
     rendered server-side as a Next.js app. We scrape the company
     directory listing to pull `(company, job, slug, link)` tuples
     without touching any private API.

  2. **GitHub bounty issues** — active Solana ecosystem orgs (Helius,
     Wormhole, Metaplex, Agave, Backpack) occasionally post issues
     with the "bounty" label. We pull those via GitHub GraphQL with
     the same token-resolution as `ai_agent_jobs`.

  The previous version of this plugin pointed at the Solana Foundation
  RSS feeds which 404'd in 2026; those have been removed.

All free, no API key, no scraping of Discord/Telegram.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone, timedelta
from typing import Any, AsyncIterator, ClassVar

import httpx

from clawflow.leads import Lead
from clawflow.sources import Source, register

log = logging.getLogger("clawflow.sources.community.solana_jobs")

# Active Solana orgs on GitHub (2026-10). Trimmed to ones that have
# current issue activity. The Source runs an issue search via GraphQL.
SOLANA_REPOS: tuple[str, ...] = (
    "solana-labs/solana",
    "solana-foundation/solana-improvement-documents",
    "anza-xyz/agave",
    "wormhole-foundation/wormhole",
    "metaplex-foundation/metaplex-program-library",
    "coral-xyz/backpack",
)

# Public Solana jobs board. Server-rendered, no auth.
SOLANA_JOBS_BOARD = "https://jobs.solana.com"

# GitHub bounty / paid-issue search labels (comma list = OR).
BOUNTY_LABEL_CLAUSE = 'label:"💎 Bounty",bounty'


def _coerce_token() -> str:
    """Resolve a GitHub token.

    Tries:  env var → clawflow.auth token store → `gh auth token`.
    The gh-cli fallback covers users who ``gh auth login``ed but never
    made a separate PAT.
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


def _board_score(title: str) -> int:
    """Score a job posting from jobs.solana.com by title keywords.

    Seniority markers bump the score because the user is senior.
    """
    t = title.lower()
    score = 25  # base; it's already a Solana-org job
    if "senior" in t or "staff" in t or "principal" in t or "lead" in t:
        score += 30
    if "engineer" in t or "developer" in t or "architect" in t:
        score += 15
    if any(k in t for k in ("rust", "solidity", "smart contract", "defi", "zk")):
        score += 20
    if "junior" in t or "intern" in t or "graduate" in t:
        score -= 30
    if "research" in t or "protocol" in t:
        score += 10
    return max(0, min(score, 100))


async def _scrape_jobs_board(client: httpx.AsyncClient) -> AsyncIterator[Lead]:
    """Scrape jobs.solana.com for job postings.

    Note: Getro (the underlying board) only renders the full HTML with
    job links on the **root path** (/) and the /jobs listing page. The
    /companies sub-page is rendered without the per-company job URLs in
    its initial HTML, so we hit the root path instead.
    """
    try:
        resp = await client.get(f"{SOLANA_JOBS_BOARD}/", follow_redirects=True)
    except (httpx.HTTPError, ConnectionError, TimeoutError) as exc:
        log.warning("solana_jobs: board fetch failed (%s)", exc)
        return
    if resp.status_code >= 400:
        log.warning("solana_jobs: board -> HTTP %s", resp.status_code)
        return
    html = resp.text
    pattern = re.compile(
        r'/companies/([a-z0-9-]+)/jobs/(\d+)-([a-z0-9-]+)',
        re.IGNORECASE,
    )
    seen: set[tuple[str, str]] = set()
    for match in pattern.finditer(html):
        company_slug, job_id, title_slug = match.groups()
        key = (company_slug, job_id)
        if key in seen:
            continue
        seen.add(key)
        title = title_slug.replace("-", " ").strip()
        title = " ".join(t.capitalize() for t in title.split())
        url = f"{SOLANA_JOBS_BOARD}/companies/{company_slug}/jobs/{job_id}-{title_slug}"
        score = _board_score(title)
        yield Lead(
            id=f"solana_jobs:board:{company_slug}:{job_id}",
            source="solana_jobs",
            title=title[:200],
            url=url,
            posted_at=None,
            score=score,
            tags=("solana", "job-board", company_slug),
            raw={"company_slug": company_slug, "job_id": job_id},
        )


@register
class SolanaJobsSource(Source):
    """Surfaces paid Solana engineering roles from jobs.solana.com + GH issues."""

    key: ClassVar[str] = "solana_jobs"
    label: ClassVar[str] = "Solana ecosystem jobs"
    description: ClassVar[str] = (
        "Senior engineering / smart-contract / Rust roles from "
        "jobs.solana.com (Solana Foundation official job board) + "
        "bounty-tagged issues in active Solana org repos "
        "(solana-labs/solana, wormhole-foundation, metaplex, agave, backpack)."
    )
    default_enabled: ClassVar[bool] = False

    async def validate_config(self, cfg: Any) -> tuple[bool, str]:
        return True, f"solana_jobs: tracking {len(SOLANA_REPOS)} GitHub repos + jobs.solana.com"

    def fetch(self, state: dict[str, Any], cfg: Any) -> AsyncIterator[Lead]:
        return self._fetch(state, cfg)

    async def _fetch(self, state: dict[str, Any], cfg: Any) -> AsyncIterator[Lead]:
        # Use a real browser-style User-Agent. The Solana jobs board
        # (Getro) returns a fully-rendered HTML page only when it sees
        # a "real" UA. Bot UAs get the JS shell with no job listings.
        async with httpx.AsyncClient(
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/126.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            },
            timeout=20.0,
            follow_redirects=True,
        ) as client:
            async for lead in _scrape_jobs_board(client):
                yield lead

        # GitHub bounty issues — best-effort, requires a token.
        token = _coerce_token()
        if not token:
            log.debug("solana_jobs: no GH token; skipping GitHub bounty scan")
            return
        async for lead in self._scan_github(token):
            yield lead

    async def _scan_github(self, token: str) -> AsyncIterator[Lead]:
        since = (datetime.now(timezone.utc) - timedelta(days=60)).strftime("%Y-%m-%d")
        repo_clause = " OR ".join(f"repo:{r}" for r in SOLANA_REPOS)
        query = f"is:issue state:open {BOUNTY_LABEL_CLAUSE} ({repo_clause}) created:>{since}"
        GQL = """
        query($q: String!, $cursor: String) {
          search(query: $q, type: ISSUE, first: 25, after: $cursor) {
            pageInfo { hasNextPage endCursor }
            nodes {
              ... on Issue {
                number title url createdAt
                comments { totalCount }
                labels(first: 20) { nodes { name } }
                repository { nameWithOwner stargazerCount }
              }
            }
          }
        }
        """
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
            for _ in range(2):
                body = {"query": GQL, "variables": {"q": query, "cursor": cursor}}
                try:
                    resp = await client.post("https://api.github.com/graphql", json=body)
                except (httpx.HTTPError, ConnectionError, TimeoutError) as exc:
                    log.warning("solana_jobs: GraphQL request failed (%s)", exc)
                    return
                if resp.status_code >= 400:
                    log.warning("solana_jobs: GraphQL HTTP %s", resp.status_code)
                    return
                try:
                    payload = resp.json()
                except ValueError:
                    return
                search = (payload.get("data") or {}).get("search") or {}
                for n in search.get("nodes") or []:
                    if not n:
                        continue
                    repo = n.get("repository") or {}
                    repo_full = repo.get("nameWithOwner") or ""
                    if not repo_full:
                        continue
                    try:
                        stars = int(repo.get("stargazerCount") or 0)
                    except (TypeError, ValueError):
                        stars = 0
                    if stars < 10:
                        continue
                    title = (n.get("title") or "").strip()
                    if not title:
                        continue
                    labels = []
                    for ln in ((n.get("labels") or {}).get("nodes") or []):
                        if isinstance(ln, dict):
                            labels.append(ln.get("name") or "")
                    comments_field = n.get("comments") or {}
                    if isinstance(comments_field, dict):
                        comments = int(comments_field.get("totalCount") or 0)
                    else:
                        comments = int(comments_field or 0)
                    score = _heuristic_score(labels, comments)
                    yield Lead(
                        id=f"solana_jobs:gh:{repo_full}#{n.get('number')}",
                        source=self.key,
                        title=title[:200],
                        url=n.get("url") or "",
                        posted_at=n.get("createdAt"),
                        score=score,
                        tags=tuple(labels) + ("solana",),
                        raw={"repo": repo_full, "number": n.get("number"), "labels": labels, "comments": comments, "stars": stars},
                    )
                page = search.get("pageInfo") or {}
                if not page.get("hasNextPage"):
                    return
                cursor = page.get("endCursor")
                if not cursor:
                    return
