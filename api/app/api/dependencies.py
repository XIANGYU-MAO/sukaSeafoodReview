from dataclasses import dataclass
from datetime import datetime

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.database import get_db
from app.models import Session, User
from app.services.auth import as_utc, session_digest, utc_now, verify_csrf_token


@dataclass
class CurrentAuth:
    user: User
    session: Session


def get_runtime_settings(request: Request) -> Settings:
    return request.app.state.settings


async def get_current_auth(
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_runtime_settings),
) -> CurrentAuth:
    raw_token = request.cookies.get(settings.SESSION_COOKIE_NAME)
    if not raw_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)

    row = (
        await db.execute(
            select(Session, User)
            .join(User, User.id == Session.user_id)
            .where(Session.token_hash == session_digest(raw_token))
        )
    ).one_or_none()
    now = utc_now()
    if row is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    session, user = row
    if (
        session.revoked_at is not None
        or as_utc(session.expires_at) <= now
        or not user.active
        or session.password_version != user.password_version
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    return CurrentAuth(user=user, session=session)


async def get_current_user(auth: CurrentAuth = Depends(get_current_auth)) -> User:
    return auth.user


async def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    return user


async def require_csrf(
    request: Request,
    auth: CurrentAuth = Depends(get_current_auth),
    settings: Settings = Depends(get_runtime_settings),
) -> CurrentAuth:
    supplied = request.headers.get("X-CSRF-Token")
    if not supplied or not verify_csrf_token(
        auth.session.token_hash, settings.CSRF_SECRET, supplied
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    return auth


async def require_review_access(
    auth: CurrentAuth = Depends(require_csrf),
) -> CurrentAuth:
    if auth.user.must_change_password:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    return auth
