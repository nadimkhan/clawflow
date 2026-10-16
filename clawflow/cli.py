"""Command-line entry point for clawflow.

This module is intentionally ``argparse``-based (no ``click``, no
``typer``) so the binary stays self-contained and easy to read. Every
subcommand wires up to a small handler function in this file; heavier
work lives in ``scheduler.py`` and the source modules.

The CLI exposes:

* ``init``              — generate a documented starter ``clawflow.yml``
* ``auth``              — store tokens for sources and for the Telegram alerter
* ``sources list``      — show every registered source and its on/off state
* ``sources add ID``    — enable a source (or print its dry-run summary)
* ``sources remove ID`` — disable a source
* ``run``               — collect → dedupe → alert cycle, once or forever
* ``status``            — last-cycle counters from ``state.json``
* ``leads``             — browse persisted leads with filters
* ``dashboard``         — ASCII status panel (real TUI ships in v0.2)
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from clawflow import __version__
from clawflow.config import (
    disable_source,
    enable_source,
    load_config,
    write_default_config,
)
from clawflow.dashboard import render_dashboard
from clawflow.auth import cmd_auth_set, cmd_auth_show
from clawflow.leads import (
    STATE_VERSION,
    add_leads,
    dedup,
    load_state,
    save_state,
)
from clawflow.scheduler import run_once, run_forever
from clawflow.sources import BUILTIN_SOURCES, get_source


# ---------------------------------------------------------------------------
# Tiny dataclass used by `clawflow leads` to filter a JSON-loaded blob
# without rehydrating every Lead. Keeps the CLI path allocation-light.
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class _LeadView:
    source: str
    title: str
    url: str
    posted_at: str
    score: int

    @classmethod
    def from_blob(cls, blob: dict) -> "_LeadView":
        return cls(
            source=str(blob.get("source", "")),
            title=str(blob.get("title", "")),
            url=str(blob.get("url", "")),
            posted_at=str(blob.get("posted_at") or ""),
            score=int(blob.get("score", 0) or 0),
        )


# ---------------------------------------------------------------------------
# Parser construction
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    """Build the top-level ``argparse`` parser with all subcommands."""
    parser = argparse.ArgumentParser(
        prog="clawflow",
        description=(
            "clawflow — freelance-opportunity aggregator. "
            "One binary, four signals, zero sales-team BS."
        ),
        epilog=(
            "Docs: https://github.com/nadimkhan/clawflow/blob/main/docs/getting-started.md"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"clawflow {__version__}",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to a clawflow.yml file (default: ./clawflow.yml or ~/.config/clawflow/config.yml).",
    )
    parser.add_argument(
        "--log-level",
        default=None,
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        help="Override logging level from config.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # init ------------------------------------------------------------------
    p_init = sub.add_parser(
        "init",
        help="Generate a starter clawflow.yml in the current directory.",
        description=(
            "Write a documented clawflow.yml with all sources disabled. "
            "Safe to run multiple times — refuses to overwrite an existing "
            "file unless --force is passed."
        ),
    )
    p_init.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing clawflow.yml without prompting.",
    )
    p_init.set_defaults(handler=_cmd_init)

    # auth ------------------------------------------------------------------
    p_auth = sub.add_parser(
        "auth",
        help="Store API tokens for sources and the Telegram alerter.",
        description=(
            "Persists small secrets (GitHub PAT, Telegram bot token + "
            "chat id) to a JSON file at the platform-default config path. "
            "Tokens are also written to your keyring if `keyring` is "
            "installed. See clawflow.auth for the storage layer."
        ),
    )
    p_auth_sub = p_auth.add_subparsers(dest="auth_command", required=True)
    p_auth_sub.add_parser("show", help="Show which tokens are set (redacted).").set_defaults(
        handler=lambda args, parser: cmd_auth_show(args)
    )
    p_auth_gh = p_auth_sub.add_parser(
        "github", help="Save a GitHub personal access token."
    )
    p_auth_gh.add_argument("--token", required=True, help="GitHub PAT (any scope-less read token is fine).")
    p_auth_gh.add_argument("--path", default=None, help="Override auth file path.")
    p_auth_gh.set_defaults(handler=lambda args, parser: cmd_auth_set(_kind_to_args("github", args)))

    p_auth_tg = p_auth_sub.add_parser(
        "telegram", help="Save a Telegram bot token and (optionally) chat id."
    )
    p_auth_tg.add_argument("--token", required=True, help="Bot token from @BotFather.")
    p_auth_tg.add_argument("--chat-id", default="", help="Chat ID to send alerts to.")
    p_auth_tg.add_argument("--path", default=None, help="Override auth file path.")
    p_auth_tg.set_defaults(handler=lambda args, parser: cmd_auth_set(_kind_to_args("telegram", args)))

    # sources ---------------------------------------------------------------
    p_sources = sub.add_parser(
        "sources",
        help="Inspect or edit the registered sources.",
        description="Manage the source registry (built-in and community).",
    )
    p_sources_sub = p_sources.add_subparsers(dest="sources_command", required=True)
    p_sources_list = p_sources_sub.add_parser(
        "list",
        help="List every registered source id and whether it's enabled.",
    )
    p_sources_list.set_defaults(handler=_cmd_sources_list)

    p_sources_add = p_sources_sub.add_parser(
        "add",
        help="Enable a source (or print its dry-run summary).",
        description=(
            "With --dry-run, prints the source metadata without enabling "
            "or making any network call. Without it, flips the source to "
            "enabled in clawflow.yml (creating the file if needed)."
        ),
    )
    p_sources_add.add_argument("source_id", help="Source id to add, e.g. github_bounties.")
    p_sources_add.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the source metadata without enabling it.",
    )
    p_sources_add.set_defaults(handler=_cmd_sources_add)

    p_sources_remove = p_sources_sub.add_parser(
        "remove",
        help="Disable a source in clawflow.yml.",
    )
    p_sources_remove.add_argument("source_id", help="Source id to disable.")
    p_sources_remove.set_defaults(handler=_cmd_sources_remove)

    # run -------------------------------------------------------------------
    p_run = sub.add_parser(
        "run",
        help="Collect leads, dedupe, and alert. Default subcommand.",
        description=(
            "Without --once, clawflow runs forever, polling every "
            "interval_minutes from your config. With --once, one cycle "
            "and exit (good for cron)."
        ),
    )
    p_run.add_argument(
        "--once",
        action="store_true",
        help="Run a single collect → dedupe → alert cycle, then exit.",
    )
    p_run.add_argument(
        "--interval-minutes",
        type=int,
        default=None,
        help="Override interval_minutes from config.",
    )
    p_run.set_defaults(handler=_cmd_run)

    # status ----------------------------------------------------------------
    p_status = sub.add_parser(
        "status",
        help="Print last-cycle stats from state.json.",
        description=(
            "Reads the configured state.json and prints version, lead "
            "count, and updated_at timestamp."
        ),
    )
    p_status.set_defaults(handler=_cmd_status)

    # leads -----------------------------------------------------------------
    p_leads = sub.add_parser(
        "leads",
        help="Browse the collected leads.",
        description="Filter and display the leads persisted in state.json.",
    )
    p_leads.add_argument(
        "--since",
        default="24h",
        help="Only show leads posted within this window (e.g. 6h, 24h, 7d). Default: 24h.",
    )
    p_leads.add_argument(
        "--source",
        action="append",
        default=None,
        help="Filter by source id (repeatable).",
    )
    p_leads.add_argument(
        "--min-score",
        type=int,
        default=0,
        help="Only show leads with score >= N.",
    )
    p_leads.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Maximum number of leads to display.",
    )
    p_leads.set_defaults(handler=_cmd_leads)

    # dashboard (stub for v0.2) --------------------------------------------
    p_dash = sub.add_parser(
        "dashboard",
        help="Open the terminal dashboard (v0.2 — currently an ASCII stub).",
        description=(
            "In v0.1 this prints a static ASCII status panel. The "
            "interactive curses/textual UI ships in v0.2 — see "
            "docs/roadmap.md."
        ),
    )
    p_dash.set_defaults(handler=_cmd_dashboard)

    # leads-ui (local web tracker) ----------------------------------------
    p_leads_ui = sub.add_parser(
        "leads-ui",
        help="Run the local leads-tracker web UI (loopback only by default).",
        description=(
            "Opens a small FastAPI app on 127.0.0.1:5555 (default) so you can "
            "see all leads with clickable URLs and mark them interested / "
            "applied / rejected / done. The DB is a single SQLite file in the "
            "current directory; loopback-only by default — pass --host to "
            "expose on your LAN. Run 'clawflow sources list' and then click "
            "around; nothing sends anything over the network from your machine."
        ),
    )
    p_leads_ui.add_argument("--host", default="127.0.0.1", help="bind address")
    p_leads_ui.add_argument("--port", type=int, default=5555, help="port")
    p_leads_ui.add_argument(
        "--db",
        default=None,
        help="path to SQLite DB (default: ./clawflow-leads.db)",
    )
    p_leads_ui.set_defaults(handler=_cmd_leads_ui)

    return parser


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------

def _cmd_init(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    """Write a starter ``clawflow.yml`` in the current directory."""
    target = Path.cwd() / "clawflow.yml"
    if target.exists() and not args.force:
        parser.error(
            f"{target} already exists. Re-run with --force to overwrite."
        )
    write_default_config(target)
    print(f"wrote {target}")
    print("Next: edit it, then `clawflow run --once`.")
    return 0


def _cmd_sources_list(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    """Print every registered source and its enabled flag from the config."""
    cfg = load_config(Path.cwd(), config_path=_config_arg(args))
    enabled_map = {sid: sc.enabled for sid, sc in cfg.sources.items()}
    for sid in BUILTIN_SOURCES():
        enabled = enabled_map.get(sid, False)
        marker = "on " if enabled else "off"
        print(f"[{marker}] {sid}")
    return 0


def _cmd_sources_add(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    """Enable a source in clawflow.yml (or print a dry-run summary)."""
    if args.dry_run:
        cls = get_source(args.source_id)
        if cls is None:
            parser.error(f"unknown source: {args.source_id!r}")
        print(f"source:        {cls.key}")
        print(f"label:         {cls.label}")
        print(f"description:   {cls.description}")
        print(f"default_on:    {cls.default_enabled}")
        print()
        print("(no live query to print until the source is enabled and run)")
        return 0
    target = Path.cwd() / "clawflow.yml"
    enable_source(target, args.source_id)
    print(f"enabled {args.source_id} in {target}")
    return 0


def _cmd_sources_remove(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    """Disable a source in clawflow.yml."""
    target = Path.cwd() / "clawflow.yml"
    disable_source(target, args.source_id)
    print(f"disabled {args.source_id} in {target}")
    return 0


def _cmd_run(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    """Dispatch to the synchronous ``run_once`` or the async ``run_forever``."""
    cfg = load_config(Path.cwd(), config_path=_config_arg(args))
    if args.interval_minutes is not None:
        cfg.interval_minutes = args.interval_minutes
    if args.once:
        return run_once(cfg)
    # run_forever is async; bridge it into the sync CLI.
    import asyncio
    return asyncio.run(run_forever(cfg))


def _cmd_status(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    """Print last-cycle stats from state.json."""
    cfg = load_config(Path.cwd(), config_path=_config_arg(args))
    state_blob = load_state(cfg.state_path)
    leads_blob = state_blob.get("leads", []) or []
    print(f"state_path      : {cfg.state_path}")
    print(f"version         : {state_blob.get('version', STATE_VERSION)}")
    print(f"leads persisted : {len(leads_blob)}")
    print(f"updated_at      : {state_blob.get('updated_at', 'never')}")
    return 0


def _cmd_leads(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    """Print a filtered view of leads from state.json."""
    cfg = load_config(Path.cwd(), config_path=_config_arg(args))
    state_blob = load_state(cfg.state_path)
    sources_filter = set(args.source) if args.source else None
    views = [
        _LeadView.from_blob(item)
        for item in state_blob.get("leads", [])
        if (sources_filter is None or item.get("source") in sources_filter)
        and int(item.get("score", 0) or 0) >= args.min_score
    ]
    if not views:
        print("(no leads match the filter)")
        return 0
    # newest-first by posted_at (lexical on ISO-8601 strings is monotonic).
    views.sort(key=lambda v: v.posted_at, reverse=True)
    for v in views[: args.limit]:
        print(f"[{v.score:>3}] {v.source:<18} {v.title}")
        print(f"      {v.url}")
    return 0


def _cmd_dashboard(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    """Print the v0.1 ASCII dashboard stub."""
    cfg = load_config(Path.cwd(), config_path=_config_arg(args))
    state_blob = load_state(cfg.state_path)
    return render_dashboard(cfg, state_blob)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _config_arg(args: argparse.Namespace) -> Path | None:
    """Return ``args.config`` if it was supplied, else ``None``."""
    return getattr(args, "config", None)


class _AuthArgs(argparse.Namespace):
    """Tiny shim so `clawflow.auth.cmd_auth_set` can read a `.kind` attribute."""

    def __init__(self, kind: str, token: str = "", chat_id: str = "", path: str | None = None):
        super().__init__()
        self.kind = kind
        self.token = token
        self.chat_id = chat_id
        self.path = path


def _kind_to_args(kind: str, args: argparse.Namespace) -> argparse.Namespace:
    """Re-bag the parsed subparser args into the shape ``cmd_auth_set`` expects."""
    return _AuthArgs(
        kind=kind,
        token=getattr(args, "token", "") or "",
        chat_id=getattr(args, "chat_id", "") or "",
        path=getattr(args, "path", None),
    )


def _cmd_leads_ui(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    """Launch the FastAPI leads-tracker app.

    Blocks until killed. Open http://127.0.0.1:5555/ in a browser.
    """
    import argparse  # noqa: F401  -- keep import for IDE happiness
    from pathlib import Path as _P
    from clawflow import leads_ui as _ui
    argv = [
        "--host", str(args.host),
        "--port", str(args.port),
    ]
    if args.db:
        argv += ["--db", args.db]
    return _ui.main(argv)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv: Sequence[str] | None = None) -> int:
    """Programmatic entry point — also referenced by ``pyproject.toml``."""
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = getattr(args, "handler", None)
    if handler is None:  # pragma: no cover — argparse requires=True prevents this
        parser.print_help()
        return 2
    return handler(args, parser)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())