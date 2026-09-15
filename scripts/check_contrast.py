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
import os
import sys

DEFAULT_BASE = "http://127.0.0.1:5173"

# Every screen worth walking, and whether it needs a signed-in account.
SCREENS = [
    ("landing", "/", False),
    ("join", "/join", False),
    ("signin", "/signin", False),
    ("pairs", "/pairs", True),
    ("type", "/type", True),
    ("chosen", "/chosen", True),
    ("messages", "/messages", True),
    ("profile", "/profile", True),
    ("settings", "/settings", True),
    ("review", "/review", True),
    # Kept in step with src/services/paths.js; override with VITE_ADMIN_PATH.
    ("admin", "/" + os.environ.get("VITE_ADMIN_PATH", "desk-5pngn47wv5na").strip("/"), True),
]

# Shared by every check below. The page is measured, not the stylesheet:
# contrast is a property of what was painted.
HELPERS = r"""
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
  const nameOf = el => (typeof el.className === 'string' && el.className)
    ? '.' + el.className.split(' ')[0] : el.tagName.toLowerCase();

  // Everything painted behind `from`, composited down the ancestor chain.
  // Returns `gradient: true` when no single colour can answer — a photograph,
  // a gradient, or a scrim — because guessing is how a page passes an audit
  // while being unreadable.
  const behind = (el, from) => {
    let bg = null, gradient = false, node = from;
    while (node) {
      const s = getComputedStyle(node);
      if (s.backgroundImage && s.backgroundImage !== 'none') { gradient = true; break; }
      const c = parse(s.backgroundColor);
      if (c && c[3] > 0) bg = bg ? over(bg, c) : c;
      if (bg && bg[3] >= 0.999) break;
      node = node.parentElement;
    }

    // A scrim painted by a *sibling* rather than an ancestor. A caption over a
    // photograph sits on one of these, and the walk up the tree cannot see it:
    // the scrim is not above the text, it is beside something above it.
    // Checking only the element's own siblings is not enough either — the veil
    // is a sibling of the caption, not of the name inside it — so this climbs
    // alongside the background walk.
    if (!gradient) {
      const box = el.getBoundingClientRect();
      const covers = r => r.left <= box.left + 0.5 && r.right >= box.right - 0.5
        && r.top <= box.top + 0.5 && r.bottom >= box.bottom - 0.5;
      for (let n = el; n && n !== document.body && !gradient; n = n.parentElement) {
        for (const sib of n.parentElement ? n.parentElement.children : []) {
          if (sib === n) continue;
          const ss = getComputedStyle(sib);
          if (ss.position === 'static' || ss.visibility === 'hidden') continue;
          const paints = (ss.backgroundImage && ss.backgroundImage !== 'none')
            || (parse(ss.backgroundColor) || [0, 0, 0, 0])[3] > 0;
          if (paints && covers(sib.getBoundingClientRect())) { gradient = true; break; }
        }
      }
    }
    return { bg, gradient };
  };

  const shown = el => {
    const s = getComputedStyle(el);
    if (s.visibility === 'hidden' || s.display === 'none') return false;
    const b = el.getBoundingClientRect();
    return b.width > 0 && b.height > 0;
  };

  // WCAG 1.4.3 exempts inactive components, and this app fades them to 0.45.
  const off = el => el.closest('[disabled]') || el.matches(':disabled');
"""

# ---- 1.4.3: text -----------------------------------------------------------

AUDIT = (
    "() => {"
    + HELPERS
    + r"""
  const fails = [], gradients = [];
  for (const el of document.querySelectorAll('*')) {
    // Only elements painting text of their own; a wrapper inherits its
    // children's words and would be counted twice.
    const text = [...el.childNodes]
      .filter(n => n.nodeType === 3).map(n => n.textContent.trim()).join(' ').trim();
    if (!text) continue;
    if (!shown(el) || off(el)) continue;

    const cs = getComputedStyle(el);

    // Every ancestor opacity folds into the text's alpha. A faded parent
    // spends contrast exactly as a pale colour does.
    let alpha = 1, node = el;
    while (node && node !== document.documentElement) {
      alpha *= px(getComputedStyle(node).opacity || '1');
      node = node.parentElement;
    }
    // Fully transparent is hidden, not faint: the photo report flag on a
    // revealed profile sits at opacity 0 until hovered or focused, by design.
    // Judging it at rest reported 1:1 for a control nobody is meant to see
    // yet. Anything partly faded is still judged, faded.
    if (alpha < 0.02) continue;

    const { bg, gradient } = behind(el, el);
    const label = nameOf(el);
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
)

# ---- 1.4.11: everything else -----------------------------------------------

CONTROLS = (
    "() => {"
    + HELPERS
    + r"""
  const fails = [];
  // Form fields only, and that restriction is the whole judgement.
  //
  // 1.4.11 asks whether you can tell where a control is. A button, a card or
  // a nav link answers that with its own content — the words are the control,
  // and 1.4.3 already governs them. An empty text field answers it with
  // nothing at all: remove its border and there is literally no pixel saying
  // a control is there. So the boundary requirement bites here and almost
  // nowhere else.
  //
  // The first pass of this check demanded a 3:1 boundary on every button and
  // card too, and produced eighteen findings, every one of which was a
  // photograph or a word doing its job. An audit that cries wolf gets muted,
  // which costs more than the check was worth.
  for (const el of document.querySelectorAll('input, select, textarea')) {
    if (!shown(el) || off(el)) continue;
    if (el.type === 'hidden' || el.type === 'file' || el.type === 'range') continue;
    // A checkbox or radio is painted by the platform and reports none of it
    // through getComputedStyle; judging it here would be inventing a number.
    if (el.type === 'checkbox' || el.type === 'radio') continue;

    const cs = getComputedStyle(el);
    const outside = behind(el, el.parentElement);
    if (outside.gradient || !outside.bg) continue;  // over a photograph; unjudgeable

    // A control is findable if its *fill* separates it from the page, or if
    // it has a border that does. Either is enough — demanding both would fail
    // every solid button in the app, which is exactly the kind of noise that
    // makes an audit get ignored.
    let best = 0, via = 'nothing';

    const fill = parse(cs.backgroundColor);
    if (fill && fill[3] > 0) {
      const composited = over(fill, outside.bg);
      const r = ratio(composited, outside.bg);
      if (r > best) { best = r; via = 'fill'; }
    }

    for (const side of ['Top', 'Right', 'Bottom', 'Left']) {
      if (cs['border' + side + 'Style'] === 'none') continue;
      if (px(cs['border' + side + 'Width']) <= 0) continue;
      const line = parse(cs['border' + side + 'Color']);
      if (!line || line[3] === 0) continue;
      const r = ratio(over(line, outside.bg), outside.bg);
      if (r > best) { best = r; via = 'border'; }
    }

    if (best < 3) {
      fails.push({
        label: nameOf(el),
        sample: (el.value || el.textContent || el.placeholder || '').trim().slice(0, 28),
        got: +best.toFixed(2),
        via,
      });
    }
  }
  return fails;
}
"""
)

# Read off whatever currently holds focus. Driven by real Tab presses from the
# Python side rather than `el.focus()`, because `:focus-visible` is what the
# rules are written against and a scripted focus does not always match it.
FOCUS = (
    "() => {"
    + HELPERS
    + r"""
  const el = document.activeElement;
  if (!el || el === document.body || !shown(el)) return null;

  const cs = getComputedStyle(el);
  const width = px(cs.outlineWidth);
  const offset = px(cs.outlineOffset);
  const hasOutline = cs.outlineStyle !== 'none' && width > 0;
  const hasShadow = cs.boxShadow && cs.boxShadow !== 'none';

  // A negative offset draws the ring inside the control, so what it sits
  // against is the control's own fill; otherwise it is outside, against
  // whatever the control is sitting on.
  const against = behind(el, offset < 0 ? el : el.parentElement);
  const base = { label: nameOf(el), tag: el.tagName.toLowerCase(),
                 sample: (el.textContent || el.value || '').trim().slice(0, 28) };

  if (!hasOutline) {
    // A box-shadow can be a perfectly good focus ring, and this cannot
    // measure one honestly — the colour is in there but so is the geometry.
    // Say so rather than passing it or failing it.
    if (hasShadow) return { ...base, ok: true, unjudged: true };
    return { ...base, ok: false, got: 0, why: 'no visible focus indicator' };
  }
  if (against.gradient || !against.bg) return { ...base, ok: true, unjudged: true };

  const ring = parse(cs.outlineColor);
  if (!ring) return { ...base, ok: true, unjudged: true };
  const got = ratio(over(ring, against.bg), against.bg);
  return { ...base, ok: got >= 3, got: +got.toFixed(2), why: 'focus ring' };
}
"""
)


async def tab_sweep(page, limit: int = 60):
    """Tab through the page and read the focus ring off whatever lands.

    Real key presses rather than `el.focus()`, because `:focus-visible` is
    what the rules are written against and a scripted focus does not always
    match it — checking the scripted state would measure a ring the keyboard
    user never sees, in either direction.
    """
    rows, seen = [], set()
    await page.evaluate("() => (document.activeElement || document.body).blur()")
    first = None
    for _ in range(limit):
        await page.keyboard.press("Tab")
        row = await page.evaluate(FOCUS)
        if row is None:
            continue
        # One ring per rule, not per element: a list that repeats the same
        # `.btn` eleven times reads as eleven problems.
        key = (row["label"], row.get("why"))
        marker = (row["label"], row["sample"])
        if first is None:
            first = marker
        elif marker == first:
            break  # wrapped around
        if key in seen:
            continue
        seen.add(key)
        rows.append(row)
    return rows


async def walk(browser, base: str, token: str | None, scheme: str):
    context = await browser.new_context(viewport={"width": 1280, "height": 900}, color_scheme=scheme)
    page = await context.new_page()
    found, over_gradient, nontext = [], set(), []

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

        for row in await page.evaluate(CONTROLS):
            nontext.append((scheme, where, {**row, "kind": "control boundary"}))
        for row in await tab_sweep(page):
            if not row["ok"]:
                nontext.append((scheme, where, {**row, "kind": row.get("why", "focus")}))

    await context.close()
    return found, over_gradient, nontext


async def main(base: str, token: str | None) -> int:
    # Findings quote the page's own text, which can hold characters a Windows
    # console codepage cannot print (the report control's flag is one). A
    # finding must never be what crashes the audit that found it.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print(
            "playwright is needed for this:\n  pip install playwright && playwright install chromium",
            file=sys.stderr,
        )
        return 2

    found, gradients, nontext = [], set(), []
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        for scheme in ("light", "dark"):
            f, g, n = await walk(browser, base, token, scheme)
            found += f
            gradients |= g
            nontext += n
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

    if nontext:
        # 1.4.11 is a separate requirement from 1.4.3 and a separate kind of
        # fix, so it is reported separately rather than mixed into a list
        # somebody is reading for text problems.
        unique: dict[tuple, tuple] = {}
        for scheme, screen, row in nontext:
            unique.setdefault((scheme, row["label"], row["kind"]), (screen, row))
        print(f"\n{len(unique)} non-text failure(s) — WCAG 1.4.11 wants 3:1:")
        for (scheme, label, kind), (screen, row) in sorted(unique.items()):
            got = row.get("got")
            measured = f"{got:>5}" if got else "  n/a"
            print(f"  [{scheme:5}] {screen:18} {label:24} {measured}  {kind}")

    if gradients:
        print(f"\n{len(gradients)} element(s) sit over a gradient and were not judged:")
        for scheme, screen, label in sorted(gradients):
            print(f"  [{scheme:5}] {screen:10} {label}")
        print("  Check these by eye, or against both ends of the gradient.")

    return 1 if (found or nontext) else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--base", default=DEFAULT_BASE, help=f"frontend origin (default {DEFAULT_BASE})")
    parser.add_argument("--token", default=None, help="a JWT for an account through onboarding")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.base, args.token)))
