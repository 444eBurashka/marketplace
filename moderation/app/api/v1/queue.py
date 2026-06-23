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


async def _load_ticket_for_response(ticket, db: AsyncSession):
    """Reload ticket with eager-loaded relationships for serialization."""
    result = await db.execute(
        select(Ticket)
        .options(
            selectinload(Ticket.field_reports),
            selectinload(Ticket.blocking_reasons),
            selectinload(Ticket.history),
        )
        .where(Ticket.id == ticket.id)
    )
    return TicketDetailResponse.model_validate(result.scalar_one())


@router.post(
    "/claim",
    status_code=200,
    responses={
        200: {"model": TicketDetailResponse, "description": "Next ticket in queue"},
        204: {"description": "Queue is empty"},
        409: {"description": "Moderator already has IN_REVIEW ticket"},
    },
)
async def claim_next_ticket(
    moderator: CurrentModerator,
    db: DB,
) -> TicketDetailResponse | None:
    """Claim next PENDING ticket from moderation queue (FOR UPDATE SKIP LOCKED)."""
    ticket = await get_next_card_for_moderator(
        moderator_id=moderator.id,
        db=db,
    )
    if ticket is None:
        return Response(status_code=204)

    return await _load_ticket_for_response(ticket, db)
