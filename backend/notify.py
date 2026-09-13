"""Telling somebody something happened, when they are not looking.

Until now nothing reached anyone outside the app: a person spent seven
comparisons unlocking somebody, spent their one opening message, and the
recipient found out whenever they next happened to open the inbox. For a loop
that turns on somebody answering, that is the hole that empties it.

**Two emails exist, and only two.** A request arrived, and a request was
answered. Those are the moments where somebody is waiting on somebody else;
everything else in the product can be discovered by opening it. Notably there
is *no* email per message in an open conversation — the socket already
delivers those to anyone with the thread open, and mailing the rest would
train people to filter us, which costs the two that matter.

**The email never carries the message.** Somebody wrote one careful thing to
one person; it is not ours to copy into a mailbox that may be read at a desk,
on a shared laptop, or over a shoulder. The sender's first name, and a link.
That is also why there is no photo in it.

**Nothing is sent to somebody who is already here.** If they were active in
the last few minutes they will see it in the app, and an email that arrives
while you are looking at the thing it describes is the kind that gets the
sender's address blocked.

**Unsubscribing works from the email, without signing in.** A preference
buried behind a login is not really a preference — the people most likely to
want out are the least likely to still have an account they can get into. The
link carries an HMAC over the user id, and it never expires: an unsubscribe
link that has gone stale is a complaint.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from backend import jobs, mail
from backend.config import settings
from backend.database import User, utcnow
from backend.logging_config import get_logger

log = get_logger(__name__)

# How recently somebody has to have been in the app for us to stay quiet. Long
# enough to cover reading a thread and thinking about a reply; short enough
# that a request arriving after lunch still reaches them.
QUIET_AFTER = timedelta(minutes=15)


def unsubscribe_token(user_id: str) -> str:
    """Proof this link came from us, for a person who is not signed in."""
    return hmac.new(
        settings.jwt_secret_key.encode(), f"unsubscribe:{user_id}".encode(), hashlib.sha256
    ).hexdigest()


def valid_unsubscribe(user_id: str, token: str) -> bool:
    return hmac.compare_digest(unsubscribe_token(user_id), token or "")


def unsubscribe_link(user: User) -> str:
    base = settings.public_web_url.rstrip("/")
    return f"{base}/unsubscribe?u={user.id}&t={unsubscribe_token(user.id)}"


def _footer(user: User) -> str:
    return (
        "\n\n—\n"
        "You're getting this because someone reached out to you on Overtone.\n"
        f"Stop these emails: {unsubscribe_link(user)}\n"
    )


def _should_send(recipient: User) -> tuple[bool, str]:
    """Whether to send at all, and why not when not — the log line is how a
    'why didn't I get an email' question gets answered."""
    if not mail.delivers():
        return False, "mail_not_configured"
    if recipient.deleted_at is not None:
        return False, "deleted"
    if not recipient.email_notifications:
        return False, "unsubscribed"
    if recipient.email_verified_at is None:
        # Never write to an address nobody has proved they can read. It might
        # be somebody else's.
        return False, "unverified"
    if recipient.last_active_at and utcnow() - recipient.last_active_at < QUIET_AFTER:
        return False, "recently_active"
    return True, "ok"


async def _queue(db: AsyncSession, recipient: User, message: mail.Message, kind: str) -> bool:
    """Through the job queue, like every other send: a provider that is down
    for a minute must cost a retry rather than the notification."""
    send, reason = _should_send(recipient)
    if not send:
        log.info("notification_skipped", kind=kind, user_id=recipient.id, reason=reason)
        return False

    await jobs.enqueue(
        db,
        jobs.JobKind.send_email,
        {"to": message.to, "subject": message.subject, "text": message.text},
        subject_id=recipient.id,
    )
    log.info("notification_queued", kind=kind, user_id=recipient.id)
    return True


def _first_name(user: User) -> str:
    return (user.display_name or "Someone").split()[0]


async def request_received(db: AsyncSession, *, recipient: User, sender: User) -> bool:
    """Somebody spent their one opening message on this person."""
    name = _first_name(sender)
    return await _queue(
        db,
        recipient,
        mail.Message(
            to=recipient.email,
            subject=f"{name} wrote to you on Overtone",
            text=(
                f"Hi {_first_name(recipient)},\n\n"
                f"{name} kept choosing you, and has written to you.\n\n"
                "We don't put the message in here — it's waiting for you:\n\n"
                f"{settings.public_web_url.rstrip('/')}/inbox\n\n"
                "Replying is what opens the conversation. Not replying is an "
                "answer too, and they aren't told either way." + _footer(recipient)
            ),
        ),
        "request_received",
    )


async def request_answered(db: AsyncSession, *, recipient: User, replier: User) -> bool:
    """The other half: the person who reached out, told that it landed."""
    name = _first_name(replier)
    return await _queue(
        db,
        recipient,
        mail.Message(
            to=recipient.email,
            subject=f"{name} replied",
            text=(
                f"Hi {_first_name(recipient)},\n\n"
                f"{name} answered you. The conversation is open — you can both "
                "write freely now.\n\n"
                f"{settings.public_web_url.rstrip('/')}/inbox" + _footer(recipient)
            ),
        ),
        "request_answered",
    )
