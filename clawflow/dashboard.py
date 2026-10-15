"""Tiny ASCII dashboard stub for v0.1 — the real curses/textual UI ships in v0.2.

The function ``render_dashboard`` prints a static panel summarising the
current config + state, then returns 0. Returning an ``int`` keeps it
interchangeable with a CLI handler.
"""
from __future__ import annotations

from typing import Any, Mapping

from clawflow.config import ClawflowConfig


def render_dashboard(
    cfg: ClawflowConfig,
    state: Mapping[str, Any] | None = None,
    width: int = 64,
) -> int:
    """Print the v0.1 ASCII dashboard and return an exit code."""
    state = state or {}
    leads_blob = state.get("leads", []) or []
    seen_blob = state.get("seen", []) or []
    bar = "─" * width

    print("┌" + bar + "┐")
    print("│ clawflow — v0.1 dashboard" + " " * (width - 26) + "│")
    print("├" + bar + "┤")
    _row("state file", str(cfg.state_path), width)
    _row("interval", f"{cfg.interval_minutes}m", width)
    _row("cycle budget", f"{cfg.cycle_budget_seconds}s", width)
    _row("leads persisted", str(len(leads_blob)), width)
    _row("seen set size", str(len(seen_blob)), width)
    _row("updated_at", str(state.get("updated_at", "never")), width)
    print("├" + bar + "┤")
    _row("enabled sources", "", width)
    enabled = [sid for sid, sc in cfg.sources.items() if sc.enabled]
    if not enabled:
        _row("", "(none — run `clawflow init` then edit clawflow.yml)", width)
    else:
        for sid in enabled:
            _row("", f"• {sid}", width)
    print("└" + bar + "┘")
    print()
    print("v0.2 will replace this with a live TUI (textual).")
    return 0


def _row(label: str, value: str, width: int) -> None:
    """Print one ``│ label      value`` row padded to ``width`` chars."""
    line = f" {label:<16} {value}" if label else f" {value}"
    line = line[: width - 1].ljust(width - 1)
    print("│" + line + "│")


if __name__ == "__main__":  # pragma: no cover
    from pathlib import Path
    cfg = ClawflowConfig()
    raise SystemExit(render_dashboard(cfg, {}))