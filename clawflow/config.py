"""YAML configuration loader and validation for clawflow.

The schema is intentionally flat — a small dataclass tree that mirrors
the YAML structure one-to-one. Validation is done by hand (no pydantic)
so the import path stays fast and the error messages stay clear.

Source-specific config keys mirror the attributes the source modules
read via ``getattr``:

* ``github_bounties``   — ``orgs``, ``languages``, ``min_bounty``,
                          ``max_age_days``, ``global_search``
* ``remote3_rss``       — no config (uses defaults)
* ``cryptojobs_sitemap``— no config (uses defaults)
* ``wordpress_cve``     — ``slugs``, ``min_severity``
* ``hn_freelancer``     — ``query``, ``min_points``, ``lookback_hours``
* ``reddit_forhire``    — ``subreddits``
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import yaml


# ---------------------------------------------------------------------------
# Dataclass schema
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SourceConfig:
    """Per-source configuration block."""

    enabled: bool = False
    severity_threshold: int = 0
    timeout_seconds: int = 30
    extra: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DedupConfig:
    """Dedup behaviour — keeps the cross-source identity stable."""

    retention_days: int = 30


@dataclass(frozen=True)
class LoggingConfig:
    """Logging configuration."""

    level: str = "INFO"
    format: str = "%(asctime)s %(levelname)s %(message)s"


@dataclass(frozen=True)
class ClawflowConfig:
    """Top-level configuration object."""

    state_path: Path = field(default_factory=lambda: Path("./state.json"))
    interval_minutes: int = 15
    cycle_budget_seconds: int = 120
    sources: Mapping[str, SourceConfig] = field(default_factory=dict)
    alerters: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    dedup: DedupConfig = field(default_factory=DedupConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)


# Module-level sentinel for tests that need the exact defaults.
DEFAULT_CONFIG = ClawflowConfig()


# Recognised source ids — every built-in plus the umbrella catch-all.
KNOWN_SOURCE_IDS: tuple[str, ...] = (
    "github_bounties",
    "remote3_rss",
    "cryptojobs_sitemap",
    "wordpress_cve",
    "hn_freelancer",
    "reddit_forhire",
)

# Per-source extra-key whitelist (anything else is rejected).
_SOURCE_EXTRA_KEYS: dict[str, frozenset[str]] = {
    "github_bounties": frozenset({
        "orgs", "languages", "min_bounty", "max_age_days", "global_search",
    }),
    "remote3_rss": frozenset(),
    "cryptojobs_sitemap": frozenset(),
    "wordpress_cve": frozenset({"slugs", "min_severity"}),
    "hn_freelancer": frozenset({"query", "min_points", "lookback_hours"}),
    "reddit_forhire": frozenset({"subreddits"}),
}


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def _candidate_paths(workdir: Path, config_path: Path | None) -> list[Path]:
    """Return the candidate config file locations in lookup order."""
    if config_path is not None:
        return [config_path]
    candidates = [
        workdir / "clawflow.yml",
        workdir / "clawflow.yaml",
    ]
    if xdg := os.environ.get("XDG_CONFIG_HOME"):
        candidates.append(Path(xdg) / "clawflow" / "config.yml")
    home_cfg = Path.home() / ".config" / "clawflow" / "config.yml"
    candidates.append(home_cfg)
    return candidates


def load_config(workdir: Path, config_path: Path | None = None) -> ClawflowConfig:
    """Load and validate a clawflow.yml, falling back to defaults.

    Resolution order:
    1. ``config_path`` argument (if given).
    2. ``./clawflow.yml`` in ``workdir``.
    3. ``./clawflow.yaml`` in ``workdir``.
    4. ``$XDG_CONFIG_HOME/clawflow/config.yml``.
    5. ``~/.config/clawflow/config.yml``.

    If no candidate exists, the documented defaults are returned.
    """
    raw: Mapping[str, Any] = {}
    for candidate in _candidate_paths(workdir, config_path):
        if candidate.is_file():
            raw = yaml.safe_load(candidate.read_text(encoding="utf-8")) or {}
            break

    if not isinstance(raw, Mapping):
        raise ValueError(f"config must be a YAML mapping, got {type(raw).__name__}")

    # state_path
    state_path = Path(raw.get("state_path", DEFAULT_CONFIG.state_path))

    # interval_minutes
    interval = _coerce_int(raw.get("interval_minutes"), DEFAULT_CONFIG.interval_minutes, "interval_minutes")
    if interval < 1:
        raise ValueError("interval_minutes must be >= 1")

    # cycle_budget_seconds
    budget = _coerce_int(
        raw.get("cycle_budget_seconds"),
        DEFAULT_CONFIG.cycle_budget_seconds,
        "cycle_budget_seconds",
    )
    if budget < 5:
        raise ValueError("cycle_budget_seconds must be >= 5")

    # sources
    sources_block = raw.get("sources") or {}
    if not isinstance(sources_block, Mapping):
        raise ValueError("sources must be a mapping of source_id -> config")
    sources: dict[str, SourceConfig] = {}
    for sid, sraw in sources_block.items():
        if not isinstance(sraw, Mapping):
            raise ValueError(f"sources.{sid} must be a mapping")
        enabled = bool(sraw.get("enabled", False))
        sev = _coerce_int(sraw.get("severity_threshold", 0), 0, f"sources.{sid}.severity_threshold")
        if not 0 <= sev <= 100:
            raise ValueError(f"sources.{sid}.severity_threshold must be 0..100")
        timeout = _coerce_int(sraw.get("timeout_seconds", 30), 30, f"sources.{sid}.timeout_seconds")
        if timeout < 1:
            raise ValueError(f"sources.{sid}.timeout_seconds must be >= 1")

        extra: dict[str, Any] = {}
        allowed = _SOURCE_EXTRA_KEYS.get(str(sid))
        if allowed is not None:
            for key, val in sraw.items():
                if key in {"enabled", "severity_threshold", "timeout_seconds"}:
                    continue
                if key not in allowed:
                    raise ValueError(
                        f"sources.{sid}.{key} is not a recognised option"
                    )
                extra[key] = val
        else:
            # Unknown source id — accept any extra keys for plugin freedom.
            for key, val in sraw.items():
                if key in {"enabled", "severity_threshold", "timeout_seconds"}:
                    continue
                extra[key] = val

        sources[str(sid)] = SourceConfig(
            enabled=enabled,
            severity_threshold=sev,
            timeout_seconds=timeout,
            extra=extra,
        )

    # alerters
    alerters_raw = raw.get("alerters") or []
    if not isinstance(alerters_raw, list):
        raise ValueError("alerters must be a list")
    alerters: list[Mapping[str, Any]] = []
    for i, item in enumerate(alerters_raw):
        if not isinstance(item, Mapping):
            raise ValueError(f"alerters[{i}] must be a mapping")
        if "type" not in item:
            raise ValueError(f"alerters[{i}].type is required")
        alerters.append(dict(item))

    # dedup
    dedup_raw = raw.get("dedup") or {}
    if not isinstance(dedup_raw, Mapping):
        raise ValueError("dedup must be a mapping")
    retention = _coerce_int(dedup_raw.get("retention_days", 30), 30, "dedup.retention_days")
    if retention < 1:
        raise ValueError("dedup.retention_days must be >= 1")
    dedup_cfg = DedupConfig(retention_days=retention)

    # logging
    log_raw = raw.get("logging") or {}
    if not isinstance(log_raw, Mapping):
        raise ValueError("logging must be a mapping")
    log_level = str(log_raw.get("level", "INFO")).upper()
    if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
        raise ValueError(f"logging.level must be one of DEBUG/INFO/WARNING/ERROR, got {log_level!r}")
    log_cfg = LoggingConfig(level=log_level, format=str(log_raw.get("format", DEFAULT_CONFIG.logging.format)))

    return ClawflowConfig(
        state_path=state_path,
        interval_minutes=interval,
        cycle_budget_seconds=budget,
        sources=sources,
        alerters=tuple(alerters),
        dedup=dedup_cfg,
        logging=log_cfg,
    )


# ---------------------------------------------------------------------------
# Edit helpers (used by `clawflow init`, `sources add`, `sources remove`)
# ---------------------------------------------------------------------------

DEFAULT_CONFIG_TEXT = (
    "# clawflow configuration — generated by `clawflow init`.\n"
    "#\n"
    "# All four built-in source CATEGORIES (six sources) are OFF by default.\n"
    "# Edit this file and flip `enabled: true` under any source you want to watch.\n"
    "#\n"
    "# Full reference: docs/sources.md and docs/alerters.md.\n"
    "\n"
    "state_path: ./state.json\n"
    "interval_minutes: 15\n"
    "cycle_budget_seconds: 120\n"
    "\n"
    "dedup:\n"
    "  retention_days: 30\n"
    "\n"
    "logging:\n"
    "  level: INFO\n"
    "  format: \"%(asctime)s %(levelname)s %(message)s\"\n"
    "\n"
    "sources:\n"
    "  github_bounties:\n"
    "    enabled: false\n"
    "    orgs: [OpenZeppelin, ethereum-optimism, aave, uniswap, chainlink]\n"
    "    languages: [solidity, typescript, rust, go]\n"
    "    min_bounty: 0\n"
    "    max_age_days: 14\n"
    "    global_search: false\n"
    "  remote3_rss:\n"
    "    enabled: false\n"
    "  cryptojobs_sitemap:\n"
    "    enabled: false\n"
    "  wordpress_cve:\n"
    "    enabled: false\n"
    "    slugs: [elementor, woocommerce, wordpress-seo, wpforms-lite, yoast-seo]\n"
    "    min_severity: medium\n"
    "  hn_freelancer:\n"
    "    enabled: false\n"
    "    query: \"freelance solidity OR smart contract OR rust web3\"\n"
    "    min_points: 5\n"
    "    lookback_hours: 24\n"
    "  reddit_forhire:\n"
    "    enabled: false\n"
    "    subreddits: [forhire]\n"
    "\n"
    "alerters:\n"
    "  - type: console\n"
)


def write_default_config(target: Path) -> None:
    """Write the documented starter ``clawflow.yml``."""
    target.write_text(DEFAULT_CONFIG_TEXT, encoding="utf-8")


def enable_source(target: Path, source_id: str) -> None:
    """Flip ``sources.<source_id>.enabled`` to True in a YAML file (in place)."""
    if not target.is_file():
        write_default_config(target)
    _flip_source(target, source_id, enabled=True)


def disable_source(target: Path, source_id: str) -> None:
    """Flip ``sources.<source_id>.enabled`` to False in a YAML file (in place)."""
    if not target.is_file():
        return
    _flip_source(target, source_id, enabled=False)


def _flip_source(target: Path, source_id: str, *, enabled: bool) -> None:
    """Naive text-level flip of ``enabled:`` under a source block.

    We avoid re-parsing/re-serialising the whole YAML to preserve the
    user's comments and formatting. A comment is added if the block
    didn't exist before.
    """
    text = target.read_text(encoding="utf-8")
    lines = text.splitlines()
    header = f"  {source_id}:"
    try:
        block_idx = next(i for i, ln in enumerate(lines) if ln.strip().startswith(header))
    except StopIteration:
        # Append a minimal block — preserve trailing newline.
        if text and not text.endswith("\n"):
            text += "\n"
        addition = f"\nsources:\n  {source_id}:\n    enabled: {str(enabled).lower()}\n"
        if "sources:" not in text:
            text += addition.lstrip("\n")
        else:
            text += addition
        target.write_text(text, encoding="utf-8")
        return

    # Find the indented keys under the block.
    i = block_idx + 1
    found = False
    while i < len(lines) and (lines[i].startswith("    ") or not lines[i].strip()):
        stripped = lines[i].strip()
        if stripped.startswith("enabled:"):
            lines[i] = f"    enabled: {str(enabled).lower()}"
            found = True
            break
        i += 1
    if not found:
        # No enabled key — insert one immediately after the header.
        lines.insert(block_idx + 1, f"    enabled: {str(enabled).lower()}")

    target.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _coerce_int(value: Any, default: int, key: str) -> int:
    """Coerce ``value`` to ``int`` with a clear error on failure."""
    if value is None:
        return default
    if isinstance(value, bool):  # bool is a subclass of int — reject explicitly.
        raise ValueError(f"{key} must be an integer, got bool")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError as exc:
            raise ValueError(f"{key} must be an integer, got {value!r}") from exc
    raise ValueError(f"{key} must be an integer, got {type(value).__name__}")


if __name__ == "__main__":  # pragma: no cover
    import argparse as _ap
    p = _ap.ArgumentParser(description="Print the effective clawflow config.")
    p.add_argument("--config", type=Path, default=None)
    a = p.parse_args()
    cfg = load_config(Path.cwd(), a.config)
    import json
    print(json.dumps(cfg, default=lambda o: str(o), indent=2))