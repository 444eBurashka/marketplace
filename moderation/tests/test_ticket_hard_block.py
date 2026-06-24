"""Tests for US-MOD-05: Hard block (irreversible).

Scenarios (from DoD):
1. hard_block_transitions_to_terminal_and_emits_event - happy path
2. hard_block_event_carries_hard_block_true - hard_block=true in B2B event
3. any_modify_on_hard_blocked_returns_403 - no modification on terminal
4. edited_event_on_hard_blocked_is_ignored - B2B EDITED ignored
5. deleted_event_removes_hard_blocked - B2B DELETED no error
6. decline_mixed_soft_hard_reasons_returns_400 - mixed reasons rejected
"""

import uuid
import pytest
from unittest.mock import AsyncMock, patch
from datetime import UTC, datetime

from sqlalchemy import select

from app.models import (
    Ticket, TicketKind, TicketStatus, BlockingReason, TicketHistory, TicketHistoryAction,
)
from app.core.config import settings


def _create_ticket(priority=3) -> Ticket:
    return Ticket(
        product_id=uuid.uuid4(),
        seller_id=uuid.uuid4(),
        category_id=uuid.uuid4(),
        kind=TicketKind.CREATE,
        status=TicketStatus.PENDING,
        queue_priority=priority,
        json_after={"title": "Test Product", "price": 1000},
    )


def _create_hard_reason(code="counterfeit") -> BlockingReason:
    return BlockingReason(
        code=code,
        title="Counterfeit",
        description="Product is counterfeit",
        hard_block=True,
        is_active=True,
    )


def _create_soft_reason(code="inappropriate_content") -> BlockingReason:
    return BlockingReason(
        code=code,
        title="Inappropriate Content",
        hard_block=False,
        is_active=True,
    )


def _mock_hard_b2b_post():
    return patch(
        "app.services.ticket_hard_block_service.httpx.AsyncClient",
        new_callable=lambda: lambda: AsyncMock(
            __aenter__=AsyncMock(return_value=AsyncMock(
                post=AsyncMock(return_value=AsyncMock(
                    status_code=204, raise_for_status=lambda: None,
                )),
            )),
        ),
    )


def _mock_soft_b2b_post():
    return patch(
        "app.services.ticket_block_service.httpx.AsyncClient",
        new_callable=lambda: lambda: AsyncMock(
            __aenter__=AsyncMock(return_value=AsyncMock(
                post=AsyncMock(return_value=AsyncMock(
                    status_code=204, raise_for_status=lambda: None,
                )),
            )),
        ),
    )


def _mock_approve_b2b_post():
    return patch(
        "app.services.ticket_approve_service.httpx.AsyncClient",
        new_callable=lambda: lambda: AsyncMock(
            __aenter__=AsyncMock(return_value=AsyncMock(
                post=AsyncMock(return_value=AsyncMock(
                    status_code=204, raise_for_status=lambda: None,
                )),
            )),
        ),
    )


# =============================================================================
# Tests
# =============================================================================


@pytest.mark.asyncio
async def test_hard_block_transitions_to_terminal_and_emits_event(
    client, db_session, auth_headers, moderator,
):
    """Happy path: IN_REVIEW -> HARD_BLOCKED (terminal) + B2B event."""
    reason = _create_hard_reason()
    db_session.add(reason)
    await db_session.flush()

    ticket = _create_ticket()
    db_session.add(ticket)
    await db_session.flush()

    r1 = await client.post("/api/v1/queue/claim", headers=auth_headers)
    assert r1.status_code == 200, r1.text
    ticket_id = r1.json()["id"]

    with _mock_hard_b2b_post():
        r2 = await client.post(
            f"/api/v1/tickets/{ticket_id}/block",
            json={
                "blocking_reason_ids": [str(reason.id)],
                "comment": "Counterfeit product",
            },
            headers=auth_headers,
        )

    assert r2.status_code == 200, r2.text
    data = r2.json()
    assert data["status"] == "HARD_BLOCKED"
    assert data["decision_comment"] == "Counterfeit product"

    # Verify blocking reasons in response
    assert len(data["blocking_reasons"]) == 1
    assert data["blocking_reasons"][0]["code"] == "counterfeit"
    assert data["blocking_reasons"][0]["hard_block"] is True

    # Verify in DB
    await db_session.refresh(ticket)
    assert ticket.status == TicketStatus.HARD_BLOCKED
    assert ticket.decision_at is not None

    # Verify history
    stmt = select(TicketHistory).where(
        TicketHistory.ticket_id == ticket.id,
        TicketHistory.action == TicketHistoryAction.HARD_BLOCKED,
    )
    history = await db_session.scalar(stmt)
    assert history is not None
    assert history.moderator_id == moderator.id


@pytest.mark.asyncio
async def test_hard_block_event_carries_hard_block_true(
    client, db_session, auth_headers, moderator,
):
    """Verify BLOCKED event to B2B has hard_block=True."""
    reason = _create_hard_reason()
    db_session.add(reason)
    await db_session.flush()

    ticket = _create_ticket()
    db_session.add(ticket)
    await db_session.flush()

    r1 = await client.post("/api/v1/queue/claim", headers=auth_headers)
    assert r1.status_code == 200
    ticket_id = r1.json()["id"]

    mock_post = AsyncMock(return_value=AsyncMock(
        status_code=204, raise_for_status=lambda: None,
    ))
    mock_client = AsyncMock(
        __aenter__=AsyncMock(return_value=AsyncMock(post=mock_post)),
    )

    with patch(
        "app.services.ticket_hard_block_service.httpx.AsyncClient",
        return_value=mock_client,
    ):
        r2 = await client.post(
            f"/api/v1/tickets/{ticket_id}/block",
            json={
                "blocking_reason_ids": [str(reason.id)],
                "comment": "Hard blocked",
            },
            headers=auth_headers,
        )

    assert r2.status_code == 200, r2.text

    mock_post.assert_awaited_once()
    call_args = mock_post.call_args
    payload = call_args.kwargs["json"]
    assert payload["event_type"] == "BLOCKED"
    assert payload["hard_block"] is True
    assert payload["product_id"] == str(ticket.product_id)
    assert payload["blocking_reason_id"] == str(reason.id)
    assert payload["moderator_id"] == str(moderator.id)
    assert payload["field_reports"] == []


@pytest.mark.asyncio
async def test_any_modify_on_hard_blocked_returns_403(
    client, db_session, auth_headers, moderator,
):
    """Approve, block, or decline on HARD_BLOCKED -> 403."""
    reason = _create_hard_reason()
    db_session.add(reason)
    await db_session.flush()

    ticket = _create_ticket()
    db_session.add(ticket)
    await db_session.flush()

    r1 = await client.post("/api/v1/queue/claim", headers=auth_headers)
    assert r1.status_code == 200
    ticket_id = r1.json()["id"]

    # Hard block the ticket first
    with _mock_hard_b2b_post():
        r2 = await client.post(
            f"/api/v1/tickets/{ticket_id}/block",
            json={"blocking_reason_ids": [str(reason.id)]},
            headers=auth_headers,
        )
    assert r2.status_code == 200
    assert r2.json()["status"] == "HARD_BLOCKED"

    # Try approve -> 403
    with _mock_approve_b2b_post():
        r3 = await client.post(
            f"/api/v1/tickets/{ticket_id}/approve",
            json={},
            headers=auth_headers,
        )
    assert r3.status_code == 403, r3.text

    # Try soft block -> 403
    soft_reason = _create_soft_reason()
    db_session.add(soft_reason)
    await db_session.flush()
    with _mock_soft_b2b_post():
        r4 = await client.post(
            f"/api/v1/tickets/{ticket_id}/block",
            json={"blocking_reason_ids": [str(soft_reason.id)]},
            headers=auth_headers,
        )
    assert r4.status_code == 403, r4.text

    # Try decline again -> 403
    with _mock_hard_b2b_post():
        r5 = await client.post(
            f"/api/v1/tickets/{ticket_id}/block",
            json={"blocking_reason_ids": [str(reason.id)]},
            headers=auth_headers,
        )
    assert r5.status_code == 403, r5.text

    # Verify status remains HARD_BLOCKED
    await db_session.refresh(ticket)
    assert ticket.status == TicketStatus.HARD_BLOCKED


@pytest.mark.asyncio
async def test_edited_event_on_hard_blocked_is_ignored(
    client, db_session, auth_headers, moderator,
):
    """PRODUCT_EDITED event on HARD_BLOCKED product -> silently ignored."""
    reason = _create_hard_reason()
    db_session.add(reason)
    await db_session.flush()

    ticket = _create_ticket()
    db_session.add(ticket)
    await db_session.flush()
    product_id = ticket.product_id
    seller_id = ticket.seller_id
    category_id = ticket.category_id

    # Claim and hard block
    r1 = await client.post("/api/v1/queue/claim", headers=auth_headers)
    assert r1.status_code == 200
    ticket_id = r1.json()["id"]

    with _mock_hard_b2b_post():
        r2 = await client.post(
            f"/api/v1/tickets/{ticket_id}/block",
            json={"blocking_reason_ids": [str(reason.id)]},
            headers=auth_headers,
        )
    assert r2.status_code == 200
    assert r2.json()["status"] == "HARD_BLOCKED"

    # Send PRODUCT_EDITED event for same product
    body = {
        "event_type": "PRODUCT_EDITED",
        "idempotency_key": str(uuid.uuid4()),
        "occurred_at": datetime.now(UTC).isoformat(),
        "payload": {
            "product_id": str(product_id),
            "seller_id": str(seller_id),
            "category_id": str(category_id),
            "queue_priority": 3,
            "json_before": {"title": "Old", "price": 500},
            "json_after": {"title": "New", "price": 2000},
        },
    }
    r3 = await client.post(
        "/api/v1/b2b/events",
        json=body,
        headers={"X-Service-Key": settings.service_key},
    )
    assert r3.status_code == 202, r3.text
    data = r3.json()
    assert data["status"] == "ignored", "EDITED should be silently ignored"

    # Verify no new ticket was created for this product
    stmt = select(Ticket).where(
        Ticket.product_id == product_id,
        Ticket.status != TicketStatus.HARD_BLOCKED,
    )
    other_tickets = (await db_session.execute(stmt)).scalars().all()
    assert len(other_tickets) == 0, "No new tickets should be created"

    # Original ticket remains HARD_BLOCKED
    await db_session.refresh(ticket)
    assert ticket.status == TicketStatus.HARD_BLOCKED


@pytest.mark.asyncio
async def test_deleted_event_removes_hard_blocked(
    client, db_session, auth_headers, moderator,
):
    """PRODUCT_DELETED event on HARD_BLOCKED product -> no error, no change."""
    reason = _create_hard_reason()
    db_session.add(reason)
    await db_session.flush()

    ticket = _create_ticket()
    db_session.add(ticket)
    await db_session.flush()
    product_id = ticket.product_id

    # Claim and hard block
    r1 = await client.post("/api/v1/queue/claim", headers=auth_headers)
    assert r1.status_code == 200
    ticket_id = r1.json()["id"]

    with _mock_hard_b2b_post():
        r2 = await client.post(
            f"/api/v1/tickets/{ticket_id}/block",
            json={"blocking_reason_ids": [str(reason.id)], "comment": "Hard blocked"},
            headers=auth_headers,
        )
    assert r2.status_code == 200
    assert r2.json()["status"] == "HARD_BLOCKED"

    # Send PRODUCT_DELETED event
    body = {
        "event_type": "PRODUCT_DELETED",
        "idempotency_key": str(uuid.uuid4()),
        "occurred_at": datetime.now(UTC).isoformat(),
        "payload": {"product_id": str(product_id)},
    }
    r3 = await client.post(
        "/api/v1/b2b/events",
        json=body,
        headers={"X-Service-Key": settings.service_key},
    )
    assert r3.status_code == 202, r3.text

    # HARD_BLOCKED ticket should still exist unchanged
    await db_session.refresh(ticket)
    assert ticket.status == TicketStatus.HARD_BLOCKED
    assert ticket.decision_comment is not None


@pytest.mark.asyncio
async def test_decline_mixed_soft_hard_reasons_returns_400(
    client, db_session, auth_headers,
):
    """Mixing soft and hard blocking reasons in /decline -> 400."""
    hard_reason = _create_hard_reason(code="counterfeit")
    soft_reason = _create_soft_reason(code="inappropriate_content")
    db_session.add(hard_reason)
    db_session.add(soft_reason)
    await db_session.flush()

    ticket = _create_ticket()
    db_session.add(ticket)
    await db_session.flush()

    r1 = await client.post("/api/v1/queue/claim", headers=auth_headers)
    assert r1.status_code == 200
    ticket_id = r1.json()["id"]

    # Try decline with mixed reasons -> 400
    with _mock_hard_b2b_post():
        r2 = await client.post(
            f"/api/v1/tickets/{ticket_id}/block",
            json={
                "blocking_reason_ids": [str(hard_reason.id), str(soft_reason.id)],
            },
            headers=auth_headers,
        )

    assert r2.status_code == 400, r2.text
    assert "mix" in r2.json()["message"].lower()

    # Ticket should still be IN_REVIEW
    await db_session.refresh(ticket)
    assert ticket.status == TicketStatus.IN_REVIEW
