"""Sending email.

This is the load-bearing part of the campus gate, not a nicety. The whole
product rests on one identity anchor — that an account belongs to somebody
with a mailbox at that university — and a domain check alone proves only that
the person knows what the domain is. Anyone can type `someone@campus.edu`. The
verification link is what turns "typed a campus address" into "reads mail at a
campus address", and until it is genuinely delivered the gate is decoration.

Two providers:

* `console` — logs the link and delivers nothing. The only honest thing to do
  in that mode is hand the token back to the caller, which is what the API
  does, and that is precisely why production refuses to start in it.
* `smtp` — real delivery. SMTP rather than one vendor's HTTP API because it is
  the one interface Amazon SES, Postmark, Resend, Mailgun, Google Workspace
  and a university's own relay all speak, and a campus launch is exactly the
  situation where the relay you end up allowed to use is not the one you
  planned for.

Delivery runs through the job queue, so a provider that is briefly down costs
a retry rather than an account that can never be verified.
"""

from __future__ import annotations

import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage

import anyio

from backend.config import settings
from backend.logging_config import get_logger

log = get_logger(__name__)


class MailNotConfigured(RuntimeError):
    """Raised at startup, not at send time. A misconfigured mailer discovered
    by the first person trying to sign up is a misconfigured mailer discovered
    too late."""


@dataclass(frozen=True, slots=True)
class Message:
    to: str
    subject: str
    text: str


def delivers() -> bool:
    """Whether mail actually leaves the building in this configuration."""
    return settings.mail_provider == "smtp"


def check_configuration() -> None:
    """Called from app startup. In production a console mailer is a hard stop:
    it would mean every signup produces a token that reaches nobody, and the
    campus gate silently admits nobody at all."""
    if settings.mail_provider not in {"console", "smtp"}:
        raise MailNotConfigured(f"Unknown mail provider {settings.mail_provider!r}.")

    if settings.mail_provider == "smtp" and not settings.smtp_host:
        raise MailNotConfigured("MAIL_PROVIDER=smtp needs SMTP_HOST.")

    if settings.is_production and not delivers():
        raise MailNotConfigured(
            "Production needs a real mail provider: nobody can verify an address "
            "that never receives a link. Set MAIL_PROVIDER=smtp."
        )


def _build(message: Message) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = settings.mail_from
    msg["To"] = message.to
    msg["Subject"] = message.subject
    msg.set_content(message.text)
    return msg


def _send_smtp(message: Message) -> None:
    """Blocking, by design — called through a worker thread below.

    STARTTLS on the submission port and implicit TLS on 465, because those are
    the two shapes every provider offers and guessing wrong fails in a way that
    looks like a network problem.
    """
    msg = _build(message)
    context = ssl.create_default_context()

    if settings.smtp_use_tls:
        with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, context=context, timeout=30) as server:
            if settings.smtp_username:
                server.login(settings.smtp_username, settings.smtp_password)
            server.send_message(msg)
        return

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as server:
        if settings.smtp_use_starttls:
            server.starttls(context=context)
        if settings.smtp_username:
            server.login(settings.smtp_username, settings.smtp_password)
        server.send_message(msg)


async def send(message: Message) -> None:
    """Deliver one message, or raise so the job queue retries it."""
    if settings.mail_provider == "console":
        # Deliberately the whole body: in this mode the log is the inbox.
        log.info("mail_console", to=message.to, subject=message.subject, body=message.text)
        return

    await anyio.to_thread.run_sync(_send_smtp, message)
    log.info("mail_sent", to=message.to, subject=message.subject)


def verification_message(*, to: str, token: str, display_name: str | None = None) -> Message:
    link = f"{settings.public_web_url.rstrip('/')}/verify?token={token}"
    hello = f"Hi {display_name}," if display_name else "Hi,"
    return Message(
        to=to,
        subject="Confirm your Overtone address",
        text=(
            f"{hello}\n\n"
            "Confirm this is your campus address to finish joining Overtone:\n\n"
            f"{link}\n\n"
            "The link works once and expires in 24 hours.\n\n"
            "If you didn't ask for this, you can ignore it — nothing happens "
            "until the link is opened.\n"
        ),
    )
