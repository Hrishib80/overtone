"""Email verification — the thing the campus gate actually rests on.

A domain check proves the person knows what the campus domain is. Anyone can
type `someone@example.com`. What makes an account mean "a student" is that a
link arrived in a mailbox at that domain and somebody opened it, so the tests
that matter here are the ones about whether the link is really sent and
whether the token ever escapes by another route.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from backend import jobs, mail
from backend.config import settings
from backend.database import EmailVerification, User
from tests.conftest import TEST_DOMAIN, register


async def _queued_emails(db_sessionmaker):
    async with db_sessionmaker() as db:
        return (await db.execute(select(jobs.Job).where(jobs.Job.kind == "send_email"))).scalars().all()


# ---------------------------------------------------------------------------
# The message
# ---------------------------------------------------------------------------


def test_the_link_points_at_the_configured_site_not_at_a_request_header():
    """A link built from a Host header is a link an attacker can aim somewhere
    else, and this one carries a credential."""
    message = mail.verification_message(to="a@b.edu", token="tok123", display_name="Ada")
    assert settings.public_web_url in message.text
    assert "tok123" in message.text
    assert message.to == "a@b.edu"


def test_the_message_says_what_happens_if_you_ignore_it():
    message = mail.verification_message(to="a@b.edu", token="t")
    assert "ignore" in message.text.lower()


def test_console_is_not_a_delivering_provider():
    """`delivers()` is what the API asks before deciding whether it is safe to
    hand the token back, so it must never be optimistic."""
    assert mail.delivers() is (settings.mail_provider == "smtp")


def test_production_refuses_to_start_without_a_real_provider(monkeypatch):
    """A console mailer in production means every signup mints a token that
    reaches nobody — the campus gate silently admits no one."""
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "mail_provider", "console")

    with pytest.raises(mail.MailNotConfigured, match="mail provider"):
        mail.check_configuration()


def test_smtp_without_a_host_is_refused(monkeypatch):
    monkeypatch.setattr(settings, "mail_provider", "smtp")
    monkeypatch.setattr(settings, "smtp_host", "")

    with pytest.raises(mail.MailNotConfigured, match="SMTP_HOST"):
        mail.check_configuration()


def test_a_working_smtp_configuration_passes(monkeypatch):
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "mail_provider", "smtp")
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")

    mail.check_configuration()


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_registering_queues_the_verification_email(client, db_sessionmaker):
    await register(client, f"newcomer@{TEST_DOMAIN}")

    queued = await _queued_emails(db_sessionmaker)
    assert len(queued) == 1
    assert queued[0].payload["to"] == f"newcomer@{TEST_DOMAIN}"
    assert "verify?token=" in queued[0].payload["text"]


@pytest.mark.asyncio
async def test_delivery_goes_through_the_queue_so_a_failure_is_retried(client, db_sessionmaker):
    """Registration must not fail because a mail provider is slow, and a link
    that failed to send once must not be lost."""
    await register(client, f"newcomer@{TEST_DOMAIN}")

    queued = await _queued_emails(db_sessionmaker)
    assert queued[0].max_attempts > 1


@pytest.mark.asyncio
async def test_the_token_is_withheld_once_mail_actually_delivers(client, monkeypatch):
    """The one that matters. Handing the token back undoes the entire point of
    sending it: the link is what proves the person reads mail at that address,
    and a token in the response proves only that they typed it."""
    monkeypatch.setattr(mail, "delivers", lambda: True)

    response = await client.post(
        "/api/auth/register",
        json={
            "email": f"newcomer@{TEST_DOMAIN}",
            "password": "a-strong-enough-password",
            "display_name": "Newcomer",
            "birthdate": "2003-05-17",
        },
    )
    assert response.status_code == 201
    assert "verification_token" not in response.json()


@pytest.mark.asyncio
async def test_the_token_is_returned_only_when_nothing_was_delivered(client):
    """With a console mailer nothing left the building, so the response is the
    only way through — which is exactly why production refuses that mode."""
    body = (await register(client, f"newcomer@{TEST_DOMAIN}")).copy()
    assert "verification_token" in body


# ---------------------------------------------------------------------------
# Resending
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resending_queues_another_link(client, db_sessionmaker):
    email = f"newcomer@{TEST_DOMAIN}"
    await register(client, email)

    response = await client.post("/api/auth/resend-verification", json={"email": email})
    assert response.status_code == 200
    assert len(await _queued_emails(db_sessionmaker)) == 2


@pytest.mark.asyncio
async def test_resending_invalidates_the_previous_link(client, db_sessionmaker):
    """Otherwise a link forwarded to somebody else keeps working after the
    owner has asked for a fresh one."""
    email = f"newcomer@{TEST_DOMAIN}"
    first = await register(client, email)

    await client.post("/api/auth/resend-verification", json={"email": email})

    stale = await client.post("/api/auth/verify-email", json={"token": first["verification_token"]})
    assert stale.status_code == 400


@pytest.mark.asyncio
async def test_resending_says_the_same_thing_for_an_unknown_address(client, db_sessionmaker):
    """On one campus, "does this person have an account" is a question about
    somebody's private life. The answer never varies."""
    known = f"newcomer@{TEST_DOMAIN}"
    await register(client, known)

    for address in (known, f"nobody@{TEST_DOMAIN}"):
        response = await client.post("/api/auth/resend-verification", json={"email": address})
        assert response.status_code == 200
        assert response.json()["status"] == "sent"

    # And no email was queued for the address that does not exist.
    queued = await _queued_emails(db_sessionmaker)
    assert [job.payload["to"] for job in queued] == [known, known]


@pytest.mark.asyncio
async def test_resending_to_a_verified_account_sends_nothing(client, db_sessionmaker):
    email = f"newcomer@{TEST_DOMAIN}"
    account = await register(client, email)
    await client.post("/api/auth/verify-email", json={"token": account["verification_token"]})

    before = len(await _queued_emails(db_sessionmaker))
    response = await client.post("/api/auth/resend-verification", json={"email": email})

    assert response.json()["status"] == "sent"
    assert len(await _queued_emails(db_sessionmaker)) == before


@pytest.mark.asyncio
async def test_resending_is_rate_limited_per_address(client):
    """Each one sends a real email to somebody who did not ask for it."""
    from backend.ratelimit import RESEND_VERIFICATION

    email = f"newcomer@{TEST_DOMAIN}"
    await register(client, email)

    last = None
    for _ in range(RESEND_VERIFICATION.allowance + 2):
        last = await client.post("/api/auth/resend-verification", json={"email": email})

    assert last.status_code == 429


# ---------------------------------------------------------------------------
# The handler
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_send_job_carries_the_whole_message(client, db_sessionmaker):
    """Built at enqueue time rather than rebuilt from a user id at send time:
    by then the account may be gone, and a link that cannot be regenerated is
    better than one that quietly stops being sent."""
    await register(client, f"newcomer@{TEST_DOMAIN}")
    job = (await _queued_emails(db_sessionmaker))[0]

    assert set(job.payload) == {"to", "subject", "text"}


@pytest.mark.asyncio
async def test_running_the_queue_delivers_and_clears(client, db_sessionmaker):
    from tests.conftest import run_jobs

    await register(client, f"newcomer@{TEST_DOMAIN}")
    assert await run_jobs(db_sessionmaker) >= 1

    async with db_sessionmaker() as db:
        remaining = (
            (
                await db.execute(
                    select(jobs.Job)
                    .where(jobs.Job.kind == "send_email")
                    .where(jobs.Job.status.in_([jobs.JobStatus.queued, jobs.JobStatus.running]))
                )
            )
            .scalars()
            .all()
        )
    assert remaining == []


@pytest.mark.asyncio
async def test_an_unverified_account_still_cannot_be_active(client, db_sessionmaker):
    """The gate itself: registering does not make somebody a member."""
    await register(client, f"newcomer@{TEST_DOMAIN}")

    async with db_sessionmaker() as db:
        user = (await db.execute(select(User).where(User.email == f"newcomer@{TEST_DOMAIN}"))).scalar_one()
        assert user.email_verified_at is None
        assert user.status == "pending_verification"

        outstanding = (
            (await db.execute(select(EmailVerification).where(EmailVerification.user_id == user.id)))
            .scalars()
            .all()
        )
    assert len(outstanding) == 1


# ---------------------------------------------------------------------------
# How the message goes out on the wire
# ---------------------------------------------------------------------------


def _rendered(display_name):
    import email as email_mod
    from email import policy as email_policy

    raw = mail._build(
        mail.verification_message(to="s@example.com", token=TOKEN, display_name=display_name)
    ).as_string()
    parsed = email_mod.message_from_string(raw, policy=email_policy.default)
    return raw, parsed


TOKEN = "RJD-qjpzlBCeAS5RyVPEQ3-pZCoFtQH0r9XawaP_ERQ"


def test_the_link_survives_a_name_in_any_script():
    """The case this is really for. A pure-ASCII body goes out untouched; one
    non-ASCII character anywhere flips the whole body to an encoded form, and
    on this campus a Telugu or accented name is the common case rather than
    the exotic one."""
    for name in ("Ada", "José", "హర్ష"):
        _raw, parsed = _rendered(name)
        assert TOKEN in parsed.get_content(), name


def test_an_ascii_message_stays_readable_on_the_wire():
    """Worth keeping: a raw message somebody can read is a signup failure
    somebody can debug."""
    raw, parsed = _rendered("Ada")
    assert parsed.get("Content-Transfer-Encoding") == "7bit"
    assert TOKEN in raw


def test_a_non_ascii_message_avoids_quoted_printable():
    """Quoted-printable is the one encoding that puts a soft break inside a
    long URL. Reversible, but it means the raw source shows a split link."""
    _raw, parsed = _rendered("హర్ష")
    assert parsed.get("Content-Transfer-Encoding") == "base64"


def test_no_body_line_exceeds_the_smtp_limit():
    for name in ("Ada", "హర్ష"):
        raw, _parsed = _rendered(name)
        assert max(len(line) for line in raw.splitlines()) <= mail.MAX_LINE
