import uuid
from typing import Annotated

from fastapi import Depends, Header
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_db
from app.models import Buyer
from shared.errors.http import UnauthorizedError
from shared.jwt import decode_token

bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_buyer(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Buyer:
    """Требует авторизованного покупателя."""
    if credentials is None:
        raise UnauthorizedError()

    payload = decode_token(credentials.credentials, settings.secret_key)
    buyer_id = payload.get("sub")
    if buyer_id is None:
        raise UnauthorizedError()

    result = await db.execute(
        select(Buyer).where(
            Buyer.id == uuid.UUID(buyer_id),
            Buyer.is_active == True,  # noqa: E712
            Buyer.deleted_at.is_(None),
        )
    )
    buyer = result.scalar_one_or_none()
    if buyer is None:
        raise UnauthorizedError(detail="Buyer not found or deactivated")

    return buyer


async def get_optional_buyer(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Buyer | None:
    """Для эндпоинтов, доступных гостям И авторизованным (корзина, каталог)."""
    if credentials is None:
        return None
    try:
        return await get_current_buyer(credentials, db)
    except UnauthorizedError:
        return None


CurrentBuyer = Annotated[Buyer, Depends(get_current_buyer)]
OptionalBuyer = Annotated[Buyer | None, Depends(get_optional_buyer)]
