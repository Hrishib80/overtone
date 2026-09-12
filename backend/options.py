"""Closed option sets for profile fields.

These are plain string tuples validated in the API layer rather than database
enums. A native Postgres enum needs a migration to add a single value, and this
list will grow — an evolving vocabulary should not be schema.

Gender identity and sexuality are deliberately NOT here: they are open,
seeded reference tables (see seeds/identity.json), because they are expressive
rather than operational.
"""

from __future__ import annotations

from typing import Final

# The only identity values matching ever touches. `visible_as` says whose
# searches you appear in; `interested_in` says whose profiles you see. Both are
# multi-select, and both are applied — visibility is mutual or it is nothing.
SEGMENTS: Final = ("man", "woman", "nonbinary")

DATING_INTENTIONS: Final = (
    "life_partner",
    "long_term",
    "long_term_open_to_short",
    "short_term_open_to_long",
    "short_term",
    "figuring_it_out",
)

RELATIONSHIP_TYPES: Final = ("monogamy", "non_monogamy", "figuring_it_out")

# One scale for every vice field, so the UI and the filters stay uniform.
FREQUENCY: Final = ("yes", "sometimes", "no", "prefer_not_to_say")

CHILDREN: Final = (
    "dont_have_children",
    "have_children",
    "prefer_not_to_say",
)

FAMILY_PLANS: Final = (
    "dont_want_children",
    "want_children",
    "open_to_children",
    "not_sure_yet",
    "prefer_not_to_say",
)

PETS: Final = ("dog", "cat", "bird", "fish", "reptile", "other", "none", "prefer_not_to_say")

EDUCATION_LEVELS: Final = (
    "high_school",
    "undergrad",
    "postgrad",
    "doctorate",
    "prefer_not_to_say",
)

# Weighted toward an Indian campus rather than a US one — this is the field
# most often copied thoughtlessly from a US product.
RELIGIONS: Final = (
    "hindu",
    "muslim",
    "christian",
    "sikh",
    "jain",
    "buddhist",
    "parsi_zoroastrian",
    "jewish",
    "spiritual",
    "agnostic",
    "atheist",
    "other",
    "prefer_not_to_say",
)

ETHNICITIES: Final = (
    "south_asian",
    "east_asian",
    "southeast_asian",
    "central_asian",
    "middle_eastern",
    "black_african_descent",
    "hispanic_latino",
    "native_american",
    "pacific_islander",
    "white_caucasian",
    "other",
    "prefer_not_to_say",
)

LANGUAGES: Final = (
    "english",
    "hindi",
    "telugu",
    "tamil",
    "kannada",
    "malayalam",
    "marathi",
    "bengali",
    "gujarati",
    "punjabi",
    "odia",
    "assamese",
    "urdu",
    "konkani",
    "tulu",
    "sanskrit",
    "other",
)

POLITICS: Final = ("liberal", "moderate", "conservative", "not_political", "other", "prefer_not_to_say")

ZODIAC: Final = (
    "aries",
    "taurus",
    "gemini",
    "cancer",
    "leo",
    "virgo",
    "libra",
    "scorpio",
    "sagittarius",
    "capricorn",
    "aquarius",
    "pisces",
)

# Only the three languages the voice pipeline can transcribe (see the model
# stack). `languages` above is what a person *speaks*; this is what we can process.
SUPPORTED_AUDIO_LANGUAGES: Final = ("en", "hi", "te")

WRITTEN_PROMPT_SLOTS: Final = 3
VOICE_PROMPT_SLOTS: Final = 1
VOICE_PROMPT_MAX_SECONDS: Final = 15

MIN_AGE: Final = 18
MAX_AGE: Final = 120

HEIGHT_CM_MIN: Final = 120
HEIGHT_CM_MAX: Final = 230


OPTION_SETS: Final[dict[str, tuple[str, ...]]] = {
    "segments": SEGMENTS,
    "dating_intentions": DATING_INTENTIONS,
    "relationship_types": RELATIONSHIP_TYPES,
    "frequency": FREQUENCY,
    "children": CHILDREN,
    "family_plans": FAMILY_PLANS,
    "pets": PETS,
    "education_levels": EDUCATION_LEVELS,
    "religions": RELIGIONS,
    "ethnicities": ETHNICITIES,
    "languages": LANGUAGES,
    "politics": POLITICS,
    "zodiac": ZODIAC,
}
