import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import Ticket, TicketStatus, TicketHistoryAction, TicketHistory
from shared.errors.http import ConflictError


async def get_next_card_for_moderator(
    moderator_id: uuid.UUID,
    db: AsyncSession,
) -> Ticket | None:
    """Get next PENDING ticket from queue and assign to moderator.

    Uses SELECT ... FOR UPDATE SKIP LOCKED for race-condition safety:
    - Skips tickets already locked by concurrent transactions
    - Atomic claim inside the active request transaction
    - Target DB: PostgreSQL (SKIP LOCKED); on SQLite the lock is a no-op.

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

    # 3. Claim next ticket with FOR UPDATE SKIP LOCKED inside active transaction
    claim_expires_at = now + timedelta(minutes=settings.claim_timeout_minutes)

    result = await db.execute(
        select(Ticket)
        .where(Ticket.status == TicketStatus.PENDING)
        .order_by(Ticket.queue_priority.asc(), Ticket.created_at.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    ticket = result.scalar_one_or_none()
    if ticket is None:
        return None  # queue is empty

    # Assign (we hold the lock — no race possible)
    ticket.status = TicketStatus.IN_REVIEW
    ticket.assigned_moderator_id = moderator_id
    ticket.claimed_at = now
    ticket.claim_expires_at = claim_expires_at
    await db.flush()

    # 4. History
    db.add(TicketHistory(
        ticket_id=ticket.id,
        action=TicketHistoryAction.CLAIMED,
        moderator_id=moderator_id,
        comment="Ticket claimed from queue",
    ))
    await db.flush()

    return ticket
