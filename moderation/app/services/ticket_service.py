import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import Ticket, TicketStatus, TicketHistoryAction, TicketHistory
from shared.errors.http import ConflictError


async def get_next_card_for_moderator(
    moderator_id: uuid.UUID,
    db: AsyncSession,
) -> Ticket | None:
    """Get next PENDING ticket from queue and assign to moderator.

    Uses atomic UPDATE ... RETURNING for race-condition safety:
    - SELECT oldest PENDING ticket
    - Atomic UPDATE with WHERE status=PENDING (optimistic lock)
    - If another request claimed it first, retries with next ticket
    - Works on both PostgreSQL and SQLite

    Returns:
        Ticket if found, None if queue is empty.
    """
    # 1. Check for existing IN_REVIEW
    existing = await db.scalar(
        select(Ticket.id).where(
            Ticket.assigned_moderator_id == moderator_id,
            Ticket.status == TicketStatus.IN_REVIEW,
        ).limit(1)
    )
    if existing is not None:
        raise ConflictError(detail="Moderator already has an IN_REVIEW ticket")

    # 2. Auto-return expired tickets
    now = datetime.now(UTC)
    expired = await db.execute(
        select(Ticket).where(
            Ticket.status == TicketStatus.IN_REVIEW,
            Ticket.claim_expires_at < now,
        )
    )
    for ticket in expired.scalars().all():
        ticket.status = TicketStatus.PENDING
        ticket.assigned_moderator_id = None
        ticket.claimed_at = None
        ticket.claim_expires_at = None
        db.add(TicketHistory(
            ticket_id=ticket.id,
            action=TicketHistoryAction.AUTO_RETURNED,
            comment=f"Auto-returned after timeout ({settings.claim_timeout_minutes} min)",
        ))
    await db.flush()

    # 3. Atomic claim: UPDATE ... RETURNING with optimistic lock
    claim_expires_at = now + timedelta(minutes=settings.claim_timeout_minutes)

    for _ in range(3):  # retry loop for race conditions
        result = await db.execute(
            select(Ticket.id)
            .where(Ticket.status == TicketStatus.PENDING)
            .order_by(Ticket.queue_priority.asc(), Ticket.created_at.asc())
            .limit(1)
        )
        candidate_id = result.scalar_one_or_none()
        if candidate_id is None:
            return None  # queue is empty

        result = await db.execute(
            update(Ticket)
            .where(Ticket.id == candidate_id)
            .where(Ticket.status == TicketStatus.PENDING)  # optimistic lock
            .values(
                status=TicketStatus.IN_REVIEW,
                assigned_moderator_id=moderator_id,
                claimed_at=now,
                claim_expires_at=claim_expires_at,
            )
            .returning(Ticket)
        )
        ticket = result.scalar_one_or_none()
        if ticket is not None:
            break  # successfully claimed
        # Another moderator claimed it first — retry with next ticket
    else:
        return None  # exhausted retries (unlikely)

    # 4. History
    db.add(TicketHistory(
        ticket_id=ticket.id,
        action=TicketHistoryAction.CLAIMED,
        moderator_id=moderator_id,
        comment="Ticket claimed from queue",
    ))
    await db.flush()

    return ticket
