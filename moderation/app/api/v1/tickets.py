import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from pydantic import BaseModel, Field

from app.core.dependencies import CurrentModerator
from app.db.session import get_db
from app.models import Ticket, TicketStatus, BlockingReason
from app.schemas.tickets import TicketDetailResponse, BlockDecisionRequest
from app.services.ticket_approve_service import approve_ticket
from app.services.ticket_block_service import soft_block_ticket
from app.services.ticket_hard_block_service import hard_block_ticket
from shared.errors.http import ForbiddenError, NotFoundError


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


async def _check_ticket_not_terminal(ticket_id: uuid.UUID, db: AsyncSession) -> Ticket:
    """Check if a ticket is HARD_BLOCKED (terminal) -> returns 403. Otherwise returns the ticket."""
    ticket = await db.get(Ticket, ticket_id)
    if ticket is None:
        raise NotFoundError(detail="Ticket not found")
    if ticket.status == TicketStatus.HARD_BLOCKED:
        raise ForbiddenError(detail="Ticket is HARD_BLOCKED and cannot be modified")
    return ticket


@router.post(
    "/{ticket_id}/approve",
    status_code=200,
    responses={
        200: {"model": TicketDetailResponse},
        403: {"description": "Not your ticket or HARD_BLOCKED terminal"},
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
    ticket_id_uuid = uuid.UUID(ticket_id)
    # Terminal guard: HARD_BLOCKED tickets cannot be approved
    await _check_ticket_not_terminal(ticket_id_uuid, db)
    ticket = await approve_ticket(
        ticket_id=ticket_id_uuid,
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
        403: {"description": "Not your ticket or HARD_BLOCKED terminal"},
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
    ticket_id_uuid = uuid.UUID(ticket_id)
    # Terminal guard: HARD_BLOCKED tickets cannot be soft blocked
    await _check_ticket_not_terminal(ticket_id_uuid, db)
    ticket = await soft_block_ticket(
        ticket_id=ticket_id_uuid,
        moderator_id=moderator.id,
        blocking_reason_ids=body.blocking_reason_ids,
        comment=body.comment,
        field_reports=[fr.model_dump() for fr in body.field_reports],
        db=db,
    )
    return await _load_ticket_for_response(ticket, db)


@router.post(
    "/{ticket_id}/decline",
    status_code=200,
    responses={
        200: {"model": TicketDetailResponse},
        400: {"description": "Invalid blocking reason (mixed soft+hard, or unknown)"},
        403: {"description": "Not your ticket or HARD_BLOCKED terminal"},
        404: {"description": "Ticket not found"},
        409: {"description": "Invalid state (not IN_REVIEW)"},
    },
)
async def decline_ticket_endpoint(
    ticket_id: str,
    body: BlockDecisionRequest,
    moderator: CurrentModerator,
    db: DB,
) -> TicketDetailResponse:
    """Decline a ticket: routes to soft or hard block based on reason flags.

    - If ANY blocking_reason has hard_block=True -> hard block (HARD_BLOCKED, terminal)
    - If ALL blocking reasons are soft -> soft block (BLOCKED)
    - Mixing soft+hard reasons returns 400.
    """
    ticket_id_uuid = uuid.UUID(ticket_id)
    # Terminal guard
    await _check_ticket_not_terminal(ticket_id_uuid, db)

    # Load blocking reasons to determine route
    reasons_result = await db.execute(
        select(BlockingReason).where(BlockingReason.id.in_(body.blocking_reason_ids))
    )
    reasons = reasons_result.scalars().all()

    has_hard = any(r.hard_block for r in reasons)
    all_hard = all(r.hard_block for r in reasons)

    if has_hard and not all_hard:
        from fastapi import HTTPException, status
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot mix soft-block and hard-block blocking reasons",
        )

    if has_hard:
        # All reasons are hard-block -> hard block
        ticket = await hard_block_ticket(
            ticket_id=ticket_id_uuid,
            moderator_id=moderator.id,
            blocking_reason_ids=body.blocking_reason_ids,
            comment=body.comment,
            db=db,
        )
    else:
        # All reasons are soft-block -> soft block
        ticket = await soft_block_ticket(
            ticket_id=ticket_id_uuid,
            moderator_id=moderator.id,
            blocking_reason_ids=body.blocking_reason_ids,
            comment=body.comment,
            field_reports=[fr.model_dump() for fr in body.field_reports],
            db=db,
        )

    return await _load_ticket_for_response(ticket, db)
