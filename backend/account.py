"""The account itself: what may be computed from it, and how it ends.

Both things here are done *to* an account rather than with it, and both come
from the same statute, so they share a file. `backend/privacy.py` holds the
logic; this is the HTTP surface and the checks that only make sense at the
edge — the password confirmation, the rate limit, the shape of the answer.

**Consent is read before every upload, not cached in a token.** A withdrawal
has to take effect on the next request, not whenever a JWT happens to expire.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend import notify, privacy
from backend.auth import current_user, verify_password
from backend.database import User, get_db
from backend.errors import NotAuthorized
from backend.logging_config import get_logger
from backend.ratelimit import CONFIRM_PASSWORD, consume

log = get_logger(__name__)
router = APIRouter(prefix="/api/account", tags=["account"])


def _consent_view(row: privacy.BiometricConsent | None) -> dict[str, Any]:
    return {
        "granted": row is not None,
        "granted_at": row.granted_at.isoformat() if row else None,
        # What they agreed to, beside what we are asking now. When these differ
        # the old grant is consent to the old words, so the UI has to ask
        # again rather than assume the answer carries over.
        "agreed_version": row.notice_version if row else None,
        "notice_version": privacy.NOTICE_VERSION,
        "needs_restatement": row is not None and row.notice_version != privacy.NOTICE_VERSION,
        "purpose": privacy.BIOMETRIC_PURPOSE,
    }


@router.get("/consent")
async def read_consent(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    return _consent_view(await privacy.current_consent(db, user.id))


@router.post("/consent", status_code=201)
async def give_consent(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    row = await privacy.grant(db, user.id)
    await db.commit()
    await db.refresh(row)
    return _consent_view(row)


@router.delete("/consent")
async def take_consent_back(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Withdrawal is one click and asks nothing in return.

    A withdrawal guarded by a password prompt, a survey, or a confirmation
    chain is a withdrawal made harder than the grant was, which is the thing
    the law is there to prevent. The cost of being wrong is low: they can
    grant it again, and the photos are untouched either way.
    """
    withdrawn = await privacy.withdraw(db, user.id)
    await db.commit()
    return {"withdrawn": withdrawn, **_consent_view(None)}


class NotificationPrefs(BaseModel):
    model_config = {"extra": "forbid"}

    email_notifications: bool


@router.get("/notifications")
async def read_notifications(
    user: User = Depends(current_user),
) -> dict[str, Any]:
    return {"email_notifications": user.email_notifications}


@router.put("/notifications")
async def set_notifications(
    body: NotificationPrefs,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    user.email_notifications = body.email_notifications
    await db.commit()
    return {"email_notifications": user.email_notifications}


class Unsubscribe(BaseModel):
    model_config = {"extra": "forbid"}

    user_id: str
    token: str


@router.post("/unsubscribe")
async def unsubscribe(body: Unsubscribe, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Turn the emails off from inside one, without signing in.

    Deliberately unauthenticated. The people most likely to want out are the
    least likely to still have an account they can get into, and a preference
    reachable only behind a login is not really a preference — it is a reason
    to click "spam" instead, which costs the sending domain far more than the
    unsubscribe would have.

    The token is an HMAC over the user id, so only a link we sent can do this,
    and it answers the same way whether or not the account exists — otherwise
    it would be an oracle for which addresses are registered.
    """
    if notify.valid_unsubscribe(body.user_id, body.token):
        user = await db.get(User, body.user_id)
        if user is not None:
            user.email_notifications = False
            await db.commit()
            log.info("unsubscribed", user_id=user.id)
    return {"status": "unsubscribed"}


class DeleteRequest(BaseModel):
    password: str = Field(min_length=1, max_length=128)


@router.post("/delete")
async def delete_account(
    req: DeleteRequest,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """End the account, and say what went.

    The password is asked for because a session token is the one credential
    somebody else might be holding, and this is the single action they could
    take with it that cannot be undone by anyone.
    """
    await consume(db, CONFIRM_PASSWORD, user.id)
    if not verify_password(req.password, user.password_hash):
        raise NotAuthorized("That password doesn't match.")

    removed = await privacy.erase(db, user)
    await db.commit()
    return {"deleted": True, "removed": removed}
