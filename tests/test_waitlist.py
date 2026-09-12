"""Caps, admission and the waitlist.

The campus fixture caps each segment at two, so the boundary is reachable.
"""

import pytest
from sqlalchemy import select

from backend.access import invite_next
from backend.database import User, UserStatus, WaitlistEntry, WaitlistStatus, utcnow
from tests.conftest import CAMPUS_DOMAIN, complete_profile, register_and_verify


async def onboard(
    client, email: str, *, visible_as: list[str], interested_in: list[str], store: dict
) -> dict:
    account = await register_and_verify(client, email)
    await complete_profile(
        client,
        account["headers"],
        visible_as=visible_as,
        interested_in=interested_in,
        store=store,
    )
    response = await client.post("/api/profile/submit", headers=account["headers"])
    assert response.status_code == 200, response.text
    return {**account, "submit": response.json()}


@pytest.mark.asyncio
async def test_submitting_a_complete_profile_activates_the_account(client, fake_storage):
    result = await onboard(
        client, f"one@{CAMPUS_DOMAIN}", visible_as=["woman"], interested_in=["man"], store=fake_storage
    )
    assert result["submit"]["status"] == "active"


@pytest.mark.asyncio
async def test_incomplete_profile_cannot_be_submitted(client, fake_storage):
    account = await register_and_verify(client, f"partial@{CAMPUS_DOMAIN}")
    response = await client.post("/api/profile/submit", headers=account["headers"])

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "app_error"


@pytest.mark.asyncio
async def test_unverified_account_cannot_be_submitted(client, fake_storage):
    from tests.conftest import register

    account = await register(client, f"unverified@{CAMPUS_DOMAIN}")
    await complete_profile(
        client, account["headers"], visible_as=["man"], interested_in=["woman"], store=fake_storage
    )
    response = await client.post("/api/profile/submit", headers=account["headers"])

    assert response.status_code == 400
    assert "Verify" in response.json()["error"]["message"]


@pytest.mark.asyncio
async def test_third_person_in_a_full_segment_is_waitlisted(client, fake_storage):
    for i in range(2):
        result = await onboard(
            client, f"w{i}@{CAMPUS_DOMAIN}", visible_as=["woman"], interested_in=["man"], store=fake_storage
        )
        assert result["submit"]["status"] == "active"

    third = await onboard(
        client, f"w2@{CAMPUS_DOMAIN}", visible_as=["woman"], interested_in=["man"], store=fake_storage
    )
    assert third["submit"]["status"] == "waitlisted"
    assert third["submit"]["waitlist"]["position"] == 1
    assert third["submit"]["waitlist"]["segment"] == "woman"


@pytest.mark.asyncio
async def test_caps_are_independent_per_segment(client, fake_storage):
    """A full women's segment must not block men from joining."""
    for i in range(2):
        await onboard(
            client, f"f{i}@{CAMPUS_DOMAIN}", visible_as=["woman"], interested_in=["man"], store=fake_storage
        )

    overflow = await onboard(
        client, f"f2@{CAMPUS_DOMAIN}", visible_as=["woman"], interested_in=["man"], store=fake_storage
    )
    assert overflow["submit"]["status"] == "waitlisted"

    man = await onboard(
        client, f"m0@{CAMPUS_DOMAIN}", visible_as=["man"], interested_in=["woman"], store=fake_storage
    )
    assert man["submit"]["status"] == "active"


@pytest.mark.asyncio
async def test_uncapped_segment_always_admits(client, fake_storage):
    """No cap row for nonbinary in the fixture, so it is uncapped."""
    for i in range(3):
        result = await onboard(
            client,
            f"nb{i}@{CAMPUS_DOMAIN}",
            visible_as=["nonbinary"],
            interested_in=["nonbinary"],
            store=fake_storage,
        )
        assert result["submit"]["status"] == "active"


@pytest.mark.asyncio
async def test_waitlist_position_counts_only_the_same_segment(client, fake_storage):
    for i in range(2):
        await onboard(
            client, f"a{i}@{CAMPUS_DOMAIN}", visible_as=["woman"], interested_in=["man"], store=fake_storage
        )
        await onboard(
            client, f"b{i}@{CAMPUS_DOMAIN}", visible_as=["man"], interested_in=["woman"], store=fake_storage
        )

    waiting_woman = await onboard(
        client, f"a9@{CAMPUS_DOMAIN}", visible_as=["woman"], interested_in=["man"], store=fake_storage
    )
    waiting_man = await onboard(
        client, f"b9@{CAMPUS_DOMAIN}", visible_as=["man"], interested_in=["woman"], store=fake_storage
    )

    # Each is first in their own queue, not second in a shared one.
    assert waiting_woman["submit"]["waitlist"]["position"] == 1
    assert waiting_man["submit"]["waitlist"]["position"] == 1


@pytest.mark.asyncio
async def test_me_reports_waitlist_position(client, fake_storage):
    for i in range(2):
        await onboard(
            client, f"c{i}@{CAMPUS_DOMAIN}", visible_as=["woman"], interested_in=["man"], store=fake_storage
        )
    waiting = await onboard(
        client, f"c9@{CAMPUS_DOMAIN}", visible_as=["woman"], interested_in=["man"], store=fake_storage
    )

    body = (await client.get("/api/auth/me", headers=waiting["headers"])).json()
    assert body["status"] == "waitlisted"
    assert body["waitlist"]["position"] == 1
    assert body["waitlist"]["invited"] is False


@pytest.mark.asyncio
async def test_submitting_twice_is_harmless(client, fake_storage):
    account = await register_and_verify(client, f"twice@{CAMPUS_DOMAIN}")
    await complete_profile(
        client, account["headers"], visible_as=["man"], interested_in=["woman"], store=fake_storage
    )

    first = await client.post("/api/profile/submit", headers=account["headers"])
    second = await client.post("/api/profile/submit", headers=account["headers"])

    assert first.json()["status"] == "active"
    assert second.json()["status"] == "active"


@pytest.mark.asyncio
async def test_a_freed_slot_is_offered_with_an_expiry(client, db_sessionmaker, scope, fake_storage):
    """invite_next must set a claim deadline, or a freed slot sits dead forever."""
    for i in range(2):
        await onboard(
            client, f"d{i}@{CAMPUS_DOMAIN}", visible_as=["woman"], interested_in=["man"], store=fake_storage
        )
    await onboard(
        client, f"d9@{CAMPUS_DOMAIN}", visible_as=["woman"], interested_in=["man"], store=fake_storage
    )

    async with db_sessionmaker() as db:
        # No room yet, so nobody is invited.
        assert await invite_next(db, scope.id, "woman") is None

        # Free a slot the way account deletion will.
        occupant = (
            (await db.execute(select(User).where(User.email == f"d0@{CAMPUS_DOMAIN}"))).scalars().first()
        )
        occupant.status = UserStatus.deleted
        occupant.deleted_at = utcnow()
        await db.commit()

        entry = await invite_next(db, scope.id, "woman")
        assert entry is not None
        assert entry.status == WaitlistStatus.invited
        assert entry.claim_expires_at is not None
        await db.commit()

        remaining = (
            (await db.execute(select(WaitlistEntry).where(WaitlistEntry.status == WaitlistStatus.waiting)))
            .scalars()
            .all()
        )
        assert remaining == []
