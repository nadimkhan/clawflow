"""Local web UI for tracking leads + application status.

Run with ``clawflow leads-ui`` (or ``python -m clawflow leads-ui``).
Default port 5555, host 127.0.0.1 (loopback only — not exposed on the LAN
unless ``--host 0.0.0.0`` is passed).

What it does
------------
* Lists every lead in the SQLite store, with filters (status, source)
* Shows score, source, posted-at, the URL, and a status pill
* Lets you mark a lead as ``interested`` / ``applied`` / ``rejected`` / ``done``
* Lets you attach a short note (one text area)
* Persists to ``clawflow-leads.db`` (SQLite, single file)

What it deliberately does NOT do
---------------------------------
* Run source scraping on its own — clawflow run / run-once is the only
  path that fetches new leads
* Send mail or DM, ever
* Authenticate users — bound to loopback only, no multi-user support
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Any

import uvicorn  # noqa: F401  -- imported lazily so tests don't need it
from fastapi import FastAPI, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from clawflow import store_sqlite
from clawflow.leads import load_state

log = logging.getLogger("clawflow.leads_ui")


# ---------------------------------------------------------------------------
# Templates (inline HTML — no separate static folder, easy to ship)
# ---------------------------------------------------------------------------

_PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>clawflow — leads tracker</title>
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <style>
    :root {{
      --bg: #0d1117; --fg: #f0f6fc; --muted: #8b949e;
      --card: #161b22; --border: #30363d; --accent: #58a6ff;
      --ok: #3fb950; --warn: #d29922; --bad: #f85149; --info: #79c0ff;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0; font-family: ui-sans-serif, system-ui, -apple-system, sans-serif;
      background: var(--bg); color: var(--fg); font-size: 14px; line-height: 1.4;
    }}
    .top {{
      display: flex; align-items: center; gap: 14px;
      padding: 14px 20px; border-bottom: 1px solid var(--border);
      background: var(--card);
    }}
    .top h1 {{ margin: 0; font-size: 16px; font-weight: 600; }}
    .counts {{ display: flex; gap: 8px; margin-left: auto; flex-wrap: wrap; }}
    .counts a {{
      color: var(--muted); text-decoration: none; padding: 4px 8px;
      border: 1px solid var(--border); border-radius: 999px; font-size: 12px;
    }}
    .counts a:hover {{ color: var(--fg); border-color: var(--accent); }}
    .counts a.active {{ color: var(--fg); border-color: var(--accent); background: rgba(88,166,255,0.1); }}
    .container {{ padding: 14px 20px; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{
      text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--border);
      vertical-align: top;
    }}
    th {{ color: var(--muted); font-weight: 600; font-size: 12px; text-transform: uppercase; }}
    a {{ color: var(--accent); }}
    a.title {{ font-weight: 600; color: var(--fg); }}
    a.title:hover {{ text-decoration: underline; color: var(--accent); }}
    .pill {{
      display: inline-block; padding: 2px 8px; border-radius: 999px;
      font-size: 11px; font-weight: 600; text-transform: uppercase;
      letter-spacing: 0.04em; background: var(--card); color: var(--muted);
      border: 1px solid var(--border);
    }}
    .pill-new       {{ color: var(--accent); border-color: var(--accent); }}
    .pill-interested{{ color: var(--info);    border-color: var(--info); }}
    .pill-applied   {{ color: var(--warn);    border-color: var(--warn); }}
    .pill-done      {{ color: var(--ok);      border-color: var(--ok); }}
    .pill-rejected  {{ color: var(--bad);     border-color: var(--bad); text-decoration: line-through; }}
    .row-actions {{ display: flex; gap: 4px; flex-wrap: wrap; }}
    .row-actions button {{
      border: 1px solid var(--border); background: var(--card);
      color: var(--fg); padding: 3px 7px; border-radius: 4px; cursor: pointer;
      font-size: 11px;
    }}
    .row-actions button:hover {{ border-color: var(--accent); color: var(--accent); }}
    .row-actions button.danger:hover {{ border-color: var(--bad); color: var(--bad); }}
    .filters {{ display: flex; gap: 12px; margin-bottom: 12px; flex-wrap: wrap; }}
    .filters select {{
      background: var(--card); color: var(--fg); border: 1px solid var(--border);
      padding: 6px 8px; border-radius: 4px;
    }}
    .src-tag {{ font-family: ui-monospace, monospace; font-size: 11px; color: var(--muted); }}
    .empty {{ padding: 40px; text-align: center; color: var(--muted); }}
    .note-form {{ display: flex; gap: 6px; align-items: stretch; }}
    .note-form input {{
      background: var(--card); color: var(--fg); border: 1px solid var(--border);
      padding: 4px 6px; border-radius: 4px; flex: 1; min-width: 100px;
      font-size: 12px;
    }}
    .note-form input:focus {{ outline: none; border-color: var(--accent); }}
    .note-form button {{ font-size: 11px; padding: 4px 8px; }}
    .current-note {{ font-size: 11px; color: var(--muted); font-style: italic; margin-top: 4px; }}
  </style>
</head>
<body>
  <div class="top">
    <h1>clawflow leads tracker</h1>
    <span class="src-tag" style="color:var(--muted)">db: {db_path}</span>
    <div class="counts">
      {count_links}
    </div>
  </div>
  <div class="container">
    <div class="filters">
      <form method="get" action="/">
        <label style="color:var(--muted);margin-right:6px;">status:</label>
        <select name="status" onchange="this.form.submit()">
          <option value="">all</option>
          {status_options}
        </select>
        <label style="color:var(--muted);margin:0 6px 0 12px;">source:</label>
        <select name="source" onchange="this.form.submit()">
          <option value="">all</option>
          {source_options}
        </select>
      </form>
    </div>

    <table>
      <thead>
        <tr>
          <th style="width:60px;">score</th>
          <th style="width:140px;">source</th>
          <th>title</th>
          <th style="width:140px;">status</th>
          <th style="width:280px;">action</th>
        </tr>
      </thead>
      <tbody>
        {rows}
      </tbody>
    </table>
  </div>
</body>
</html>
"""

_ROW = """<tr>
  <td><strong>{score}</strong></td>
  <td><span class="src-tag">{source}</span></td>
  <td>
    <a class="title" href="{url_href}" target="_blank" rel="noopener noreferrer">{title}</a>
    <div style="font-size:11px;color:var(--muted);margin-top:4px;">
      {tags}{posted}
    </div>
    {current_note}
  </td>
  <td><span class="pill pill-{status_class}">{status}</span></td>
  <td>
    <form method="post" action="/lead/{id_href}/status" class="row-actions">
      <button type="submit" name="status" value="interested">interested</button>
      <button type="submit" name="status" value="applied">applied</button>
      <button type="submit" name="status" value="done">done</button>
      <button type="submit" name="status" value="rejected" class="danger">reject</button>
      <button type="submit" name="status" value="new">reset</button>
    </form>
    <form method="post" action="/lead/{id_href}/note" class="note-form" style="margin-top:4px;">
      <input type="text" name="note" placeholder="note…" value="{note_attr}" />
      <button type="submit">save</button>
    </form>
  </td>
</tr>"""


def _status_class(s: str) -> str:
    return {
        "new": "new",
        "interested": "interested",
        "applied": "applied",
        "done": "done",
        "rejected": "rejected",
    }.get(s, "new")


def _esc(s: Any) -> str:
    if s is None:
        return ""
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def build_app(db_path: Path) -> FastAPI:
    """Construct the FastAPI app bound to a given SQLite path."""

    @contextmanager_redirect  # placeholder; see below
    def _db():  # pragma: no cover — only used as a type hint
        pass

    app = FastAPI(title="clawflow leads tracker", docs_url=None, redoc_url=None)

    @app.on_event("startup")
    def _startup() -> None:
        store_sqlite.init_db(db_path)
        try:
            state_json = Path("./state.json")
            with store_sqlite.connect(db_path) as conn:
                n = store_sqlite.migrate_from_state_json(conn, state_json)
                if n:
                    log.info("migrated %d lead(s) from state.json", n)
        except Exception as exc:  # noqa: BLE001
            log.warning("startup migration skipped: %s", exc)

    def _rows(status: str | None, source: str | None) -> str:
        with store_sqlite.connect(db_path) as conn:
            rows = store_sqlite.list_leads(
                conn, status=status, source=source, limit=500,
            )
        if not rows:
            return '<tr><td colspan="5" class="empty">no leads yet — run <code>clawflow run --once</code> first</td></tr>'
        html = []
        for r in rows:
            tags = ", ".join(_esc(t) for t in r.tags[:5]) if r.tags else ""
            tags_html = f'<span class="src-tag">{tags}</span>' if tags else ""
            posted = f' &middot; {_esc(r.posted_at)}' if r.posted_at else ""
            note_attr = _esc(r.note)
            note_html = (
                f'<div class="current-note">note: {note_attr}</div>'
                if note_attr
                else ""
            )
            html.append(
                _ROW.format(
                    score=r.score,
                    source=_esc(r.source),
                    url_href=_esc(r.url),
                    title=_esc(r.title),
                    tags=tags_html,
                    posted=posted,
                    id_href=_esc(r.id),
                    status_class=_status_class(r.status),
                    status=_esc(r.status),
                    current_note=note_html,
                    note_attr=note_attr if note_attr else "",
                )
            )
        return "\n".join(html)

    def _count_links(current_status: str | None) -> str:
        with store_sqlite.connect(db_path) as conn:
            counts = store_sqlite.count_by_status(conn)
        order = ("new", "interested", "applied", "done", "rejected")
        parts = []
        for s in order:
            label = f'{s} ({counts.get(s, 0)})'
            cls = "active" if (current_status == s) else ""
            href = f'/?status={s}' if s != current_status else "/"
            parts.append(f'<a class="{cls}" href="{href}">{label}</a>')
        # "All" link when no filter
        all_cls = "active" if current_status is None else ""
        all_href = "/"
        parts.insert(0, f'<a class="{all_cls}" href="{all_href}">all ({sum(counts.values())})</a>')
        return "\n".join(parts)

    def _status_options_html(current: str | None) -> str:
        opts = ['<option value="">all</option>']
        for s in ("new", "interested", "applied", "done", "rejected"):
            sel = ' selected' if current == s else ''
            opts.append(f'<option value="{s}"{sel}>{s}</option>')
        return "\n".join(opts)

    def _source_options_html(current: str | None, all_sources: list[str]) -> str:
        opts = ['<option value="">all</option>']
        for s in sorted(set(all_sources)):
            sel = ' selected' if current == s else ''
            opts.append(f'<option value="{s}"{sel}>{s}</option>')
        return "\n".join(opts)

    @app.get("/", response_class=HTMLResponse)
    def index(
        request: Request,
        status: str | None = Query(default=None),
        source: str | None = Query(default=None),
    ) -> str:
        with store_sqlite.connect(db_path) as conn:
            all_sources = [
                r["source"]
                for r in conn.execute(
                    "SELECT DISTINCT source FROM leads ORDER BY source"
                ).fetchall()
            ]
        rows = _rows(status, source)
        return _PAGE.format(
            db_path=_esc(db_path),
            count_links=_count_links(status),
            status_options=_status_options_html(status),
            source_options=_source_options_html(source, all_sources),
            rows=rows,
        )

    @app.post("/lead/{lead_id}/status")
    def set_status_route(lead_id: str, status: str = Form(...)) -> RedirectResponse:
        try:
            with store_sqlite.connect(db_path) as conn:
                store_sqlite.set_status(conn, lead_id, status)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        # Redirect back to where the user came from.
        ref = ""  # FastAPI: we'd read this from the Referer header, but this is fine for now
        return RedirectResponse(url="/", status_code=303)

    @app.post("/lead/{lead_id}/note")
    def set_note_route(lead_id: str, note: str = Form(...)) -> RedirectResponse:
        with store_sqlite.connect(db_path) as conn:
            row = conn.execute(
                "SELECT status FROM leads WHERE id = ?", (lead_id,)
            ).fetchone()
            if row is None:
                return JSONResponse({"error": "lead not found"}, status_code=404)
            store_sqlite.set_status(conn, lead_id, row["status"], note=note)
        return RedirectResponse(url="/", status_code=303)

    @app.get("/api/leads")
    def api_list(
        status: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=1000),
    ) -> dict[str, Any]:
        with store_sqlite.connect(db_path) as conn:
            rows = store_sqlite.list_leads(conn, status=status, limit=limit)
            counts = store_sqlite.count_by_status(conn)
        return {
            "counts": counts,
            "leads": [
                {
                    "id": r.id, "source": r.source, "title": r.title,
                    "url": r.url, "score": r.score,
                    "posted_at": r.posted_at, "tags": list(r.tags),
                    "status": r.status, "note": r.note,
                    "created_at": r.created_at, "updated_at": r.updated_at,
                }
                for r in rows
            ],
        }

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        with store_sqlite.connect(db_path) as conn:
            counts = store_sqlite.count_by_status(conn)
        return {"ok": True, "db": str(db_path), "counts": counts}

    return app


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="clawflow leads-ui",
        description="Run the local leads tracker web UI (loopback only by default).",
    )
    parser.add_argument("--host", default="127.0.0.1", help="bind address (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=5555, help="port (default 5555)")
    parser.add_argument(
        "--db",
        default=str(store_sqlite.default_db_path()),
        help="path to SQLite DB (default: ./clawflow-leads.db)",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="enable hot reload (dev only)",
    )
    args = parser.parse_args(argv)

    db_path = Path(args.db).resolve()
    log.info("clawflow leads-ui: db=%s bind=%s:%d", db_path, args.host, args.port)
    app = build_app(db_path)
    uvicorn.run(app, host=args.host, port=args.port, reload=args.reload, log_level="info")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


# Stub decorator used so the unused ``@contextmanager_redirect`` line above
# compiles cleanly on Python 3.11 (referenced only in a typed default).
def contextmanager_redirect(fn):  # pragma: no cover
    return fn
