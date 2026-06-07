import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import hash_password, verify_password
from app.db.session import get_db
from app.models import RefreshToken, Seller
from app.schemas.auth import LoginRequest, RegisterRequest, TokenPair
from shared.errors.http import ConflictError, UnauthorizedError
from shared.auth.jwt import create_access_token, create_refresh_token, hash_token

router = APIRouter()

DB = Annotated[AsyncSession, Depends(get_db)]


@router.post("/register", response_model=TokenPair, status_code=201)
async def register_seller(body: RegisterRequest, db: DB) -> TokenPair:
    # Проверяем уникальность email
    exists = await db.scalar(select(Seller.id).where(Seller.email == body.email))
    if exists:
        raise ConflictError(detail="Email already registered")

    seller = Seller(
        email=body.email,
        hashed_password=hash_password(body.password),
        company_name=body.company_name,
        inn=body.inn,
    )
    db.add(seller)
    await db.flush()  # получаем seller.id до commit

    return await _issue_tokens(seller, db)


@router.post("/login", response_model=TokenPair)
async def login_seller(body: LoginRequest, db: DB) -> TokenPair:
    result = await db.execute(
        select(Seller).where(Seller.email == body.email, Seller.deleted_at.is_(None))
    )
    seller = result.scalar_one_or_none()

    if seller is None or not verify_password(body.password, seller.hashed_password):
        raise UnauthorizedError(detail="Invalid email or password")
    if not seller.is_active:
        raise UnauthorizedError(detail="Account is deactivated")

    return await _issue_tokens(seller, db)


@router.post("/refresh", response_model=TokenPair)
async def refresh_tokens(refresh_token: str, db: DB) -> TokenPair:
    token_hash = hash_token(refresh_token)
    result = await db.execute(
        select(RefreshToken)
        .where(RefreshToken.token_hash == token_hash)
        .with_for_update()
    )
    db_token = result.scalar_one_or_none()

    if db_token is None or not db_token.is_valid:
        raise UnauthorizedError(detail="Invalid or expired refresh token")

    # Отзываем старый токен (rotation)
    db_token.revoked_at = datetime.now(UTC)
    await db.flush()

    seller = await db.get(Seller, db_token.seller_id)
    return await _issue_tokens(seller, db)


@router.post("/logout", status_code=204)
async def logout_seller(refresh_token: str, db: DB) -> None:
    token_hash = hash_token(refresh_token)
    result = await db.execute(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    )
    db_token = result.scalar_one_or_none()
    if db_token:
        db_token.revoked_at = datetime.now(UTC)


# ─── Приватная утилита ──────────────────────

async def _issue_tokens(seller: Seller, db: AsyncSession) -> "TokenPair":
    access = create_access_token(
        subject=str(seller.id),
        secret_key=settings.secret_key,
        expires_minutes=settings.access_token_expire_minutes,
        extra_claims={"role": "seller"},
    )
    raw_refresh, expires_at = create_refresh_token(
        subject=str(seller.id),
        secret_key=settings.secret_key,
        expires_days=settings.refresh_token_expire_days,
    )
    db.add(RefreshToken(
        seller_id=seller.id,
        token_hash=hash_token(raw_refresh),
        expires_at=expires_at,
    ))
    return TokenPair(access_token=access, refresh_token=raw_refresh)
