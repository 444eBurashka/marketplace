import uuid
from typing import Annotated

from fastapi import Depends, Header
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_db
from app.models import Seller
from shared.errors.http import ForbiddenError, UnauthorizedError
from shared.auth.jwt import decode_token

bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_seller(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Seller:
    """Базовая авторизация — любой активный продавец."""
    if credentials is None:
        raise UnauthorizedError()

    payload = decode_token(credentials.credentials, settings.secret_key)
    seller_id = payload.get("sub")
    if seller_id is None:
        raise UnauthorizedError()

    result = await db.execute(
        select(Seller).where(
            Seller.id == uuid.UUID(seller_id),
            Seller.is_active == True,  # noqa: E712
            Seller.deleted_at.is_(None),
        )
    )
    seller = result.scalar_one_or_none()
    if seller is None:
        raise UnauthorizedError(detail="Seller not found or deactivated")

    return seller


# Алиас для читаемости в роутерах
CurrentSeller = Annotated[Seller, Depends(get_current_seller)]
