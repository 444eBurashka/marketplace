import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import (
    Ticket, TicketStatus, TicketHistoryAction, TicketHistory,
    TicketFieldReport, BlockingReason, TicketBlockingReason,
)
from shared.errors.http import ConflictError, ForbiddenError, NotFoundError


BLOCKED_EVENT_URL = f"{settings.b2b_internal_url}/api/v1/moderation/events"


async def soft_block_ticket(
    ticket_id: uuid.UUID,
    moderator_id: uuid.UUID,
    blocking_reason_ids: list[uuid.UUID],
    comment: str | None,
    field_reports: list[dict[str, Any]],
    db: AsyncSession,
) -> Ticket:
    ticket = await db.get(Ticket, ticket_id)
    if ticket is None:
        raise NotFoundError(detail="Ticket not found")

    if ticket.status != TicketStatus.IN_REVIEW:
        raise ConflictError(detail=f"Ticket is {ticket.status.value}, not IN_REVIEW")

    if ticket.assigned_moderator_id != moderator_id:
        raise ForbiddenError(detail="This ticket is assigned to another moderator")

    # Validate blocking reasons exist and are soft-block compatible
    reasons_result = await db.execute(
        select(BlockingReason).where(BlockingReason.id.in_(blocking_reason_ids))
    )
    reasons = {r.id: r for r in reasons_result.scalars().all()}

    for rid in blocking_reason_ids:
        if rid not in reasons:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Blocking reason not found: {rid}")
        if reasons[rid].hard_block:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Reason '{reasons[rid].code}' is hard-block only, cannot use for soft block",
            )

    # Validate field reports
    for fr in field_reports:
        field_path = fr.get("field_path", "")
        if not field_path or not field_path.strip():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="field_path is required in field_reports")

    # Apply block
    now = datetime.now(UTC)
    ticket.status = TicketStatus.BLOCKED
    ticket.decision_at = now
    ticket.decision_comment = comment or None

    # Link blocking reasons
    for rid in blocking_reason_ids:
        db.add(TicketBlockingReason(ticket_id=ticket.id, blocking_reason_id=rid))

    # Save field reports
    for fr in field_reports:
        db.add(TicketFieldReport(
            ticket_id=ticket.id,
            field_path=fr["field_path"],
            message=fr.get("message", ""),
            severity=fr.get("severity", "ERROR"),
        ))

    # History
    db.add(TicketHistory(
        ticket_id=ticket.id,
        action=TicketHistoryAction.BLOCKED,
        moderator_id=moderator_id,
        comment=comment or "Blocked with notes",
    ))
    await db.flush()
    await db.refresh(ticket)

    # Send BLOCKED event to B2B
    await _send_blocked_to_b2b(ticket, reasons[blocking_reason_ids[0]], field_reports)

    return ticket


async def _send_blocked_to_b2b(
    ticket: Ticket,
    reason: BlockingReason,
    field_reports: list[dict[str, Any]],
) -> None:
    now = datetime.now(UTC)
    payload = {
        "idempotency_key": str(ticket.id),
        "product_id": str(ticket.product_id),
        "event_type": "BLOCKED",
        "hard_block": False,
        "blocking_reason_id": str(reason.id),
        "moderator_comment": ticket.decision_comment,
        "moderator_id": str(ticket.assigned_moderator_id),
        "field_reports": [
            {
                "field_name": fr["field_path"],
                "sku_id": None,
                "comment": fr.get("message", ""),
            }
            for fr in field_reports
        ],
        "occurred_at": now.isoformat(),
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(
            BLOCKED_EVENT_URL,
            json=payload,
            headers={"X-Service-Key": settings.service_key},
            timeout=10.0,
        )
        response.raise_for_status()
