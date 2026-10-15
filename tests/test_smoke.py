"""Smoke tests for clawflow v0.1.

These tests run against the real package surface — the dataclass,
the dedup helpers, the atomic state-store, the config loader, the source
registry, the alerters, and the CLI parser — without ever performing a
network call. They are the gate for `pytest -q` in CI.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from clawflow import __version__
from clawflow.alerters import (
    ConsoleAlerter,
    EmailAlerter,
    TelegramAlerter,
    kinds,
)
from clawflow.cli import build_parser, main
from clawflow.config import (
    DEFAULT_CONFIG,
    ClawflowConfig,
    SourceConfig,
    enable_source,
    load_config,
    write_default_config,
)
from clawflow.leads import (
    STATE_VERSION,
    Lead,
    add_leads,
    dedup,
    load_state,
    save_state,
)
from clawflow.scheduler import run_once
from clawflow.sources import BUILTIN_SOURCES, get_source, register, Source


# ---------------------------------------------------------------------------
# Lead dataclass
# ---------------------------------------------------------------------------

def test_lead_dataclass_roundtrip() -> None:
    """Lead serialises and deserialises cleanly via dataclasses + dict."""
    posted = datetime(2026, 10, 15, 10, 0, tzinfo=timezone.utc)
    lead = Lead(
        id="abc123",
        source="github_bounties",
        title="Fix bug in solidity compiler",
        url="https://github.com/example/repo/issues/1",
        posted_at=posted,
        score=80,
        tags=("solidity", "bounty"),
        raw={"bounty_usd": 500},
    )
    blob = json.dumps(lead.to_dict())
    round_tripped = Lead.from_dict(json.loads(blob))
    assert round_tripped.id == lead.id
    assert round_tripped.source == lead.source
    assert round_tripped.title == lead.title
    assert round_tripped.url == lead.url
    assert round_tripped.score == lead.score
    assert round_tripped.tags == lead.tags
    assert round_tripped.raw == lead.raw


def test_lead_dedup_key_is_stable() -> None:
    """``Lead.dedup_key`` is always ``"{source}:{id}"`` — the cross-run identity."""
    a = Lead(
        id="x", source="hn_freelancer", title="t", url="u",
        posted_at=None, score=0,
    )
    b = Lead(
        id="x", source="hn_freelancer", title="other title", url="other url",
        posted_at=None, score=99,
    )
    assert a.dedup_key() == b.dedup_key() == "hn_freelancer:x"


def test_lead_from_dict_tolerates_missing_posted_at() -> None:
    """``from_dict`` does not crash when ``posted_at`` is missing/garbage."""
    lead = Lead.from_dict({
        "id": "1",
        "source": "x",
        "title": "y",
        "url": "z",
        "score": 0,
        "tags": [],
        "raw": {},
    })
    assert lead.posted_at is None
    bad = Lead.from_dict({
        "id": "1", "source": "x", "title": "y", "url": "z",
        "posted_at": "not-an-iso", "score": 0, "tags": [], "raw": {},
    })
    assert bad.posted_at is None


# ---------------------------------------------------------------------------
# Dedup
# ---------------------------------------------------------------------------

def test_dedupe_returns_new_and_repeats() -> None:
    """``dedup`` splits an iterable into ``(new, repeats)`` against a seen-set.

    The seen-set is consulted but NOT mutated — the caller decides when
    to persist.
    """
    seen: set[str] = {"hn_freelancer:a"}  # 'a' is already seen
    fresh = Lead(
        id="a", source="hn_freelancer", title="t", url="u1",
        posted_at=None, score=10,
    )
    novel = Lead(
        id="b", source="hn_freelancer", title="t", url="u2",
        posted_at=None, score=20,
    )
    new, repeats = dedup([fresh, novel], seen)
    assert [l.id for l in new] == ["b"]
    assert [l.id for l in repeats] == ["a"]
    # Caller-owned set is untouched by dedup itself.
    assert seen == {"hn_freelancer:a"}


def test_dedupe_does_not_mutate_seen_for_repeats() -> None:
    """A second occurrence of an already-seen key stays in ``repeats``."""
    seed = Lead(
        id="b", source="web3_sitemap", title="t", url="u",
        posted_at=None, score=0,
    )
    seen: set[str] = set()
    new, repeats = dedup([seed], seen)
    assert [l.id for l in new] == ["b"]
    assert repeats == []
    assert seen == set()  # dedup does NOT mutate seen
    # Second call with the key pre-populated must land it in repeats.
    seen.add("web3_sitemap:b")
    repeat = Lead(
        id="b", source="web3_sitemap", title="t2", url="u2",
        posted_at=None, score=0,
    )
    new2, repeats2 = dedup([repeat], seen)
    assert new2 == []
    assert [l.id for l in repeats2] == ["b"]


# ---------------------------------------------------------------------------
# JSON state store
# ---------------------------------------------------------------------------

def test_load_state_returns_safe_defaults_when_missing(tmp_path: Path) -> None:
    """A missing state file is reported as empty, not a crash."""
    state = load_state(tmp_path / "missing.json")
    assert state["leads"] == []
    assert state["seen"] == []
    assert state["version"] == STATE_VERSION


def test_add_leads_is_atomic_and_merges_seen(tmp_path: Path) -> None:
    """``add_leads`` writes a parseable JSON file and unions the seen-set."""
    target = tmp_path / "state.json"
    a = Lead(
        id="1", source="github_bounties", title="t", url="u",
        posted_at=None, score=10,
    )
    b = Lead(
        id="1", source="github_bounties", title="t", url="u",
        posted_at=None, score=10,
    )  # duplicate of a
    c = Lead(
        id="2", source="github_bounties", title="t2", url="u2",
        posted_at=None, score=20,
    )
    state = add_leads(target, [a, b, c])
    assert {l["id"] for l in state["leads"]} == {"1", "2"}
    raw = json.loads(target.read_text(encoding="utf-8"))
    assert raw["version"] == STATE_VERSION
    assert "github_bounties:1" in raw["seen"]
    assert "github_bounties:2" in raw["seen"]


def test_save_state_handles_corrupt_file(tmp_path: Path) -> None:
    """A corrupt state file is treated as empty, never a hard error."""
    target = tmp_path / "state.json"
    target.write_text("not json", encoding="utf-8")
    state = load_state(target)
    assert state["leads"] == []
    assert state["seen"] == []
    assert state["version"] == STATE_VERSION


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------

def test_default_config_is_valid(tmp_path: Path) -> None:
    """Loading from an empty directory returns the documented defaults."""
    cfg = load_config(tmp_path)
    assert isinstance(cfg, ClawflowConfig)
    assert cfg.interval_minutes == DEFAULT_CONFIG.interval_minutes
    assert cfg.cycle_budget_seconds == DEFAULT_CONFIG.cycle_budget_seconds
    assert cfg.state_path == DEFAULT_CONFIG.state_path


def test_load_config_parses_yaml(tmp_path: Path) -> None:
    """A hand-written clawflow.yml is parsed and overrides take effect."""
    (tmp_path / "clawflow.yml").write_text(
        "interval_minutes: 5\n"
        "cycle_budget_seconds: 30\n"
        "sources:\n"
        "  github_bounties:\n"
        "    enabled: true\n"
        "    orgs: [foo, bar]\n",
        encoding="utf-8",
    )
    cfg = load_config(tmp_path)
    assert cfg.interval_minutes == 5
    assert cfg.cycle_budget_seconds == 30
    assert cfg.sources["github_bounties"].enabled is True
    assert cfg.sources["github_bounties"].extra["orgs"] == ["foo", "bar"]


def test_load_config_rejects_bad_types(tmp_path: Path) -> None:
    """Non-int interval_minutes must raise a clear validation error."""
    (tmp_path / "clawflow.yml").write_text(
        "interval_minutes: 'soon'\n", encoding="utf-8"
    )
    with pytest.raises(ValueError):
        load_config(tmp_path)


def test_load_config_rejects_unknown_source_key(tmp_path: Path) -> None:
    """Unknown extra keys on a known source id are rejected with a clear msg."""
    (tmp_path / "clawflow.yml").write_text(
        "sources:\n  github_bounties:\n    enabled: true\n    bogus: 1\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="bogus"):
        load_config(tmp_path)


def test_enable_source_creates_file(tmp_path: Path) -> None:
    """``enable_source`` writes a fresh clawflow.yml if none exists."""
    target = tmp_path / "clawflow.yml"
    enable_source(target, "hn_freelancer")
    text = target.read_text(encoding="utf-8")
    assert "hn_freelancer" in text
    assert "enabled: true" in text


# ---------------------------------------------------------------------------
# Source registry
# ---------------------------------------------------------------------------

def test_registry_has_six_builtin_sources() -> None:
    """All six built-in source ids must be present in the registry."""
    ids = set(BUILTIN_SOURCES())
    assert {
        "github_bounties",
        "remote3_rss",
        "cryptojobs_sitemap",
        "wordpress_cve",
        "hn_freelancer",
        "reddit_forhire",
    }.issubset(ids)


def test_registry_get_returns_class() -> None:
    """``get_source(id)`` returns the source class (not an instance)."""
    cls = get_source("github_bounties")
    assert cls is not None
    instance = cls()
    assert instance.key == "github_bounties"


def test_registry_unknown_key_returns_none() -> None:
    """Unknown source id must return ``None`` (not raise)."""
    assert get_source("does_not_exist") is None


def test_community_source_register_decorator() -> None:
    """A ``@register``-decorated class lands in the registry."""
    @register
    class _TmpSource(Source):
        key = "tmp_decorator_smoke"
        label = "tmp"
        description = "tmp"
        default_enabled = False

        async def validate_config(self, cfg):
            return True, "ok"

        def fetch(self, state, cfg):
            return self._fetch(state, cfg)

        async def _fetch(self, state, cfg):
            if False:
                yield Lead(
                    id="x", source=self.key, title="t", url="u",
                    posted_at=None, score=0,
                )

    try:
        assert get_source("tmp_decorator_smoke") is _TmpSource
    finally:
        # Clean up so the test is hermetic.
        from clawflow.sources import _REGISTRY  # type: ignore[attr-defined]
        _REGISTRY.pop("tmp_decorator_smoke", None)


# ---------------------------------------------------------------------------
# Alerter registry
# ---------------------------------------------------------------------------

def test_alerters_registry_has_three_kinds() -> None:
    """All three built-in alerters are registered."""
    assert {"console", "telegram", "email"}.issubset(set(kinds()))
    assert isinstance(ConsoleAlerter("console"), ConsoleAlerter)
    assert isinstance(TelegramAlerter("telegram"), TelegramAlerter)
    assert isinstance(EmailAlerter("email"), EmailAlerter)


def test_console_alerter_respects_severity_threshold() -> None:
    """A lead below the alerter's threshold is silently filtered out."""
    alerter = ConsoleAlerter(name="console", severity_threshold=80)
    leads = [
        Lead(id="a", source="x", title="high", url="u1", posted_at=None, score=90),
        Lead(id="b", source="x", title="low", url="u2", posted_at=None, score=10),
    ]
    out = alerter._filter(leads)
    assert [l.id for l in out] == ["a"]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def test_cli_help_exits_zero() -> None:
    """``clawflow --help`` is informative and exits 0."""
    parser = build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["--help"])
    assert exc.value.code == 0


def test_cli_run_once_is_default() -> None:
    """``clawflow run`` with no flag defaults to a real loop; --once switches."""
    parser = build_parser()
    args = parser.parse_args(["run"])
    assert args.command == "run"
    assert args.once is False
    args = parser.parse_args(["run", "--once"])
    assert args.once is True


def test_cli_sources_subcommands_exist() -> None:
    """``sources list``, ``sources add``, ``sources remove`` all parse."""
    parser = build_parser()
    for argv in (
        ["sources", "list"],
        ["sources", "add", "foo"],
        ["sources", "remove", "bar"],
    ):
        args = parser.parse_args(argv)
        assert args.command == "sources"


def test_main_entrypoint_is_callable() -> None:
    """``clawflow.cli.main`` is importable and has the right shape."""
    assert callable(main)
    assert isinstance(build_parser(), argparse.ArgumentParser)
    assert isinstance(__version__, str)


def test_main_help_runs(capsys: pytest.CaptureFixture[str]) -> None:
    """Invoking ``main([\"--help\"])`` exits 0 and prints the help banner."""
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "clawflow" in out.lower()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only smoke check")
def test_cli_module_invocation(tmp_path: Path) -> None:
    """``python -m clawflow --help`` works end-to-end."""
    result = subprocess.run(
        [sys.executable, "-m", "clawflow", "--help"],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0
    assert "clawflow" in result.stdout.lower()