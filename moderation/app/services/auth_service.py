from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import verify_password
from app.models import Moderator, RefreshToken
from app.schemas.auth import TokenResponse
from shared.auth.jwt import create_access_token, create_refresh_token, hash_token
from shared.errors.http import UnauthorizedError


async def authenticate_moderator(
    email: str,
    password: str,
    db: AsyncSession,
) -> TokenResponse:
    """Authenticate a moderator by email + password."""
    result = await db.execute(
        select(Moderator).where(
            Moderator.email == email,
            Moderator.is_active == True,
        )
    )
    moderator = result.scalar_one_or_none()
    if moderator is None or not verify_password(password, moderator.hashed_password):
        raise UnauthorizedError(detail="Invalid email or password")

    moderator.last_login_at = datetime.now(UTC)
    await db.flush()

    return await _issue_tokens(moderator, db)


async def refresh_tokens(
    raw_refresh_token: str,
    db: AsyncSession,
) -> TokenResponse:
    """Refresh access token using a valid refresh token."""
    token_hash = hash_token(raw_refresh_token)
    result = await db.execute(
        select(RefreshToken)
        .where(RefreshToken.token_hash == token_hash)
        .with_for_update()
    )
    db_token = result.scalar_one_or_none()

    if db_token is None or not db_token.is_valid:
        raise UnauthorizedError(detail="Invalid or expired refresh token")

    # Rotate: revoke old token
    db_token.revoked_at = datetime.now(UTC)
    await db.flush()

    moderator = await db.get(Moderator, db_token.moderator_id)
    if moderator is None or not moderator.is_active:
        raise UnauthorizedError(detail="Moderator not found or deactivated")

    return await _issue_tokens(moderator, db)


async def logout_moderator(
    raw_refresh_token: str,
    db: AsyncSession,
) -> None:
    """Revoke a refresh token."""
    token_hash = hash_token(raw_refresh_token)
    result = await db.execute(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    )
    db_token = result.scalar_one_or_none()
    if db_token:
        db_token.revoked_at = datetime.now(UTC)


async def _issue_tokens(moderator: Moderator, db: AsyncSession) -> TokenResponse:
    """Create access + refresh tokens and persist the refresh token."""
    access = create_access_token(
        subject=str(moderator.id),
        secret_key=settings.secret_key,
        algorithm=settings.algorithm,
        expires_minutes=settings.access_token_expire_minutes,
        extra_claims={"role": moderator.role.value},
    )
    raw_refresh, expires_at = create_refresh_token(
        subject=str(moderator.id),
        secret_key=settings.secret_key,
        algorithm=settings.algorithm,
        expires_days=settings.refresh_token_expire_days,
    )

    db.add(RefreshToken(
        moderator_id=moderator.id,
        token_hash=hash_token(raw_refresh),
        expires_at=expires_at,
    ))
    await db.flush()

    return TokenResponse(
        access_token=access,
        refresh_token=raw_refresh,
        token_type="bearer",
        expires_in=settings.access_token_expire_minutes * 60,
        user_id=moderator.id,
        role=moderator.role.value,
    )