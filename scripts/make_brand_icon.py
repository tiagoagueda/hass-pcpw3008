#!/usr/bin/env python3
"""
Generate the placeholder brand icon for the integration.

This is a TEMPORARY mark: a Bluetooth rune on a rounded blue tile, standing in
until a proper product icon exists. It deliberately avoids any Home Assistant
branding — the brands repository forbids that for custom integrations, because
it misleads users into thinking the integration is official.

Since HA 2026.3.0 a custom integration serves its own brand images from a
`brand/` folder next to manifest.json; they take priority over the brands CDN
and need no manifest entry.

    python scripts/make_brand_icon.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parents[1] / "custom_components" / "pcpw3008" / "brand"

BLUE = (0, 130, 252, 255)
BLUE_DARK = (10, 61, 145, 255)
WHITE = (255, 255, 255, 255)

# The Bluetooth mark as one continuous stroke, in a unit box (y grows downward).
RUNE = [
    (0.28, 0.32),
    (0.72, 0.68),
    (0.50, 0.86),
    (0.50, 0.14),
    (0.72, 0.32),
    (0.28, 0.68),
]


def render(size: int, background: tuple[int, int, int, int]) -> Image.Image:
    """Draw at 4x and downsample, so the diagonals come out clean."""
    ss = 4
    px = size * ss
    img = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    draw.rounded_rectangle(
        [(0, 0), (px - 1, px - 1)], radius=int(px * 0.22), fill=background
    )

    points = [(x * px, y * px) for x, y in RUNE]
    width = max(1, int(px * 0.085))
    draw.line(points, fill=WHITE, width=width, joint="curve")
    # Square joins leave notches at the sharp tips; cap them with discs.
    r = width / 2
    for x, y in points:
        draw.ellipse([(x - r, y - r), (x + r, y + r)], fill=WHITE)

    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, size, bg in (
        ("icon.png", 256, BLUE),
        ("icon@2x.png", 512, BLUE),
        ("dark_icon.png", 256, BLUE_DARK),
        ("dark_icon@2x.png", 512, BLUE_DARK),
    ):
        path = OUT / name
        render(size, bg).save(path, "PNG", optimize=True)
        print(f"{path.relative_to(OUT.parents[2])}  {path.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
