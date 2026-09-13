"""Draw the Overtone mark, and write the 1024px master.

    python scripts/make_mark.py
    python scripts/make_icons.py      # then cut the favicons from it

**This overwrites both masters.** If the artwork is ever replaced by a real
export from a design tool, drop it over `src/styles/overtone_favcon.png` and
run only `make_icons.py` — running this again would paint over it.

The mark is two overlapping circles with the lens between them striped in
both colours: the pair, and the thing they share. It is the mechanic drawn in
one shape, which is as close as a 16px square gets to explaining the product.

**Generated rather than exported.** The previous master was a 900 KB raster
nobody could change without the original tool. This is the geometry that
produces it, so the mark can be re-cut at any size, the colours can follow the
palette when the palette moves, and a change is a diff rather than a new
binary. Drawn at 4x and downsampled, which is cheaper than anti-aliasing every
edge by hand and gives cleaner arcs than Pillow's own.

The two hues are the landing page's pair — the warm one and the cool one
somebody chooses between in the hero — carried at icon saturation. A favicon
is looked at at 16 pixels across, where the muted versions that read well as
large fills on a blue ground turn to grey.

**Two cuts of it, because 16px is not a small 1024px.** The full mark stripes
the lens, which is the nicest part of it and the first thing to die: at 16
across, nine bands average into one brown smear. The small cut drops them and
leaves the lens as ground, so what survives is the silhouette — two circles,
overlapping — which is the half of the idea worth keeping. It is also drawn
tighter: the mark is a landscape shape in a square box, so the size that looks
composed at 1024 is throwing away a third of the pixels at 16.
"""

from __future__ import annotations

import colorsys
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FULL = ROOT / "src" / "styles" / "overtone_favcon.png"
SMALL = ROOT / "src" / "styles" / "overtone_favcon_small.png"

SIZE = 1024
SUPERSAMPLE = 4

# The ground the previous master used, kept so the icon does not change colour
# underneath people who already have it pinned.
GROUND = (20, 34, 45)

# Hue matches the landing pair (336 / 14); saturation and lightness are lifted
# for something the size of a full stop.
COOL = (336, 74, 50)
WARM = (22, 88, 52)

# Gap between the stripes, and the arcs bounding the lens. Same colour as the
# ground, so the lens reads as cut out of both circles rather than painted on.
RULE = 0.012

# radius and overlap as fractions; stripes in the lens, 0 for none.
#
# The small cut overlaps much harder. That is not only about detail: more
# overlap makes the whole mark narrower, and a narrower landscape shape wastes
# less of a square box — which at 16 pixels is the difference between circles
# eight pixels tall and circles eleven.
CUTS = {
    "full": {"radius": 0.245, "overlap": 0.35, "stripes": 9},
    "small": {"radius": 0.32, "overlap": 0.55, "stripes": 0},
}


def rgb(hsl: tuple[int, int, int]) -> tuple[int, int, int]:
    h, s, light = hsl
    r, g, b = colorsys.hls_to_rgb(h / 360, light / 100, s / 100)
    return (round(r * 255), round(g * 255), round(b * 255))


def draw(Image, ImageDraw, ImageChops, *, radius: float, overlap: float, stripes: int):
    """One cut of the mark, at SUPERSAMPLE times the final size."""
    n = SIZE * SUPERSAMPLE
    cool, warm = rgb(COOL), rgb(WARM)

    r = n * radius
    centre_y = n / 2
    # Circles sit either side of centre, closer together the more they overlap.
    offset = r * (1 - overlap)
    left_x, right_x = n / 2 - offset, n / 2 + offset

    def box(x: float) -> tuple[float, float, float, float]:
        return (x - r, centre_y - r, x + r, centre_y + r)

    image = Image.new("RGB", (n, n), GROUND)
    canvas = ImageDraw.Draw(image)
    canvas.ellipse(box(left_x), fill=cool)
    canvas.ellipse(box(right_x), fill=warm)

    def disc(x: float):
        mask = Image.new("L", (n, n), 0)
        ImageDraw.Draw(mask).ellipse(box(x), fill=255)
        return mask

    lens = ImageChops.darker(disc(left_x), disc(right_x))

    # Stripes are painted full-width on their own layer and then masked to the
    # lens, so each band is clipped to the shape and the pointed top and bottom
    # come out for free. With none, the lens is left as ground: the silhouette
    # without the detail that cannot survive being shrunk.
    bands = Image.new("RGB", (n, n), GROUND)
    if stripes:
        band = ImageDraw.Draw(bands)
        top, height = centre_y - r, r * 2
        gap = n * RULE
        step = height / stripes
        for i in range(stripes):
            band.rectangle(
                (0, top + i * step + gap / 2, n, top + (i + 1) * step - gap / 2),
                fill=(cool if i % 2 else warm),
            )
    image.paste(bands, (0, 0), lens)

    # The arcs bounding the lens, in the ground colour — the dark outline that
    # makes the overlap read as a third shape rather than a colour clash.
    width = max(1, round(n * RULE))
    canvas.ellipse(box(left_x), outline=GROUND, width=width)
    canvas.ellipse(box(right_x), outline=GROUND, width=width)

    return image.resize((SIZE, SIZE), Image.LANCZOS)


def main() -> int:
    try:
        from PIL import Image, ImageChops, ImageDraw
    except ImportError:
        print("Pillow is needed for this: pip install Pillow", file=sys.stderr)
        return 2

    for name, path in (("full", FULL), ("small", SMALL)):
        draw(Image, ImageDraw, ImageChops, **CUTS[name]).save(path, optimize=True)
        print(f"  {path.name:<30} {SIZE}x{SIZE}  {path.stat().st_size / 1024:>5.0f} KB")

    print("Now run: python scripts/make_icons.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
