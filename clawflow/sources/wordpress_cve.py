"""WordPress plugin CVE source.

Polls the WPVulnerability JSON API for each configured plugin slug and yields
one :class:`Lead` per vulnerability, scored by severity. Results are cached
on the ``state`` dict for 24 hours so a daily digest does not re-fire on the
same CVE.

Severity -> score mapping (clamped 0-100 to match other sources):

    CRITICAL = 95
    HIGH     = 80
    MEDIUM   = 55
    LOW      = 25
    UNKNOWN  = 10

If the API returns a 404 we record an empty cache entry so subsequent runs
don't keep hammering that slug.

The endpoint is unauthenticated public JSON; if it ever moves the URL
constant below is the only thing that needs editing.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, AsyncIterator, ClassVar

import httpx

from clawflow.leads import Lead
from clawflow.sources import Source, register

WP_CVE_API = "https://wpsiapi.com/v1/plugins/{slug}"

DEFAULT_SLUGS: tuple[str, ...] = (
    "elementor",
    "woocommerce",
    "wordpress-seo",
    "wpforms-lite",
)

SEVERITY_SCORE: dict[str, int] = {
    "CRITICAL": 95,
    "HIGH": 80,
    "MEDIUM": 55,
    "LOW": 25,
    "UNKNOWN": 10,
}

# Cache entries older than this are re-fetched; the orchestrator runs daily.
CACHE_TTL_SECONDS = 24 * 60 * 60

log = logging.getLogger("clawflow.sources.wordpress_cve")


def _normalize_severity(value: str | None) -> str:
    """Uppercase / strip a severity string; default to UNKNOWN if unknown."""
    v = (value or "").upper().strip()
    return v if v in SEVERITY_SCORE else "UNKNOWN"


@register
class WordPressCVESource(Source):
    """Poll WPVulnerability API for plugin CVEs across a configurable slug list."""

    key: ClassVar[str] = "wordpress_cve"
    label: ClassVar[str] = "WordPress Plugin CVEs"
    description: ClassVar[str] = (
        "WPVulnerability.net API for vulnerabilities in a list of WordPress plugin slugs."
    )
    default_enabled: ClassVar[bool] = True

    async def validate_config(self, cfg: Any) -> tuple[bool, str]:
        try:
            slugs = list(getattr(cfg, "slugs", None) or DEFAULT_SLUGS)
        except TypeError as exc:
            return False, f"wordpress_cve: bad cfg ({exc})"
        if not slugs:
            return False, "wordpress_cve: slugs list is empty"
        return True, f"wordpress_cve: tracking {len(slugs)} plugin slug(s)"

    def fetch(self, state: dict[str, Any], cfg: Any) -> AsyncIterator[Lead]:
        """Async generator entry point."""
        return self._fetch(state, cfg)

    async def _fetch(self, state: dict[str, Any], cfg: Any) -> AsyncIterator[Lead]:
        slugs = list(getattr(cfg, "slugs", None) or DEFAULT_SLUGS)
        cache: dict[str, dict[str, Any]] = dict(
            state.get("wordpress_cve_cache", {}) or {}
        )
        now = time.time()
        try:
            async with httpx.AsyncClient(
                headers={"User-Agent": "clawflow/0.1"},
                timeout=20.0,
            ) as client:
                for slug in slugs:
                    entry = cache.get(slug)
                    if entry and (now - float(entry.get("_ts", 0))) < CACHE_TTL_SECONDS:
                        # Cache hit within TTL: silently skip — the daily
                        # digest should not re-fire on the same CVE.
                        log.debug("wordpress_cve: %s cache fresh; skipping", slug)
                        continue
                    try:
                        resp = await client.get(WP_CVE_API.format(slug=slug))
                    except (httpx.HTTPError, ConnectionError, TimeoutError) as exc:
                        log.warning("wordpress_cve: GET %s failed (%s)", slug, exc)
                        continue
                    if resp.status_code == 404:
                        log.debug("wordpress_cve: %s -> 404 (no data)", slug)
                        cache[slug] = {"_ts": now, "leads": []}
                        continue
                    if resp.status_code >= 400:
                        log.warning(
                            "wordpress_cve: GET %s -> %s", slug, resp.status_code
                        )
                        continue
                    try:
                        data = resp.json()
                    except ValueError as exc:
                        log.warning(
                            "wordpress_cve: %s JSON decode failed (%s)", slug, exc
                        )
                        continue
                    leads_for_slug = list(self._data_to_leads(slug, data))
                    cache[slug] = {
                        "_ts": now,
                        "leads": [ld.to_dict() for ld in leads_for_slug],
                    }
                    for lead in leads_for_slug:
                        yield lead
        except Exception as exc:
            log.error("wordpress_cve: fatal (%s); bailing", exc)
            return
        finally:
            state["wordpress_cve_cache"] = cache
            state["wordpress_cve_cache_ts"] = now

    def _data_to_leads(self, slug: str, data: Any) -> AsyncIterator[Lead]:
        """Yield :class:`Lead` records for every vulnerability in ``data``.

        Tolerates a few response shapes: ``{"vulnerabilities": [...]}``,
        ``{"vulnerability": [...] or {...}}``, ``{"data": [...]}``, or a
        bare list. Unknown shapes yield zero leads (and don't raise).
        """
        vulns: list[Any] = []
        if isinstance(data, dict):
            for key in ("vulnerabilities", "vulnerability", "issues", "data"):
                value = data.get(key)
                if isinstance(value, list):
                    vulns = value
                    break
                if key == "vulnerability" and isinstance(value, dict):
                    vulns = [value]
                    break
        elif isinstance(data, list):
            vulns = [v for v in data if isinstance(v, dict)]

        for v in vulns:
            if not isinstance(v, dict):
                continue
            try:
                title = (
                    v.get("title")
                    or v.get("name")
                    or f"{slug}: vulnerability"
                )
                raw_severity = (
                    v.get("severity")
                    or (v.get("cvss") or {}).get("severity")
                    or ""
                )
                severity = _normalize_severity(raw_severity)
                score = SEVERITY_SCORE[severity]
                cve_id = v.get("cve") or v.get("id") or ""
                patched = (
                    v.get("fixed_in")
                    or v.get("patch")
                    or v.get("fixedIn")
                    or ""
                )
                url = (
                    v.get("url")
                    or v.get("reference")
                    or v.get("link")
                    or f"https://wpatching.com/plugins/{slug}"
                )
                description = (v.get("description") or v.get("desc") or "")[:300]
                lead_id = f"wpcve:{slug}:{cve_id or title[:40]}"
                yield Lead(
                    id=lead_id,
                    source=self.key,
                    title=title[:200],
                    url=url,
                    posted_at=datetime.now(timezone.utc),
                    score=score,
                    tags=(f"wp:{slug}", f"severity:{severity}"),
                    raw={
                        "slug": slug,
                        "cve": cve_id,
                        "severity": severity,
                        "fixed_in": patched,
                        "description": description,
                        "payload": v,
                    },
                )
            except Exception as exc:
                log.warning("wordpress_cve: skip vuln for %s (%s)", slug, exc)
                continue