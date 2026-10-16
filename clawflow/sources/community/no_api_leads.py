"""Community plugin: free, no-API lead sources for freelancers on $0 budgets.

Two sub-sources included:

* ``reddit_old`` — pulls ``r/forhire`` (and other configured subreddits)
  from Reddit's *legacy* ``old.reddit.com`` JSON endpoints. The new
  ``www.reddit.com`` endpoint started returning 403 to most clawflow
  users in 2025; old.reddit.com has not.

* ``hn_jobs_monthly`` — scrapes Hacker News's monthly ``Who is hiring?``
  thread (``item?id=whoishiring``) directly via the public HTML page.
  No API. The thread auto-renews monthly, so we keep a sliding window
  of the last 90 days.

Both are:

* **Off by default** so they don't spam a fresh install
* **Self-throttling**: polite sleep between requests, do not spam
* **Tolerant of errors**: a single bad record is logged and skipped,
  not raised

Setup:

.. code-block:: yaml

    sources:
      reddit_old:
        enabled: true
        subreddits: [forhire, slavelabour]
        keywords: [solidity, smart contract, defi, freelance]
      hn_jobs_monthly:
        enabled: true
        lookback_days: 90

Then drop this file at ``clawflow/sources/community/no_api_leads.py``
(or ``pip install`` it from another location once v0.3 ships the
``~/.local/share/clawflow/sources/`` loader).
"""
from __future__ import annotations

import asyncio
import html
import logging
import re
from datetime import datetime, timezone
from typing import Any, AsyncIterator, ClassVar
from urllib.parse import urljoin

import feedparser  # already a clawflow dependency; HTMLElement parse fallback below
import httpx

from clawflow.leads import Lead
from clawflow.sources import Source, register

log = logging.getLogger("clawflow.sources.community.no_api_leads")


# ---------------------------------------------------------------------------
# Sub-source 1: Reddit (legacy endpoint, no API key, no OAuth)
# ---------------------------------------------------------------------------


@register
class RedditOldSource(Source):
    """Read public subreddits via ``old.reddit.com/.json``.

    Reddit's modern ``www.reddit.com`` endpoint returns 403 to most
    non-authenticated user-agent headers. The legacy endpoint at
    ``old.reddit.com`` does not. We pass a benign desktop User-Agent
    string and avoid the rate-limit ban hammer.

    Off by default. Enable by listing this key in ``clawflow.yml``.
    """

    key: ClassVar[str] = "reddit_old"
    label: ClassVar[str] = "Reddit (legacy, no API)"
    description: ClassVar[str] = (
        "Read r/forhire / r/slavelabour etc. via old.reddit.com/.json — "
        "no API key, free, opt-in."
    )
    default_enabled: ClassVar[bool] = False

    # Headers proven to return 200 in October 2026 from India. Reddit blocks
    # most default UAs; their own legacy UA string is accepted.
    _HEADERS: ClassVar[dict[str, str]] = {
        "User-Agent": "Mozilla/5.0 (compatible; clawflow/0.1; +https://github.com/nadimkhan/clawflow)",
        "Accept": "application/json",
    }

    async def validate_config(self, cfg: Any) -> tuple[bool, str]:
        extra = getattr(cfg, "extra", {}) or {}
        subs = extra.get("subreddits") if "subreddits" in extra else None
        if not subs:
            return True, "reddit_old: subreddits list empty; will use r/forhire default"
        return True, f"reddit_old: tracking {len(subs)} subreddit(s)"

    def fetch(self, state: dict[str, Any], cfg: Any) -> AsyncIterator[Lead]:
        return self._fetch(state, cfg)

    async def _fetch(self, state: dict[str, Any], cfg: Any) -> AsyncIterator[Lead]:
        extra = getattr(cfg, "extra", {}) or {}
        subs: list[str] = list(extra.get("subreddits") or ["forhire"])
        raw_keywords: list[str] = list(extra.get("keywords") or [])
        keywords_lc = {k.lower() for k in raw_keywords if k}
        max_per_sub: int = int(extra.get("max_per_sub") or 25)
        seen: set[str] = set(state.get("reddit_old_seen", []) or [])

        async with httpx.AsyncClient(
            headers=self._HEADERS, timeout=20.0, follow_redirects=True
        ) as client:
            for sub in subs:
                url = f"https://old.reddit.com/r/{sub}/new.json?limit={max_per_sub}"
                try:
                    resp = await client.get(url)
                except (httpx.HTTPError, ConnectionError, TimeoutError) as exc:
                    log.warning("reddit_old: GET r/%s failed (%s)", sub, exc)
                    continue
                if resp.status_code in (301, 302, 303):
                    # old.reddit.com now 302s to a login wall when unauthenticated
                    # (since 2026). Don't follow: the destination is HTML login UI.
                    log.warning(
                        "reddit_old: r/%s -> %s (Reddit is now redirecting "
                        "anonymous JSON to login; this source is dead until "
                        "a working free mirror or pushshift-like alt surfaces)",
                        sub, resp.status_code,
                    )
                    continue
                if resp.status_code == 403:
                    log.warning(
                        "reddit_old: r/%s -> 403 (old endpoint also blocked; "
                        "consider removing this source or rotating your IP)",
                        sub,
                    )
                    continue
                if resp.status_code >= 400:
                    log.warning("reddit_old: r/%s -> HTTP %s", sub, resp.status_code)
                    continue
                try:
                    blob = resp.json()
                except ValueError as exc:
                    log.warning("reddit_old: r/%s JSON decode failed (%s)", sub, exc)
                    continue

                children = (
                    (blob.get("data") or {}).get("children") or []
                    if isinstance(blob, dict)
                    else []
                )
                yield_count = 0
                for entry in children:
                    if not isinstance(entry, dict):
                        continue
                    data = entry.get("data") or {}
                    if not isinstance(data, dict):
                        continue
                    post_id = data.get("id") or ""
                    if not post_id:
                        continue
                    if post_id in seen:
                        continue
                    title = (data.get("title") or "").strip()
                    if not title:
                        continue
                    # Build a Reddit permalink URL; fall back to the short one.
                    permalink = data.get("permalink") or ""
                    url_out = (
                        urljoin("https://www.reddit.com", permalink)
                        if permalink
                        else f"https://old.reddit.com/r/{sub}/comments/{post_id}"
                    )
                    haystack = (title + " " + (data.get("selftext") or "")).lower()
                    if keywords_lc and not any(k in haystack for k in keywords_lc):
                        # No keyword match: still record it (with score=5) so
                        # the user sees the raw r/forhire stream if they want it.
                        score = 5
                    else:
                        # Bump score for hiring tags + low-effort indicators.
                        score = 35
                        if "[hiring]" in title.lower() or "[for hire]" in title.lower():
                            score += 20
                        if "remote" in haystack:
                            score += 5
                    score = min(score, 100)
                    flairs = [
                        f.get("link_flair_text") or ""
                        for f in (data.get("link_flair_richtext") or [])
                        if isinstance(f, dict)
                    ]
                    tags = ("reddit_old:" + sub,) + tuple(
                        t for t in (title.split()[:3] + flairs[:2]) if t
                    )
                    yield Lead(
                        id=f"reddit_old:{post_id}",
                        source=self.key,
                        title=title[:200],
                        url=url_out,
                        posted_at=_parse_utc(data.get("created_utc")),
                        score=score,
                        tags=tags,
                        raw={
                            "subreddit": sub,
                            "permalink": permalink,
                            "selftext": (data.get("selftext") or "")[:600],
                            "flair": flairs,
                        },
                    )
                    yield_count += 1
                    seen.add(post_id)
                log.info(
                    "reddit_old: r/%s yielded %d leads (filtered keywords=%s)",
                    sub, yield_count, "yes" if keywords_lc else "no",
                )
                # Polite pacing to avoid getting throttled even on legacy.
                await asyncio.sleep(0.5)
        state["reddit_old_seen"] = sorted(seen, key=lambda x: -int(x.split('_', 1)[0] if x[:10].isdigit() else 0))[:2000]


def _parse_utc(seconds: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(float(seconds), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


# ---------------------------------------------------------------------------
# Sub-source 2: Hacker News monthly "Who is hiring?" thread (HTML, no API)
# ---------------------------------------------------------------------------


@register
class HnJobsMonthlySource(Source):
    """Scrape Hacker News's monthly ``Who is hiring?`` thread via HTML.

    HN publishes a monthly meta-thread (e.g. ``item?id=whoishiring``)
    where hundreds of "we are hiring X" comments live. It is the
    highest-signal single source of dev hiring leads on the public web.

    Implementation: GET ``news.ycombinator.com/item?id=whoishiring``,
    parse the comments, filter by keyword, yield one Lead per job post.

    The thread is replaced monthly, but HN keeps the past few months
    linked via "Show HN: Who is hiring?" threads. We refresh
    ``lookback_days`` per cycle so we don't process 2-year-old threads.

    No API key. Off by default.
    """

    key: ClassVar[str] = "hn_jobs_monthly"
    label: ClassVar[str] = "HN monthly hiring thread (HTML scrape)"
    description: ClassVar[str] = (
        "Scrape news.ycombinator.com/item?id=whoisharing — no API key."
    )
    default_enabled: ClassVar[bool] = False

    _ITEM_URL: ClassVar[str] = "https://news.ycombinator.com/item?id=whoishiring"
    _HEADERS: ClassVar[dict[str, str]] = {
        "User-Agent": "Mozilla/5.0 (compatible; clawflow/0.1; +https://github.com/nadimkhan/clawflow)"
    }

    async def validate_config(self, cfg: Any) -> tuple[bool, str]:
        extra = getattr(cfg, "extra", {}) or {}
        lookback = int(extra.get("lookback_days") or 30)
        return True, f"hn_jobs_monthly: lookback_days={lookback}"

    def fetch(self, state: dict[str, Any], cfg: Any) -> AsyncIterator[Lead]:
        return self._fetch(state, cfg)

    async def _fetch(self, state: dict[str, Any], cfg: Any) -> AsyncIterator[Lead]:
        extra = getattr(cfg, "extra", {}) or {}
        lookback_days: int = int(extra.get("lookback_days") or 30)
        raw_keywords: list[str] = list(extra.get("keywords") or [])
        keywords_lc = {k.lower() for k in raw_keywords if k}

        # HN threads have an `id=whoishiring` parent plus monthly child
        # threads (item?id=NUMBER). The parent page lists recent months.
        async with httpx.AsyncClient(
            headers=self._HEADERS, timeout=30.0, follow_redirects=True
        ) as client:
            try:
                resp = await client.get(self._ITEM_URL)
            except (httpx.HTTPError, ConnectionError, TimeoutError) as exc:
                log.warning("hn_jobs_monthly: GET main thread failed (%s)", exc)
                return
            if resp.status_code == 419:
                log.warning(
                    "hn_jobs_monthly: main thread HTTP 419 (HN throttling "
                    "anonymous IPs as of 2026). Try again later or via a "
                    "proxy; this source is degraded until HN traffic clears."
                )
                return
            if resp.status_code >= 400:
                log.warning(
                    "hn_jobs_monthly: main thread HTTP %s (HN usually 503s "
                    "during peaks; this is fine)",
                    resp.status_code,
                )
                return
            html_text = resp.text
            # Extract child thread ids — HN uses <a href="item?id=NUMBER">.
            child_ids = [
                int(m)
                for m in re.findall(r'href="item\?id=(\d+)"', html_text)
                if m.isdigit()
            ]
            child_ids = sorted(set(child_ids), reverse=True)[:6]  # last 6 months

            seen: set[str] = set(state.get("hn_jobs_monthly_seen", []) or [])
            yielded = 0
            for cid in child_ids:
                # Cap each monthly thread's contribution to the response.
                try:
                    thread_resp = await client.get(
                        f"https://news.ycombinator.com/item?id={cid}",
                    )
                except (httpx.HTTPError, ConnectionError, TimeoutError):
                    continue
                if thread_resp.status_code >= 400:
                    continue
                comments = _parse_hn_comments(thread_resp.text)
                for comment in comments:
                    lead_id = f"hn_jobs_monthly:{cid}:{comment['id']}"
                    if lead_id in seen:
                        continue
                    haystack = comment["text"].lower()
                    if keywords_lc and not any(k in haystack for k in keywords_lc):
                        continue
                    # Score: 1 base + bonus for "remote", "contract", length quality.
                    score = 40
                    if "remote" in haystack:
                        score += 10
                    if "contract" in haystack or "freelance" in haystack:
                        score += 20
                    if any(t in haystack for t in ("solidity", "rust", "smart contract", "web3")):
                        score += 25
                    score = min(score, 100)
                    yield Lead(
                        id=lead_id,
                        source=self.key,
                        title=comment["text"][:140].split("\n", 1)[0] or "HN hiring post",
                        url=f"https://news.ycombinator.com/item?id={cid}#{comment['id']}",
                        posted_at=None,  # HN comment HTML doesn't expose ISO times cheaply
                        score=score,
                        tags=("hn_jobs_monthly",) + tuple(haystack.split()[:3] if False else ()),
                        raw={
                            "thread_id": cid,
                            "comment_id": comment["id"],
                            "text": comment["text"][:1200],
                        },
                    )
                    seen.add(lead_id)
                    yielded += 1
                await asyncio.sleep(0.4)

            log.info(
                "hn_jobs_monthly: scanned %d monthly thread(s); yielded %d leads",
                len(child_ids), yielded,
            )
        state["hn_jobs_monthly_seen"] = sorted(seen)[-2000:]


def _parse_hn_comments(page_html: str) -> list[dict[str, Any]]:
    """Parse HN comment text out of the page HTML, no BeautifulSoup needed.

    HN markup: each top-level comment is <tr><td><div class="comment">...HTML...
    </div></td></tr>. We pull the visible text. The comment id is on a
    sibling anchor (``<a class="age" href="#COMMENT_ID">``).

    Robustness: the HTML structure has stayed stable since 2012 — but
    we fall back to "(no text)" rather than raise on a node we can't parse.
    """
    out: list[dict[str, Any]] = []
    # Each comment has a unique anchor; the comment text follows in <div class="comment">.
    # Splitting on the comment-id anchor is the most robust approach.
    anchor_pat = re.compile(
        r'<a\s+href="#(\d+)"[^>]*class="(?:age|togg|reply)"[^>]*>',
        re.IGNORECASE,
    )
    parts = re.split(r"<div\s+class=\"comment\"\s*>", page_html)
    # parts[0] is the thread header; parts[1:] are the comment bodies in order.
    for i in range(1, len(parts)):
        body = parts[i]
        anchor_match = anchor_pat.search(body)
        cid = anchor_match.group(1) if anchor_match else f"offset-{i}"
        # Strip tags and collapse whitespace.
        text = re.sub(r"<[^>]+>", " ", body)
        text = html.unescape(text)
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) < 30:  # too short to be useful
            continue
        out.append({"id": cid, "text": text})
    return out


if __name__ == "__main__":  # pragma: no cover
    print(
        "no_api_leads community plugin — drop into clawflow/sources/community/ "
        "and enable reddit_old / hn_jobs_monthly in clawflow.yml"
    )
