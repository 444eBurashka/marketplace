
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    B2BEventInbox,
    B2BEventType,
    Ticket,
    TicketKind,
    TicketStatus,
    TicketHistoryAction,
    TicketHistory,
)
from shared.errors.http import ConflictError


async def process_b2b_event(
    *,
    event_type: str,
    idempotency_key: uuid.UUID,
    occurred_at: datetime,
    payload: dict,
    db: AsyncSession,
) -> dict:
    """Process an incoming B2B event with idempotency and ticket state transitions."""
    existing = await db.scalar(
        select(B2BEventInbox.id).where(
            B2BEventInbox.idempotency_key == idempotency_key
        )
    )
    if existing is not None:
        raise ConflictError(detail="Duplicate event (idempotency_key already processed)")

    inbox_event = B2BEventInbox(
        idempotency_key=idempotency_key,
        event_type=B2BEventType(event_type),
        occurred_at=occurred_at,
        raw_payload=payload,
        processed_at=datetime.now(UTC),
    )
    db.add(inbox_event)
    await db.flush()

    if event_type == "PRODUCT_CREATED":
        return await _handle_created(payload, db)
    elif event_type == "PRODUCT_EDITED":
        return await _handle_edited(payload, db)
    elif event_type == "PRODUCT_DELETED":
        return await _handle_deleted(payload, db)
    else:
        raise ValueError(f"Unknown event_type: {event_type}")


async def _handle_created(payload: dict, db: AsyncSession) -> dict:
    """PRODUCT_CREATED -> create PENDING ticket."""
    ticket = Ticket(
        product_id=uuid.UUID(payload["product_id"]),
        seller_id=uuid.UUID(payload["seller_id"]),
        category_id=uuid.UUID(payload["category_id"]) if payload.get("category_id") else None,
        kind=TicketKind.CREATE,
        status=TicketStatus.PENDING,
        queue_priority=payload.get("queue_priority", 3),
        json_after=payload.get("json_after"),
        json_before=None,
    )
    db.add(ticket)
    await db.flush()
    db.add(TicketHistory(
        ticket_id=ticket.id,
        action=TicketHistoryAction.CREATED,
        comment="Ticket created from PRODUCT_CREATED event",
    ))
    await db.flush()
    return {"id": str(ticket.id), "product_id": str(ticket.product_id), "status": ticket.status.value}


async def _handle_edited(payload: dict, db: AsyncSession) -> dict:
    """
    PRODUCT_EDITED:
    - Active PENDING/IN_REVIEW ticket -> update json_after
    - Otherwise -> new EDIT ticket in PENDING
    """
    product_id = uuid.UUID(payload["product_id"])
    json_before = payload.get("json_before")
    json_after = payload.get("json_after")

    active_ticket = await db.scalar(
        select(Ticket).where(
            Ticket.product_id == product_id,
            Ticket.status.in_([TicketStatus.PENDING, TicketStatus.IN_REVIEW]),
        ).limit(1)
    )

    if active_ticket is not None:
        active_ticket.json_after = json_after
        if json_before:
            active_ticket.json_before = json_before
        await db.flush()
        db.add(TicketHistory(
            ticket_id=active_ticket.id,
            action=TicketHistoryAction.CREATED,
            comment="Ticket updated from PRODUCT_EDITED event (json_after refreshed)",
        ))
        await db.flush()
        return {"id": str(active_ticket.id), "product_id": str(product_id), "status": active_ticket.status.value}

    ticket = Ticket(
        product_id=product_id,
        seller_id=uuid.UUID(payload["seller_id"]),
        category_id=uuid.UUID(payload["category_id"]) if payload.get("category_id") else None,
        kind=TicketKind.EDIT,
        status=TicketStatus.PENDING,
        queue_priority=payload.get("queue_priority", 3),
        json_before=json_before,
        json_after=json_after,
    )
    db.add(ticket)
    await db.flush()
    db.add(TicketHistory(
        ticket_id=ticket.id,
        action=TicketHistoryAction.CREATED,
        comment="Edit ticket created from PRODUCT_EDITED event",
    ))
    await db.flush()
    return {"id": str(ticket.id), "product_id": str(product_id), "status": ticket.status.value}


async def _handle_deleted(payload: dict, db: AsyncSession) -> dict:
    """PRODUCT_DELETED -> close all PENDING/IN_REVIEW tickets."""
    product_id = uuid.UUID(payload["product_id"])
    result = await db.execute(
        select(Ticket).where(
            Ticket.product_id == product_id,
            Ticket.status.in_([TicketStatus.PENDING, TicketStatus.IN_REVIEW]),
        )
    )
    tickets = result.scalars().all()
    closed_ids = []
    for ticket in tickets:
        ticket.status = TicketStatus.HARD_BLOCKED
        ticket.decision_comment = "Product deleted by seller"
        ticket.decision_at = datetime.now(UTC)
        db.add(TicketHistory(
            ticket_id=ticket.id,
            action=TicketHistoryAction.RELEASED,
            comment="Ticket auto-closed due to PRODUCT_DELETED event",
        ))
        closed_ids.append(str(ticket.id))
    await db.flush()
    return {"closed_tickets": closed_ids, "product_id": str(product_id)}
