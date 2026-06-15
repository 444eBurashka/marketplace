
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_db
from app.schemas.b2b_events import IncomingB2BEvent
from app.services.b2b_event_service import process_b2b_event
from shared.errors.http import ConflictError, ForbiddenError
from shared.service_auth import verify_service_key

router = APIRouter()
DB = Annotated[AsyncSession, Depends(get_db)]


@router.post("", status_code=202)
async def receive_b2b_event(
    body: IncomingB2BEvent,
    db: DB,
    _: None = Depends(verify_service_key(settings.service_key)),
) -> dict:
    """
    Receive a product event from B2B.

    Supported event types:
    - PRODUCT_CREATED -> creates a PENDING moderation ticket
    - PRODUCT_EDITED -> creates a new ticket or updates existing
    - PRODUCT_DELETED -> closes open tickets for the product

    Idempotency: key-based via idempotency_key (409 on duplicate).
    Auth: X-Service-Key header (not JWT).
    """
    try:
        result = await process_b2b_event(
            event_type=body.event_type,
            idempotency_key=body.idempotency_key,
            occurred_at=body.occurred_at,
            payload=body.payload,
            db=db,
        )
        return result
    except ConflictError:
        raise ConflictError(detail="Duplicate event (idempotency_key already processed)")
