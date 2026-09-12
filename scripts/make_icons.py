"""Cut the favicons from the source artwork.

    python scripts/make_icons.py

The source is a 1024px master kept in `src/styles/`; a browser wants something
about a hundredth of that, and pointing a `<link rel="icon">` straight at the
master costs every visitor ~900 KB on first load — more than the entire CSS
and JS bundle put together.

Run this after re-exporting the master. The derived files are committed rather
than generated at build time, so the repo stays buildable without Pillow and a
deploy never depends on an image library being present.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "src" / "styles" / "overtone_favcon.png"
OUT = ROOT / "public"

# What browsers actually ask for. The .ico carries the small sizes in one
# file for the tab; the 180 is the iOS home-screen icon.
PNG_SIZES = {"favicon-32.png": 32, "favicon-180.png": 180}
ICO_SIZES = [(16, 16), (32, 32), (48, 48)]


def main() -> int:
    try:
        from PIL import Image
    except ImportError:
        print("Pillow is needed for this: pip install Pillow", file=sys.stderr)
        return 2

    if not SOURCE.exists():
        print(f"No source artwork at {SOURCE}", file=sys.stderr)
        return 2

    OUT.mkdir(parents=True, exist_ok=True)
    image = Image.open(SOURCE).convert("RGBA")
    print(f"{SOURCE.name}: {image.size[0]}x{image.size[1]}, {SOURCE.stat().st_size / 1024:.0f} KB")

    for name, size in PNG_SIZES.items():
        image.resize((size, size), Image.LANCZOS).save(OUT / name, optimize=True)
        print(f"  {name:<16} {(OUT / name).stat().st_size / 1024:>6.1f} KB")

    # Pillow takes the largest requested size from whatever it is handed, so
    # downscale once first rather than asking it to fit 1024px into 48.
    image.resize((64, 64), Image.LANCZOS).save(OUT / "favicon.ico", sizes=ICO_SIZES)
    print(f"  {'favicon.ico':<16} {(OUT / 'favicon.ico').stat().st_size / 1024:>6.1f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
