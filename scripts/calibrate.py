"""What the unlock dials actually do, before there is real data to tune them on.

    python scripts/calibrate.py              # the current settings
    python scripts/calibrate.py --sweep      # candidate settings, side by side
    python scripts/calibrate.py --trials 50000

Phase 07 says to tune the unlock threshold (0.70) and the confidence level
(90%) on real decisions, and that is still true — nothing here is a substitute
for watching people use it. What this answers is the question you have to ask
*before* you can read real data: what do these numbers mean?

The model is the mechanism, not an approximation of it. A decided pair is a
Bernoulli trial about one person — shown against someone who looks like them
and is rated like them, were they chosen? — so a viewer's real feeling about
somebody is a rate `p`, and the app's job is to notice when `p` is high without
being fooled by a run of luck. This draws trials at a known `p` and calls the
real `affinity.classify` after each one, exactly as `apply_decision` does.

The two numbers that matter are in tension and are the whole design:

* **Cost** — how many comparisons someone must get through before a person
  they genuinely like becomes reachable. Too high and most viewers never see
  an unlock, so the app feels dead.
* **False unlocks** — how often a coin flip clears the bar anyway. Every one
  of those is somebody being told they have a type they do not have, and then
  spending their one opening message on it.

What this cannot tell you is the distribution of `p` across real people, which
is the thing that decides whether the current setting is right. That needs the
beta.
"""

from __future__ import annotations

import argparse
import random
import statistics
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))

from backend.affinity import (  # noqa: E402
    MAX_TRIALS,
    UNLOCK_CONFIDENCE_Z,
    UNLOCK_THRESHOLD,
    AffinityState,
    classify,
)

# The rates worth asking about. 0.50 is the null hypothesis the pairing is
# built to create — similar face, similar rating, so a viewer with no
# preference is a coin flip — and its row is the false-unlock rate.
RATES = [0.50, 0.60, 0.70, 0.80, 0.90, 1.00]

# Confidence levels by their z, since the dial is written as a percentage but
# used as a z-score and the two are easy to confuse.
Z_FOR = {"80%": 1.282, "90%": 1.645, "95%": 1.960, "99%": 2.576}


def run_one(p: float, rng: random.Random, *, threshold: float, z: float, max_trials: int):
    """One viewer's record of one person, played to its conclusion."""
    picked = shown = 0
    while True:
        shown += 1
        if rng.random() < p:
            picked += 1
        state, _low, _high = classify(picked, shown, threshold=threshold, z=z, max_trials=max_trials)
        if state is AffinityState.unlocked:
            return True, shown
        if state is AffinityState.settled:
            return False, shown


def table(trials: int, *, threshold: float, z: float, max_trials: int, seed: int = 20260913):
    rows = []
    for p in RATES:
        rng = random.Random(seed + int(p * 1000))
        unlocked_at, settled_at = [], []
        for _ in range(trials):
            ok, shown = run_one(p, rng, threshold=threshold, z=z, max_trials=max_trials)
            (unlocked_at if ok else settled_at).append(shown)
        rows.append(
            {
                "p": p,
                "unlock_rate": len(unlocked_at) / trials,
                "median": statistics.median(unlocked_at) if unlocked_at else None,
                "p90": (statistics.quantiles(unlocked_at, n=10)[8] if len(unlocked_at) > 10 else None),
                "give_up": statistics.median(settled_at) if settled_at else None,
            }
        )
    return rows


def fastest_unlock(*, threshold: float, z: float, max_trials: int) -> int | None:
    """The shortest possible run of straight picks that clears the bar.

    Worth printing next to the table because it is the number anybody
    reasoning about the feature actually holds in their head, and it falls out
    of the other two dials rather than being chosen.
    """
    for n in range(1, max_trials + 1):
        state, _, _ = classify(n, n, threshold=threshold, z=z, max_trials=max_trials)
        if state is AffinityState.unlocked:
            return n
    return None


def show(title: str, rows, *, threshold: float, z: float, max_trials: int) -> None:
    fastest = fastest_unlock(threshold=threshold, z=z, max_trials=max_trials)
    print(f"\n  {title}")
    print(
        f"  threshold {threshold:.2f}   z {z:.3f}   max trials {max_trials}   "
        f"fastest unlock {fastest} straight picks"
    )
    print("    true rate   unlocks   median   p90   gives up after")
    for r in rows:
        median = f"{r['median']:.0f}" if r["median"] else "-"
        p90 = f"{r['p90']:.0f}" if r["p90"] else "-"
        give_up = f"{r['give_up']:.0f}" if r["give_up"] else "never"
        note = "  <- coin flip: this is the false-unlock rate" if r["p"] == 0.50 else ""
        print(f"      {r['p']:.2f}      {r['unlock_rate']:6.1%}   {median:>6}  {p90:>4}   {give_up:>6}{note}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--trials", type=int, default=20000, help="records per rate")
    parser.add_argument("--sweep", action="store_true", help="compare candidate settings")
    args = parser.parse_args()

    # Printed output stays ASCII: this runs in a Windows console at cp1252,
    # where an en-dash arrives as a replacement character and makes a table
    # of numbers look broken.
    print(
        "\n  A decided pair is a Bernoulli trial about one person. This draws"
        "\n  trials at a known rate and calls the real affinity.classify after"
        "\n  each one, exactly as apply_decision does."
    )

    if not args.sweep:
        show(
            "As shipped",
            table(args.trials, threshold=UNLOCK_THRESHOLD, z=UNLOCK_CONFIDENCE_Z, max_trials=MAX_TRIALS),
            threshold=UNLOCK_THRESHOLD,
            z=UNLOCK_CONFIDENCE_Z,
            max_trials=MAX_TRIALS,
        )
    else:
        for threshold in (0.60, 0.65, 0.70, 0.75):
            for label, z in (("90%", Z_FOR["90%"]),):
                show(
                    f"threshold {threshold:.2f} at {label} confidence",
                    table(args.trials, threshold=threshold, z=z, max_trials=MAX_TRIALS),
                    threshold=threshold,
                    z=z,
                    max_trials=MAX_TRIALS,
                )
        for label in ("80%", "90%", "95%"):
            show(
                f"threshold {UNLOCK_THRESHOLD:.2f} at {label} confidence",
                table(args.trials, threshold=UNLOCK_THRESHOLD, z=Z_FOR[label], max_trials=MAX_TRIALS),
                threshold=UNLOCK_THRESHOLD,
                z=Z_FOR[label],
                max_trials=MAX_TRIALS,
            )

    print(
        "\n  Reading it: the 0.50 row is people you have no real preference about,\n"
        "  so its unlock rate is the price of the setting. The 0.80 and 0.90 rows\n"
        "  are people you do prefer, and their median is what the feature costs\n"
        "  the viewer. Lower the threshold and both go up together - there is no\n"
        "  setting that buys cheap unlocks without buying wrong ones too.\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
