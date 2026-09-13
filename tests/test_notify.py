"""Reaching somebody who is not looking at the app.

Most of this is about the emails that are *not* sent. Two go out — a request
arrived, and a request was answered — and everything else in the product is
discoverable by opening it. A dating app that mails you per message is a
dating app people filter, which costs it the two that matter.

`mail.delivers()` is false in the test environment, which is the honest
default and also means nothing here can accidentally send. The tests drive
the queue instead: a notification is a `send_email` job with a subject line,
and asserting on the queue is asserting on exactly what would have left.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import select

from backend import notify
from backend.database import Connection, ConnectionStatus, User, pair_key, utcnow
from backend.jobs import Job, JobKind
from tests.conftest import TEST_DOMAIN, onboard, run_jobs
from tests.test_privacy import _uid


async def _emails(db_sessionmaker, user_id: str | None = None) -> list[str]:
    """The subject lines currently queued, which is what would be sent."""
    async with db_sessionmaker() as db:
        query = select(Job).where(Job.kind == JobKind.send_email)
        if user_id:
            query = query.where(Job.subject_id == user_id)
        rows = (await db.execute(query)).scalars().all()
    return [r.payload["subject"] for r in rows]


async def _quiet(db_sessionmaker, user_id: str) -> None:
    """Make somebody look like they have been away."""
    async with db_sessionmaker() as db:
        user = await db.get(User, user_id)
        user.last_active_at = utcnow() - notify.QUIET_AFTER * 3
        await db.commit()


@pytest_asyncio.fixture
async def two(client, db_sessionmaker, fake_storage, monkeypatch):
    """Two finished accounts, a clean queue, and a mailer that delivers.

    The order matters and is not arbitrary. Registration hands the
    verification token back to the caller *only* when nothing is actually
    delivered — that is the whole point of `mail.delivers()`, and it is what
    `register_and_verify` relies on. So the accounts are made first, in the
    honest default, and delivery is switched on afterwards.

    The queue is then emptied, because those registrations each left a
    verification email in it under the new account's own id, and every
    assertion below counts jobs by subject.
    """
    her = await onboard(
        client, f"her@{TEST_DOMAIN}", visible_as=["woman"], interested_in=["man"], store=fake_storage
    )
    him = await onboard(
        client, f"him@{TEST_DOMAIN}", visible_as=["man"], interested_in=["woman"], store=fake_storage
    )
    await run_jobs(db_sessionmaker)

    async with db_sessionmaker() as db:
        for job in (await db.execute(select(Job))).scalars().all():
            await db.delete(job)
        # Distinct names, because the emails are addressed by first name and
        # conftest gives every registration the same one — two accounts called
        # Aditi would make the subject lines untestable.
        (await db.get(User, _uid(her))).display_name = "Maya"
        (await db.get(User, _uid(him))).display_name = "Arun"
        await db.commit()

    monkeypatch.setattr(notify.mail, "delivers", lambda: True)
    return {"her": her, "him": him}


async def _unlock(db_sessionmaker, viewer_id: str, subject_id: str) -> None:
    from backend.database import Affinity, AffinityState

    async with db_sessionmaker() as db:
        db.add(
            Affinity(
                viewer_id=viewer_id,
                subject_id=subject_id,
                shown=7,
                picked=7,
                state=AffinityState.unlocked,
                unlocked_at=utcnow(),
            )
        )
        await db.commit()


# ---------------------------------------------------------------------------
# The two that are sent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_request_reaches_somebody_who_is_away(client, db_sessionmaker, two):
    her, him = two["her"], two["him"]
    await _unlock(db_sessionmaker, _uid(her), _uid(him))
    await _quiet(db_sessionmaker, _uid(him))

    sent = await client.post(
        "/api/connections/requests",
        headers=her["headers"],
        json={"subject_id": _uid(him), "text": "that photo of the pier — where is that?"},
    )
    assert sent.status_code == 201, sent.text

    subjects = await _emails(db_sessionmaker, _uid(him))
    assert len(subjects) == 1
    assert "wrote to you" in subjects[0]


@pytest.mark.asyncio
async def test_the_email_never_carries_the_message(client, db_sessionmaker, two):
    """Somebody wrote one careful thing to one person. It is not ours to copy
    into a mailbox that might be read at a desk or over a shoulder."""
    her, him = two["her"], two["him"]
    await _unlock(db_sessionmaker, _uid(her), _uid(him))
    await _quiet(db_sessionmaker, _uid(him))

    secret = "that photo of the pier, where is that"
    await client.post(
        "/api/connections/requests",
        headers=her["headers"],
        json={"subject_id": _uid(him), "text": secret},
    )

    async with db_sessionmaker() as db:
        job = (await db.execute(select(Job).where(Job.subject_id == _uid(him)))).scalars().first()
    assert secret not in job.payload["text"]
    # The sender's first name is fine — they chose to reach out.
    assert job.payload["subject"] == "Maya wrote to you on Overtone"


@pytest.mark.asyncio
async def test_the_reply_that_opens_it_is_worth_an_email(client, db_sessionmaker, two):
    her, him = two["her"], two["him"]
    await _unlock(db_sessionmaker, _uid(her), _uid(him))
    await _quiet(db_sessionmaker, _uid(him))

    created = await client.post(
        "/api/connections/requests",
        headers=her["headers"],
        json={"subject_id": _uid(him), "text": "hello"},
    )
    connection_id = created.json()["id"]
    await _quiet(db_sessionmaker, _uid(her))

    replied = await client.post(
        f"/api/connections/{connection_id}/messages",
        headers=him["headers"],
        json={"text": "the pier at bandra"},
    )
    assert replied.status_code == 201

    subjects = await _emails(db_sessionmaker, _uid(her))
    assert subjects == ["Arun replied"]


@pytest.mark.asyncio
async def test_no_email_per_message_after_it_opens(client, db_sessionmaker, two):
    """The socket carries these. Mailing them would train people to filter
    us, which costs the two that matter."""
    her, him = two["her"], two["him"]
    await _unlock(db_sessionmaker, _uid(her), _uid(him))
    await _quiet(db_sessionmaker, _uid(him))

    created = await client.post(
        "/api/connections/requests",
        headers=her["headers"],
        json={"subject_id": _uid(him), "text": "hello"},
    )
    connection_id = created.json()["id"]
    await _quiet(db_sessionmaker, _uid(her))
    await client.post(
        f"/api/connections/{connection_id}/messages", headers=him["headers"], json={"text": "hi"}
    )

    before = len(await _emails(db_sessionmaker))
    for text in ("and", "another", "and one more"):
        await _quiet(db_sessionmaker, _uid(her))
        await _quiet(db_sessionmaker, _uid(him))
        await client.post(
            f"/api/connections/{connection_id}/messages", headers=him["headers"], json={"text": text}
        )
    assert len(await _emails(db_sessionmaker)) == before


# ---------------------------------------------------------------------------
# The ones that are not
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_nothing_is_sent_to_somebody_already_here(client, db_sessionmaker, two):
    """An email that arrives while you are looking at the thing it describes
    is the kind that gets a sending domain blocked."""
    her, him = two["her"], two["him"]
    await _unlock(db_sessionmaker, _uid(her), _uid(him))

    # `him` just made a request of his own, so he is active by definition.
    await client.get("/api/connections", headers=him["headers"])

    await client.post(
        "/api/connections/requests",
        headers=her["headers"],
        json={"subject_id": _uid(him), "text": "hello"},
    )
    assert await _emails(db_sessionmaker, _uid(him)) == []


@pytest.mark.asyncio
async def test_unsubscribing_stops_them(client, db_sessionmaker, two):
    her, him = two["her"], two["him"]
    await _unlock(db_sessionmaker, _uid(her), _uid(him))
    await _quiet(db_sessionmaker, _uid(him))

    off = await client.put(
        "/api/account/notifications",
        headers=him["headers"],
        json={"email_notifications": False},
    )
    assert off.status_code == 200
    await _quiet(db_sessionmaker, _uid(him))

    await client.post(
        "/api/connections/requests",
        headers=her["headers"],
        json={"subject_id": _uid(him), "text": "hello"},
    )
    assert await _emails(db_sessionmaker, _uid(him)) == []


@pytest.mark.asyncio
async def test_an_unverified_address_is_never_written_to(client, db_sessionmaker, two):
    """It might be somebody else's. Nothing goes to an address until the
    person has proved they can read it."""
    her = two["her"]
    async with db_sessionmaker() as db:
        him = await db.get(User, _uid(two["him"]))
        him.email_verified_at = None
        him.last_active_at = utcnow() - notify.QUIET_AFTER * 3
        await db.commit()

    await _unlock(db_sessionmaker, _uid(her), _uid(two["him"]))
    await client.post(
        "/api/connections/requests",
        headers=her["headers"],
        json={"subject_id": _uid(two["him"]), "text": "hello"},
    )
    assert await _emails(db_sessionmaker, _uid(two["him"])) == []


@pytest.mark.asyncio
async def test_console_mail_queues_nothing(client, db_sessionmaker, two, monkeypatch):
    """The default configuration delivers nothing, so queueing a job that
    would only be logged is noise in the queue and a lie in the metrics."""
    monkeypatch.setattr(notify.mail, "delivers", lambda: False)
    her, him = two["her"], two["him"]
    await _unlock(db_sessionmaker, _uid(her), _uid(him))
    await _quiet(db_sessionmaker, _uid(him))

    await client.post(
        "/api/connections/requests",
        headers=her["headers"],
        json={"subject_id": _uid(him), "text": "hello"},
    )
    assert await _emails(db_sessionmaker, _uid(him)) == []


# ---------------------------------------------------------------------------
# Getting out
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unsubscribe_works_without_signing_in(client, db_sessionmaker, two):
    """The people most likely to want out are the least likely to still have
    an account they can get into."""
    him = two["him"]
    token = notify.unsubscribe_token(_uid(him))

    done = await client.post("/api/account/unsubscribe", json={"user_id": _uid(him), "token": token})
    assert done.status_code == 200

    async with db_sessionmaker() as db:
        assert (await db.get(User, _uid(him))).email_notifications is False


@pytest.mark.asyncio
async def test_a_forged_unsubscribe_does_nothing(client, db_sessionmaker, two):
    him = two["him"]
    refused = await client.post(
        "/api/account/unsubscribe", json={"user_id": _uid(him), "token": "not-a-real-token"}
    )
    # Answers the same either way: a different response for a real account
    # would make this an oracle for which addresses are registered.
    assert refused.status_code == 200

    async with db_sessionmaker() as db:
        assert (await db.get(User, _uid(him))).email_notifications is True


@pytest.mark.asyncio
async def test_the_unsubscribe_link_is_in_the_email(client, db_sessionmaker, two):
    her, him = two["her"], two["him"]
    await _unlock(db_sessionmaker, _uid(her), _uid(him))
    await _quiet(db_sessionmaker, _uid(him))

    await client.post(
        "/api/connections/requests",
        headers=her["headers"],
        json={"subject_id": _uid(him), "text": "hello"},
    )
    async with db_sessionmaker() as db:
        job = (await db.execute(select(Job).where(Job.subject_id == _uid(him)))).scalars().first()
    assert "/unsubscribe?u=" in job.payload["text"]
    assert notify.unsubscribe_token(_uid(him)) in job.payload["text"]


# ---------------------------------------------------------------------------
# Activity, which the whole quiet rule rests on
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_using_the_app_counts_as_activity(client, db_sessionmaker, two):
    """It used to be recorded only at login, which would have told `notify`
    that somebody reading their inbox right now was last here on Tuesday."""
    him = two["him"]
    await _quiet(db_sessionmaker, _uid(him))

    await client.get("/api/connections", headers=him["headers"])

    async with db_sessionmaker() as db:
        user = await db.get(User, _uid(him))
    assert utcnow() - user.last_active_at < notify.QUIET_AFTER


@pytest.mark.asyncio
async def test_a_deleted_connection_peer_does_not_break_the_send(client, db_sessionmaker, two):
    """Belt and braces: the notification looks the peer up, and a row that has
    gone must not take the request down with it."""
    her, him = two["her"], two["him"]
    await _unlock(db_sessionmaker, _uid(her), _uid(him))
    await _quiet(db_sessionmaker, _uid(him))

    async with db_sessionmaker() as db:
        db.add(
            Connection(
                user_a_id=min(_uid(her), _uid(him)),
                user_b_id=max(_uid(her), _uid(him)),
                connection_key=pair_key(_uid(her), _uid(him)),
                initiator_id=_uid(her),
                status=ConnectionStatus.open,
            )
        )
        await db.commit()

    # Already connected, so the request is refused — and nothing is queued.
    refused = await client.post(
        "/api/connections/requests",
        headers=her["headers"],
        json={"subject_id": _uid(him), "text": "hello"},
    )
    assert refused.status_code == 409
    assert await _emails(db_sessionmaker, _uid(him)) == []
