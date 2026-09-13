"""Message requests: what an unlock entitles someone to do.

Unlocking a person reveals their profile. It does not open a conversation —
one side being sure is not the same as two sides agreeing, and a dating app
that lets certainty alone put a message in someone's inbox has rebuilt the
thing everyone hates about dating apps.

So the unlock buys exactly one opening message. It lands in a request inbox,
where the recipient can reply or decline. A reply is what opens the chat; up
to that point the sender cannot send again, which is the whole mechanism —
there is no way here to send six messages to someone who has not answered.

The exception is a mutual crossing. When both people have independently
unlocked each other, there is nothing left to ask permission for and the
conversation simply opens, with no request and no initiator.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.affinity import (
    admirer_ids,
    audience,
    is_unlocked,
    share_of,
    unlocked_subject_ids,
)
from backend.database import (
    ChatMessage,
    ChatStatus,
    Connection,
    ConnectionStatus,
    User,
    pair_key,
    utcnow,
)
from backend.errors import AppError, Conflict, NotAuthorized, NotFound
from backend.logging_config import get_logger
from backend.profile_view import full_profile_view, primary_photo_url
from backend.safety import blocked_ids, is_blocked

log = get_logger(__name__)

# Long enough for a real opener, short enough that the inbox stays an inbox.
MAX_MESSAGE_LENGTH = 2000


def _clean(text: str) -> str:
    body = (text or "").strip()
    if not body:
        raise AppError("A message needs some words in it.")
    return body[:MAX_MESSAGE_LENGTH]


async def find(db: AsyncSession, user_a: str, user_b: str) -> Connection | None:
    return (
        await db.execute(select(Connection).where(Connection.connection_key == pair_key(user_a, user_b)))
    ).scalar_one_or_none()


async def _load_for(db: AsyncSession, connection_id: str, user_id: str) -> Connection:
    """Fetch a connection this user is actually part of.

    Membership is checked in the query rather than after it, and a connection
    someone is not part of reads as absent rather than forbidden — a 403 would
    confirm the id names a real conversation.
    """
    connection = (
        await db.execute(
            select(Connection)
            .where(Connection.id == connection_id)
            .where(or_(Connection.user_a_id == user_id, Connection.user_b_id == user_id))
        )
    ).scalar_one_or_none()
    if connection is None:
        raise NotFound("That conversation doesn't exist.")
    return connection


def peer_id(connection: Connection, user_id: str) -> str:
    return connection.user_b_id if connection.user_a_id == user_id else connection.user_a_id


def _new(user_a: str, user_b: str, *, status: str, initiator_id: str | None) -> Connection:
    # Stored in the same canonical order the key is built from, so the row and
    # its key can never disagree about who it is between.
    first, second = sorted((user_a, user_b))
    return Connection(
        user_a_id=first,
        user_b_id=second,
        connection_key=pair_key(user_a, user_b),
        initiator_id=initiator_id,
        status=status,
        opened_at=utcnow() if status == ConnectionStatus.open else None,
    )


async def on_unlock(db: AsyncSession, *, viewer_id: str, subject_id: str) -> Connection | None:
    """Called the moment a viewer unlocks someone. Returns a connection if
    this unlock was the second half of a mutual one.

    Two shapes of mutual crossing, and they are the same event seen from
    different sides: the other person already unlocked this viewer and simply
    has not written yet, or they already wrote and are waiting. Either way the
    viewer has now agreed, so there is nothing left for them to answer.
    """
    existing = await find(db, viewer_id, subject_id)

    if existing is not None:
        if existing.status == ConnectionStatus.requested and existing.initiator_id != viewer_id:
            existing.status = ConnectionStatus.open
            existing.opened_at = utcnow()
            log.info("connection_opened", connection_id=existing.id, reason="unlock_answered_request")
            return existing
        # An open connection needs nothing; a declined one stays declined.
        return None

    if not await is_unlocked(db, subject_id, viewer_id):
        return None

    connection = _new(viewer_id, subject_id, status=ConnectionStatus.open, initiator_id=None)
    db.add(connection)
    await db.flush()
    log.info("connection_opened", connection_id=connection.id, reason="mutual_unlock")
    return connection


async def send_request(
    db: AsyncSession, *, sender: User, recipient_id: str, text: str
) -> tuple[Connection, ChatMessage]:
    """Spend the one opening message an unlock buys."""
    if recipient_id == sender.id:
        raise AppError("You can't reach out to yourself.")
    # Deliberately the same message a stranger gets. Confirming that a
    # specific person blocked you is information the blocker did not agree to
    # share, and it is the thing that turns a block into a provocation.
    if await is_blocked(db, sender.id, recipient_id):
        raise NotAuthorized("You can't reach that person.")
    # Either direction earns the message. Unlocking them is the original
    # route; *them* having unlocked you is the new one, and it is the same
    # fact from the other side — somebody has already declared certainty
    # about this pair, so there is no case where nobody has chosen anybody.
    #
    # Writing back to someone who chose you opens the conversation outright
    # rather than sending a request: the `mutual` branch below already does
    # that, and it is right here for the same reason. They asked by choosing;
    # answering is not a thing they need to approve a second time.
    if not await is_unlocked(db, sender.id, recipient_id) and not await is_unlocked(
        db, recipient_id, sender.id
    ):
        raise NotAuthorized("You haven't unlocked that person yet.")

    body = _clean(text)
    existing = await find(db, sender.id, recipient_id)

    if existing is not None:
        if existing.status == ConnectionStatus.declined:
            raise Conflict("That request was answered already.")
        if existing.status == ConnectionStatus.open:
            raise Conflict("You're already connected — just send a message.")
        if existing.initiator_id == sender.id:
            raise Conflict("You've already reached out. They'll see it when they're ready.")
        # They asked first and this viewer has since unlocked them: replying
        # is what opens it, which is exactly what this message is.
        existing.status = ConnectionStatus.open
        existing.opened_at = utcnow()
        message = ChatMessage(connection_id=existing.id, from_user_id=sender.id, message_text=body)
        db.add(message)
        await db.flush()
        return existing, message

    # A recipient who has already unlocked the sender has nothing left to
    # agree to — skip the request and open it outright.
    mutual = await is_unlocked(db, recipient_id, sender.id)
    status = ConnectionStatus.open if mutual else ConnectionStatus.requested
    connection = _new(sender.id, recipient_id, status=status, initiator_id=None if mutual else sender.id)
    db.add(connection)
    await db.flush()

    message = ChatMessage(connection_id=connection.id, from_user_id=sender.id, message_text=body)
    db.add(message)
    await db.flush()

    log.info("connection_requested", connection_id=connection.id, status=status, mutual=mutual)
    return connection, message


async def is_unanswered_request(db: AsyncSession, connection_id: str) -> bool:
    """Whether the next message here would be the one that opens it.

    Asked *before* the write, because `post_message` is what flips the status
    — afterwards there is no way to tell the reply that opened a conversation
    from the fortieth message in it.
    """
    connection = await db.get(Connection, connection_id)
    return connection is not None and connection.status == ConnectionStatus.requested


async def post_message(db: AsyncSession, *, sender: User, connection_id: str, text: str) -> ChatMessage:
    """Send into an existing conversation.

    The recipient's first message is what opens a request — accepting is not a
    separate button, it is answering. The initiator, meanwhile, cannot send
    again while the request is outstanding: that single-message limit is the
    entire protection the inbox offers.
    """
    connection = await _load_for(db, connection_id, sender.id)
    body = _clean(text)

    if await is_blocked(db, sender.id, peer_id(connection, sender.id)):
        raise Conflict("That conversation is closed.")
    if connection.status == ConnectionStatus.declined:
        raise Conflict("That conversation is closed.")

    if connection.status == ConnectionStatus.requested:
        if connection.initiator_id == sender.id:
            raise Conflict("Wait for a reply before sending anything else.")
        connection.status = ConnectionStatus.open
        connection.opened_at = utcnow()
        log.info("connection_opened", connection_id=connection.id, reason="replied")

    message = ChatMessage(connection_id=connection.id, from_user_id=sender.id, message_text=body)
    db.add(message)
    await db.flush()
    return message


async def decline(db: AsyncSession, *, user: User, connection_id: str) -> Connection:
    connection = await _load_for(db, connection_id, user.id)
    if connection.status == ConnectionStatus.open:
        raise Conflict("That conversation is already open.")
    if connection.initiator_id == user.id:
        raise Conflict("You sent that one.")

    connection.status = ConnectionStatus.declined
    connection.closed_at = utcnow()
    log.info("connection_declined", connection_id=connection.id)
    return connection


async def mark_read(db: AsyncSession, *, user: User, connection_id: str) -> int:
    connection = await _load_for(db, connection_id, user.id)
    unread = (
        (
            await db.execute(
                select(ChatMessage)
                .where(ChatMessage.connection_id == connection.id)
                .where(ChatMessage.from_user_id != user.id)
                .where(ChatMessage.status != ChatStatus.read)
            )
        )
        .scalars()
        .all()
    )
    for message in unread:
        message.status = ChatStatus.read
    return len(unread)


async def messages(
    db: AsyncSession, *, user: User, connection_id: str, limit: int = 50, before: str | None = None
) -> tuple[Connection, list[ChatMessage]]:
    connection = await _load_for(db, connection_id, user.id)

    query = select(ChatMessage).where(ChatMessage.connection_id == connection.id)
    if before:
        anchor = await db.get(ChatMessage, before)
        if anchor is not None:
            query = query.where(ChatMessage.created_at < anchor.created_at)

    rows = (await db.execute(query.order_by(ChatMessage.created_at.desc()).limit(limit))).scalars().all()
    # Newest-first is how you page backwards; oldest-first is how you read.
    return connection, list(reversed(rows))


def serialise_message(message: ChatMessage, user_id: str) -> dict[str, Any]:
    return {
        "id": message.id,
        "text": message.message_text or "",
        "mine": message.from_user_id == user_id,
        "sent_at": message.created_at.isoformat() if message.created_at else None,
        "status": message.status,
    }


async def peer_card(db: AsyncSession, user_id: str) -> dict[str, Any]:
    peer = await db.get(User, user_id)
    return {
        "id": user_id,
        "display_name": peer.display_name if peer else None,
        "photo_url": await primary_photo_url(db, user_id),
    }


async def counts(db: AsyncSession, user: User) -> dict[str, int]:
    """The three numbers the navbar puts on screen, and nothing else.

    The bar is on every signed-in page, so this runs on every page load. It
    used to run `inbox()` — which serialises a full profile for every unlocked
    person and every admirer, fetches a `peer_card` per connection, and reads
    every message in every thread to find the last one — to render three small
    integers. The cost had no relationship to what was displayed.

    So this deliberately duplicates the *grouping rules* rather than reusing
    the builder: what is expensive there is the serialisation, and there is no
    way to reuse the shape without paying for it. The rules are small enough
    to restate and `test_counts_agree_with_the_inbox` pins them together, so
    the duplication cannot drift silently — which is the only reason it is
    acceptable.
    """
    rows = (
        await db.execute(
            select(
                Connection.id,
                Connection.user_a_id,
                Connection.user_b_id,
                Connection.status,
                Connection.initiator_id,
            ).where(or_(Connection.user_a_id == user.id, Connection.user_b_id == user.id))
        )
    ).all()

    connected_peers = {b if a == user.id else a for _, a, b, _, _ in rows}
    open_ids = [cid for cid, _, _, status, _ in rows if status == ConnectionStatus.open]
    requests = sum(
        1
        for _, _, _, status, initiator in rows
        if status == ConnectionStatus.requested and initiator != user.id
    )

    # One aggregate for "how many threads have something unread", rather than
    # every message in every thread. `distinct` is doing the work that
    # `reduce(c.unread > 0)` did on the client.
    unread_threads = 0
    if open_ids:
        unread_threads = (
            await db.scalar(
                select(func.count(func.distinct(ChatMessage.connection_id)))
                .where(ChatMessage.connection_id.in_(open_ids))
                .where(ChatMessage.from_user_id != user.id)
                .where(ChatMessage.status != ChatStatus.read)
            )
        ) or 0

    separated = await blocked_ids(db, user.id)
    hidden = connected_peers | separated

    unlocked = sum(1 for s in await unlocked_subject_ids(db, user.id) if s not in hidden)
    admirers = sum(1 for v in await admirer_ids(db, user.id) if v not in hidden)

    return {
        "type": unlocked,
        "chosen": admirers,
        # A request and an unread message are the same thing to the person
        # looking at the bar: somebody is waiting on them.
        "messages": requests + unread_threads,
    }


async def inbox(db: AsyncSession, user: User) -> dict[str, Any]:
    """Everything this person currently has in play, in five groups.

    `unlocked` is the one that has no counterpart in a swipe app: people whose
    profiles are open to this viewer and who have not been written to yet. It
    is a prompt, not a match list — the viewer has decided, and the other
    person still knows nothing about it.

    `admirers` is its mirror, and it is the newer idea: people who keep
    choosing *this* person. Seeing them is what turns being chosen into
    something you can act on rather than something that happens to you.
    """
    # Declined connections are fetched too, and then left out of every
    # group. They still have to be *known*: a declined peer must not fall back
    # into the unlocked prompt below, or the app would spend the rest of the
    # year suggesting someone write again to a person who said no.
    connections = (
        (
            await db.execute(
                select(Connection)
                .where(or_(Connection.user_a_id == user.id, Connection.user_b_id == user.id))
                .order_by(Connection.created_at.desc())
            )
        )
        .scalars()
        .all()
    )

    ids = [c.id for c in connections]
    last_by_connection: dict[str, ChatMessage] = {}
    unread_by_connection: dict[str, int] = {}

    if ids:
        for message in (
            (
                await db.execute(
                    select(ChatMessage)
                    .where(ChatMessage.connection_id.in_(ids))
                    .order_by(ChatMessage.connection_id, ChatMessage.created_at.desc())
                )
            )
            .scalars()
            .all()
        ):
            last_by_connection.setdefault(message.connection_id, message)

        unread_by_connection = dict(
            (
                await db.execute(
                    select(ChatMessage.connection_id, func.count(ChatMessage.id))
                    .where(ChatMessage.connection_id.in_(ids))
                    .where(ChatMessage.from_user_id != user.id)
                    .where(ChatMessage.status != ChatStatus.read)
                    .group_by(ChatMessage.connection_id)
                )
            ).all()
        )

    conversations: list[dict[str, Any]] = []
    incoming: list[dict[str, Any]] = []
    sent: list[dict[str, Any]] = []
    connected_peers: set[str] = set()

    for connection in connections:
        other = peer_id(connection, user.id)
        connected_peers.add(other)
        if connection.status == ConnectionStatus.declined:
            continue

        last = last_by_connection.get(connection.id)
        entry = {
            "id": connection.id,
            "status": connection.status,
            "peer": await peer_card(db, other),
            "unread": unread_by_connection.get(connection.id, 0),
            "last_message": serialise_message(last, user.id) if last else None,
            "opened_at": connection.opened_at.isoformat() if connection.opened_at else None,
        }
        if connection.status == ConnectionStatus.open:
            conversations.append(entry)
        elif connection.initiator_id == user.id:
            sent.append(entry)
        else:
            incoming.append(entry)

    # Blocking clears the affinity, so this is belt and braces — but the
    # inbox is the one place a blocked person reappearing would be most
    # visible, and the query costs nothing.
    separated = await blocked_ids(db, user.id)
    unlocked = [
        await full_profile_view(db, subject_id)
        for subject_id in await unlocked_subject_ids(db, user.id)
        if subject_id not in connected_peers and subject_id not in separated
    ]

    # People who keep choosing this person, and have not been talked to yet.
    # Anyone already in a conversation, a request or a decline is left out —
    # they are further along than "interested", and showing them twice would
    # make the inbox read as two inboxes.
    admirers = [
        await full_profile_view(db, viewer_id)
        for viewer_id in await admirer_ids(db, user.id)
        if viewer_id not in connected_peers and viewer_id not in separated
    ]

    admirer_count, seen_by = await audience(db, user.id)
    return {
        "unlocked": unlocked,
        "admirers": admirers,
        "reach": {
            # The totals rather than the filtered list: somebody with four
            # admirers, three of whom they are already talking to, has four.
            "admirers": admirer_count,
            "seen_by": seen_by,
            "share": share_of(admirer_count, seen_by),
        },
        "requests": incoming,
        "sent": sent,
        "conversations": conversations,
    }
