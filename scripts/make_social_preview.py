"""Generate the GitHub social-preview PNG for clawflow.

Output: ``social-preview.png`` in the project root (1280x640).

Run from the project root: ``python scripts/make_social_preview.py``.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 1280, 640
OUT = Path(__file__).resolve().parent.parent / "social-preview.png"

# Try to use a monospaced font that ships with Pillow; fall back to default.
def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    candidates = [
        "C:/Windows/Fonts/consolab.ttf" if bold else "C:/Windows/Fonts/consola.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf" if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/System/Library/Fonts/SFNSMono.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except (OSError, FileNotFoundError):
            continue
    return ImageFont.load_default()


def _palette() -> dict[str, str]:
    # A dark, GitHub-banner-style palette; no shiny gradients on purpose.
    return {
        "bg": "#0d1117",          # near-black, matches GitHub dark
        "bg_bar": "#161b22",
        "fg": "#f0f6fc",
        "muted": "#8b949e",
        "accent": "#58a6ff",       # GitHub blue
        "accent2": "#7ee787",      # GitHub green
        "border": "#30363d",
        "diamond": "#f0b90b",      # Solidity yellow
        "wp": "#21759b",           # WordPress blue
    }


def _rounded(draw: ImageDraw.ImageDraw, xy: tuple, radius: int, **kwargs) -> None:
    """Draw a rounded rectangle (PIL >= 8.2 has it natively; this stays portable)."""
    x1, y1, x2, y2 = xy
    if not hasattr(draw, "rounded_rectangle"):
        draw.rectangle(xy, **kwargs)
        return
    draw.rounded_rectangle(xy, radius=radius, **kwargs)


def main() -> int:
    p = _palette()
    img = Image.new("RGB", (WIDTH, HEIGHT), p["bg"])
    draw = ImageDraw.Draw(img)

    # Top thin border bar (gives a "developer tool" feel without being noisy)
    draw.rectangle((0, 0, WIDTH, 8), fill=p["accent"])

    # Project name (huge)
    title_font = _font(size=160)
    title = "clawflow"
    draw.text((80, 80), title, fill=p["fg"], font=title_font)

    # Sub-title (one line)
    sub_font = _font(size=42)
    draw.text(
        (84, 260),
        "One binary. Four signals. Zero sales-team BS.",
        fill=p["muted"],
        font=sub_font,
    )

    # Three "tag" chips
    chip_y = 360
    chip_x = 84
    chips = [
        ("GitHub bounties", p["accent"]),
        ("WordPress CVEs", p["wp"]),
        ("Solidity / Web3", p["diamond"]),
        ("HN & Reddit", p["accent2"]),
    ]
    chip_font = _font(size=24, bold=True)
    pad_x, pad_y = 18, 10
    cur_x = chip_x
    for label, color in chips:
        text_w = draw.textlength(label, font=chip_font)
        chip_w = text_w + pad_x * 2
        chip_h = 44
        _rounded(
            draw,
            (cur_x, chip_y, cur_x + chip_w, chip_y + chip_h),
            radius=22,
            outline=color,
            width=2,
        )
        draw.text((cur_x + pad_x, chip_y + pad_y), label, fill=color, font=chip_font)
        cur_x += chip_w + 14

    # Add the tagline just below the chips so the lower half feels less empty
    tag_font = _font(size=28)
    draw.text(
        (84, 440),
        "Runs on your machine.  No SaaS.  No API key.  No subscriptions.",
        fill=p["accent"],
        font=tag_font,
    )

    # Author & tagline (bottom) — moved up to avoid the empty-bottom look
    foot_font = _font(size=28)
    draw.text(
        (84, HEIGHT - 130),
        "Built by Nadim Khan  •  github.com/nadimkhan/clawflow",
        fill=p["muted"],
        font=foot_font,
    )
    sub_foot = _font(size=22)
    draw.text(
        (84, HEIGHT - 80),
        "Free, MIT-licensed, runs locally.  pip install clawflow",
        fill=p["accent2"],
        font=sub_foot,
    )

    img.save(OUT, "PNG", optimize=True)
    print(f"wrote {OUT}  ({OUT.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
