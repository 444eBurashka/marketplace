"""Tests for US-MOD-02: Get next card from queue.

Scenarios (from DoD):
1. next_returns_oldest_pending — oldest PENDING goes to IN_REVIEW, assigned
2. concurrent_two_moderators_get_different_cards — two moderators don't get same card
3. empty_queue_returns_204 — empty queue returns 204
4. moderator_already_has_in_review_returns_409 — second claim rejected
"""

import uuid
import pytest
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update, func

from app.core.config import settings
from app.models import Ticket, TicketKind, TicketStatus, TicketHistory, TicketHistoryAction


def _create_ticket(
    product_id: uuid.UUID | None = None,
    priority: int = 3,
    created_at: datetime | None = None,
) -> Ticket:
    ticket = Ticket(
        product_id=product_id or uuid.uuid4(),
        seller_id=uuid.uuid4(),
        category_id=uuid.uuid4(),
        kind=TicketKind.CREATE,
        status=TicketStatus.PENDING,
        queue_priority=priority,
        json_after={"title": "Test Product", "price": 1000},
    )
    if created_at:
        ticket.created_at = created_at
    return ticket


@pytest.mark.asyncio
async def test_next_returns_oldest_pending(client, db_session, auth_headers, moderator):
    """Happy path: oldest PENDING -> IN_REVIEW, assigned to moderator."""
    now = datetime.now(UTC)
    t1 = _create_ticket(priority=1, created_at=now - timedelta(hours=2))
    t2 = _create_ticket(priority=1, created_at=now - timedelta(hours=1))
    db_session.add_all([t1, t2])
    await db_session.flush()

    response = await client.post("/api/v1/tickets/next", headers=auth_headers)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["id"] == str(t1.id), "Should get oldest ticket"
    assert data["status"] == "IN_REVIEW"
    assert data["assigned_moderator_id"] == str(moderator.id)

    # Verify in DB
    await db_session.refresh(t1)
    assert t1.status == TicketStatus.IN_REVIEW
    assert t1.assigned_moderator_id == moderator.id
    assert t1.claimed_at is not None
    assert t1.claim_expires_at is not None

    # Verify history
    stmt = select(TicketHistory).where(
        TicketHistory.ticket_id == t1.id,
        TicketHistory.action == TicketHistoryAction.CLAIMED,
    )
    history = await db_session.scalar(stmt)
    assert history is not None
    assert history.moderator_id == moderator.id


@pytest.mark.asyncio
async def test_concurrent_two_moderators_get_different_cards(
    client, db_session, auth_headers, moderator,
    second_auth_headers, second_moderator,
):
    """Two moderators don't get the same card."""
    t1 = _create_ticket(priority=2)
    t2 = _create_ticket(priority=2)
    db_session.add_all([t1, t2])
    await db_session.flush()

    # Moderator 1 claims
    r1 = await client.post("/api/v1/tickets/next", headers=auth_headers)
    assert r1.status_code == 200, r1.text
    id1 = r1.json()["id"]

    # Moderator 2 claims — should get a different ticket
    r2 = await client.post("/api/v1/tickets/next", headers=second_auth_headers)
    assert r2.status_code == 200, r2.text
    id2 = r2.json()["id"]

    assert id1 != id2, "Two moderators got the same card!"

    t1_db = await db_session.get(Ticket, uuid.UUID(id1))
    t2_db = await db_session.get(Ticket, uuid.UUID(id2))
    assert t1_db.status == TicketStatus.IN_REVIEW
    assert t2_db.status == TicketStatus.IN_REVIEW
    assert t1_db.assigned_moderator_id == moderator.id
    assert t2_db.assigned_moderator_id == second_moderator.id


@pytest.mark.asyncio
async def test_empty_queue_returns_204(client, db_session, auth_headers):
    """Empty queue returns 204 No Content."""
    response = await client.post("/api/v1/tickets/next", headers=auth_headers)
    assert response.status_code == 204, response.text


@pytest.mark.asyncio
async def test_moderator_already_has_in_review_returns_409(
    client, db_session, auth_headers, moderator,
):
    """Second claim while already IN_REVIEW -> 409."""
    t1 = _create_ticket(priority=2)
    db_session.add(t1)
    await db_session.flush()

    # First claim works
    r1 = await client.post("/api/v1/tickets/next", headers=auth_headers)
    assert r1.status_code == 200, r1.text

    # Second claim rejected even though another ticket exists
    t2 = _create_ticket(priority=2)
    db_session.add(t2)
    await db_session.flush()

    r2 = await client.post("/api/v1/tickets/next", headers=auth_headers)
    assert r2.status_code == 409, r2.text


@pytest.mark.asyncio
async def test_priority_ordering_respected(client, db_session, auth_headers, moderator):
    """Ticket with queue_priority=1 is returned before priority=3."""
    t_low = _create_ticket(priority=3)  # low priority
    t_high = _create_ticket(priority=1)  # high priority
    db_session.add_all([t_low, t_high])
    await db_session.flush()

    # Should get the higher priority ticket first
    response = await client.post("/api/v1/tickets/next", headers=auth_headers)
    assert response.status_code == 200, response.text
    assert response.json()["id"] == str(t_high.id), "Higher priority should come first"
