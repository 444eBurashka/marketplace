import uuid
from typing import Annotated

from fastapi import Depends, Header
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_db
from app.models import Moderator
from shared.auth.jwt import decode_token
from shared.errors.http import ForbiddenError, UnauthorizedError

bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_moderator(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Moderator:
    """Require valid JWT for any active moderator."""
    if credentials is None:
        raise UnauthorizedError()

    payload = decode_token(credentials.credentials, settings.secret_key)
    moderator_id = payload.get("sub")
    if moderator_id is None:
        raise UnauthorizedError()

    result = await db.execute(
        select(Moderator).where(
            Moderator.id == uuid.UUID(moderator_id),
            Moderator.is_active == True,
        )
    )
    moderator = result.scalar_one_or_none()
    if moderator is None:
        raise UnauthorizedError(detail="Moderator not found or deactivated")
    return moderator


CurrentModerator = Annotated[Moderator, Depends(get_current_moderator)]


async def require_admin(moderator: CurrentModerator) -> Moderator:
    if moderator.role != "ADMIN":
        raise ForbiddenError(detail="Admin role required")
    return moderator


AdminOnly = Annotated[Moderator, Depends(require_admin)]


async def verify_service_key(
    x_service_key: str | None = Header(default=None),
) -> bool:
    if x_service_key is None or x_service_key != settings.service_key:
        raise ForbiddenError(detail="Invalid service key")
    return True


ServiceKeyVerified = Annotated[bool, Depends(verify_service_key)]