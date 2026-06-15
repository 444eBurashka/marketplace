import uuid
from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import Ticket, TicketStatus, TicketHistoryAction, TicketHistory
from shared.errors.http import ConflictError, ForbiddenError, NotFoundError


MODERATED_EVENT_URL = f"{settings.b2b_internal_url}/api/v1/moderation/events"


async def approve_ticket(
    ticket_id: uuid.UUID,
    moderator_id: uuid.UUID,
    comment: str | None,
    db: AsyncSession,
) -> Ticket:
    """Approve a ticket: validations + status change + B2B event.

    Preconditions:
    - Ticket exists (404)
    - Status is IN_REVIEW (409)
    - Assigned to current moderator (403)
    - Not edited after claim (409)
    - Has SKU in json_after (409)

    Postconditions:
    - Status set to APPROVED
    - decision_at and decision_comment set
    - TicketHistory(APPROVED) added
    - MODERATED event sent to B2B
    """
    ticket = await db.get(Ticket, ticket_id)
    if ticket is None:
        raise NotFoundError(detail="Ticket not found")

    if ticket.status != TicketStatus.IN_REVIEW:
        raise ConflictError(detail=f"Ticket is {ticket.status.value}, not IN_REVIEW")

    if ticket.assigned_moderator_id != moderator_id:
        raise ForbiddenError(detail="This ticket is assigned to another moderator")

    # Check if product was edited after claim
    if ticket.updated_at and ticket.claimed_at and ticket.updated_at > ticket.claimed_at:
        raise ConflictError(detail="Product was edited during review, please re-claim the ticket")

    # Check SKU presence
    sku = (ticket.json_after or {}).get("sku")
    if not sku:
        raise ConflictError(detail="Product has no SKU, cannot approve")

    # Approve
    now = datetime.now(UTC)
    ticket.status = TicketStatus.APPROVED
    ticket.decision_at = now
    ticket.decision_comment = comment or None

    db.add(TicketHistory(
        ticket_id=ticket.id,
        action=TicketHistoryAction.APPROVED,
        moderator_id=moderator_id,
        comment=comment or "Approved",
    ))
    await db.flush()

    # Send MODERATED event to B2B
    await _send_moderated_to_b2b(ticket)

    return ticket


async def _send_moderated_to_b2b(ticket: Ticket) -> None:
    """Send MODERATED event to B2B service synchronously."""
    now = datetime.now(UTC)
    payload = {
        "idempotency_key": str(ticket.id),
        "product_id": str(ticket.product_id),
        "event_type": "MODERATED",
        "moderator_id": str(ticket.assigned_moderator_id),
        "moderator_comment": ticket.decision_comment,
        "hard_block": False,
        "field_reports": [],
        "occurred_at": now.isoformat(),
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(
            MODERATED_EVENT_URL,
            json=payload,
            headers={"X-Service-Key": settings.service_key},
            timeout=10.0,
        )
        response.raise_for_status()
