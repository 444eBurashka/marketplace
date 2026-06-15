"""Tests for US-MOD-03: Approve ticket by moderator.

Scenarios (from DoD):
1. approve_transitions_to_moderated_and_emits_event — happy path
2. approve_others_card_returns_403 — not your ticket
3. approve_after_edited_returns_409 — edited during review
4. approve_without_sku_returns_409 — no SKU
"""

import uuid
import pytest
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

from sqlalchemy import select, update

from app.models import (
    Ticket, TicketKind, TicketStatus, TicketHistoryAction,
)


def _create_ticket(priority: int = 3, sku: str | None = "TEST-SKU-001") -> Ticket:
    return Ticket(
        product_id=uuid.uuid4(),
        seller_id=uuid.uuid4(),
        category_id=uuid.uuid4(),
        kind=TicketKind.CREATE,
        status=TicketStatus.PENDING,
        queue_priority=priority,
        json_after={"title": "Test Product", "price": 1000, "sku": sku} if sku
        else {"title": "Test Product", "price": 1000},
    )


def _mock_b2b_post():
    """Patch httpx.AsyncClient.post to avoid real HTTP calls to B2B."""
    return patch(
        "app.services.ticket_approve_service.httpx.AsyncClient",
        new_callable=lambda: lambda: AsyncMock(
            __aenter__=AsyncMock(return_value=AsyncMock(
                post=AsyncMock(return_value=AsyncMock(status_code=204, raise_for_status=lambda: None))
            ))
        )
    )


@pytest.mark.asyncio
async def test_approve_transitions_to_moderated_and_emits_event(
    client, db_session, auth_headers, moderator,
):
    """Happy path: IN_REVIEW -> APPROVED + MODERATED event sent to B2B."""
    ticket = _create_ticket(sku="VALID-SKU-999")
    db_session.add(ticket)
    await db_session.flush()

    # Claim the ticket
    r1 = await client.post("/api/v1/tickets/next", headers=auth_headers)
    assert r1.status_code == 200, r1.text
    ticket_id = r1.json()["id"]

    with _mock_b2b_post() as mock_client:
        r2 = await client.post(
            f"/api/v1/tickets/{ticket_id}/approve",
            json={"comment": "All good!"},
            headers=auth_headers,
        )
    assert r2.status_code == 200, r2.text
    data = r2.json()
    assert data["status"] == "APPROVED"
    assert data["decision_comment"] == "All good!"
    assert data["assigned_moderator_id"] == str(moderator.id)

    # Verify in DB
    await db_session.refresh(ticket)
    assert ticket.status == TicketStatus.APPROVED
    assert ticket.decision_at is not None
    assert ticket.decision_comment == "All good!"

    # Verify history
    from app.models import TicketHistory
    stmt = select(TicketHistory).where(
        TicketHistory.ticket_id == ticket.id,
        TicketHistory.action == TicketHistoryAction.APPROVED,
    )
    history = await db_session.scalar(stmt)
    assert history is not None
    assert history.moderator_id == moderator.id


@pytest.mark.asyncio
async def test_approve_others_card_returns_403(
    client, db_session, auth_headers, moderator,
    second_auth_headers, second_moderator,
):
    """Approve another moderator's card -> 403."""
    ticket = _create_ticket()
    db_session.add(ticket)
    await db_session.flush()

    # Moderator 1 claims
    r1 = await client.post("/api/v1/tickets/next", headers=auth_headers)
    assert r1.status_code == 200
    ticket_id = r1.json()["id"]

    # Moderator 2 tries to approve — 403
    with _mock_b2b_post():
        r2 = await client.post(
            f"/api/v1/tickets/{ticket_id}/approve",
            json={},
            headers=second_auth_headers,
        )
    assert r2.status_code == 403, r2.text
    # Ticket should still be IN_REVIEW
    await db_session.refresh(ticket)
    assert ticket.status == TicketStatus.IN_REVIEW


@pytest.mark.asyncio
async def test_approve_after_edited_returns_409(
    client, db_session, auth_headers, moderator,
):
    """Product edited during review -> approve returns 409."""
    ticket = _create_ticket()
    db_session.add(ticket)
    await db_session.flush()

    # Claim
    r1 = await client.post("/api/v1/tickets/next", headers=auth_headers)
    assert r1.status_code == 200
    ticket_id = r1.json()["id"]

    # Simulate edit: update json_after and updated_at (as B2B event would do)
    from sqlalchemy import func
    new_time = datetime.now(UTC) + timedelta(minutes=5)
    stmt = (
        update(Ticket.__table__)
        .where(Ticket.__table__.c.id == ticket.id)
        .values(
            json_after={"title": "Edited", "price": 9999, "sku": "TEST-SKU-001"},
            updated_at=new_time,
        )
    )
    await db_session.execute(stmt)
    await db_session.refresh(ticket)

    # Try to approve — 409
    with _mock_b2b_post():
        r2 = await client.post(
            f"/api/v1/tickets/{ticket_id}/approve",
            json={},
            headers=auth_headers,
        )
    assert r2.status_code == 409, r2.text
    assert "edited" in r2.json()["message"].lower()


@pytest.mark.asyncio
async def test_approve_without_sku_returns_409(
    client, db_session, auth_headers, moderator,
):
    """Product without SKU -> approve returns 409."""
    ticket = _create_ticket(sku=None)
    db_session.add(ticket)
    await db_session.flush()

    r1 = await client.post("/api/v1/tickets/next", headers=auth_headers)
    assert r1.status_code == 200
    ticket_id = r1.json()["id"]

    with _mock_b2b_post():
        r2 = await client.post(
            f"/api/v1/tickets/{ticket_id}/approve",
            json={},
            headers=auth_headers,
        )
    assert r2.status_code == 409, r2.text
    assert "SKU" in r2.json()["message"]
