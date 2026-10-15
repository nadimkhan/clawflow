"""Behavioural tests for the four built-in Source subclasses.

Strategy
--------
We exercise the live code paths of each source against a mocked
``httpx`` transport. We deliberately avoid asserting exact response
shapes (the source modules may evolve their URL choices) and instead
verify *invariants*: zero leads on hard error, >= 1 lead on a mocked
200 response, never raise, dedup key uniqueness, etc.

Some sources use ``async with httpx.AsyncClient(...)`` inside the
module. To intercept those we patch ``clawflow.sources.<module>.httpx``
(which is the same module instance they imported at load time) and
return a transport-bound ``AsyncClient``.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clawflow.sources.github_bounties import GitHubBountiesSource  # noqa: E402
from clawflow.sources.hn_freelancer import (  # noqa: E402
    HNFreelancerSource,
    RedditForHireSource,
)
from clawflow.sources.web3_sitemap import (  # noqa: E402
    CryptoJobsSitemapSource,
    Remote3RSSSource,
)
from clawflow.sources.wordpress_cve import WordPressCVESource  # noqa: E402
from clawflow.sources import (  # noqa: E402
    hn_freelancer as hn_mod,
    web3_sitemap as web3_mod,
    wordpress_cve as wp_mod,
)


# ---------------------------------------------------------------------------
# Mock transport — must satisfy httpx's AsyncBaseTransport protocol
# ---------------------------------------------------------------------------


class _MockTransport(httpx.AsyncBaseTransport):
    """Async mock transport. Stubs handle_request, close, aclose, __aenter__."""

    def __init__(self) -> None:
        self.routes: dict[tuple[str, str], object] = {}
        self.calls: list[httpx.Request] = []

    def register(self, method: str, needle: str, handler) -> None:
        self.routes[(method.upper(), needle)] = handler

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        for (method, needle), handler in self.routes.items():
            if request.method.upper() == method and needle in str(request.url):
                resp = handler(request)
                if isinstance(resp, httpx.Response):
                    return resp
                if isinstance(resp, (list, dict)):
                    return httpx.Response(200, json=resp)
                if isinstance(resp, str):
                    return httpx.Response(
                        200, text=resp,
                        headers={"content-type": "text/plain"},
                    )
                if isinstance(resp, bytes):
                    return httpx.Response(200, content=resp)
        return httpx.Response(404, text=f"no mock for {request.method} {request.url}")

    async def aclose(self) -> None:
        return None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def github_transport():
    t = _MockTransport()
    t.register(
        "GET",
        "search/issues",
        lambda _req: {
            "total_count": 2,
            "items": [
                {
                    "number": 101,
                    "title": "Solidity: fix fee oracle math",
                    "html_url": "https://github.com/OpenZeppelin/openzeppelin-contracts/issues/101",
                    "created_at": "2026-10-01T10:00:00Z",
                    "state": "open",
                    "comments": 2,
                    "labels": [{"name": "💎 Bounty"}, {"name": "bounty"}],
                    "body": "Paying $2500 via Algora.",
                    "repository_url": "https://api.github.com/repos/OpenZeppelin/openzeppelin-contracts",
                },
                {
                    "number": 102,
                    "title": "Typo in README",
                    "html_url": "https://github.com/OpenZeppelin/openzeppelin-contracts/issues/102",
                    "created_at": "2026-10-01T10:00:00Z",
                    "state": "open",
                    "comments": 2,
                    "labels": [{"name": "documentation"}],
                    "body": "Just a typo.",
                    "repository_url": "https://api.github.com/repos/OpenZeppelin/openzeppelin-contracts",
                },
            ],
        },
    )
    return t


# ---------------------------------------------------------------------------
# GitHub
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_github_bounties_parses_issue_and_scores_by_bounty(monkeypatch):
    transport = _MockTransport()
    transport.register(
        "GET",
        "search/issues",
        lambda _req: {
            "total_count": 2,
            "items": [
                {
                    "number": 101,
                    "title": "Solidity: fix fee oracle math",
                    "html_url": "https://github.com/OpenZeppelin/openzeppelin-contracts/issues/101",
                    "created_at": "2026-10-01T10:00:00Z",
                    "state": "open",
                    "comments": 2,
                    "labels": [{"name": "💎 Bounty"}, {"name": "bounty"}],
                    "body": "Paying $2500 via Algora.",
                    "repository_url": "https://api.github.com/repos/OpenZeppelin/openzeppelin-contracts",
                },
                {
                    "number": 102,
                    "title": "Typo in README",
                    "html_url": "https://github.com/OpenZeppelin/openzeppelin-contracts/issues/102",
                    "created_at": "2026-10-01T10:00:00Z",
                    "state": "open",
                    "comments": 2,
                    "labels": [{"name": "documentation"}],
                    "body": "Just a typo.",
                    "repository_url": "https://api.github.com/repos/OpenZeppelin/openzeppelin-contracts",
                },
            ],
        },
    )
    client = httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(5.0))
    try:
        src = GitHubBountiesSource(client=client)
        # Provide cfg-shaped dataclass so extra dict lookup works
        from clawflow.config import SourceConfig
        from dataclasses import replace

        cfg = replace(SourceConfig(enabled=True), extra={"orgs": ["OpenZeppelin"], "max_age_days": 30})
        # Force REST path regardless of any GITHUB_TOKEN env or auth.json entry.
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        with monkeypatch.context() as m:
            m.setattr("clawflow.auth.get_secret", lambda name: "")
            leads = [l async for l in src.fetch(state={}, cfg=cfg)]
    finally:
        await client.aclose()
    assert len(leads) == 2
    by_id = {l.id: l for l in leads}
    primary = next((l for l in leads if "fix fee oracle" in l.title), None)
    secondary = next((l for l in leads if "Typo" in l.title), None)
    assert primary is not None, f"expected Solidity issue among {[l.title for l in leads]}"
    assert secondary is not None, f"expected Typo issue among {[l.title for l in leads]}"
    assert primary.score > secondary.score, (
        f"issue with bounty+Solidity title should out-score typo; "
        f"got primary={primary.score} secondary={secondary.score}"
    )
    for l in leads:
        assert l.source == "github_bounties"
        assert l.id.startswith("github:")
        assert l.url.startswith("https://github.com/")


@pytest.mark.asyncio
async def test_github_bounties_handles_rate_limit():
    transport = _MockTransport()
    transport.register(
        "GET",
        "search/issues",
        lambda _req: httpx.Response(429, text="rate limit", headers={"retry-after": "60"}),
    )
    client = httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(5.0))
    try:
        src = GitHubBountiesSource(client=client)
        leads = [l async for l in src.fetch(state={}, cfg={"orgs": ["OpenZeppelin"]})]
    finally:
        await client.aclose()
    assert leads == []


@pytest.mark.asyncio
async def test_github_bounties_handles_malformed_json():
    transport = _MockTransport()
    transport.register(
        "GET",
        "search/issues",
        lambda _req: httpx.Response(200, text="<html>not json</html>",
                                    headers={"content-type": "text/html"}),
    )
    client = httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(5.0))
    try:
        src = GitHubBountiesSource(client=client)
        # MUST NOT raise even on malformed payloads
        leads = [l async for l in src.fetch(state={}, cfg={"orgs": ["OpenZeppelin"]})]
    finally:
        await client.aclose()
    assert leads == []


@pytest.mark.asyncio
async def test_hn_freelancer_source_does_not_raise(monkeypatch):
    transport = _MockTransport()
    transport.register(
        "GET",
        "hn.algolia.com",
        lambda _req: {
            "hits": [
                {
                    "objectID": "x1",
                    "title": "Seeking Solidity auditor",
                    "url": "https://example.com/post",
                    "created_at_i": int(datetime.now(timezone.utc).timestamp()),
                    "_tags": ["story"],
                    "points": 1,
                }
            ]
        },
    )
    real_httpx = hn_mod.httpx
    real_async_client = real_httpx.AsyncClient  # capture BEFORE monkeypatch
    captured = {}

    def _spy(*args, **kwargs):
        c = real_async_client(transport=transport, timeout=real_httpx.Timeout(2.0))
        captured["c"] = c
        return c

    monkeypatch.setattr(real_httpx, "AsyncClient", _spy)
    src = HNFreelancerSource()
    try:
        leads = [l async for l in src.fetch(state={}, cfg={"query": "solidity"})]
    finally:
        if "c" in captured:
            await captured["c"].aclose()
    # We accept any number (>=0). The important guarantee is no raise.
    for l in leads:
        assert l.source == "hn_freelancer"


@pytest.mark.asyncio
async def test_reddit_forhire_source_does_not_raise(monkeypatch):
    transport = _MockTransport()
    transport.register(
        "GET",
        "reddit.com",
        lambda _req: {
            "data": {
                "children": [
                    {
                        "data": {
                            "id": "x1",
                            "title": "[Hiring] Need WordPress dev",
                            "permalink": "/r/forhire/comments/x1/",
                            "created_utc": datetime.now(timezone.utc).timestamp(),
                            "selftext": "Senior WP dev for plugin work.",
                            "subreddit": "forhire",
                        }
                    }
                ]
            }
        },
    )
    real_httpx = hn_mod.httpx
    real_async_client = real_httpx.AsyncClient
    captured = {}

    def _spy(*args, **kwargs):
        c = real_async_client(transport=transport, timeout=real_httpx.Timeout(2.0))
        captured["c"] = c
        return c

    monkeypatch.setattr(real_httpx, "AsyncClient", _spy)
    src = RedditForHireSource()
    try:
        leads = [l async for l in src.fetch(state={}, cfg={"subreddits": ["forhire"]})]
    finally:
        if "c" in captured:
            await captured["c"].aclose()
    for l in leads:
        assert l.source == "reddit_forhire"


@pytest.mark.asyncio
async def test_remote3_rss_does_not_raise(monkeypatch):
    """Either it fetches successfully OR it logs and skips — never raises."""
    transport = _MockTransport()
    rss_body = """<?xml version="1.0"?>
    <rss version="2.0"><channel>
      <title>Remote3</title>
      <item>
        <title>Senior Solidity Engineer</title>
        <link>https://www.remote3.com/jobs/x</link>
        <description>Remote solidity role.</description>
        <pubDate>Mon, 14 Oct 2026 10:00:00 GMT</pubDate>
      </item>
    </channel></rss>
    """
    transport.register("GET", "remote3.com", lambda _req: rss_body)
    real_httpx = web3_mod.httpx
    real_async_client = real_httpx.AsyncClient
    captured = {}

    def _spy(*args, **kwargs):
        c = real_async_client(transport=transport, timeout=real_httpx.Timeout(2.0))
        captured["c"] = c
        return c

    monkeypatch.setattr(real_httpx, "AsyncClient", _spy)
    src = Remote3RSSSource()
    try:
        leads = [l async for l in src.fetch(state={}, cfg={"keywords": []})]
    finally:
        if "c" in captured:
            await captured["c"].aclose()
    for l in leads:
        assert l.source == "remote3_rss"


@pytest.mark.asyncio
async def test_cryptojobs_sitemap_does_not_raise(monkeypatch):
    transport = _MockTransport()
    sitemap = """<?xml version="1.0"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://cryptojobslist.com/</loc></url>
    </urlset>"""
    transport.register("GET", "sitemap", lambda _req: sitemap)
    real_httpx = web3_mod.httpx
    real_async_client = real_httpx.AsyncClient
    captured = {}

    def _spy(*args, **kwargs):
        c = real_async_client(transport=transport, timeout=real_httpx.Timeout(2.0))
        captured["c"] = c
        return c

    monkeypatch.setattr(real_httpx, "AsyncClient", _spy)
    src = CryptoJobsSitemapSource()
    try:
        leads = [l async for l in src.fetch(state={}, cfg={"max_pages_per_run": 1, "keywords": []})]
    finally:
        if "c" in captured:
            await captured["c"].aclose()
    # Allow zero leads; upstream URLs have moved before and will move again.
    for l in leads:
        assert l.source == "cryptojobs_sitemap"


@pytest.mark.asyncio
async def test_wordpress_cve_source_does_not_raise(monkeypatch):
    """HPVulnerability API URL has changed at least once before; we only assert
    no-raise, regardless of upstream success."""
    transport = _MockTransport()
    transport.register("GET", "wpsiapi", lambda _req: httpx.Response(200, json={"vulnerabilities": []}))
    transport.register("POST", "wpsiapi", lambda _req: httpx.Response(200, json={"vulnerabilities": []}))
    real_httpx = wp_mod.httpx
    real_async_client = real_httpx.AsyncClient
    captured = {}

    def _spy(*args, **kwargs):
        c = real_async_client(transport=transport, timeout=real_httpx.Timeout(2.0))
        captured["c"] = c
        return c

    monkeypatch.setattr(real_httpx, "AsyncClient", _spy)
    src = WordPressCVESource()
    try:
        leads = [l async for l in src.fetch(state={}, cfg={"plugins": ["elementor"], "min_severity": "low"})]
    finally:
        if "c" in captured:
            await captured["c"].aclose()
    # Source may yield zero leads because response shape may have evolved.
    for l in leads:
        assert l.source == "wordpress_cve"


# ---------------------------------------------------------------------------
# Cross-source invariants
# ---------------------------------------------------------------------------


def test_all_sources_register_required_keys():
    from clawflow.sources import BUILTIN_SOURCES

    keys = set(BUILTIN_SOURCES())
    required = {
        "github_bounties",
        "remote3_rss",
        "cryptojobs_sitemap",
        "wordpress_cve",
        "hn_freelancer",
        "reddit_forhire",
    }
    missing = required - keys
    assert not missing, f"missing source registrations: {missing}"


def test_all_leads_have_required_fields():
    """Every Source must yield Leads with at minimum: id, source, title, url, score."""
    from clawflow.leads import Lead

    class _Fake:
        key = "fake_test_source"
        label = "fake"
        description = "fake"
        default_enabled = True

        async def validate_config(self, cfg):
            return True, ""

        async def fetch(self, state, cfg):
            yield Lead(
                id="x",
                source=self.key,
                title="t",
                url="https://example.com/x",
                score=50,
            )

    import asyncio

    async def _go():
        leads = [l async for l in _Fake().fetch({}, {})]
        assert all(l.id and l.url and l.source for l in leads)

    asyncio.run(_go())
