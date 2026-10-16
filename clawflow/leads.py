"""Lead model, dedup helpers, and atomic JSON state store for clawflow.

This module is intentionally tiny and dependency-free (stdlib only). It defines
the canonical ``Lead`` record that every Source yields, plus helpers for:

* building a stable cross-run identity (``Lead.dedup_key``)
* splitting a fresh batch into (new, repeats) against a seen-set (``dedup``)
* reading and atomically writing the on-disk state JSON (``load_state``,
  ``save_state``, ``add_leads``)

State file layout (JSON, top-level keys)::

    {
      "version": 1,
      "leads":   [ {...Lead.to_dict...}, ... ],
      "seen":    ["source:id", ...],
      "updated_at": "2026-01-01T00:00:00+00:00",
      "<source_key>": { ... source-specific cache ... }
    }

The ``add_leads`` helper updates ``leads`` and ``seen`` together so that the
seen-set is always a superset of every lead currently in ``leads``.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

log = logging.getLogger("clawflow.leads")

# Bumped whenever the on-disk schema changes in a non-backwards-compatible way.
STATE_VERSION = 1


def _utcnow_iso() -> str:
    """Return current UTC time as ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class Lead:
    """A normalized lead / job / bounty / CVE record produced by a Source.

    ``id`` must be unique within ``source``. ``url`` is stored as a plain
    string (validation is the source's responsibility); the dataclass itself
    is intentionally lightweight so it pickles, JSON-serializes, and round-trips
    through ``from_dict`` cleanly.
    """

    id: str
    source: str
    title: str
    url: str
    posted_at: datetime | None = None
    score: int = 0
    tags: tuple[str, ...] = ()
    raw: dict[str, Any] = field(default_factory=dict)

    def dedup_key(self) -> str:
        """Stable cross-run identity: ``"{source}:{id}"``."""
        return f"{self.source}:{self.id}"

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dict."""
        d = asdict(self)
        if isinstance(self.posted_at, datetime):
            d["posted_at"] = self.posted_at.isoformat()
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Lead:
        """Inverse of :meth:`to_dict`; tolerates missing / malformed fields."""
        d = dict(d)
        posted = d.get("posted_at")
        if isinstance(posted, str):
            try:
                d["posted_at"] = datetime.fromisoformat(posted)
            except ValueError:
                d["posted_at"] = None
        d["tags"] = tuple(d.get("tags") or ())
        return cls(**d)


def dedup(
    leads: Iterable[Lead],
    seen: set[str],
) -> tuple[list[Lead], list[Lead]]:
    """Split ``leads`` into ``(new, repeats)`` based on dedup_key membership.

    ``seen`` is consulted but NOT mutated; the caller decides when to persist.
    A repeated key shows up in ``repeats`` so callers can log or count it.
    """
    new: list[Lead] = []
    repeats: list[Lead] = []
    for lead in leads:
        key = lead.dedup_key()
        if key in seen:
            repeats.append(lead)
        else:
            new.append(lead)
    return new, repeats


def load_state(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Load JSON state from disk; return sane defaults on missing/corrupt file."""
    p = Path(path)
    if not p.exists():
        return {"leads": [], "seen": [], "version": STATE_VERSION}
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        log.warning("load_state: unreadable %s (%s); returning empty", p, exc)
        return {"leads": [], "seen": [], "version": STATE_VERSION}
    try:
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("top-level JSON is not an object")
    except (json.JSONDecodeError, ValueError) as exc:
        log.warning("load_state: corrupt JSON in %s (%s); returning empty", p, exc)
        return {"leads": [], "seen": [], "version": STATE_VERSION}
    data.setdefault("leads", [])
    data.setdefault("seen", [])
    data.setdefault("version", STATE_VERSION)
    return data


def save_state(path: str | os.PathLike[str], data: dict[str, Any]) -> None:
    """Atomically write JSON state via temp-file + ``os.replace``.

    The temp file lives in the same directory so ``os.replace`` is atomic on
    POSIX and on Windows. On any failure we attempt to remove the temp file
    so we never leave cruft behind.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=p.name + ".", dir=str(p.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True, default=str)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, p)
    except Exception:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise


def add_leads(
    path: str | os.PathLike[str],
    leads: Iterable[Lead],
    *,
    merge_seen: bool = True,
    sqlite_path: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Append new leads to ``path``, update the seen-set, persist atomically.

    ``merge_seen=True`` (the common case): keys already in the on-disk seen-set
    are not re-added; existing leads are not re-written. The seen-set on disk
    always ends up as a strict superset of the lead IDs in the file.

    If ``sqlite_path`` is provided, also insert the same leads into the
    SQLite store (used by the leads-ui web app). This is a one-way write:
    SQLite errors are logged but never break the JSON store; the web UI
    is an optional consumer.

    Returns the updated state dict so callers can inspect the post-write state
    without re-reading the file.
    """
    state = load_state(path)
    seen: set[str] = set(state.get("seen", []) or []) if merge_seen else set()
    existing_leads: list[dict[str, Any]] = list(state.get("leads", []) or [])
    appended: list[dict[str, Any]] = []
    appended_leads: list[Lead] = []
    for lead in leads:
        key = lead.dedup_key()
        if key in seen:
            continue
        seen.add(key)
        appended.append(lead.to_dict())
        appended_leads.append(lead)
    state["leads"] = existing_leads + appended
    state["seen"] = sorted(seen)
    state["version"] = STATE_VERSION
    state["updated_at"] = _utcnow_iso()
    save_state(path, state)
    if sqlite_path is not None and appended_leads:
        try:
            from clawflow import store_sqlite  # local import: don't break no-httpx imports
            store_sqlite.init_db(sqlite_path)
            with store_sqlite.connect(sqlite_path) as conn:
                store_sqlite.upsert_many(conn, appended_leads)
        except Exception as exc:  # noqa: BLE001
            log.warning("add_leads: sqlite write skipped (%s)", exc)
    return state