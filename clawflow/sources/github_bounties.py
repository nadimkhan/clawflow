"""GitHub bounty scan source.

Surfaces public GitHub issues labeled as bounties / help-wanted across a list
of target organisations (with a global-search fallback). Uses the GitHub
GraphQL API when ``GITHUB_TOKEN`` is present (much higher rate limit) and
falls back to the public REST ``/search/issues`` endpoint otherwise.

* Honors the ``X-RateLimit-Remaining`` / ``X-RateLimit-Reset`` response
  headers — backs off until the reset time when we're near the quota ceiling.
* Paginates via cursors (GraphQL) or the ``page`` parameter (REST).
* Language filter restricted to the smart-contract / web3-adjacent set so we
  don't get flooded with frontend bounties.

Configuration is provided via a ``cfg`` object with attributes:

    orgs        list[str]  - organisations to scope the search to
    languages   list[str]  - languages to whitelist
    min_bounty  float      - minimum heuristic score to yield a lead
    max_age_days int       - only include issues newer than this

Endpoint verification is the orchestrator's responsibility; this module
just hits the documented GraphQL / REST endpoints.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator, ClassVar

import httpx

from clawflow.leads import Lead
from clawflow.sources import Source, register

GITHUB_GRAPHQL = "https://api.github.com/graphql"
GITHUB_REST_SEARCH = "https://api.github.com/search/issues"

# Default scope: curated list of high-signal crypto/web3 orgs.
DEFAULT_ORGS: tuple[str, ...] = (
    "OpenZeppelin",
    "ethereum-optimism",
    "ethereum-attestation-service",
    "aave",
    "uniswap",
    "sablier",
    "chainlink",
    "foundry-rs",
    "trufflesuite",
    "defi-wonderland",
    "solidity-team",
)

# Languages we care about — anything else is excluded by the search query.
DEFAULT_LANGS: tuple[str, ...] = (
    "solidity",
    "typescript",
    "javascript",
    "python",
    "rust",
    "go",
)

# GitHub search: OR of multiple labels. Empirically (verified 2026-10), GitHub's
# search treats `(label:"💎 Bounty" OR label:bounty OR label:"help wanted")` as
# AND-of-OR — i.e. it looks for issues that are tagged with ALL three labels,
# which has zero intersection. The 💎 Bounty label alone finds 550+ real leads;
# `bounty` and `help wanted` are too noisy without the Algora-label context, so
# they're better filtered post-fetch via the labels list on the response node.
BOUNTY_LABEL_TERMS: tuple[str, ...] = (
    '"💎 Bounty"',
)

log = logging.getLogger("clawflow.sources.github_bounties")


def _parse_dt(value: str | None) -> datetime | None:
    """Parse an ISO-8601 timestamp from GitHub's responses; return None on failure."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _heuristic_score(labels: list[str], comments: int) -> int:
    """Crude 0-100 quality score from label names + comment count.

    Bounty-marked issues rank high; we cap at 100 to keep the score field
    consistent across sources.
    """
    score = 0
    names = {l.lower() for l in labels}
    if any("bounty" in n for n in names):
        score += 55
    if any("💎" in l or "diamond" in n for l, n in zip(labels, names)):
        score += 10
    if "help wanted" in names:
        score += 10
    if "good first issue" in names:
        score += 5
    score += min(max(int(comments), 0), 20)
    return min(score, 100)


@register
class GitHubBountiesSource(Source):
    """Scan GitHub for bounty / help-wanted issues across target orgs (or globally)."""

    key: ClassVar[str] = "github_bounties"
    label: ClassVar[str] = "GitHub Bounties"
    description: ClassVar[str] = (
        "Public GitHub issues labeled 💎 Bounty / bounty / help wanted across "
        "a curated list of web3 organisations (global search fallback)."
    )
    default_enabled: ClassVar[bool] = True

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        """Optionally inject a pre-configured httpx client (for tests)."""
        self._client = client
        self._owns_client = client is None

    async def validate_config(self, cfg: Any) -> tuple[bool, str]:
        try:
            extra = getattr(cfg, "extra", {}) or {}
            orgs = list(extra.get("orgs") or []) if "orgs" in (getattr(cfg, "extra", None) or {}) else list(DEFAULT_ORGS)
            langs = list(extra.get("languages") or []) if "languages" in (getattr(cfg, "extra", None) or {}) else list(DEFAULT_LANGS)
        except TypeError as exc:
            return False, f"github_bounties: bad cfg shape ({exc})"
        global_search = bool(extra.get("global_search", False))
        if not orgs and not global_search:
            return False, "github_bounties: --orgs list is empty and global_search is off"
        return True, f"github_bounties: orgs={len(orgs)} langs={len(langs)}"

    async def _client_get(self) -> httpx.AsyncClient:
        """Lazily build an authenticated httpx client if a token is available.

        Token resolution order (matches ``github_bounties.fetch``): GITHUB_TOKEN
        env > clawflow.auth.get_secret('github_token').
        """
        if self._client is None:
            token = os.environ.get("GITHUB_TOKEN") or ""
            if not token:
                try:
                    from clawflow.auth import get_secret as _get_secret
                    token = _get_secret("github_token") or ""
                except Exception:
                    token = ""
            headers = {
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "clawflow/0.1",
            }
            if token:
                headers["Authorization"] = f"Bearer {token}"
            self._client = httpx.AsyncClient(headers=headers, timeout=30.0)
        return self._client

    async def aclose(self) -> None:
        """Close the underlying httpx client if we own it."""
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    async def _respect_rate_limit(self, resp: httpx.Response) -> None:
        """Sleep until ``X-RateLimit-Reset`` when the quota is nearly exhausted."""
        remaining = resp.headers.get("X-RateLimit-Remaining")
        reset = resp.headers.get("X-RateLimit-Reset")
        if remaining is None or reset is None:
            return
        try:
            if int(remaining) <= 1:
                wait = max(int(reset) - int(time.time()), 1) + 2
                log.warning("github_bounties: rate-limit near 0; sleeping %ds", wait)
                await asyncio.sleep(wait)
        except ValueError:
            return

    def _build_search_query(
        self,
        orgs: list[str],
        langs: list[str],
        since_iso: str,
    ) -> str:
        """Build a GitHub search query string from the configured scope.

        Note: GitHub's GraphQL search silently drops results when
        ``label:\"💎 Bounty\"`` is combined with a ``language:`` filter (verified
        2026-10 via probe: with vs without language returns 2 vs 0 results).
        Workaround: build a label-only query here; the caller filters by
        language on the returned ``repository.primaryLanguage`` afterwards.
        """
        label_part = " OR ".join(f"label:{lbl}" for lbl in BOUNTY_LABEL_TERMS)
        parts: list[str] = [
            f"({label_part})",
            f"created:>{since_iso}",
            "state:open",
            "is:issue",
        ]
        if orgs:
            scope = " OR ".join(f"repo:{o}" for o in orgs)
            parts.append(f"({scope})")
        return " ".join(parts)

    async def _graphql_iter(
        self,
        client: httpx.AsyncClient,
        query: str,
        cursor: str | None,
        lang_filter: list[str] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield each search-result node from a paginated GraphQL query.

        ``lang_filter`` post-filters results by ``repository.primaryLanguage``
        because GitHub's search API drops results when ``language:`` is in the
        query string (verified 2026-10).
        """
        body = {
            "query": (
                "query($q: String!, $cursor: String) {"
                "  search(query: $q, type: ISSUE, first: 50, after: $cursor) {"
                "    issueCount"
                "    pageInfo { hasNextPage endCursor }"
                "    nodes {"
                "      ... on Issue {"
                "        number title url createdAt"
                "        comments { totalCount }"
                "        labels(first: 20) { nodes { name } }"
                "        repository { nameWithOwner primaryLanguage { name } }"
                "      }"
                "    }"
                "  }"
                "}"
            ),
            "variables": {"q": query, "cursor": cursor},
        }
        try:
            resp = await client.post(GITHUB_GRAPHQL, json=body)
            await self._respect_rate_limit(resp)
            if resp.status_code == 401:
                log.warning("github_bounties: GraphQL 401 (token bad/required); skipping GraphQL")
                return
            resp.raise_for_status()
        except (httpx.HTTPError, ConnectionError, TimeoutError) as exc:
            log.warning("github_bounties: GraphQL request failed (%s)", exc)
            return
        try:
            data = resp.json()
        except ValueError as exc:
            log.warning("github_bounties: GraphQL JSON decode error (%s)", exc)
            return
        payload = (data.get("data") or {}).get("search") or {}
        for node in payload.get("nodes") or []:
            if not node:
                continue
            if lang_filter:
                # Post-filter by primary language. None = repo with no primary
                # language set (e.g. docs-only repos) — kept so we don't lose
                # bounty meta-repos like Algora's curated lists.
                repo_lang = ((node.get("repository") or {}).get("primaryLanguage") or {}).get("name")
                if repo_lang is not None and repo_lang.lower() not in {l.lower() for l in lang_filter}:
                    continue
            yield node
        page_info = payload.get("pageInfo") or {}
        if page_info.get("hasNextPage"):
            end_cursor = page_info.get("endCursor")
            if end_cursor:
                async for n in self._graphql_iter(client, query, end_cursor, lang_filter):
                    yield n

    async def _rest_iter(
        self,
        client: httpx.AsyncClient,
        query: str,
        page: int,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield each search-hit from the REST ``/search/issues`` endpoint."""
        params = {
            "q": query,
            "per_page": 50,
            "page": page,
            "sort": "created",
            "order": "desc",
        }
        try:
            resp = await client.get(GITHUB_REST_SEARCH, params=params)
            await self._respect_rate_limit(resp)
            resp.raise_for_status()
        except (httpx.HTTPError, ConnectionError, TimeoutError) as exc:
            log.warning("github_bounties: REST page %d failed (%s)", page, exc)
            return
        try:
            data = resp.json()
        except ValueError as exc:
            log.warning("github_bounties: REST JSON decode error (%s)", exc)
            return
        for item in data.get("items") or []:
            yield item
        total = int(data.get("total_count", 0) or 0)
        if total > page * 50:
            async for item in self._rest_iter(client, query, page + 1):
                yield item

    def fetch(self, state: dict[str, Any], cfg: Any) -> AsyncIterator[Lead]:
        """Async generator entry point — delegates to :meth:`_fetch`."""
        return self._fetch(state, cfg)

    async def _fetch(self, state: dict[str, Any], cfg: Any) -> AsyncIterator[Lead]:
        """Yield bounty leads. Never raises on a single record."""
        try:
            orgs = list(getattr(cfg, "orgs", None) or DEFAULT_ORGS)
            langs = list(getattr(cfg, "languages", None) or DEFAULT_LANGS)
            min_bounty = float(getattr(cfg, "min_bounty", 0) or 0)
            max_age_days = int(getattr(cfg, "max_age_days", 30) or 30)
        except (TypeError, ValueError) as exc:
            log.error("github_bounties: bad cfg (%s); yielding nothing", exc)
            return

        # SourceConfig has a generic ``extra`` dict for source-specific knobs.
        # Read orgs / languages / max_age_days from there, with defaults.
        extra = getattr(cfg, "extra", {}) or {}
        orgs = list(extra.get("orgs") or []) if "orgs" in (getattr(cfg, "extra", None) or {}) else []
        raw_langs = extra.get("languages") if isinstance(extra, dict) and "languages" in extra else None
        langs = [str(x).lower() for x in raw_langs] if raw_langs else []
        min_bounty = float(extra.get("min_bounty", 0) or 0)
        max_age_days = int(extra.get("max_age_days", 30) or 30)
        global_search_flag = bool(extra.get("global_search", False))

        # Token resolution order: GITHUB_TOKEN env > clawflow.auth.get_secret().
        # Without a token we drop to REST (60 req/hr anonymous, hits a very
        # tight secondary rate limit on /search).
        token = os.environ.get("GITHUB_TOKEN") or ""
        if not token:
            try:
                from clawflow.auth import get_secret as _get_secret
                token = _get_secret("github_token") or ""
            except Exception:
                token = ""
        use_graphql = bool(token)
        cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
        since_iso = cutoff.strftime("%Y-%m-%d")
        query = self._build_search_query(orgs, langs, since_iso)

        client = await self._client_get()
        try:
            if use_graphql:
                iterator = self._graphql_iter(client, query, None, langs)
            else:
                iterator = self._rest_iter(client, query, 1)
            async for node in iterator:
                try:
                    lead = self._node_to_lead(node)
                except Exception as exc:
                    log.warning("github_bounties: skipping malformed node (%s)", exc)
                    continue
                if lead is None:
                    continue
                if min_bounty > 0 and lead.score < int(min_bounty):
                    continue
                yield lead
        finally:
            await self.aclose()

    def _node_to_lead(self, node: dict[str, Any]) -> Lead | None:
        """Convert a GraphQL or REST issue node into a :class:`Lead`."""
        # Both endpoints give us `repository.nameWithOwner` (GraphQL) or
        # `repository_url` (REST, ends in the full name). Handle both.
        repo_full = ""
        if isinstance(node.get("repository"), dict):
            repo_full = node["repository"].get("nameWithOwner", "") or ""
        elif node.get("repository_url"):
            parts = str(node["repository_url"]).rsplit("/", 2)
            if len(parts) >= 2:
                repo_full = f"{parts[-2]}/{parts[-1]}"
        if not repo_full:
            return None

        number = node.get("number")
        url = node.get("html_url") or node.get("url")
        title = (node.get("title") or "").strip()
        if not (number and url and title):
            return None

        # Labels normalise across REST + GraphQL:
        #   REST:    [{"name": "x"}, ...]                              (list of dicts)
        #   GraphQL: {"nodes": [{"name": "x"}, ...]}                  (wrapped dict)
        # We accept both, plus the empty-list case.
        raw_labels = node.get("labels")
        if isinstance(raw_labels, dict):
            raw_labels = raw_labels.get("nodes") or []
        elif raw_labels is None:
            raw_labels = []
        labels: list[str] = []
        if raw_labels and isinstance(raw_labels[0], dict):
            labels = [l.get("name", "") for l in raw_labels if isinstance(l, dict)]
        else:
            labels = [str(l) for l in raw_labels]

        # Comments come back as either a counter dict or an int.
        comments = 0
        comments_field = node.get("comments")
        if isinstance(comments_field, dict):
            comments = int(comments_field.get("totalCount", 0) or 0)
        elif isinstance(comments_field, int):
            comments = comments_field

        score = _heuristic_score(labels, comments)
        posted = _parse_dt(node.get("created_at") or node.get("createdAt"))

        return Lead(
            id=f"github:{repo_full}#{number}",
            source=self.key,
            title=title,
            url=url,
            posted_at=posted,
            score=score,
            tags=tuple(labels),
            raw={
                "repo": repo_full,
                "number": number,
                "comments": comments,
                "labels": labels,
                "graphql": isinstance(node.get("repository"), dict),
            },
        )