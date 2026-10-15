"""Alerter registry + three built-in alerters: console, telegram, email.

An alerter receives a list of ``Lead`` objects after each cycle and is
responsible for delivering them to wherever the user wants (terminal,
Telegram chat, SMTP inbox). Severity is enforced at the alerter level:

    if lead.score < alerter.severity_threshold:
        skip silently

Built-in alerters are registered by importing this module. Adding a
community alerter is a one-liner: subclass ``Alerter`` and call
``register_alerter("my_kind", MyAlerter)``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Any, Iterable, Mapping

import clawflow.leads as _leads_mod
from clawflow.leads import Lead


log = logging.getLogger("clawflow.alerters")


# ---------------------------------------------------------------------------
# Alerter base class + registry
# ---------------------------------------------------------------------------

@dataclass
class Alerter:
    """Base class for alerters — subclass and override ``send``."""

    name: str
    severity_threshold: int = 0
    max_items_per_cycle: int = 50
    options: Mapping[str, Any] | None = None

    async def send(self, leads: Iterable[Lead]) -> int:  # pragma: no cover
        """Deliver ``leads`` to the user. Return count of items sent."""
        raise NotImplementedError

    def _filter(self, leads: Iterable[Lead]) -> list[Lead]:
        out: list[Lead] = []
        for lead in leads:
            if lead.score < self.severity_threshold:
                continue
            out.append(lead)
            if len(out) >= self.max_items_per_cycle:
                break
        return out


_REGISTRY: dict[str, type[Alerter]] = {}


def register_alerter(kind: str) -> Callable[[type[Alerter]], type[Alerter]]:
    """Class decorator factory: ``@register_alerter("console")`` registers a class.

    Re-registering an existing kind replaces the previous entry (useful
    for tests and plugin overlays).
    """
    if not kind:
        raise ValueError("kind must be a non-empty string")

    def decorator(cls: type[Alerter]) -> type[Alerter]:
        if kind in _REGISTRY and _REGISTRY[kind] is not cls:
            log.debug("alerter registry: %r already registered; replacing", kind)
        _REGISTRY[kind] = cls
        return cls

    return decorator


def kinds() -> tuple[str, ...]:
    """Return every registered alerter kind, sorted."""
    return tuple(sorted(_REGISTRY))


def make_alerter(spec: Mapping[str, Any]) -> Alerter:
    """Instantiate an alerter from a YAML mapping."""
    kind = str(spec.get("type") or spec.get("kind") or "").strip()
    if not kind:
        raise ValueError("alerter spec is missing 'type'")
    cls = _REGISTRY.get(kind)
    if cls is None:
        raise ValueError(f"unknown alerter type: {kind!r}")
    options = {k: v for k, v in spec.items() if k not in {"type", "kind"}}
    sev = int(options.pop("severity_threshold", 0) or 0)
    cap = int(options.pop("max_items_per_cycle", 50) or 50)
    return cls(name=kind, severity_threshold=sev, max_items_per_cycle=cap, options=options)


# ---------------------------------------------------------------------------
# Built-in alerters
# ---------------------------------------------------------------------------

@register_alerter("console")
class ConsoleAlerter(Alerter):
    """Print leads as a colour-free, terminal-width-friendly table."""

    async def send(self, leads: Iterable[Lead]) -> int:
        items = self._filter(leads)
        if not items:
            return 0
        for lead in items:
            title = (lead.title or "").strip()[:120] or "(untitled)"
            print(f"[{lead.score:>3}] {lead.source:<18} {title}")
            print(f"      {lead.url}")
        return len(items)


@register_alerter("telegram")
class TelegramAlerter(Alerter):
    """Post leads to a Telegram chat via the Bot HTTP API.

    Required env vars (or ``options`` overrides):

    * ``TELEGRAM_BOT_TOKEN``  — the bot token issued by @BotFather
    * ``TELEGRAM_CHAT_ID``    — the chat to post into
    """

    DEFAULT_PARSE_MODE = "markdown"

    async def send(self, leads: Iterable[Lead]) -> int:
        items = self._filter(leads)
        if not items:
            return 0
        token = str((self.options or {}).get("bot_token") or os.environ.get("TELEGRAM_BOT_TOKEN", "")).strip()
        chat_id = str((self.options or {}).get("chat_id") or os.environ.get("TELEGRAM_CHAT_ID", "")).strip()
        if not token or not chat_id:
            log.warning("telegram alerter: missing bot_token or chat_id; skipping")
            return 0
        parse_mode = str((self.options or {}).get("parse_mode") or self.DEFAULT_PARSE_MODE).lower()
        try:
            import httpx  # local import — keeps the dep surface honest
        except ImportError:  # pragma: no cover — httpx is in pyproject
            log.error("telegram alerter: httpx not installed")
            return 0
        sent = 0
        async with httpx.AsyncClient(timeout=20.0) as client:
            for lead in items:
                text = self._format_message(lead)
                payload = {
                    "chat_id": chat_id,
                    "text": text,
                    "disable_web_page_preview": True,
                }
                if parse_mode in {"markdown", "html"}:
                    payload["parse_mode"] = parse_mode
                try:
                    resp = await client.post(
                        f"https://api.telegram.org/bot{token}/sendMessage",
                        json=payload,
                    )
                except httpx.HTTPError as exc:
                    log.warning("telegram send failed: %s", exc)
                    continue
                if resp.status_code >= 400:
                    log.warning("telegram send -> %s: %s", resp.status_code, resp.text[:200])
                    continue
                sent += 1
        return sent

    @staticmethod
    def _format_message(lead: Lead) -> str:
        title = (lead.title or "").strip()[:200] or "(untitled)"
        url = (lead.url or "").strip()
        tags = " ".join(f"#{t}" for t in (lead.tags or ())[:5])
        bits = [f"*{title}*", url]
        if tags:
            bits.append(tags)
        bits.append(f"_score {lead.score} • {lead.source}_")
        return "\n".join(bits)


@register_alerter("email")
class EmailAlerter(Alerter):
    """Send an SMTP email containing all new leads.

    Required options (or env vars):

    * ``host``           — SMTP server hostname
    * ``port``           — SMTP port (default 587)
    * ``username``       — auth username (or ``$SMTP_USERNAME``)
    * ``password_env``   — env var holding the SMTP password
    * ``use_tls``        — ``true`` to start TLS (default true)
    * ``from_addr``      — sender address
    * ``to_addrs``       — list of recipient addresses
    """

    async def send(self, leads: Iterable[Lead]) -> int:
        items = self._filter(leads)
        if not items:
            return 0
        opts = dict(self.options or {})
        host = str(opts.get("host") or os.environ.get("SMTP_HOST", "")).strip()
        if not host:
            log.warning("email alerter: smtp.host not configured; skipping")
            return 0
        port = int(opts.get("port") or os.environ.get("SMTP_PORT") or 587)
        username = str(opts.get("username") or os.environ.get("SMTP_USERNAME", "")).strip()
        password_env = str(opts.get("password_env") or "SMTP_PASSWORD")
        password = os.environ.get(password_env, "")
        use_tls = bool(opts.get("use_tls", True))
        from_addr = str(opts.get("from_addr") or username)
        to_addrs = list(opts.get("to_addrs") or [])
        if not from_addr or not to_addrs:
            log.warning("email alerter: from_addr / to_addrs not set; skipping")
            return 0

        msg = EmailMessage()
        subject_template = str(opts.get("subject_template") or "[clawflow] {n} new lead(s)")
        msg["Subject"] = subject_template.format(n=len(items))
        msg["From"] = from_addr
        msg["To"] = ", ".join(to_addrs)
        msg.set_content(self._render_text(items))
        msg.add_alternative(self._render_html(items), subtype="html")

        try:
            with smtplib.SMTP(host, port, timeout=30) as smtp:
                smtp.ehlo()
                if use_tls:
                    smtp.starttls()
                    smtp.ehlo()
                if username:
                    smtp.login(username, password)
                smtp.send_message(msg)
        except (smtplib.SMTPException, OSError) as exc:
            log.warning("email alerter: send failed (%s)", exc)
            return 0
        return len(items)

    @staticmethod
    def _render_text(items: list[Lead]) -> str:
        chunks = ["clawflow — new leads", ""]
        for lead in items:
            chunks.append(f"- [{lead.score}] {lead.source}: {lead.title}")
            chunks.append(f"    {lead.url}")
        return "\n".join(chunks) + "\n"

    @staticmethod
    def _render_html(items: list[Lead]) -> str:
        rows: list[str] = []
        for lead in items:
            rows.append(
                f"<li><b>[{lead.score}] {lead.source}</b>: "
                f"<a href=\"{lead.url}\">{lead.title}</a></li>"
            )
        return (
            "<html><body><h3>clawflow — new leads</h3>"
            f"<ul>{''.join(rows)}</ul></body></html>"
        )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

async def dispatch_alerts(
    cfg: Any,
    leads: list[Lead],
) -> int:
    """Send ``leads`` to every alerter in ``cfg.alerters``.

    Returns the total number of lead deliveries that succeeded.
    Individual alerter failures are logged and do not abort the cycle.
    """
    if not leads:
        return 0
    specs = list(cfg.alerters or [{"type": "console"}])
    total = 0
    for spec in specs:
        try:
            alerter = make_alerter(spec)
        except (ValueError, TypeError) as exc:
            log.warning("could not build alerter from %r: %s", spec, exc)
            continue
        try:
            total += await alerter.send(leads)
        except Exception as exc:  # pragma: no cover — last-resort guard
            log.warning("alerter %s raised: %s", alerter.name, exc)
    return total


if __name__ == "__main__":  # pragma: no cover
    print("registered alerter kinds:", kinds())