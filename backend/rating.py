"""Glicko-2, with weighted comparisons.

Elo would work, but it carries one number and a fixed K-factor, so it cannot
tell a 1500 compared twice from a 1500 compared four hundred times. Glicko-2
adds a rating deviation — an explicit uncertainty — and that single addition
solves three problems this system has at once:

* cold start, because a new profile enters wide and settles as evidence
  arrives, with no hand-tuned provisional period;
* pair selection, because candidates can be matched on interval overlap rather
  than point distance, which is also how an uncertain newcomer gets rated fast;
* the unlock threshold, which needs a confidence interval to be honest.

The one extension here is **weight**. A pair is judged twice — once on a photo
alone, once with the whole profile showing — and the informed judgement should
count for more. Weight scales each comparison's contribution to both the
information quantity and the rating change, which is the natural way to express
that inside the standard derivation.

Everything in this module is a pure function. The database knows nothing about
the maths and the maths knows nothing about the database.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Final

# Glicko-2 works on an internal scale; these convert to and from the familiar
# 1500-centred one.
SCALE: Final = 173.7178
BASE_RATING: Final = 1500.0

# Starting values. A deviation of 350 is maximum uncertainty — it settles
# within roughly ten to fifteen comparisons.
DEFAULT_RATING: Final = 1500.0
DEFAULT_DEVIATION: Final = 350.0
DEFAULT_VOLATILITY: Final = 0.06

# Constrains how much volatility itself may move. Lower is steadier; 0.5 is the
# usual choice for systems where form does not swing wildly, which is true of a
# face and untrue of a chess player.
TAU: Final = 0.5

CONVERGENCE: Final = 1e-6
MAX_ITERATIONS: Final = 100

# A deviation is never allowed below this: a rating that claims near-certainty
# stops responding to new evidence, and people do change their photo.
MIN_DEVIATION: Final = 30.0
MAX_DEVIATION: Final = DEFAULT_DEVIATION


@dataclass(frozen=True, slots=True)
class Rating:
    rating: float = DEFAULT_RATING
    deviation: float = DEFAULT_DEVIATION
    volatility: float = DEFAULT_VOLATILITY

    @property
    def mu(self) -> float:
        return (self.rating - BASE_RATING) / SCALE

    @property
    def phi(self) -> float:
        return self.deviation / SCALE

    def interval(self, z: float = 1.96) -> tuple[float, float]:
        """The range this rating plausibly lies in. Pair selection uses the
        overlap of two intervals rather than the distance between two points."""
        margin = z * self.deviation
        return self.rating - margin, self.rating + margin

    def overlaps(self, other: Rating, z: float = 1.96) -> bool:
        low, high = self.interval(z)
        other_low, other_high = other.interval(z)
        return low <= other_high and other_low <= high


@dataclass(frozen=True, slots=True)
class Comparison:
    """One judged matchup, from the perspective of the subject being rated."""

    opponent: Rating
    score: float  # 1.0 chosen, 0.0 not chosen
    weight: float = 1.0


def _g(phi: float) -> float:
    return 1.0 / math.sqrt(1.0 + 3.0 * phi * phi / (math.pi * math.pi))


def _expected(mu: float, opponent_mu: float, opponent_phi: float) -> float:
    return 1.0 / (1.0 + math.exp(-_g(opponent_phi) * (mu - opponent_mu)))


def expected_score(subject: Rating, opponent: Rating) -> float:
    """Probability the subject is chosen. Pair generation aims this at 0.5,
    which is simultaneously the fairest matchup and the most informative one."""
    return _expected(subject.mu, opponent.mu, opponent.phi)


def _new_volatility(phi: float, sigma: float, v: float, delta: float, tau: float) -> float:
    """Illinois variant of regula falsi, as specified by Glickman."""
    a = math.log(sigma * sigma)
    delta_sq = delta * delta
    phi_sq = phi * phi

    def f(x: float) -> float:
        exp_x = math.exp(x)
        numerator = exp_x * (delta_sq - phi_sq - v - exp_x)
        denominator = 2.0 * (phi_sq + v + exp_x) ** 2
        return numerator / denominator - (x - a) / (tau * tau)

    lower = a
    if delta_sq > phi_sq + v:
        upper = math.log(delta_sq - phi_sq - v)
    else:
        k = 1
        while f(a - k * tau) < 0 and k < MAX_ITERATIONS:
            k += 1
        upper = a - k * tau
        lower, upper = upper, a

    f_lower, f_upper = f(lower), f(upper)
    for _ in range(MAX_ITERATIONS):
        if abs(upper - lower) <= CONVERGENCE:
            break
        mid = lower + (lower - upper) * f_lower / (f_upper - f_lower)
        f_mid = f(mid)
        if f_mid * f_upper <= 0:
            lower, f_lower = upper, f_upper
        else:
            f_lower /= 2.0
        upper, f_upper = mid, f_mid

    return math.exp(upper / 2.0)


def decay(subject: Rating) -> Rating:
    """A rating period with no comparisons. Certainty decays; the rating does not.

    Without this, someone who stops appearing keeps a confident rating forever
    and the pair generator goes on trusting stale evidence.
    """
    phi_star = math.sqrt(subject.phi**2 + subject.volatility**2)
    return Rating(
        rating=subject.rating,
        deviation=min(MAX_DEVIATION, SCALE * phi_star),
        volatility=subject.volatility,
    )


def update(subject: Rating, comparisons: list[Comparison], *, tau: float = TAU) -> Rating:
    """Apply a rating period's worth of comparisons.

    Batching is deliberate — Glicko-2 is defined over a period rather than a
    single game, and it is also what lets concurrent judgements of the same
    person be applied in one ordered pass instead of racing each other.
    """
    if not comparisons:
        return decay(subject)

    mu, phi = subject.mu, subject.phi

    variance_terms = 0.0
    delta_terms = 0.0
    for comparison in comparisons:
        g = _g(comparison.opponent.phi)
        e = _expected(mu, comparison.opponent.mu, comparison.opponent.phi)
        variance_terms += comparison.weight * g * g * e * (1.0 - e)
        delta_terms += comparison.weight * g * (comparison.score - e)

    if variance_terms <= 0.0:
        # Every matchup was so lopsided it carried no information.
        return decay(subject)

    v = 1.0 / variance_terms
    delta = v * delta_terms

    sigma = _new_volatility(phi, subject.volatility, v, delta, tau)
    phi_star = math.sqrt(phi * phi + sigma * sigma)
    phi_new = 1.0 / math.sqrt(1.0 / (phi_star * phi_star) + 1.0 / v)
    mu_new = mu + phi_new * phi_new * delta_terms

    return Rating(
        rating=SCALE * mu_new + BASE_RATING,
        deviation=min(MAX_DEVIATION, max(MIN_DEVIATION, SCALE * phi_new)),
        volatility=sigma,
    )


def wilson_interval(picked: int, shown: int, z: float = 1.645) -> tuple[float, float]:
    """Both ends of a Wilson score interval over a pick rate.

    Raw rates are useless at the sample sizes this system actually sees — two
    out of two is 100% and means nothing. This asks the honest question
    instead: what range of true rates is consistent with what we have seen?

    Both ends earn their keep. The lower bound *opens* a question — a
    preference is only acted on once we are confident it is at least strong.
    The upper bound *closes* one: when even the optimistic end sits below the
    threshold, further comparisons would only be spent confirming a no, and
    the system should stop spending them.

    Default z is 90% confidence rather than 95%, because at launch volumes the
    stricter bound is unreachable for almost everyone.
    """
    if shown <= 0:
        # No evidence constrains nothing — the honest interval is the whole
        # range, which reads as neither unlocked nor settled downstream.
        return (0.0, 1.0)

    p = picked / shown
    z_sq = z * z
    centre = (p + z_sq / (2 * shown)) / (1 + z_sq / shown)
    margin = (z / (1 + z_sq / shown)) * math.sqrt(p * (1 - p) / shown + z_sq / (4 * shown * shown))
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def wilson_lower_bound(picked: int, shown: int, z: float = 1.645) -> float:
    """The lower end alone. See `wilson_interval`."""
    return wilson_interval(picked, shown, z)[0]
