"""Small stdlib-only helpers used across clawflow.

Atomic writes, ISO date parsing, time-budget decorator, slugify, and
hash-based dedup keys all live here so neither ``leads`` nor
``scheduler`` has to reimplement them.
"""
from __future__ import annotations

import functools
import hashlib
import json
import os
import re
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

def utcnow_iso() -> str:
    """Return current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def parse_iso_dt(value: str | None) -> datetime | None:
    """Parse an ISO-8601 string into an aware datetime; tolerant of 'Z'."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def parse_window(spec: str) -> datetime:
    """Parse a ``Nd`` / ``Nh`` / ``Nm`` window into a cutoff datetime.

    Anything unparseable falls back to 24 hours — keeps behaviour
    predictable instead of failing on a typo.
    """
    spec = (spec or "").strip().lower()
    if spec.endswith("d") and spec[:-1].isdigit():
        return datetime.now(timezone.utc) - __import__("datetime").timedelta(days=int(spec[:-1]))
    if spec.endswith("h") and spec[:-1].isdigit():
        return datetime.now(timezone.utc) - __import__("datetime").timedelta(hours=int(spec[:-1]))
    if spec.endswith("m") and spec[:-1].isdigit():
        return datetime.now(timezone.utc) - __import__("datetime").timedelta(minutes=int(spec[:-1]))
    return datetime.now(timezone.utc) - __import__("datetime").timedelta(hours=24)


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------

def lead_hash(*parts: str) -> str:
    """Stable SHA-256 hex digest over the concatenation of ``parts``."""
    h = hashlib.sha256()
    for part in parts:
        h.update(part.encode("utf-8"))
        h.update(b"\x1f")
    return h.hexdigest()


# ---------------------------------------------------------------------------
# String helpers
# ---------------------------------------------------------------------------

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(value: str) -> str:
    """Lower-case, dash-joined slug suitable for filenames and ids."""
    value = (value or "").strip().lower()
    value = _SLUG_RE.sub("-", value).strip("-")
    return value or "untitled"


# ---------------------------------------------------------------------------
# Filesystem helpers
# ---------------------------------------------------------------------------

def atomic_write_json(path: Path | str, payload: Any) -> None:
    """Write ``payload`` as JSON to ``path`` atomically (temp + replace)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".clawflow-", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True, default=str)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass
        raise


# ---------------------------------------------------------------------------
# Decorators
# ---------------------------------------------------------------------------

def time_budget(seconds: float) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator: log + return None if the wrapped call exceeds ``seconds``."""
    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            start = time.monotonic()
            try:
                return fn(*args, **kwargs)
            finally:
                elapsed = time.monotonic() - start
                if elapsed > seconds:
                    import logging
                    logging.getLogger("clawflow.util").warning(
                        "%s took %.2fs (budget %.2fs)",
                        fn.__qualname__, elapsed, seconds,
                    )
        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# Lazy filter for head-of-titles
# ---------------------------------------------------------------------------

def short(value: str, limit: int = 80) -> str:
    """Truncate ``value`` to ``limit`` chars with an ellipsis if needed."""
    value = (value or "").strip()
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)] + "…"


if __name__ == "__main__":  # pragma: no cover
    print("clawflow.util — module, not a CLI.")