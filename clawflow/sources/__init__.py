"""Source base class, registry, and lazy sub-module loader for clawflow.

A ``Source`` is anything that can yield ``Lead`` objects — GitHub, RSS feeds,
HTTP APIs, Reddit JSON, CVE feeds, etc. Each concrete source is a subclass
of :class:`Source` that defines a ``key`` and implements ``fetch`` as an
async generator.

The registry is intentionally tiny: a class decorator (:func:`register`) and
a pair of helpers (:func:`all_sources`, :func:`get_source`). Sub-modules
are imported lazily the first time the registry is consulted, so importing
``clawflow.sources`` itself stays cheap.
"""
from __future__ import annotations

import importlib
import logging
from abc import ABC, abstractmethod
from typing import Any, AsyncIterator, ClassVar

from clawflow.leads import Lead

log = logging.getLogger("clawflow.sources")


class Source(ABC):
    """Abstract base for every clawflow lead source.

    Subclasses set class-level metadata (``key``, ``label``, ``description``,
    ``default_enabled``) and implement two methods:

    * :meth:`validate_config` — a cheap, non-raising check that returns
      ``(ok, message)`` so the CLI can surface config problems early.
    * :meth:`fetch` — an async generator yielding :class:`Lead` records.
      Implementations MUST yield (not return a list) and MUST NOT raise on
      individual records; log + continue on a single bad record.
    """

    key: ClassVar[str] = ""
    label: ClassVar[str] = ""
    description: ClassVar[str] = ""
    default_enabled: ClassVar[bool] = True

    @abstractmethod
    async def validate_config(self, cfg: Any) -> tuple[bool, str]:
        """Validate ``cfg``; return ``(ok, message)``. Must not raise."""
        raise NotImplementedError

    @abstractmethod
    async def fetch(self, state: dict[str, Any], cfg: Any) -> AsyncIterator[Lead]:
        """Async generator yielding Lead records. Subclasses MUST yield."""
        raise NotImplementedError
        yield  # pragma: no cover -- makes this an async generator for type checkers


# Module-level registry: key -> Source subclass.
_REGISTRY: dict[str, type[Source]] = {}


def register(cls: type[Source]) -> type[Source]:
    """Class decorator that registers ``cls`` in the source registry by ``cls.key``.

    Re-registering an existing key logs at debug and replaces the previous
    entry — useful for tests and plugin overlays.
    """
    if not getattr(cls, "key", None):
        raise ValueError(f"{cls.__name__} must define a non-empty `key` classvar")
    if cls.key in _REGISTRY and _REGISTRY[cls.key] is not cls:
        log.debug("source registry: %r already registered; replacing", cls.key)
    _REGISTRY[cls.key] = cls
    return cls


# Track which sub-modules we've already imported so we never double-register.
_SUBMODULES_LOADED: set[str] = set()


def _BUILTIN_SUBMODULES() -> tuple[str, ...]:
    """The set of source sub-modules shipped with clawflow."""
    return (
        "github_bounties",
        "web3_sitemap",
        "wordpress_cve",
        "hn_freelancer",
    )


def _load_submodules() -> None:
    """Import every built-in sub-module exactly once so its @register fires."""
    for name in _BUILTIN_SUBMODULES():
        if name in _SUBMODULES_LOADED:
            continue
        try:
            importlib.import_module(f"clawflow.sources.{name}")
        except Exception as exc:  # pragma: no cover -- defensive
            log.warning("failed to import source module %s: %s", name, exc)
        _SUBMODULES_LOADED.add(name)


def all_sources() -> list[type[Source]]:
    """Return every registered :class:`Source` subclass.

    Triggers a one-time lazy import of every built-in sub-module on first
    call; subsequent calls return the cached registry contents.
    """
    _load_submodules()
    return list(_REGISTRY.values())


def get_source(key: str) -> type[Source] | None:
    """Look up a :class:`Source` subclass by ``key``; returns ``None`` if absent."""
    _load_submodules()
    return _REGISTRY.get(key)


def BUILTIN_SOURCES() -> list[str]:
    """Return the keys of every registered built-in source."""
    _load_submodules()
    return sorted(_REGISTRY.keys())


__all__ = [
    "Source",
    "register",
    "all_sources",
    "get_source",
    "BUILTIN_SOURCES",
]