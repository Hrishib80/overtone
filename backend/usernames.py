"""Usernames: the only identity an account has.

There is no email. Somebody joins with a username and a password, and signs in
with the same two things. That makes this module the whole of "who is this
account", so the rules are pure and kept in one place — the registration
route, the availability check, the migration that gave existing accounts one,
and the tests all have to agree about what a valid name is, and four copies of
a regex do not stay in agreement.

**Private, not a handle.** Other members never see a username; they see a
display name. A username that was public would be a way to find a specific
person, which on a campus is exactly the thing a dating app should not hand
out. Reviewers see it, because a report has to name an account unambiguously.

**Case-insensitive, stored lowercase.** `Shri` and `shri` are one account. Two
accounts that differ only by case are an impersonation waiting to happen, and
somebody typing their own name with a capital on a phone keyboard should not be
told the password is wrong.

**The cost of having no email, stated once:** there is no password recovery.
A forgotten password is a lost account. That is the trade for not collecting
an address, and the join form says so rather than letting somebody find out.
"""

from __future__ import annotations

import re

MIN_LENGTH = 3
MAX_LENGTH = 20

# Letters, digits, underscore, period. Starts with a letter or digit, so a name
# cannot hide behind leading punctuation; never ends with a period, and never
# has two in a row, both of which read as typos and invite look-alike names.
_SHAPE = re.compile(r"^[a-z0-9][a-z0-9_.]*$")

# Names that would read as the app speaking. Nobody else sees a username, but a
# reviewer does, and "admin" in a moderation queue is a confusion nobody needs.
RESERVED = frozenset(
    {
        "admin",
        "administrator",
        "api",
        "help",
        "me",
        "mod",
        "moderator",
        "null",
        "official",
        "overtone",
        "root",
        "settings",
        "staff",
        "support",
        "system",
        "undefined",
    }
)


def normalise(raw: str | None) -> str:
    """What gets stored and compared. Surrounding space is a paste artefact,
    and case never distinguishes two accounts."""
    return (raw or "").strip().lower()


def problem(name: str) -> str | None:
    """Why `name` cannot be a username, in words a person can act on — or None.

    Expects an already-normalised name. Returns the *first* problem rather
    than all of them: somebody fixing a form wants the next thing to change,
    not a list.
    """
    if len(name) < MIN_LENGTH:
        return f"Use at least {MIN_LENGTH} characters."
    if len(name) > MAX_LENGTH:
        return f"Use {MAX_LENGTH} characters or fewer."
    if not _SHAPE.match(name):
        if not name[0].isalnum():
            return "Start with a letter or a number."
        return "Use only letters, numbers, underscores and full stops."
    if name.endswith("."):
        return "It can't end with a full stop."
    if ".." in name:
        return "It can't have two full stops in a row."
    if name in RESERVED:
        return "That one is reserved. Try another."
    return None


def derive(seed: str, taken: set[str]) -> str:
    """A valid, unused username built from arbitrary text.

    Used to give every account that existed before usernames a name of its
    own, from the part of its old address before the `@`. Deterministic, so
    running it twice over the same accounts in the same order gives the same
    names — which is what makes the migration safe to reason about.
    """
    base = seed.split("@", 1)[0].lower()
    base = re.sub(r"[^a-z0-9_.]", "_", base)
    base = re.sub(r"\.{2,}", ".", base).strip("._")
    if not base:
        base = "member"
    base = base[:MAX_LENGTH].rstrip(".")
    if len(base) < MIN_LENGTH:
        base = (base + "_member")[:MAX_LENGTH]
    if base in RESERVED:
        base = (base + "_1")[:MAX_LENGTH]

    candidate, n = base, 1
    while candidate in taken or problem(candidate):
        n += 1
        suffix = str(n)
        candidate = base[: MAX_LENGTH - len(suffix)].rstrip(".") + suffix
    return candidate
