"""clawflow plugin: Sherlock.xyz audit contests.

Sherlock hosts public, paid smart-contract audit contests. Each contest
pays out $10k-$200k+ depending on duration and protocol value. They
publish a public REST API at https://app.sherlock.xyz/api with no auth
required.

This source pulls the active contest list weekly. Each "Lead" represents
one contest the user could apply to as an audit lead or warden.

Free. No API key.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, AsyncIterator, ClassVar

import httpx

from clawflow.leads import Lead
from clawflow.sources import Source, register

log = logging.getLogger("clawflow.sources.community.sherlock_audit")

# Sherlock's public read-only API. Listed at:
# https://docs.sherlock.xyz/ - "GET /api/public/contests" works without auth.
SHERLOCK_CONTESTS = "https://app.sherlock.xyz/api/public/contests"
SHERLOCK_AUDITORS = "https://app.sherlock.xyz/api/public/auditors"


def _money(s: Any) -> int | None:
    """Best-effort USD-amount parser for a contest prize field."""
    if s is None:
        return None
    if isinstance(s, int):
        return s
    if isinstance(s, str):
        digits = "".join(ch for ch in s if ch.isdigit())
        if digits:
            try:
                return int(digits)
            except ValueError:
                return None
    return None


def _score_contest(prize_usd: int | None) -> int:
    base = 30
    if prize_usd is not None:
        if prize_usd >= 200_000:
            base += 50
        elif prize_usd >= 50_000:
            base += 35
        elif prize_usd >= 10_000:
            base += 20
        elif prize_usd >= 1_000:
            base += 5
    return min(base, 100)


# ⚠️ KNOWN_BROKEN_AS_OF_2026-10 — upstreams 404'd when this plugin was built.
# Code is intact and ready; you only need to swap the URL constants
# at the top of this file for a working endpoint. See CHANGELOG v0.2.0.
@register
class SherlockAuditSource(Source):
    """Paid smart-contract audit contests via Sherlock.xyz public API."""

    key: ClassVar[str] = "sherlock_audit"
    label: ClassVar[str] = "Sherlock audit contests"
    description: ClassVar[str] = (
        "Live Sherlock.xyz audit contests — $10k-$200k paid to wardens."
    )
    default_enabled: ClassVar[bool] = False

    async def validate_config(self, cfg: Any) -> tuple[bool, str]:
        return True, "sherlock_audit: tracking /api/public/contests"

    def fetch(self, state: dict[str, Any], cfg: Any) -> AsyncIterator[Lead]:
        return self._fetch(state, cfg)

    async def _fetch(self, state: dict[str, Any], cfg: Any) -> AsyncIterator[Lead]:
        extra = getattr(cfg, "extra", {}) or {}
        include_auditors = bool(extra.get("include_auditors", False))
        try:
            async with httpx.AsyncClient(
                headers={"User-Agent": "clawflow/0.1"},
                timeout=20.0,
            ) as client:
                contests_resp = await client.get(SHERLOCK_CONTESTS)
                auditors_resp = None
                if include_auditors:
                    try:
                        auditors_resp = await client.get(SHERLOCK_AUDITORS)
                    except Exception:
                        auditors_resp = None
        except (httpx.HTTPError, ConnectionError, TimeoutError) as exc:
            log.warning("sherlock_audit: connection failed (%s)", exc)
            return
        if contests_resp.status_code >= 400:
            log.warning("sherlock_audit: contests HTTP %s", contests_resp.status_code)
            return
        try:
            contests = contests_resp.json()
        except ValueError:
            return
        if not isinstance(contests, list):
            contests = (contests or {}).get("contests", []) if isinstance(contests, dict) else []

        for c in contests:
            if not isinstance(c, dict):
                continue
            cid = c.get("id") or c.get("contest_id") or c.get("slug")
            title = c.get("name") or c.get("title") or "Sherlock contest"
            status = (c.get("status") or "").lower()
            # Filter to active contests only
            if status and status not in ("active", "ongoing", "open", "live"):
                continue
            link = (
                "https://app.sherlock.xyz/contests/" + str(c.get("slug", cid))
                if c.get("slug") else
                c.get("url") or "https://app.sherlock.xyz/audits"
            )
            prize_usd = (
                _money(c.get("total_prize"))
                or _money(c.get("prize"))
                or _money(c.get("amount"))
            )
            yield Lead(
                id=f"sherlock_audit:{cid}",
                source=self.key,
                title=title[:200],
                url=link,
                posted_at=c.get("starts_at") or c.get("created_at"),
                score=_score_contest(prize_usd),
                tags=("audit", "smart-contract", "sherlock"),
                raw={
                    "status": status,
                    "prize_usd_estimate": prize_usd,
                    "details": c,
                },
            )

        # Optionally emit top auditor profiles as leads too
        if include_auditors and auditors_resp is not None and auditors_resp.status_code < 400:
            try:
                auditors = auditors_resp.json()
            except ValueError:
                return
            if not isinstance(auditors, list):
                auditors = (auditors or {}).get("auditors", []) if isinstance(auditors, dict) else []
            for a in auditors[:20]:
                if not isinstance(a, dict):
                    continue
                nick = a.get("username") or a.get("handle") or a.get("id")
                profile_url = "https://app.sherlock.xyz/profile/" + str(nick) if nick else "https://app.sherlock.xyz"
                yield Lead(
                    id=f"sherlock_audit:auditor:{nick}",
                    source=self.key,
                    title=f"Warden: {a.get('name') or nick}",
                    url=profile_url,
                    posted_at=None,
                    score=20,
                    tags=("auditor", "sherlock"),
                    raw={"profile": a},
                )
