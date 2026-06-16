import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import hash_password, verify_password
from app.db.session import get_db
from app.models import Buyer, RefreshToken
from app.schemas.auth import LoginRequest, RegisterRequest, TokenPair
from shared.errors.http import ConflictError, UnauthorizedError
from shared.auth.jwt import create_access_token, create_refresh_token, hash_token

router = APIRouter()
DB = Annotated[AsyncSession, Depends(get_db)]


@router.post("/register", response_model=TokenPair, status_code=201)
async def register_buyer(body: RegisterRequest, db: DB) -> TokenPair:
    exists = await db.scalar(select(Buyer.id).where(Buyer.email == body.email))
    if exists:
        raise ConflictError(detail="Email already registered")

    buyer = Buyer(
        email=body.email,
        hashed_password=hash_password(body.password),
        first_name=body.first_name,
        last_name=body.last_name,
    )
    db.add(buyer)
    await db.flush()
    return await _issue_tokens(buyer, db)


@router.post("/login", response_model=TokenPair)
async def login_buyer(body: LoginRequest, db: DB) -> TokenPair:
    result = await db.execute(
        select(Buyer).where(Buyer.email == body.email, Buyer.deleted_at.is_(None))
    )
    buyer = result.scalar_one_or_none()
    if buyer is None or not verify_password(body.password, buyer.hashed_password):
        raise UnauthorizedError(detail="Invalid email or password")
    if not buyer.is_active:
        raise UnauthorizedError(detail="Account is deactivated")
    return await _issue_tokens(buyer, db)


@router.post("/refresh", response_model=TokenPair)
async def refresh_tokens(refresh_token: str, db: DB) -> TokenPair:
    token_hash_val = hash_token(refresh_token)
    result = await db.execute(
        select(RefreshToken)
        .where(RefreshToken.token_hash == token_hash_val)
        .with_for_update()
    )
    db_token = result.scalar_one_or_none()
    if db_token is None or not db_token.is_valid:
        raise UnauthorizedError(detail="Invalid or expired refresh token")

    db_token.revoked_at = datetime.now(UTC)
    await db.flush()

    buyer = await db.get(Buyer, db_token.buyer_id)
    return await _issue_tokens(buyer, db)


@router.post("/logout", status_code=204)
async def logout_buyer(refresh_token: str, db: DB) -> None:
    token_hash_val = hash_token(refresh_token)
    result = await db.execute(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash_val)
    )
    db_token = result.scalar_one_or_none()
    if db_token:
        db_token.revoked_at = datetime.now(UTC)


async def _issue_tokens(buyer: Buyer, db: AsyncSession) -> TokenPair:
    access = create_access_token(
        subject=str(buyer.id),
        secret_key=settings.secret_key,
        expires_minutes=settings.access_token_expire_minutes,
        extra_claims={"role": "buyer"},
    )
    raw_refresh, expires_at = create_refresh_token(
        subject=str(buyer.id),
        secret_key=settings.secret_key,
        expires_days=settings.refresh_token_expire_days,
    )
    db.add(RefreshToken(
        buyer_id=buyer.id,
        token_hash=hash_token(raw_refresh),
        expires_at=expires_at,
    ))
    return TokenPair(access_token=access, refresh_token=raw_refresh)
