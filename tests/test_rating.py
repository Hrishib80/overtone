"""Glicko-2 properties.

Checked as behaviour rather than against hard-coded numbers, except for
Glickman's own worked example — that one is the reference implementation test
and must match to three decimals.
"""

import math

import pytest

from backend.rating import (
    DEFAULT_DEVIATION,
    MIN_DEVIATION,
    Comparison,
    Rating,
    decay,
    expected_score,
    update,
    wilson_lower_bound,
)


def test_matches_glickmans_worked_example():
    """The paper's example: 1500/200 against three opponents, W L L."""
    subject = Rating(rating=1500, deviation=200, volatility=0.06)
    result = update(
        subject,
        [
            Comparison(Rating(1400, 30, 0.06), 1.0),
            Comparison(Rating(1550, 100, 0.06), 0.0),
            Comparison(Rating(1700, 300, 0.06), 0.0),
        ],
        tau=0.5,
    )

    assert result.rating == pytest.approx(1464.06, abs=0.05)
    assert result.deviation == pytest.approx(151.52, abs=0.05)
    assert result.volatility == pytest.approx(0.05999, abs=0.0001)


def test_equal_ratings_are_a_coin_flip():
    """Pair generation aims for this: the most informative matchup is 50/50."""
    assert expected_score(Rating(1500, 50), Rating(1500, 50)) == pytest.approx(0.5)


def test_a_higher_rating_is_favoured():
    assert expected_score(Rating(1700, 50), Rating(1300, 50)) > 0.8


def test_being_chosen_raises_the_rating():
    before = Rating(1500, 200)
    after = update(before, [Comparison(Rating(1500, 200), 1.0)])
    assert after.rating > before.rating


def test_being_rejected_lowers_the_rating():
    before = Rating(1500, 200)
    after = update(before, [Comparison(Rating(1500, 200), 0.0)])
    assert after.rating < before.rating


def test_evidence_reduces_uncertainty():
    before = Rating(1500, 350)
    after = update(before, [Comparison(Rating(1500, 100), 1.0) for _ in range(5)])
    assert after.deviation < before.deviation


def test_a_new_profile_settles_within_about_fifteen_comparisons():
    """Cold start has to resolve fast or the pair generator has nothing to work
    with. This is the property that replaces a provisional period."""
    subject = Rating()
    for _ in range(15):
        subject = update(subject, [Comparison(Rating(1500, 60), 1.0)])

    # From maximum uncertainty (350) to well under half of that is "settled"
    # for this purpose — the pair generator can already treat the interval as
    # meaningfully narrower than a newcomer's.
    assert subject.deviation < 150
    assert subject.rating > 1600


def test_deviation_never_collapses_to_certainty():
    """A rating that stops responding to evidence is wrong — people change
    their photo."""
    subject = Rating(1500, 50)
    for _ in range(200):
        subject = update(subject, [Comparison(Rating(1500, 50), 1.0)])
    assert subject.deviation >= MIN_DEVIATION


def test_an_idle_period_widens_uncertainty_without_moving_the_rating():
    before = Rating(1700, 80)
    after = decay(before)

    assert after.rating == before.rating
    assert after.deviation > before.deviation


def test_no_comparisons_is_treated_as_an_idle_period():
    before = Rating(1600, 90)
    assert update(before, []) == decay(before)


def test_decay_is_capped_at_maximum_uncertainty():
    subject = Rating(1500, 340, 0.06)
    for _ in range(200):
        subject = decay(subject)
    assert subject.deviation <= DEFAULT_DEVIATION


def test_a_weighted_comparison_moves_the_rating_further():
    """Round two is judged with the whole profile showing, so it counts more."""
    snap = update(Rating(1500, 200), [Comparison(Rating(1500, 200), 1.0, weight=1.0)])
    informed = update(Rating(1500, 200), [Comparison(Rating(1500, 200), 1.0, weight=2.5)])

    assert informed.rating > snap.rating


def test_beating_a_strong_opponent_is_worth_more():
    weak = update(Rating(1500, 200), [Comparison(Rating(1200, 50), 1.0)])
    strong = update(Rating(1500, 200), [Comparison(Rating(1800, 50), 1.0)])
    assert strong.rating > weak.rating


def test_an_uncertain_opponent_moves_the_rating_less():
    """Little is known about them, so the result says little about us."""
    certain = update(Rating(1500, 200), [Comparison(Rating(1500, 30), 1.0)])
    uncertain = update(Rating(1500, 200), [Comparison(Rating(1500, 350), 1.0)])
    assert certain.rating > uncertain.rating


def test_intervals_overlap_for_comparable_profiles():
    assert Rating(1500, 100).overlaps(Rating(1560, 100))
    assert not Rating(1500, 30).overlaps(Rating(2000, 30))


def test_a_newcomer_overlaps_a_wide_band():
    """Which is exactly how they get rated quickly."""
    newcomer = Rating(1500, 350)
    assert newcomer.overlaps(Rating(1200, 60))
    assert newcomer.overlaps(Rating(1800, 60))


def test_ratings_stay_finite_under_a_long_losing_run():
    subject = Rating()
    for _ in range(100):
        subject = update(subject, [Comparison(Rating(1500, 80), 0.0)])

    assert math.isfinite(subject.rating)
    assert math.isfinite(subject.deviation)
    assert subject.volatility > 0


# ---- Wilson bound ---------------------------------------------------------


@pytest.mark.parametrize(
    ("picked", "shown", "ceiling"),
    [(2, 2, 0.55), (5, 5, 0.75), (8, 8, 0.80)],
)
def test_small_samples_cannot_clear_the_threshold(picked, shown, ceiling):
    """A perfect record over two rounds must not unlock anything."""
    assert wilson_lower_bound(picked, shown) < ceiling


def test_a_long_record_does_clear_it():
    assert wilson_lower_bound(30, 30) > 0.85


def test_the_bound_rises_with_evidence():
    assert wilson_lower_bound(3, 3) < wilson_lower_bound(10, 10) < wilson_lower_bound(40, 40)


def test_the_bound_is_below_the_raw_rate():
    """The whole point: it never flatters a small sample."""
    for picked, shown in [(1, 1), (4, 5), (9, 10), (90, 100)]:
        assert wilson_lower_bound(picked, shown) < picked / shown


def test_no_observations_is_zero():
    assert wilson_lower_bound(0, 0) == 0.0
