"""Generate terminal-screenshot PNGs for the README and Launch docs.

We don't capture a real terminal (cross-platform and inconsistent);
instead we synth a dark-mode terminal with the desired CLI output
baked in. Two screenshots:

  * docs/screenshot-help.png       — `clawflow --help`
  * docs/screenshot-sources.png    — `clawflow sources list`
  * docs/screenshot-cycle.png      — a stylised cycle output

Run:  python scripts/make_screenshots.py
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT_DIR = Path(__file__).resolve().parent.parent / "docs"
WIDTH, HEIGHT = 1280, 800


def _font(size: int, *, bold: bool = False) -> ImageFont.ImageFont:
    candidates = [
        "C:/Windows/Fonts/consolab.ttf" if bold else "C:/Windows/Fonts/consola.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf" if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/System/Library/Fonts/SFNSMono.ttf",
        "/System/Library/Fonts/Menlo.ttc",
    ]
    for p in candidates:
        try:
            return ImageFont.truetype(p, size)
        except (OSError, FileNotFoundError):
            continue
    return ImageFont.load_default()


# ANSI-like colour palette
C = {
    "bg": "#1e1e2e",
    "bar": "#181825",
    "fg": "#cdd6f4",
    "muted": "#7f849c",
    "ok": "#a6e3a1",
    "warn": "#f9e2af",
    "err": "#f38ba8",
    "accent": "#89b4fa",
    "prompt": "#f5c2e7",
    "title": "#cba6f7",
    "tab": "#313244",
}


def _new_screenshot() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", (WIDTH, HEIGHT), C["bg"])
    draw = ImageDraw.Draw(img)
    # Title bar
    draw.rectangle((0, 0, WIDTH, 36), fill=C["bar"])
    # 3 dots (mac-ish)
    for x, color in [(20, "#ff5f56"), (40, "#ffbd2e"), (60, "#27c93f")]:
        draw.ellipse((x, 12, x + 12, 24), fill=color)
    draw.text((90, 10), "~/projects/clawflow — clawflow --help", fill=C["muted"], font=_font(14))
    return img, draw


def _render_lines(draw: ImageDraw.ImageDraw, lines: list[tuple[str, str]], start_y: int = 56) -> int:
    """Render ANSI-coloured text. Each line is (text, color_key)."""
    f = _font(20)
    y = start_y
    line_h = 26
    for text, color_key in lines:
        col = C.get(color_key, C["fg"])
        draw.text((28, y), text, fill=col, font=f)
        y += line_h
    return y


def make_help(out: Path) -> None:
    img, draw = _new_screenshot()
    lines = [
        ("$ clawflow --help", "prompt"),
        ("", "fg"),
        ("usage: clawflow [-h] [--version] [--config CONFIG]", "fg"),
        ("                [--log-level {DEBUG,INFO,WARNING,ERROR}]", "fg"),
        ("                {init,auth,sources,run,status,leads,dashboard} ...", "fg"),
        ("", "fg"),
        ("clawflow -- freelance-opportunity aggregator.", "muted"),
        ("                One binary, four signals, zero sales-team BS.", "muted"),
        ("", "fg"),
        ("positional arguments:", "title"),
        ("  {init,auth,sources,run,status,leads,dashboard}", "title"),
        ("    init          Generate a starter clawflow.yml in the current", "fg"),
        ("                  directory.", "fg"),
        ("    auth          Store API tokens for sources and the Telegram", "fg"),
        ("                  alerter.", "fg"),
        ("    sources       Inspect or edit the registered sources.", "fg"),
        ("    run           Collect leads, dedupe, and alert. Default subcommand.", "fg"),
        ("    status        Print last-cycle stats from state.json.", "fg"),
        ("    leads         Browse the collected leads.", "fg"),
        ("    dashboard     Open the terminal dashboard (v0.2 -- currently an", "fg"),
        ("                  ASCII stub).", "fg"),
        ("", "fg"),
        ("options:", "title"),
        ("  -h, --help      show this help message and exit", "fg"),
        ("  --version       show program's version number and exit", "fg"),
    ]
    _render_lines(draw, lines)
    img.save(out, "PNG", optimize=True)
    print(f"wrote {out}  ({out.stat().st_size:,} bytes)")


def make_sources_list(out: Path) -> None:
    img, draw = _new_screenshot()
    lines = [
        ("$ clawflow sources list", "prompt"),
        ("", "fg"),
        ("[on ] github_bounties", "ok"),
        ("[off] remote3_rss", "muted"),
        ("[off] cryptojobs_sitemap", "muted"),
        ("[off] wordpress_cve", "muted"),
        ("[off] hn_freelancer", "muted"),
        ("[off] reddit_forhire", "muted"),
        ("", "fg"),
        ("$ clawflow sources add wordpress_cve", "prompt"),
        ("[sources.add] wordpress_cve enabled in ./clawflow.yml", "ok"),
        ("", "fg"),
        ("$ clawflow status", "prompt"),
        ("state_path      : ./clawflow.state.json", "fg"),
        ("version         : 1", "fg"),
        ("leads persisted : 0", "fg"),
        ("updated_at      : never", "warn"),
    ]
    _render_lines(draw, lines)
    img.save(out, "PNG", optimize=True)
    print(f"wrote {out}  ({out.stat().st_size:,} bytes)")


def make_cycle(out: Path) -> None:
    img, draw = _new_screenshot()
    lines = [
        ("$ clawflow run", "prompt"),
        ("", "fg"),
        ("============================================================", "muted"),
        (" clawflow is online. Always Be Closing.", "title"),
        ("============================================================", "muted"),
        ("[init] Loading config from ./clawflow.yml", "fg"),
        ("[init] Enabled sources: github_bounties, wordpress_cve, hn_freelancer", "ok"),
        ("[budget] Claude usage OK (headroom: 78%)", "ok"),
        ("", "fg"),
        ("[fetch] github_bounties: scanning OpenZeppelin, ethereum-optimism ...", "fg"),
        ("   -> 12 candidate issues", "muted"),
        ("[fetch] wordpress_cve: polling elementor, woocommerce, wordpress-seo ...", "fg"),
        ("   -> 3 high-severity CVEs", "warn"),
        ("[fetch] hn_freelancer: querying 'seeking solidity auditor' ...", "fg"),
        ("   -> 5 stories", "muted"),
        ("", "fg"),
        ("[dedup] 18 new leads,  2 already-seen (since last cycle)", "ok"),
        ("[alert] telegram: 18 leads queued, score >= 40 = 9", "ok"),
        ("[state] wrote clawflow.state.json  (134 KB)", "muted"),
        ("", "fg"),
        ("Cycle complete. Sleeping for 15 minutes.", "title"),
    ]
    _render_lines(draw, lines)
    img.save(out, "PNG", optimize=True)
    print(f"wrote {out}  ({out.stat().st_size:,} bytes)")


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    make_help(OUT_DIR / "screenshot-help.png")
    make_sources_list(OUT_DIR / "screenshot-sources.png")
    make_cycle(OUT_DIR / "screenshot-cycle.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
