"""Find text nobody can read, on every screen, in both themes.

    python scripts/check_contrast.py --token "<a signed-in JWT>"
    python scripts/check_contrast.py --token "..." --base http://127.0.0.1:5173

Needs a running frontend and a token for an account that is through
onboarding; without one it checks only the screens a signed-out visitor sees.

**Why a browser and not a stylesheet reader.** Contrast is a property of what
was painted, not of what was written: a colour arrives through a token that
changes with the theme, lands on a background three ancestors up, and gets
faded by an `opacity` on something in between. Reading the CSS tells you what
somebody intended. This reads what a person would actually be looking at.

It has already earned its place. The first run found nine unreadable elements
across five screens — the pair view's own instruction line at 2.0:1, and three
page titles that were white on the sky at 20px — none of which were visible as
bugs to anyone glancing at the screens, because you can read text you already
know the words of.

Two exemptions, both deliberate:

* **Disabled controls.** WCAG 1.4.3 exempts inactive components, and this app
  fades them to 0.45. Counting them buries the real findings.
* **Anything over a gradient**, which has no single colour behind it. Those are
  listed separately rather than guessed at — guessing is how a page can pass an
  audit while being unreadable.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

DEFAULT_BASE = "http://127.0.0.1:5173"

# Every screen worth walking, and whether it needs a signed-in account.
SCREENS = [
    ("landing", "/", False),
    ("join", "/join", False),
    ("signin", "/signin", False),
    ("pairs", "/pairs", True),
    ("inbox", "/inbox", True),
    ("settings", "/settings", True),
    ("review", "/review", True),
]

AUDIT = r"""
() => {
  const px = s => parseFloat(s) || 0;
  const parse = s => {
    const m = (s || '').match(/[\d.]+/g);
    if (!m) return null;
    // `color-mix()` computes to `color(srgb r g b / a)` with channels in 0..1
    // rather than 0..255. Reading those as bytes turns a pale sky into
    // near-black and invents failures on every sticky header in the app.
    const unit = /^color\(/.test(s) ? 255 : 1;
    return [+m[0] * unit, +m[1] * unit, +m[2] * unit, m.length > 3 ? +m[3] : 1];
  };
  const over = (fg, bg) => {
    const a = fg[3];
    return [0, 1, 2].map(i => fg[i] * a + bg[i] * (1 - a)).concat(1);
  };
  const lum = c => {
    const v = c.slice(0, 3).map(x => {
      x /= 255;
      return x <= 0.03928 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4);
    });
    return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2];
  };
  const ratio = (a, b) => {
    const la = lum(a), lb = lum(b);
    return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
  };

  const fails = [], gradients = [];
  for (const el of document.querySelectorAll('*')) {
    // Only elements painting text of their own; a wrapper inherits its
    // children's words and would be counted twice.
    const text = [...el.childNodes]
      .filter(n => n.nodeType === 3).map(n => n.textContent.trim()).join(' ').trim();
    if (!text) continue;

    const box = el.getBoundingClientRect();
    if (!box.width || !box.height) continue;
    const cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || cs.display === 'none') continue;
    if (el.closest('[disabled]') || el.matches(':disabled')) continue;

    // Every ancestor opacity folds into the text's alpha. A faded parent
    // spends contrast exactly as a pale colour does.
    let alpha = 1, node = el;
    while (node && node !== document.documentElement) {
      alpha *= px(getComputedStyle(node).opacity || '1');
      node = node.parentElement;
    }

    let bg = null, gradient = false;
    node = el;
    while (node) {
      const s = getComputedStyle(node);
      if (s.backgroundImage && s.backgroundImage !== 'none') { gradient = true; break; }
      const c = parse(s.backgroundColor);
      if (c && c[3] > 0) bg = bg ? over(bg, c) : c;
      if (bg && bg[3] >= 0.999) break;
      node = node.parentElement;
    }

    const label = (typeof el.className === 'string' && el.className)
      ? '.' + el.className.split(' ')[0] : el.tagName.toLowerCase();
    const sample = text.slice(0, 38).replace(/\s+/g, ' ');

    if (gradient || !bg) { gradients.push({ label, sample }); continue; }

    const own = parse(cs.color);
    const eff = over([own[0], own[1], own[2], own[3] * alpha], bg);
    const size = px(cs.fontSize), weight = +cs.fontWeight || 400;
    // WCAG's "large text": 24px, or 18.66px when bold.
    const need = (size >= 24 || (size >= 18.66 && weight >= 700)) ? 3 : 4.5;
    const got = ratio(eff, bg);
    if (got < need) {
      fails.push({ label, sample, got: +got.toFixed(2), need, size: Math.round(size), weight });
    }
  }
  return { fails, gradients };
}
"""


async def walk(browser, base: str, token: str | None, scheme: str):
    context = await browser.new_context(viewport={"width": 1280, "height": 900}, color_scheme=scheme)
    page = await context.new_page()
    found, over_gradient = [], set()

    await page.goto(base, wait_until="networkidle")
    if token:
        await page.evaluate(f"localStorage.setItem('overtone_token', {token!r})")

    for name, path, needs_auth in SCREENS:
        if needs_auth and not token:
            continue
        await page.goto(f"{base}{path}", wait_until="networkidle")
        await page.wait_for_timeout(700)
        # Where it actually ended up, not where it was sent. The router sends a
        # signed-in visitor at "/" to their home, and reporting the pair view's
        # failures under "landing" sent me looking in the wrong file.
        landed = await page.evaluate("location.pathname")
        where = name if landed == path else f"{name}->{landed.strip('/') or '/'}"

        result = await page.evaluate(AUDIT)
        for row in result["fails"]:
            found.append((scheme, where, row))
        for row in result["gradients"]:
            over_gradient.add((scheme, where, row["label"]))

    await context.close()
    return found, over_gradient


async def main(base: str, token: str | None) -> int:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print(
            "playwright is needed for this:\n  pip install playwright && playwright install chromium",
            file=sys.stderr,
        )
        return 2

    found, gradients = [], set()
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        for scheme in ("light", "dark"):
            f, g = await walk(browser, base, token, scheme)
            found += f
            gradients |= g
        await browser.close()

    if not token:
        print("No --token given: only the signed-out screens were checked.\n")

    if found:
        # Deduplicated: one rule failing on six screens is one thing to fix,
        # and a list that repeats it six times reads as six.
        unique: dict[tuple, tuple] = {}
        for scheme, screen, row in found:
            unique.setdefault((scheme, row["label"], row["got"]), (screen, row))
        print(f"{len(unique)} distinct failure(s), {len(found)} element(s) in total:\n")
        for (scheme, label, got), (screen, row) in sorted(unique.items()):
            print(
                f"  [{scheme:5}] {screen:18} {label:24} "
                f"{got:>5} (needs {row['need']})  "
                f"{row['size']}px/{row['weight']}  {row['sample']!r}"
            )
    else:
        print("Nothing below the threshold.")

    if gradients:
        print(f"\n{len(gradients)} element(s) sit over a gradient and were not judged:")
        for scheme, screen, label in sorted(gradients):
            print(f"  [{scheme:5}] {screen:10} {label}")
        print("  Check these by eye, or against both ends of the gradient.")

    return 1 if found else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--base", default=DEFAULT_BASE, help=f"frontend origin (default {DEFAULT_BASE})")
    parser.add_argument("--token", default=None, help="a JWT for an account through onboarding")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.base, args.token)))
