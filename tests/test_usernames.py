"""Usernames: the only identity an account has.

Split the way `test_pairing.py` is — the rules first, pure and fast, then the
HTTP surface that applies them. The rules are what the registration route, the
availability check and the migration all have to agree on, so they are
exercised directly rather than only through a form.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from backend import usernames
from backend.database import User
from tests.conftest import ADULT_BIRTHDATE, register

# ---------------------------------------------------------------------------
# The rules
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["ravi", "ravi_k", "ravi.k", "r4v1", "abc", "a" * 20, "2fast"])
def test_ordinary_names_are_allowed(name):
    assert usernames.problem(name) is None


@pytest.mark.parametrize(
    ("name", "says"),
    [
        ("ab", "at least 3"),
        ("a" * 21, "20 characters or fewer"),
        ("_ravi", "Start with a letter or a number"),
        (".ravi", "Start with a letter or a number"),
        ("ravi k", "only letters, numbers"),
        ("ravi-k", "only letters, numbers"),
        ("ravi@k", "only letters, numbers"),
        ("ravi.", "end with a full stop"),
        ("ravi..k", "two full stops"),
        ("admin", "reserved"),
        ("overtone", "reserved"),
    ],
)
def test_a_refusal_says_what_to_change(name, says):
    """Every refusal is a sentence somebody can act on, not "invalid"."""
    reason = usernames.problem(name)
    assert reason is not None
    assert says.lower() in reason.lower()


def test_case_and_surrounding_space_never_distinguish_two_accounts():
    assert usernames.normalise("  Ravi_K ") == "ravi_k"
    assert usernames.normalise(None) == ""


@pytest.mark.parametrize(
    ("seed", "expected"),
    [
        ("ravi@demo.edu", "ravi"),
        ("Shri@Gmail.com", "shri"),
        ("first.last@college.ac.in", "first.last"),
        ("a@b.c", "a_member"),
        ("admin@example.com", "admin_1"),
        ("weird+tag@x.com", "weird_tag"),
        ("..dots..@x.com", "dots"),
        ("@nothing.com", "member"),
    ],
)
def test_an_old_address_becomes_a_valid_username(seed, expected):
    name = usernames.derive(seed, set())
    assert name == expected
    assert usernames.problem(name) is None


def test_collisions_get_a_number_and_stay_valid():
    taken: set[str] = set()
    names = []
    for seed in ("ravi@a.com", "ravi@b.com", "ravi@c.com"):
        name = usernames.derive(seed, taken)
        taken.add(name)
        names.append(name)
    assert names == ["ravi", "ravi2", "ravi3"]


def test_a_collision_at_the_length_limit_still_fits():
    """The number has to replace characters rather than overflow the limit,
    or the migration would write a name the app itself refuses."""
    long = "x" * 20
    taken = {long}
    name = usernames.derive(f"{long}@a.com", taken)
    assert len(name) <= usernames.MAX_LENGTH
    assert name != long
    assert usernames.problem(name) is None


def test_the_migration_and_the_app_agree_about_the_rules():
    """The migration carries its own copy of the rules, deliberately — it has
    to keep producing the same names after the app's rules change. This is
    the check that the two copies started out identical."""
    import importlib.util
    from pathlib import Path

    path = Path(__file__).parents[1] / "alembic" / "versions" / "b6c2e8f14a90_usernames_replace_email.py"
    spec = importlib.util.spec_from_file_location("usernames_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    assert set(usernames.RESERVED) == migration.RESERVED
    assert (migration.MIN_LENGTH, migration.MAX_LENGTH) == (usernames.MIN_LENGTH, usernames.MAX_LENGTH)
    for seed in ("ravi@demo.edu", "a@b.c", "admin@x.com", "weird+tag@x.com", "x" * 30 + "@y.com"):
        assert migration._derive(seed, set()) == usernames.derive(seed, set())


# ---------------------------------------------------------------------------
# Over HTTP
# ---------------------------------------------------------------------------


async def _join(client, username):
    return await client.post(
        "/api/auth/register",
        json={
            "username": username,
            "password": "a-strong-enough-password",
            "display_name": "Someone",
            "birthdate": ADULT_BIRTHDATE.isoformat(),
        },
    )


@pytest.mark.asyncio
async def test_a_username_is_stored_lowercase(client, db_sessionmaker):
    assert (await _join(client, "  RaviK ")).status_code == 201
    async with db_sessionmaker() as db:
        stored = (await db.execute(select(User.username))).scalars().all()
    assert stored == ["ravik"]


@pytest.mark.asyncio
async def test_a_taken_name_is_refused_whatever_its_case(client):
    """Two accounts that differ only by a capital letter are an impersonation
    waiting to happen."""
    await register(client, "ravik")
    again = await _join(client, "RaviK")
    assert again.status_code == 409
    body = again.json()
    assert body["error"]["message"] == "That username is taken."
    # Named, so the form can point at the right box.
    assert body["fields"][0]["field"] == "username"


@pytest.mark.asyncio
async def test_an_invalid_name_is_refused_with_the_reason(client):
    response = await _join(client, "ravi k")
    assert response.status_code == 400
    body = response.json()
    assert "only letters, numbers" in body["error"]["message"]
    assert body["fields"][0]["field"] == "username"


@pytest.mark.asyncio
async def test_signing_in_ignores_case(client):
    """Somebody typing their own name with a capital on a phone keyboard
    should not be told their password is wrong."""
    await register(client, "ravik")
    signed_in = await client.post(
        "/api/auth/login", json={"username": "RaviK", "password": "a-strong-enough-password"}
    )
    assert signed_in.status_code == 200, signed_in.text


@pytest.mark.asyncio
async def test_a_wrong_username_and_a_wrong_password_look_the_same(client):
    """Neither answer may say which half was wrong — that would turn the login
    form into a way to find out which usernames exist."""
    await register(client, "ravik")
    wrong_password = await client.post("/api/auth/login", json={"username": "ravik", "password": "nope-nope"})
    no_such_user = await client.post(
        "/api/auth/login", json={"username": "nobody_here", "password": "a-strong-enough-password"}
    )
    unregistrable = await client.post("/api/auth/login", json={"username": "a b", "password": "x"})

    assert wrong_password.status_code == no_such_user.status_code == unregistrable.status_code == 401
    messages = {r.json()["error"]["message"] for r in (wrong_password, no_such_user, unregistrable)}
    assert messages == {"Incorrect username or password."}


@pytest.mark.asyncio
async def test_availability_answers_the_three_cases(client):
    await register(client, "ravik")

    free = (await client.get("/api/auth/username-available", params={"name": "Meera_S"})).json()
    assert free == {"username": "meera_s", "available": True, "reason": None}

    taken = (await client.get("/api/auth/username-available", params={"name": "RAVIK"})).json()
    assert taken["available"] is False
    assert taken["reason"] == "That one is taken."

    invalid = (await client.get("/api/auth/username-available", params={"name": "no"})).json()
    assert invalid["available"] is False
    assert "at least 3" in invalid["reason"]


@pytest.mark.asyncio
async def test_the_token_and_me_carry_the_username_and_no_email(client):
    from jose import jwt

    from backend.config import settings

    account = await register(client, "ravik")
    claims = jwt.decode(account["access_token"], settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    assert claims["username"] == "ravik"
    assert "email" not in claims

    me = (await client.get("/api/auth/me", headers=account["headers"])).json()
    assert me["username"] == "ravik"
    assert "email" not in me
    assert "email_verified" not in me


@pytest.mark.asyncio
async def test_other_members_never_see_a_username(client, db_sessionmaker, fake_storage):
    """A username is a login handle, not a way to find a particular person.
    The profile serialisers are the one place it could leak to another
    member, so they are checked for it directly."""
    from backend.profile_view import full_profile_view
    from tests.conftest import onboard

    await onboard(client, "private_name", visible_as=["woman"], interested_in=["man"], store=fake_storage)
    async with db_sessionmaker() as db:
        user_id = (await db.execute(select(User.id).where(User.username == "private_name"))).scalar_one()
        view = await full_profile_view(db, user_id)

    assert "private_name" not in repr(view)
