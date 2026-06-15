import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from pydantic import BaseModel, Field

from app.core.dependencies import CurrentModerator
from app.db.session import get_db
from app.models import Ticket, TicketFieldReport, BlockingReason, TicketHistory
from app.models import TicketBlockingReason
from app.schemas.tickets import TicketDetailResponse, BlockDecisionRequest
from app.services.ticket_service import get_next_card_for_moderator
from app.services.ticket_approve_service import approve_ticket
from app.services.ticket_block_service import soft_block_ticket


class ApproveRequest(BaseModel):
    comment: str | None = Field(default=None, max_length=2000)


router = APIRouter()
DB = Annotated[AsyncSession, Depends(get_db)]


async def _load_ticket_for_response(ticket: Ticket, db: AsyncSession) -> TicketDetailResponse:
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

    return await _load_ticket_for_response(ticket, db)


@router.post(
    "/{ticket_id}/approve",
    status_code=200,
    responses={
        200: {"model": TicketDetailResponse},
        403: {"description": "Not your ticket"},
        404: {"description": "Ticket not found"},
        409: {"description": "Invalid state (not IN_REVIEW, edited, no SKU)"},
    },
)
async def approve_ticket_endpoint(
    ticket_id: str,
    body: ApproveRequest,
    moderator: CurrentModerator,
    db: DB,
) -> TicketDetailResponse:
    """Approve a ticket: IN_REVIEW -> APPROVED + send MODERATED event to B2B."""
    ticket = await approve_ticket(
        ticket_id=uuid.UUID(ticket_id),
        moderator_id=moderator.id,
        comment=body.comment,
        db=db,
    )
    return await _load_ticket_for_response(ticket, db)


@router.post(
    "/{ticket_id}/block",
    status_code=200,
    responses={
        200: {"model": TicketDetailResponse},
        400: {"description": "Invalid blocking reason or field reports"},
        403: {"description": "Not your ticket"},
        404: {"description": "Ticket not found"},
        409: {"description": "Invalid state (not IN_REVIEW)"},
    },
)
async def block_ticket_endpoint(
    ticket_id: str,
    body: BlockDecisionRequest,
    moderator: CurrentModerator,
    db: DB,
) -> TicketDetailResponse:
    """Soft block a ticket: IN_REVIEW -> BLOCKED + field reports + B2B event."""
    ticket = await soft_block_ticket(
        ticket_id=uuid.UUID(ticket_id),
        moderator_id=moderator.id,
        blocking_reason_ids=body.blocking_reason_ids,
        comment=body.comment,
        field_reports=[fr.model_dump() for fr in body.field_reports],
        db=db,
    )
    # Explicitly load relationships for response (selectinload may skip identity-map objects)
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
