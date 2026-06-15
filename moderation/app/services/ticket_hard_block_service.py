import uuid
from datetime import UTC, datetime

import httpx
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import (
    Ticket, TicketStatus, TicketHistoryAction, TicketHistory,
    BlockingReason, TicketBlockingReason,
)
from shared.errors.http import ConflictError, ForbiddenError, NotFoundError


HARD_BLOCKED_EVENT_URL = f"{settings.b2b_internal_url}/api/v1/moderation/events"


async def hard_block_ticket(
    ticket_id: uuid.UUID,
    moderator_id: uuid.UUID,
    blocking_reason_ids: list[uuid.UUID],
    comment: str | None,
    db: AsyncSession,
) -> Ticket:
    """Hard block a ticket: IN_REVIEW -> HARD_BLOCKED (terminal) + B2B event.

    Preconditions:
    - Ticket exists (404)
    - Status is IN_REVIEW (409)
    - Assigned to current moderator (403)
    - All blocking_reason_ids exist in DB (400)
    - Every blocking_reason has hard_block=True (400)

    Postconditions:
    - Status set to HARD_BLOCKED (terminal)
    - Blocking reasons linked (many-to-many)
    - decision_at and decision_comment set
    - TicketHistory(HARD_BLOCKED) added
    - BLOCKED event sent to B2B with hard_block=true
    """
    ticket = await db.get(Ticket, ticket_id)
    if ticket is None:
        raise NotFoundError(detail="Ticket not found")

    if ticket.status != TicketStatus.IN_REVIEW:
        raise ConflictError(detail=f"Ticket is {ticket.status.value}, not IN_REVIEW")

    if ticket.assigned_moderator_id != moderator_id:
        raise ForbiddenError(detail="This ticket is assigned to another moderator")

    # Validate blocking reasons exist and are hard-block compatible
    reasons_result = await db.execute(
        select(BlockingReason).where(BlockingReason.id.in_(blocking_reason_ids))
    )
    reasons = {r.id: r for r in reasons_result.scalars().all()}

    for rid in blocking_reason_ids:
        if rid not in reasons:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Blocking reason not found: {rid}")
        if not reasons[rid].hard_block:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Reason '{reasons[rid].code}' is a soft-block reason, cannot use for hard block",
            )

    # Apply hard block
    now = datetime.now(UTC)
    ticket.status = TicketStatus.HARD_BLOCKED
    ticket.decision_at = now
    ticket.decision_comment = comment or None

    # Link blocking reasons
    for rid in blocking_reason_ids:
        db.add(TicketBlockingReason(ticket_id=ticket.id, blocking_reason_id=rid))

    # History
    db.add(TicketHistory(
        ticket_id=ticket.id,
        action=TicketHistoryAction.HARD_BLOCKED,
        moderator_id=moderator_id,
        comment=comment or "Hard blocked",
    ))
    await db.flush()
    await db.refresh(ticket)

    # Send BLOCKED event to B2B
    await _send_hard_blocked_to_b2b(ticket, reasons[blocking_reason_ids[0]])

    return ticket


async def _send_hard_blocked_to_b2b(
    ticket: Ticket,
    reason: BlockingReason,
) -> None:
    """Send BLOCKED + hard_block=true event to B2B service synchronously."""
    now = datetime.now(UTC)
    payload = {
        "idempotency_key": str(ticket.id),
        "product_id": str(ticket.product_id),
        "event_type": "BLOCKED",
        "hard_block": True,
        "blocking_reason_id": str(reason.id),
        "moderator_comment": ticket.decision_comment,
        "moderator_id": str(ticket.assigned_moderator_id),
        "field_reports": [],
        "occurred_at": now.isoformat(),
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(
            HARD_BLOCKED_EVENT_URL,
            json=payload,
            headers={"X-Service-Key": settings.service_key},
            timeout=10.0,
        )
        response.raise_for_status()
