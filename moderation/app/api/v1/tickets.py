from typing import Annotated

from fastapi import APIRouter, Depends, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.dependencies import CurrentModerator
from app.db.session import get_db
from app.models import Ticket
from app.schemas.tickets import TicketDetailResponse
from app.services.ticket_service import get_next_card_for_moderator

router = APIRouter()
DB = Annotated[AsyncSession, Depends(get_db)]


@router.post(
    "/next",
    status_code=200,
    responses={
        200: {"model": TicketDetailResponse, "description": "Next ticket in queue"},
        204: {"description": "Queue is empty"},
        409: {"description": "Moderator already has IN_REVIEW ticket"},
    },
)
async def get_next_ticket(
    moderator: CurrentModerator,
    db: DB,
) -> TicketDetailResponse | None:
    """Get next PENDING ticket from moderation queue."""
    ticket = await get_next_card_for_moderator(
        moderator_id=moderator.id,
        db=db,
    )
    if ticket is None:
        return Response(status_code=204)

    # Eagerly load relationships for serialization
    result = await db.execute(
        select(Ticket)
        .options(
            selectinload(Ticket.field_reports),
            selectinload(Ticket.blocking_reasons),
            selectinload(Ticket.history),
        )
        .where(Ticket.id == ticket.id)
    )
    ticket = result.scalar_one()

    return TicketDetailResponse.model_validate(ticket)
