"""Two-phase async scheduler: collect → dedupe → alert.

The scheduler is the orchestrator the CLI talks to. It owns the cycle
budget, runs every enabled source concurrently, applies the on-disk
dedup set, hands the new leads to each alerter, and persists the result.

Two entry points:

* ``run_once(cfg)`` — synchronous, single cycle, returns an int exit code.
* ``run_forever(cfg)`` — async, polls until SIGINT, returns an int exit code.
"""
from __future__ import annotations

import asyncio
import logging
import signal
import sys
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

from clawflow.alerters import dispatch_alerts
from clawflow.config import ClawflowConfig
from clawflow.leads import Lead, add_leads, load_state


log = logging.getLogger("clawflow.scheduler")


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def run_once(cfg: ClawflowConfig) -> int:
    """Execute one collect → dedupe → alert cycle and return an exit code."""
    logging.basicConfig(
        level=getattr(logging, cfg.logging.level, logging.INFO),
        format=cfg.logging.format,
        stream=sys.stderr,
    )
    try:
        return asyncio.run(_one_cycle(cfg))
    except KeyboardInterrupt:  # pragma: no cover — Ctrl-C in the bridge
        return 130


async def run_forever(cfg: ClawflowConfig) -> int:
    """Run cycles forever until SIGINT; returns an int exit code."""
    logging.basicConfig(
        level=getattr(logging, cfg.logging.level, logging.INFO),
        format=cfg.logging.format,
        stream=sys.stderr,
    )
    stop = asyncio.Event()

    def _on_signal(signame: str) -> None:
        log.info("received %s; shutting down after current cycle", signame)
        stop.set()

    loop = asyncio.get_running_loop()
    for signame in ("SIGINT", "SIGTERM"):
        try:
            loop.add_signal_handler(
                getattr(signal, signame), _on_signal, signame
            )
        except (NotImplementedError, RuntimeError):  # pragma: no cover — Windows
            pass

    while not stop.is_set():
        try:
            await _one_cycle(cfg)
        except Exception as exc:  # pragma: no cover — last-resort guard
            log.error("cycle crashed: %s", exc)
        try:
            await asyncio.wait_for(stop.wait(), timeout=cfg.interval_minutes * 60)
        except asyncio.TimeoutError:
            continue
        else:
            break
    return 0


# ---------------------------------------------------------------------------
# Core cycle
# ---------------------------------------------------------------------------

async def _one_cycle(cfg: ClawflowConfig) -> int:
    """Execute a single cycle. Returns an int exit code."""
    cycle_start = time.monotonic()
    budget = cfg.cycle_budget_seconds
    log.info(
        "cycle start (interval=%dm, budget=%ds)", cfg.interval_minutes, budget
    )

    state = load_state(cfg.state_path)
    seen: set[str] = set(state.get("seen", []) or [])

    # Phase 1 — collect --------------------------------------------------------
    sources = _enabled_sources(cfg)
    if not sources:
        log.info("no sources enabled — exiting cycle")
        return 0

    log.info("phase 1: collecting from %d source(s)", len(sources))
    collected = await asyncio.wait_for(
        _collect_all(sources, state, cfg),
        timeout=budget,
    )

    # Phase 2 — dedupe ---------------------------------------------------------
    log.info("phase 2: deduping %d candidate lead(s)", len(collected))
    new_leads: list[Lead] = []
    repeats = 0
    for lead in collected:
        key = lead.dedup_key()
        if key in seen:
            repeats += 1
            continue
        seen.add(key)
        new_leads.append(lead)

    # Phase 3 — alert ----------------------------------------------------------
    log.info(
        "phase 3: alerting %d new lead(s) (%d repeats skipped)",
        len(new_leads), repeats,
    )
    alerts_sent = 0
    if new_leads:
        # Apply the user's profile filter before persistence or alerting.
        # Dropped leads don't count as alerts and don't go to state.json/db.
        from clawflow.profile_filter import filter_leads
        before_count = len(new_leads)
        new_leads = filter_leads(new_leads, cfg.profile)
        if len(new_leads) < before_count:
            log.info(
                "profile_filter: kept %d / %d after profile match",
                len(new_leads), before_count,
            )
        if new_leads:
            alerts_sent = await dispatch_alerts(cfg, new_leads)

    # Persist --------------------------------------------------------------
    # SQLite path lives next to state.json by convention; pass it through
    # so the leads-ui web app sees new leads immediately.
    if new_leads:
        sqlite_db = str(Path(cfg.state_path).with_name("clawflow-leads.db"))
        add_leads(cfg.state_path, new_leads, sqlite_path=sqlite_db)

    elapsed = time.monotonic() - cycle_start
    log.info(
        "cycle done: new=%d repeats=%d alerts=%d elapsed=%.2fs",
        len(new_leads), repeats, alerts_sent, elapsed,
    )
    return 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _enabled_sources(cfg: ClawflowConfig) -> list[tuple[str, Any]]:
    """Return [(source_id, source_class), ...] for enabled sources only."""
    # Local import: keeps the module-level path free of source side-effects.
    from clawflow.sources import BUILTIN_SOURCES, get_source
    out: list[tuple[str, Any]] = []
    for sid in BUILTIN_SOURCES():
        sc = cfg.sources.get(sid)
        if sc is None or not sc.enabled:
            continue
        cls = get_source(sid)
        if cls is None:  # pragma: no cover — registry drift
            log.warning("source %s enabled in config but not registered", sid)
            continue
        out.append((sid, cls))
    return out


async def _collect_all(
    sources: list[tuple[str, Any]],
    state: dict[str, Any],
    cfg: ClawflowConfig,
) -> list[Lead]:
    """Run every enabled source's fetch concurrently; flatten the leads."""
    async def _run_one(sid: str, cls: Any) -> list[Lead]:
        cfg_obj = _build_source_cfg(sid, cfg)
        try:
            instance = cls()
        except Exception as exc:  # pragma: no cover — defensive
            log.warning("could not instantiate %s: %s", sid, exc)
            return []
        try:
            ok, msg = await instance.validate_config(cfg_obj)
            if not ok:
                log.warning("config invalid for %s: %s", sid, msg)
                return []
            aggregator: list[Lead] = []
            async for lead in instance.fetch(state, cfg_obj):
                aggregator.append(lead)
            return aggregator
        except Exception as exc:  # pragma: no cover — single source failure
            log.warning("source %s failed: %s", sid, exc)
            return []

    tasks = [_run_one(sid, cls) for sid, cls in sources]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    leads: list[Lead] = []
    for result in results:
        if isinstance(result, list):
            leads.extend(result)
        elif isinstance(result, Exception):  # pragma: no cover
            log.warning("source task raised: %s", result)
    return leads


def _build_source_cfg(sid: str, cfg: ClawflowConfig) -> Any:
    """Return a per-source cfg object with attributes the source expects."""
    sc = cfg.sources.get(sid)
    extras = sc.extra if sc else {}
    timeout = sc.timeout_seconds if sc else 30

    class _SourceCfg:
        pass

    obj = _SourceCfg()
    obj.timeout_seconds = timeout
    obj.severity_threshold = sc.severity_threshold if sc else 0
    for key, val in extras.items():
        setattr(obj, key, val)
    return obj


if __name__ == "__main__":  # pragma: no cover
    from pathlib import Path
    cfg = ClawflowConfig()
    sys.exit(run_once(cfg))