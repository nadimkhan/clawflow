"""Token storage for clawflow.

This is the deliberately tiny credential module. It does two things:

1. Stores API tokens for sources that need them (currently just
   ``github_bounties``).
2. Stores Telegram bot tokens + chat IDs for the ``telegram`` alerter.

It is intentionally NOT a general-purpose secret manager. If you already
have ``keyring`` installed, we use that. Otherwise we fall back to
plaintext on disk in a per-user file with tightened permissions on POSIX.
This is documented in ``docs/security.md`` (and is acceptable for v0.1
because the tokens are read-only public-data API tokens — leaking a
GitHub PAT is bad but recoverable by revoking the token, and leaking a
Telegram bot token means rotating it via BotFather).
"""
from __future__ import annotations

import json
import logging
import os
import platform
import stat
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("clawflow.auth")

# Resolved once on first call so repeated lookups stay cheap.
_KEYRING = None


def _try_keyring():
    """Return a ``keyring``-compatible object or ``None`` if unavailable."""
    global _KEYRING
    if _KEYRING is not None:
        return _KEYRING
    try:
        import keyring  # type: ignore

        _KEYRING = keyring
        return keyring
    except Exception:  # noqa: BLE001 — keyring raises broadly on missing backends
        _KEYRING = False  # type: ignore
        return None


@dataclass
class AuthStore:
    """Persisted at ``auth_path``. Add fields here as new auth types arrive."""

    github_token: str = ""
    telegram_token: str = ""
    telegram_chat_id: str = ""
    extras: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, blob: dict[str, Any]) -> "AuthStore":
        return cls(
            github_token=blob.get("github_token", "") or "",
            telegram_token=blob.get("telegram_token", "") or "",
            telegram_chat_id=blob.get("telegram_chat_id", "") or "",
            extras=blob.get("extras", {}) or {},
        )


def default_auth_path() -> Path:
    """Return the per-user auth file path.

    POSIX:   ``$XDG_CONFIG_HOME/clawflow/auth.json`` (default ``~/.config``)
    Windows: ``%LOCALAPPDATA%\\clawflow\\auth.json``
    """
    system = platform.system().lower()
    if system == "windows":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "clawflow" / "auth.json"
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "clawflow" / "auth.json"


def _tighten_posix_permissions(path: Path) -> None:
    """Set owner-read/write only on POSIX; best-effort and silent on errors."""
    if platform.system().lower() == "windows":
        return
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:  # pragma: no cover — non-fatal
        log.warning("could not tighten permissions on %s", path)


def load_auth(path: Path | None = None) -> AuthStore:
    """Load the auth store from disk. Returns an empty store on missing file."""
    target = path or default_auth_path()
    if not target.exists():
        return AuthStore()
    try:
        blob = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:  # noqa: BLE001
        log.warning("could not parse %s: %s — starting empty", target, e)
        return AuthStore()
    return AuthStore.from_dict(blob)


def save_auth(store: AuthStore, path: Path | None = None) -> Path:
    """Persist ``store`` to disk; create parent dirs; tighten perms on POSIX."""
    target = path or default_auth_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(store.to_dict(), indent=2), encoding="utf-8")
    _tighten_posix_permissions(target)
    log.info("saved auth to %s", target)
    return target


def get_secret(name: str, path: Path | None = None) -> str:
    """Return a named token.

    Lookup order: keyring → env var → auth.json on disk. Empty string if
    nothing is set anywhere.
    """
    # 1. Environment wins (lets users override per-shell).
    env_val = os.environ.get(name.upper())
    if env_val:
        return env_val
    # 2. Keyring — only if the user opted in by installing it; only useful
    #    if some other tool already stored the secret under the same name.
    kr = _try_keyring()
    if kr:
        try:
            val = kr.get_password("clawflow", name)
            if val:
                return val
        except Exception:  # noqa: BLE001 — keyring raises broadly
            pass
    # 3. Plaintext fallback on disk.
    store = load_auth(path)
    mapping = {
        "github_token": store.github_token,
        "telegram_token": store.telegram_token,
        "telegram_chat_id": store.telegram_chat_id,
    }
    return mapping.get(name, "")


def set_secret(name: str, value: str, path: Path | None = None) -> Path:
    """Store a secret in keyring (if available) AND plaintext fallback.

    The duplicate write is intentional: if a user later removes keyring,
    the fallback is still there, and the file is in ``~/.config/clawflow``
    on POSIX which has owner-only perms after ``_tighten_posix_permissions``.
    """
    target = path or default_auth_path()
    store = load_auth(target)
    if name == "github_token":
        store.github_token = value
    elif name == "telegram_token":
        store.telegram_token = value
    elif name == "telegram_chat_id":
        store.telegram_chat_id = value
    else:
        store.extras[name] = value
    saved = save_auth(store, target)
    kr = _try_keyring()
    if kr:
        try:
            kr.set_password("clawflow", name, value)
        except Exception:  # noqa: BLE001
            log.warning("keyring write failed for %s — using plaintext fallback", name)
    return saved


# CLI subcommand helpers ------------------------------------------------------


def cmd_auth_set(args: Any) -> int:
    """``clawflow auth github --token X`` / ``clawflow auth telegram …``."""
    target = Path(args.path) if getattr(args, "path", None) else default_auth_path()
    if args.kind == "github":
        if not args.token:
            print("error: --token is required for `auth github`", file=__import__("sys").stderr)
            return 2
        set_secret("github_token", args.token, target)
        print(f"✓ github_token saved to {target}")
        return 0
    if args.kind == "telegram":
        if not args.token:
            print("error: --token is required for `auth telegram`", file=__import__("sys").stderr)
            return 2
        if not args.chat_id:
            print(
                "warning: --chat-id not provided. Send any message to your bot, "
                "then run with --chat-id once you know it.",
                file=__import__("sys").stderr,
            )
        set_secret("telegram_token", args.token, target)
        if args.chat_id:
            set_secret("telegram_chat_id", args.chat_id, target)
            print(f"✓ telegram token + chat_id saved to {target}")
        else:
            print(f"✓ telegram token saved to {target} (chat_id pending)")
        return 0
    print(f"error: unknown auth kind {args.kind!r}", file=__import__("sys").stderr)
    return 2


def cmd_auth_show(_args: Any) -> int:
    """``clawflow auth show`` — print redacted status of every known secret."""
    store = load_auth()
    sys = __import__("sys")
    sys.stdout.write(f"auth_path        : {default_auth_path()}\n")
    sys.stdout.write(f"github_token     : {'set (last 4: ' + _redact(store.github_token) + ')' if store.github_token else '(not set)'}\n")
    sys.stdout.write(f"telegram_token   : {'set (last 4: ' + _redact(store.telegram_token) + ')' if store.telegram_token else '(not set)'}\n")
    sys.stdout.write(f"telegram_chat_id : {'set' if store.telegram_chat_id else '(not set)'}\n")
    return 0


def _redact(token: str) -> str:
    if len(token) < 4:
        return "****"
    return token[-4:]
