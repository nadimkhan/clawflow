"""SQLite-backed lead store with status tracking.

Replaces the JSON state file (``state.json``) for the *primary* durable
copy of leads. JSON state.json is still written by ``save_state`` for
backwards compatibility and for the ``clawflow leads`` CLI view, but
the source of truth for the web UI and status tracking is SQLite.

Schema (single table, ``leads``):

* ``id``        text primary key — same dedup_key() used by clawflow.leads
* ``source``    text not null   — github_bounties, wordpress_cve, ...
* ``title``     text not null
* ``url``       text not null
* ``posted_at`` text — ISO-8601 from upstream, nullable
* ``score``     integer        — heuristic 0-100
* ``tags``      text           — comma-joined for SQLite's lack of arrays
* ``raw``       text           — JSON blob of upstream payload
* ``status``    text           — new | interested | applied | rejected | done
* ``note``      text           — free-form user note
* ``created_at`` text          — ISO when we first saw it
* ``updated_at`` text          — ISO when status/note last changed

Status defaults to ``new``. The web UI (``leads_ui.py``) flips it to one
of the other four values and persists.

Migration
---------
``init_db()`` will, on first run, copy every lead from the JSON state
file into SQLite (idempotent via INSERT OR IGNORE).
"""
from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

log = logging.getLogger("clawflow.store_sqlite")

# Allowed status values; anything else is rejected by ``set_status``.
STATUS_NEW = "new"
STATUS_INTERESTED = "interested"
STATUS_APPLIED = "applied"
STATUS_REJECTED = "rejected"
STATUS_DONE = "done"
ALLOWED_STATUSES: tuple[str, ...] = (
    STATUS_NEW, STATUS_INTERESTED, STATUS_APPLIED, STATUS_REJECTED, STATUS_DONE,
)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id           TEXT PRIMARY KEY,
    source       TEXT NOT NULL,
    title        TEXT NOT NULL,
    url          TEXT NOT NULL,
    posted_at    TEXT,
    score        INTEGER DEFAULT 0,
    tags         TEXT DEFAULT '',
    raw          TEXT DEFAULT '{}',
    status       TEXT NOT NULL DEFAULT 'new',
    note         TEXT DEFAULT '',
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS leads_status_idx ON leads(status);
CREATE INDEX IF NOT EXISTS leads_source_idx ON leads(source);
CREATE INDEX IF NOT EXISTS leads_created_at_idx ON leads(created_at DESC);
"""


@dataclass
class LeadRow:
    id: str
    source: str
    title: str
    url: str
    posted_at: str | None
    score: int
    tags: tuple[str, ...]
    raw: dict[str, Any]
    status: str
    note: str
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "LeadRow":
        # tags is stored as comma-joined text; restore to tuple
        tags_str = row["tags"] or ""
        tags = tuple(t for t in tags_str.split(",") if t)
        try:
            raw = json.loads(row["raw"] or "{}")
        except (TypeError, ValueError):
            raw = {}
        return cls(
            id=row["id"],
            source=row["source"],
            title=row["title"],
            url=row["url"],
            posted_at=row["posted_at"],
            score=int(row["score"] or 0),
            tags=tags,
            raw=raw,
            status=row["status"],
            note=row["note"] or "",
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


def default_db_path() -> Path:
    """Resolve the DB path next to state.json by default.

    State file default is ``./state.json``; DB lives at ``./clawflow-leads.db``.
    """
    return Path("./clawflow-leads.db").resolve()


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connect(path: str | Path):
    """Yield a sqlite3 connection with row_factory and WAL mode set."""
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), isolation_level=None)  # autocommit on/off is per-statement
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
    finally:
        conn.close()


def init_db(path: str | Path | None = None) -> Path:
    """Create the schema if it doesn't exist; return the resolved db path."""
    db = Path(path) if path else default_db_path()
    with connect(db) as conn:
        conn.executescript(_SCHEMA)
    log.info("init_db: schema ready at %s", db)
    return db


def upsert_lead(conn: sqlite3.Connection, lead: Any) -> bool:
    """Insert-or-ignore a lead. Returns True if newly inserted.

    ``lead`` may be a ``Lead`` dataclass (from clawflow.leads) or a dict
    — whichever the caller has handy.
    """
    if hasattr(lead, "to_dict"):
        d = lead.to_dict()
    elif isinstance(lead, dict):
        d = lead
    else:
        raise TypeError(f"upsert_lead: expected Lead or dict, got {type(lead)}")

    now = _utcnow()
    tags = d.get("tags") or []
    if isinstance(tags, (list, tuple)):
        tags_csv = ",".join(str(t) for t in tags)
    else:
        tags_csv = str(tags or "")
    raw_json = json.dumps(d.get("raw") or {}, ensure_ascii=False)
    posted = d.get("posted_at")
    if posted is not None and not isinstance(posted, str):
        posted = posted.isoformat() if hasattr(posted, "isoformat") else str(posted)
    try:
        score = int(d.get("score") or 0)
    except (TypeError, ValueError):
        score = 0

    cur = conn.execute(
        """
        INSERT OR IGNORE INTO leads
            (id, source, title, url, posted_at, score, tags, raw, status, note, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            d["id"],
            d["source"],
            d.get("title") or "",
            d.get("url") or "",
            posted,
            score,
            tags_csv,
            raw_json,
            STATUS_NEW,
            "",
            now,
            now,
        ),
    )
    return cur.rowcount > 0


def upsert_many(conn: sqlite3.Connection, leads: Iterable[Any]) -> int:
    """Insert many leads; return count of NEW inserts (deduped by id)."""
    n_new = 0
    for lead in leads:
        if upsert_lead(conn, lead):
            n_new += 1
    return n_new


def list_leads(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
    source: str | None = None,
    limit: int = 200,
) -> list[LeadRow]:
    """Fetch leads, newest first. Optional status/source filter."""
    clauses = []
    args: list[Any] = []
    if status:
        clauses.append("status = ?")
        args.append(status)
    if source:
        clauses.append("source = ?")
        args.append(source)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    args.append(int(limit))
    rows = conn.execute(
        f"SELECT * FROM leads {where} ORDER BY datetime(created_at) DESC LIMIT ?",
        args,
    ).fetchall()
    return [LeadRow.from_row(r) for r in rows]


def count_by_status(conn: sqlite3.Connection) -> dict[str, int]:
    """Return a small dict like {'new': 12, 'applied': 3, ...} for the dashboard."""
    out = {s: 0 for s in ALLOWED_STATUSES}
    rows = conn.execute(
        "SELECT status, COUNT(*) AS n FROM leads GROUP BY status"
    ).fetchall()
    for r in rows:
        out[r["status"]] = int(r["n"])
    return out


def set_status(
    conn: sqlite3.Connection,
    lead_id: str,
    status: str,
    note: str | None = None,
) -> bool:
    """Update one lead's status (and optionally the note). Returns True if updated."""
    if status not in ALLOWED_STATUSES:
        raise ValueError(f"status must be one of {ALLOWED_STATUSES!r}, got {status!r}")
    sets = ["status = ?", "updated_at = ?"]
    args: list[Any] = [status, _utcnow()]
    if note is not None:
        sets.append("note = ?")
        args.append(note)
    args.append(lead_id)
    cur = conn.execute(
        f"UPDATE leads SET {', '.join(sets)} WHERE id = ?",
        args,
    )
    return cur.rowcount > 0


def migrate_from_state_json(conn: sqlite3.Connection, state_json_path: Path) -> int:
    """One-shot migration: copy each lead from state.json into SQLite.

    Idempotent: ``INSERT OR IGNORE`` keeps the dedup key.
    Returns the number of newly-migrated rows (excludes already-present).
    """
    if not state_json_path.exists():
        return 0
    try:
        state = json.loads(state_json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    leads = state.get("leads") or []
    if not leads:
        return 0
    n_new = 0
    for d in leads:
        # Existing state.json records are already-shaped dicts; wrap in dict.
        wrapped = d if isinstance(d, dict) else {"raw": d}
        # tags if present as a tuple on dict-keys, normalize to list
        if "tags" not in wrapped:
            wrapped["tags"] = []
        if upsert_lead(conn, wrapped):
            n_new += 1
    return n_new


__all__ = [
    "LeadRow",
    "STATUS_NEW", "STATUS_INTERESTED", "STATUS_APPLIED", "STATUS_REJECTED", "STATUS_DONE",
    "ALLOWED_STATUSES",
    "default_db_path", "init_db", "connect",
    "upsert_lead", "upsert_many", "list_leads", "count_by_status", "set_status",
    "migrate_from_state_json",
]
