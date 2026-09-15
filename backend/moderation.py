"""The queue a human actually opens.

Reporting without review is a promise the product does not keep: the rows
accumulate, nobody reads them, and the person who reported learns that
reporting does nothing. This module is the other half.

Three decisions shape it.

**The unit of review is the person, not the report.** Four reports about one
account are one question — is this person doing the thing? — and answering it
four times is both tedious and wrong, because the fourth answer would be given
with the first three already decided. So a reviewer acts on a subject, and
every open report about them resolves in that act.

**Reviewers are made from the command line.** There is no endpoint that grants
`is_reviewer`, deliberately: the account that can suspend other people must
not be reachable from a surface an attacker already holds a session on.

**The queue is ordered by weight of evidence, not by arrival.** Four reports
from four unrelated people is the pattern worth seeing first; one report that
happens to be older is not. Age breaks ties, so nothing sits forever.
"""

from __future__ import annotations

import enum
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend import jobs
from backend.auth import current_user
from backend.database import (
    ChatMessage,
    Connection,
    MediaAsset,
    MediaKind,
    MediaStatus,
    Profile,
    PromptLibrary,
    PromptResponse,
    Report,
    ReportStatus,
    User,
    UserStatus,
    get_db,
    utcnow,
)
from backend.errors import AppError, NotFound
from backend.logging_config import get_logger

log = get_logger(__name__)
router = APIRouter(prefix="/api/moderation", tags=["moderation"])

MAX_REVIEWER_NOTE = 2000


class Action(enum.StrEnum):
    dismiss = "dismiss"  # nothing here; the reports close as dismissed
    remove_photo = "remove_photo"  # one photo goes, the account stays
    approve_photo = "approve_photo"  # a held photo is fine; let it through
    suspend = "suspend"  # the account stops appearing, and cannot sign in


async def require_reviewer(user: User = Depends(current_user)) -> User:
    """Refuse everyone else, and say nothing about what is behind the door.

    A 404 rather than a 403 for a non-reviewer: confirming that a moderation
    surface exists at this path is information, and it is not information
    anybody outside the team needs.
    """
    if not user.is_reviewer:
        raise NotFound("Not found.")
    return user


# ---------------------------------------------------------------------------
# Reading the queue
# ---------------------------------------------------------------------------


async def _subject_summary(
    db: AsyncSession,
    subject: User,
    reports: list[Report],
    held: list[MediaAsset] | None = None,
) -> dict[str, Any]:
    held = held or []
    waiting = [r.created_at for r in reports if r.status == ReportStatus.open]
    waiting += [p.created_at for p in held]
    return {
        "user": {
            "id": subject.id,
            "display_name": subject.display_name,
            "username": subject.username,
            "status": subject.status,
            "age": subject.age,
            "joined_at": subject.created_at.isoformat() if subject.created_at else None,
        },
        "open_reports": sum(1 for r in reports if r.status == ReportStatus.open),
        "total_reports": len(reports),
        "held_photos": len(held),
        # The distinct reasons, because "three people said harassment" and
        # "one each of three different things" are not the same signal.
        "reasons": sorted({r.reason for r in reports if r.status == ReportStatus.open}),
        # What the machine said, kept apart from what people said — a reviewer
        # weighs those two differently and should not have to untangle them.
        "held_reasons": sorted({p.gate_reason for p in held if p.gate_reason}),
        "oldest_open": min((t.isoformat() for t in waiting if t), default=None),
    }


@router.get("/queue")
async def read_queue(
    include_closed: bool = Query(default=False),
    _: User = Depends(require_reviewer),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Everyone with something outstanding, heaviest first.

    Two sources feed it: people somebody reported, and photos the automatic
    screening was not sure about. One queue rather than two, because it is one
    job — a reviewer opening a subject wants everything known about them on
    the screen, and a held photo on an account that also has three reports is
    not a separate errand.
    """
    wanted = list(ReportStatus) if include_closed else [ReportStatus.open]
    rows = (
        (await db.execute(select(Report).where(Report.status.in_(wanted)).order_by(Report.created_at.desc())))
        .scalars()
        .all()
    )

    by_subject: dict[str, list[Report]] = {}
    for row in rows:
        by_subject.setdefault(row.subject_id, []).append(row)

    waiting_photos = (
        (
            await db.execute(
                select(MediaAsset)
                .where(MediaAsset.status == MediaStatus.held)
                .order_by(MediaAsset.created_at)
            )
        )
        .scalars()
        .all()
    )
    held_by_subject: dict[str, list[MediaAsset]] = {}
    for photo in waiting_photos:
        held_by_subject.setdefault(photo.user_id, []).append(photo)

    out = []
    for subject_id in dict.fromkeys([*by_subject, *held_by_subject]):
        subject = await db.get(User, subject_id)
        if subject is None:
            # The account deleted itself while its reports were open. Nothing
            # left to review, and the rows go with it.
            continue
        out.append(
            await _subject_summary(
                db,
                subject,
                by_subject.get(subject_id, []),
                held_by_subject.get(subject_id, []),
            )
        )

    # A held photo is one piece of work and so is a report, so they add. An
    # account with three reports outranking one held photo is the order a
    # reviewer would have chosen anyway.
    out.sort(key=lambda s: (-(s["open_reports"] + s["held_photos"]), s["oldest_open"] or ""))
    return {"subjects": out}


async def staff_profile(db: AsyncSession, subject: User) -> dict[str, Any]:
    """A person as staff see them: every photo that reached storage (held and
    rejected included), every answer with its question, and the basics.

    Shared by the review queue and the admin portal, so a reviewer judging a
    report and an admin judging an application are looking at the same thing.
    """
    user_id = subject.id
    photos = (
        (
            await db.execute(
                select(MediaAsset)
                .where(MediaAsset.user_id == user_id)
                .where(MediaAsset.kind == MediaKind.photo)
                .where(MediaAsset.status != MediaStatus.pending_upload)
                .order_by(MediaAsset.display_order, MediaAsset.created_at)
            )
        )
        .scalars()
        .all()
    )

    # Joined to the library so the reviewer reads the question, not its slug.
    # "greatest_strength" sitting above somebody's answer is the kind of detail
    # that makes a queue feel like a database rather than a person's profile.
    answers = (
        await db.execute(
            select(PromptResponse, PromptLibrary)
            .join(PromptLibrary, PromptLibrary.id == PromptResponse.prompt_id)
            .where(PromptResponse.user_id == user_id)
            .order_by(PromptResponse.slot)
        )
    ).all()

    profile = await db.get(Profile, user_id)

    return {
        "user": {
            "id": subject.id,
            "display_name": subject.display_name,
            "username": subject.username,
            "status": subject.status,
            "age": subject.age,
            "joined_at": subject.created_at.isoformat() if subject.created_at else None,
            "pronouns": getattr(profile, "pronouns", None),
            "location": getattr(profile, "location", None),
        },
        "photos": [
            {
                "id": photo.id,
                # The rejected ones too: a reviewer judging a photo has to
                # be able to see the photo, and rejected is where the one
                # that was reported often already sits.
                "url": photo.public_url,
                "status": photo.status,
                "gate_reason": photo.gate_reason,
                # What the machine actually saw. A verdict with no working
                # shown is not something a person can second-guess, which is
                # the only reason they were asked.
                "screen_detail": photo.screen_detail,
                "review_approved": photo.review_approved,
                "is_primary": photo.is_primary,
            }
            for photo in photos
        ],
        "prompts": [
            {
                "id": answer.id,
                "prompt_id": answer.prompt_id,
                "text": prompt.text,
                "slot": answer.slot,
                "kind": answer.kind,
                "body": answer.body,
            }
            for answer, prompt in answers
        ],
    }


@router.get("/subjects/{user_id}")
async def read_subject(
    user_id: str,
    _: User = Depends(require_reviewer),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Everything needed to decide, on one screen.

    A reviewer who has to open four tabs to see what was reported makes worse
    decisions than one who does not, so the photos, the answers, the reports
    and the conversation they came from are all resolved here.
    """
    subject = await db.get(User, user_id)
    if subject is None:
        raise NotFound("That account no longer exists.")

    reports = (
        (
            await db.execute(
                select(Report).where(Report.subject_id == user_id).order_by(Report.created_at.desc())
            )
        )
        .scalars()
        .all()
    )

    rendered_reports = []
    for report in reports:
        reporter = await db.get(User, report.reporter_id) if report.reporter_id else None
        rendered_reports.append(
            {
                "id": report.id,
                "reason": report.reason,
                "note": report.note,
                "status": report.status,
                "created_at": report.created_at.isoformat() if report.created_at else None,
                # A name, not just an id — a reviewer weighing four reports
                # needs to see whether they are four people or one person
                # four times.
                "reporter": (
                    {"id": reporter.id, "display_name": reporter.display_name} if reporter else None
                ),
                "about_photo": report.subject_media_id,
                "about_prompt": report.subject_prompt_id,
                "reviewer_note": report.reviewer_note,
                "conversation": await _conversation(db, report),
            }
        )

    return {
        **await staff_profile(db, subject),
        "reports": rendered_reports,
    }


async def _conversation(db: AsyncSession, report: Report) -> list[dict[str, Any]] | None:
    """The messages a report points at, if it points at any.

    `context` carries a connection id when the report came from a thread. It
    is read back rather than trusted: a reviewer seeing an unrelated
    conversation because somebody put a different id in the field would be
    worse than seeing none.
    """
    if not report.context:
        return None
    connection = await db.get(Connection, report.context)
    if connection is None:
        return None
    if report.subject_id not in (connection.user_a_id, connection.user_b_id):
        return None

    messages = (
        (
            await db.execute(
                select(ChatMessage)
                .where(ChatMessage.connection_id == connection.id)
                .order_by(ChatMessage.created_at)
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "from_subject": message.from_user_id == report.subject_id,
            "text": message.message_text,
            "at": message.created_at.isoformat() if message.created_at else None,
        }
        for message in messages
    ]


# ---------------------------------------------------------------------------
# Deciding
# ---------------------------------------------------------------------------


class Decision(BaseModel):
    model_config = {"extra": "forbid"}

    action: Action
    note: str = Field(min_length=1, max_length=MAX_REVIEWER_NOTE)
    # Required by remove_photo, meaningless to the others.
    media_id: str | None = None


@router.post("/subjects/{user_id}/decide")
async def decide(
    user_id: str,
    body: Decision,
    reviewer: User = Depends(require_reviewer),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """One decision, closing every open report about this person.

    The note is required, in all three cases. A dismissal with no reason
    recorded is indistinguishable from nobody having looked, which is the
    exact thing this queue exists to stop.
    """
    subject = await db.get(User, user_id)
    if subject is None:
        raise NotFound("That account no longer exists.")
    if subject.id == reviewer.id:
        raise AppError("You can't review yourself.")

    open_reports = (
        (
            await db.execute(
                select(Report).where(Report.subject_id == user_id).where(Report.status == ReportStatus.open)
            )
        )
        .scalars()
        .all()
    )
    held_photos = (
        (
            await db.execute(
                select(MediaAsset)
                .where(MediaAsset.user_id == user_id)
                .where(MediaAsset.status == MediaStatus.held)
            )
        )
        .scalars()
        .all()
    )
    # A held photo is work outstanding even with no report behind it — that is
    # the whole point of the machine putting things in this queue.
    if not open_reports and not held_photos:
        raise AppError("There is nothing open about this account.")

    outcome: dict[str, Any] = {"action": body.action}

    if body.action in (Action.remove_photo, Action.approve_photo):
        if not body.media_id:
            raise AppError("Say which photo.")
        photo = await db.get(MediaAsset, body.media_id)
        if photo is None or photo.user_id != user_id or photo.kind != MediaKind.photo:
            raise NotFound("That photo isn't on this account.")

        if body.action == Action.remove_photo:
            photo.status = MediaStatus.rejected
            photo.gate_reason = "removed_by_review"
            await _reelect_primary(db, user_id)
            outcome["removed_photo"] = photo.id
        else:
            if photo.status != MediaStatus.held:
                raise AppError("That photo isn't waiting on anyone.")
            # `review_approved` is what stops the next automatic pass putting
            # it straight back in the queue. The job then does the rest —
            # electing a primary and embedding — rather than this handler
            # half-doing the worker's work with none of the model available.
            photo.review_approved = True
            photo.status = MediaStatus.uploaded
            photo.gate_reason = None
            await jobs.enqueue(
                db,
                jobs.JobKind.process_photo,
                {"asset_id": photo.id},
                subject_id=user_id,
            )
            outcome["approved_photo"] = photo.id

    elif body.action == Action.suspend:
        subject.status = UserStatus.suspended
        outcome["suspended"] = True

    resolved = ReportStatus.dismissed if body.action == Action.dismiss else ReportStatus.actioned
    now = utcnow()
    for report in open_reports:
        report.status = resolved
        report.reviewer_id = reviewer.id
        report.reviewed_at = now
        report.reviewer_note = body.note.strip()[:MAX_REVIEWER_NOTE]

    await db.commit()
    log.info(
        "report_decided",
        subject_id=user_id,
        reviewer_id=reviewer.id,
        action=str(body.action),
        reports_closed=len(open_reports),
    )
    outcome["reports_closed"] = len(open_reports)
    outcome["held_photos"] = len(held_photos)
    return outcome


@router.post("/subjects/{user_id}/reinstate")
async def reinstate(
    user_id: str,
    reviewer: User = Depends(require_reviewer),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Undo a suspension.

    Reversible on purpose. A suspension made on thin evidence that cannot be
    lifted is a suspension nobody will make on thin evidence *or* on good
    evidence, because the cost of being wrong is the same either way.
    """
    subject = await db.get(User, user_id)
    if subject is None:
        raise NotFound("That account no longer exists.")
    if subject.status != UserStatus.suspended:
        raise AppError("That account isn't suspended.")

    subject.status = UserStatus.active
    await db.commit()
    log.info("account_reinstated", subject_id=user_id, reviewer_id=reviewer.id)
    return {"status": subject.status}


async def _reelect_primary(db: AsyncSession, user_id: str) -> None:
    """Hand the pair view a photo after one has been taken away.

    Without this, removing somebody's only shown photo leaves an account that
    still has photos and shows none — visible in pairs as a blank.
    """
    photos = (
        (
            await db.execute(
                select(MediaAsset)
                .where(MediaAsset.user_id == user_id)
                .where(MediaAsset.kind == MediaKind.photo)
                .where(MediaAsset.status == MediaStatus.processed)
                .order_by(MediaAsset.display_order, MediaAsset.created_at, MediaAsset.id)
            )
        )
        .scalars()
        .all()
    )
    for index, photo in enumerate(photos):
        photo.is_primary = index == 0
