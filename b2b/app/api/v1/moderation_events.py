from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_db
from app.schemas.moderation_events import ModerationEventRequest
from app.services.moderation_events import process_moderation_event

router = APIRouter()

DB = Annotated[AsyncSession, Depends(get_db)]


def _verify_service_key(x_service_key: str | None = Header(default=None)) -> None:
    """Проверяет X-Service-Key — только Moderation-сервис может вызывать этот endpoint."""
    if x_service_key != settings.service_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "Invalid or missing service key"},
        )


ServiceKeyDep = Annotated[None, Depends(_verify_service_key)]


@router.post(
    "",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def receive_moderation_event(
    body: ModerationEventRequest,
    _: ServiceKeyDep,
    db: DB,
) -> None:
    """
    Приём событий от Moderation Service: MODERATED, BLOCKED (с hard_block flag).
    Идемпотентность через idempotency_key.
    BLOCKED → отправляет PRODUCT_BLOCKED в B2C.
    """
    try:
        await process_moderation_event(body, db)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": str(exc)},
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "VALIDATION_ERROR", "message": str(exc)},
        )