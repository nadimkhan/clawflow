"""Freelance / for-hire sources.

Two sub-sources share helpers and the same user-agent discipline:

* :class:`HNFreelancerSource` — polls the HN Algolia search API for stories
  matching a freelance/web3 keyword query. No auth. Returns up to 50 hits
  per call; we cache the last-seen ``objectID`` set so duplicate fetches
  within a session don't re-yield the same story.
* :class:`RedditForHireSource` — hits Reddit's public JSON endpoint for
  ``/r/<sub>/new``. No auth, but a descriptive ``User-Agent`` is required
  or Reddit returns 429. Filters for ``[Hiring]`` post titles and tracks
  the most-recently-seen permalink per subreddit so re-runs don't duplicate.

Both sources persist their bookkeeping on the ``state`` dict; the
orchestrator owns the on-disk state file.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, AsyncIterator, ClassVar

import httpx

from clawflow.leads import Lead
from clawflow.sources import Source, register

HN_ALGOLIA = "https://hn.algolia.com/api/v1/search"
REDDIT_BASE = "https://www.reddit.com/r/{sub}/new.json?limit=25"

# Default keyword query — surfaced via cfg.query.
HN_DEFAULT_QUERY = "freelance solidity OR smart contract OR rust web3"

# Descriptive UA required by Reddit; falls under their API ToS guidance.
REDDIT_USER_AGENT = "clawflow/0.1 (+https://github.com/nadim/clawflow)"

log = logging.getLogger("clawflow.sources.hn_freelancer")


def _parse_dt(value: str | None) -> datetime | None:
    """Parse an ISO-8601 timestamp; return None on failure."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


@register
class HNFreelancerSource(Source):
    """Hacker News Algolia search filtered to freelance web3 stories."""

    key: ClassVar[str] = "hn_freelancer"
    label: ClassVar[str] = "HN Freelancer"
    description: ClassVar[str] = (
        "Hacker News Algolia search for freelance/solidity/rust/web3 stories."
    )
    default_enabled: ClassVar[bool] = True

    async def validate_config(self, cfg: Any) -> tuple[bool, str]:
        query = getattr(cfg, "query", None) or HN_DEFAULT_QUERY
        return True, f"hn_freelancer: query={query[:60]}..."

    def fetch(self, state: dict[str, Any], cfg: Any) -> AsyncIterator[Lead]:
        """Async generator entry point."""
        return self._fetch(state, cfg)

    async def _fetch(self, state: dict[str, Any], cfg: Any) -> AsyncIterator[Lead]:
        query = getattr(cfg, "query", None) or HN_DEFAULT_QUERY
        seen: set[str] = set(state.get("hn_seen", []) or [])
        try:
            async with httpx.AsyncClient(
                headers={"User-Agent": "clawflow/0.1"},
                timeout=20.0,
            ) as client:
                params = {"query": query, "tags": "story", "hitsPerPage": 50}
                try:
                    resp = await client.get(HN_ALGOLIA, params=params)
                except (httpx.HTTPError, ConnectionError, TimeoutError) as exc:
                    log.warning("hn_freelancer: GET failed (%s)", exc)
                    return
                if resp.status_code >= 400:
                    log.warning("hn_freelancer: status %s", resp.status_code)
                    return
                try:
                    data = resp.json()
                except ValueError as exc:
                    log.warning("hn_freelancer: JSON decode error (%s)", exc)
                    return
                for hit in data.get("hits", []) or []:
                    try:
                        object_id = hit.get("objectID", "")
                        if not object_id or object_id in seen:
                            continue
                        title = hit.get("title") or hit.get("story_title") or "(untitled)"
                        url = (
                            hit.get("url")
                            or hit.get("story_url")
                            or f"https://news.ycombinator.com/item?id={object_id}"
                        )
                        posted = _parse_dt(hit.get("created_at"))
                        score = min(int(hit.get("points") or 0), 100)
                        seen.add(object_id)
                        yield Lead(
                            id=f"hn:{object_id}",
                            source=self.key,
                            title=title[:200],
                            url=url,
                            posted_at=posted,
                            score=score,
                            tags=("hn", "freelance"),
                            raw={
                                "author": hit.get("author"),
                                "points": hit.get("points"),
                                "num_comments": hit.get("num_comments"),
                            },
                        )
                    except Exception as exc:
                        log.warning("hn_freelancer: skip hit (%s)", exc)
                        continue
        except Exception as exc:
            log.error("hn_freelancer: fatal (%s); bailing", exc)
            return
        finally:
            # Bound the seen-set so it can't grow forever.
            state["hn_seen"] = sorted(seen)[-1000:]


@register
class RedditForHireSource(Source):
    """Reddit public JSON feed for ``/r/<sub>/new`` filtered to ``[Hiring]`` posts."""

    key: ClassVar[str] = "reddit_forhire"
    label: ClassVar[str] = "Reddit r/forhire"
    description: ClassVar[str] = (
        "Reddit public JSON feed for r/forhire (and friends); "
        "[Hiring]-tagged posts only."
    )
    default_enabled: ClassVar[bool] = True

    async def validate_config(self, cfg: Any) -> tuple[bool, str]:
        subs = list(getattr(cfg, "subreddits", None) or ["forhire"])
        return True, f"reddit_forhire: subs={subs}"

    def fetch(self, state: dict[str, Any], cfg: Any) -> AsyncIterator[Lead]:
        """Async generator entry point."""
        return self._fetch(state, cfg)

    async def _fetch(self, state: dict[str, Any], cfg: Any) -> AsyncIterator[Lead]:
        subs = list(getattr(cfg, "subreddits", None) or ["forhire"])
        last_seen: dict[str, str] = dict(state.get("reddit_last_seen", {}) or {})
        try:
            async with httpx.AsyncClient(
                headers={"User-Agent": REDDIT_USER_AGENT},
                timeout=20.0,
            ) as client:
                for sub in subs:
                    url = REDDIT_BASE.format(sub=sub)
                    try:
                        resp = await client.get(url)
                    except (httpx.HTTPError, ConnectionError, TimeoutError) as exc:
                        log.warning(
                            "reddit_forhire: GET r/%s failed (%s)", sub, exc
                        )
                        continue
                    if resp.status_code == 429:
                        log.warning(
                            "reddit_forhire: rate-limited on r/%s; skipping", sub
                        )
                        continue
                    if resp.status_code >= 400:
                        log.warning(
                            "reddit_forhire: r/%s -> %s", sub, resp.status_code
                        )
                        continue
                    try:
                        payload = resp.json()
                    except ValueError as exc:
                        log.warning(
                            "reddit_forhire: r/%s JSON decode error (%s)", sub, exc
                        )
                        continue
                    children = (((payload or {}).get("data") or {}).get("children")) or []
                    newest_permalink = last_seen.get(sub, "")
                    sub_newest = newest_permalink
                    for child in children:
                        try:
                            d = child.get("data") or {}
                            title = (d.get("title") or "").strip()
                            if "[hiring]" not in title.lower():
                                continue
                            permalink = d.get("permalink", "")
                            if permalink and permalink == newest_permalink:
                                # Already yielded this one last run; stop.
                                break
                            full_url = (
                                f"https://www.reddit.com{permalink}"
                                if permalink
                                else d.get("url_overridden_by_dest", "")
                            )
                            if not full_url:
                                continue
                            created_utc = d.get("created_utc")
                            posted = (
                                datetime.fromtimestamp(
                                    float(created_utc), tz=timezone.utc
                                )
                                if created_utc is not None
                                else None
                            )
                            if permalink:
                                sub_newest = permalink
                            yield Lead(
                                id=f"reddit:{d.get('id') or permalink}",
                                source=self.key,
                                title=title[:200],
                                url=full_url,
                                posted_at=posted,
                                score=min(int(d.get("score", 0) or 0), 100),
                                tags=("reddit", f"r/{sub}"),
                                raw={
                                    "subreddit": sub,
                                    "author": d.get("author"),
                                    "selftext": (d.get("selftext") or "")[:300],
                                },
                            )
                        except Exception as exc:
                            log.warning(
                                "reddit_forhire: skip post on r/%s (%s)", sub, exc
                            )
                            continue
                    if sub_newest:
                        last_seen[sub] = sub_newest
        except Exception as exc:
            log.error("reddit_forhire: fatal (%s); bailing", exc)
            return
        finally:
            state["reddit_last_seen"] = last_seen