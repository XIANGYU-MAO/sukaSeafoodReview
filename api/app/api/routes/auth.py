from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import (
    CurrentAuth,
    get_current_auth,
    get_runtime_settings,
    require_csrf,
)
from app.config import Settings
from app.database import get_db
from app.models import Session
from app.schemas.auth import (
    AuthState,
    ChangePasswordRequest,
    LoginName,
    LoginOptionsResponse,
    LoginRequest,
)
from app.services.auth import (
    FIXED_USERS,
    LOGIN_FAILURE_LIMIT,
    LOGIN_LOCK_MINUTES,
    as_utc,
    csrf_token,
    generate_session_token,
    hash_password,
    require_valid_new_password,
    resolve_client_address,
    session_digest,
    user_by_id_for_update,
    user_by_name_for_update,
    utc_now,
    verify_dummy_password,
    verify_password,
)
from app.services.settings import get_system_settings


router = APIRouter(prefix="/auth", tags=["auth"])
INVALID_CREDENTIALS = "Invalid credentials"
TEMPORARILY_UNAVAILABLE = "Authentication temporarily unavailable"


async def public_state(
    auth: CurrentAuth, settings: Settings, db: AsyncSession
) -> AuthState:
    system_settings = await get_system_settings(db)
    return AuthState(
        id=auth.user.id,
        name=auth.user.name,
        role=auth.user.role,
        must_change_password=auth.user.must_change_password,
        csrf_token=csrf_token(auth.session.token_hash, settings.CSRF_SECRET),
        team_progress_visible=(
            auth.user.role == "admin"
            or system_settings.reviewer_team_progress_visible
        ),
    )


def client_address(request: Request) -> str:
    peer = request.client.host if request.client is not None else "unknown"
    return resolve_client_address(
        peer,
        request.headers.get("X-Forwarded-For"),
        request.app.state.trusted_proxy_networks,
    )


def set_session_cookie(
    response: Response, settings: Settings, token: str
) -> None:
    response.set_cookie(
        key=settings.SESSION_COOKIE_NAME,
        value=token,
        max_age=settings.SESSION_HOURS * 60 * 60,
        path="/sukaseafood",
        secure=settings.secure_cookie,
        httponly=True,
        samesite="lax",
    )


def clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        key=settings.SESSION_COOKIE_NAME,
        path="/sukaseafood",
        secure=settings.secure_cookie,
        httponly=True,
        samesite="lax",
    )


@router.get("/names", response_model=LoginOptionsResponse)
async def names(db: AsyncSession = Depends(get_db)) -> LoginOptionsResponse:
    system_settings = await get_system_settings(db)
    return LoginOptionsResponse(
        login_name_mode=system_settings.login_name_mode,
        names=(
            [LoginName(name=name) for name, _ in FIXED_USERS]
            if system_settings.login_name_mode == "choices"
            else []
        ),
    )


@router.post("/login", response_model=AuthState)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_runtime_settings),
) -> AuthState:
    now = utc_now()
    address = client_address(request)
    limiter = request.app.state.login_limiter
    if limiter.is_limited(address, now):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=TEMPORARILY_UNAVAILABLE,
        )

    user = await db.scalar(user_by_name_for_update(payload.name))
    if (
        user is not None
        and user.locked_until is not None
        and as_utc(user.locked_until) <= now
    ):
        user.failed_login_count = 0
        user.locked_until = None
    account_locked = bool(
        user is not None
        and user.locked_until is not None
        and as_utc(user.locked_until) > now
    )
    if user is None:
        verify_dummy_password(payload.password)
        password_valid = False
    else:
        password_valid = verify_password(payload.password, user.password_hash)

    if user is None or not user.active or account_locked or not password_valid:
        client_limited = limiter.record_failure(address, now)
        account_limited = account_locked
        if user is not None:
            if not account_locked:
                user.failed_login_count += 1
                if user.failed_login_count >= LOGIN_FAILURE_LIMIT:
                    user.locked_until = now + timedelta(minutes=LOGIN_LOCK_MINUTES)
                    account_limited = True
            await db.commit()
        if client_limited or account_limited:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=TEMPORARILY_UNAVAILABLE,
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=INVALID_CREDENTIALS
        )

    user.failed_login_count = 0
    user.locked_until = None
    limiter.clear(address)
    raw_token = generate_session_token()
    session = Session(
        user_id=user.id,
        token_hash=session_digest(raw_token),
        password_version=user.password_version,
        expires_at=now + timedelta(hours=settings.SESSION_HOURS),
    )
    db.add(session)
    await db.commit()
    set_session_cookie(response, settings, raw_token)
    return await public_state(CurrentAuth(user=user, session=session), settings, db)


@router.get("/me", response_model=AuthState)
async def me(
    auth: CurrentAuth = Depends(get_current_auth),
    settings: Settings = Depends(get_runtime_settings),
    db: AsyncSession = Depends(get_db),
) -> AuthState:
    return await public_state(auth, settings, db)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    auth: CurrentAuth = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_runtime_settings),
) -> None:
    auth.session.revoked_at = utc_now()
    await db.commit()
    clear_session_cookie(response, settings)


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    payload: ChangePasswordRequest,
    response: Response,
    auth: CurrentAuth = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_runtime_settings),
) -> None:
    # Keep the service-level guard even though the request schema rejects the
    # same bounds, so non-HTTP callers cannot bypass the password contract.
    require_valid_new_password(payload.new_password)
    user = await db.scalar(user_by_id_for_update(auth.user.id))
    if (
        user is None
        or not user.active
        or auth.session.password_version != user.password_version
    ):
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    if not verify_password(payload.current_password, user.password_hash):
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is invalid",
        )
    if verify_password(payload.new_password, user.password_hash):
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New password must be different",
        )

    now = utc_now()
    user.password_hash = hash_password(payload.new_password)
    user.password_version += 1
    user.must_change_password = False
    user.failed_login_count = 0
    user.locked_until = None
    await db.execute(
        update(Session)
        .where(Session.user_id == user.id, Session.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    await db.commit()
    clear_session_cookie(response, settings)
