"""Cut the favicons from the source artwork.

    python scripts/make_icons.py

The sources are the two 1024px masters `scripts/make_mark.py` writes; a
browser wants something about a hundredth of that, and pointing a
`<link rel="icon">` straight at a master costs every visitor most of a
megabyte on first load — more than the entire CSS and JS bundle put together.

**Two masters, because the small sizes get a simpler drawing.** The tab icon
is cut from the small one, whose lens is left plain: striping it is the nicest
thing about the mark at full size and the first thing to turn to mud at 16
pixels. The iOS home-screen icon is big enough for the real thing.

Run this after `make_mark.py`. The derived files are committed rather than
generated at build time, so the repo stays buildable without Pillow and a
deploy never depends on an image library being present.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FULL = ROOT / "src" / "styles" / "overtone_favcon.png"
SMALL = ROOT / "src" / "styles" / "overtone_favcon_small.png"
OUT = ROOT / "public"

# What browsers actually ask for, and which drawing each one gets. The .ico
# carries the small sizes in one file for the tab; the 180 is the iOS
# home-screen icon and is the one size with room for the stripes.
PNG_SIZES = {"favicon-32.png": (32, SMALL), "favicon-180.png": (180, FULL)}
ICO_SIZES = [(16, 16), (32, 32), (48, 48)]


def main() -> int:
    try:
        from PIL import Image
    except ImportError:
        print("Pillow is needed for this: pip install Pillow", file=sys.stderr)
        return 2

    missing = [p for p in (FULL, SMALL) if not p.exists()]
    if missing:
        for p in missing:
            print(f"No source artwork at {p}", file=sys.stderr)
        print("Run: python scripts/make_mark.py", file=sys.stderr)
        return 2

    OUT.mkdir(parents=True, exist_ok=True)

    for name, (size, source) in PNG_SIZES.items():
        image = Image.open(source).convert("RGBA")
        image.resize((size, size), Image.LANCZOS).save(OUT / name, optimize=True)
        kb = (OUT / name).stat().st_size / 1024
        print(f"  {name:<16} {size:>4}px from {source.name:<28} {kb:>6.1f} KB")

    # Pillow takes the largest requested size from whatever it is handed, so
    # downscale once first rather than asking it to fit 1024px into 48.
    small = Image.open(SMALL).convert("RGBA")
    small.resize((64, 64), Image.LANCZOS).save(OUT / "favicon.ico", sizes=ICO_SIZES)
    kb = (OUT / "favicon.ico").stat().st_size / 1024
    print(f"  {'favicon.ico':<16} {'16/32/48':>8} from {SMALL.name:<28} {kb:>6.1f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
