"""Tests for US-MOD-04: Soft block with field reports.

Scenarios (from DoD):
1. soft_block_transitions_to_blocked_with_field_reports - happy path
2. soft_block_emits_event_to_b2b - verify BLOCKED event sent with correct payload
3. soft_block_unknown_reason_returns_400 - invalid blocking_reason_id
4. soft_block_others_card_returns_403 - not your ticket
5. soft_block_invalid_field_name_returns_400 - empty field_path
6. soft_block_hard_block_reason_returns_400 - hard_block-only reason
"""

import uuid
import pytest
from unittest.mock import AsyncMock, patch

from sqlalchemy import select

from app.models import (
    Ticket, TicketKind, TicketStatus, BlockingReason, TicketFieldReport,
)


def _create_ticket(priority=3):
    return Ticket(
        product_id=uuid.uuid4(),
        seller_id=uuid.uuid4(),
        category_id=uuid.uuid4(),
        kind=TicketKind.CREATE,
        status=TicketStatus.PENDING,
        queue_priority=priority,
        json_after={"title": "Test Product", "price": 1000},
    )


def _create_soft_reason(code="inappropriate_content"):
    return BlockingReason(
        code=code,
        title="Inappropriate Content",
        description="Product contains inappropriate content",
        hard_block=False,
        is_active=True,
    )


def _create_hard_reason(code="counterfeit"):
    return BlockingReason(
        code=code,
        title="Counterfeit",
        description="Product is counterfeit",
        hard_block=True,
        is_active=True,
    )


def _mock_b2b_post():
    """Patch httpx.AsyncClient.post to avoid real HTTP calls to B2B."""
    return patch(
        "app.services.ticket_block_service.httpx.AsyncClient",
        new_callable=lambda: lambda: AsyncMock(
            __aenter__=AsyncMock(return_value=AsyncMock(
                post=AsyncMock(return_value=AsyncMock(
                    status_code=204,
                    raise_for_status=lambda: None,
                )),
            )),
        ),
    )


@pytest.mark.asyncio
async def test_soft_block_transitions_to_blocked_with_field_reports(
    client, db_session, auth_headers, moderator,
):
    reason = _create_soft_reason()
    db_session.add(reason)
    await db_session.flush()

    ticket = _create_ticket()
    db_session.add(ticket)
    await db_session.flush()

    r1 = await client.post("/api/v1/tickets/next", headers=auth_headers)
    assert r1.status_code == 200, r1.text
    ticket_id = r1.json()["id"]

    with _mock_b2b_post():
        r2 = await client.post(
            f"/api/v1/tickets/{ticket_id}/block",
            json={
                "blocking_reason_ids": [str(reason.id)],
                "comment": "Violates content policy",
                "field_reports": [
                    {"field_path": "images[0].url", "message": "Bad image", "severity": "ERROR"},
                    {"field_path": "description", "message": "Misleading", "severity": "WARNING"},
                ],
            },
            headers=auth_headers,
        )

    assert r2.status_code == 200, r2.text
    data = r2.json()
    assert data["status"] == "BLOCKED"
    assert data["decision_comment"] == "Violates content policy"
    assert len(data["field_reports"]) == 2
    assert len(data["blocking_reasons"]) == 1
    assert data["blocking_reasons"][0]["code"] == "inappropriate_content"

    await db_session.refresh(ticket)
    assert ticket.status == TicketStatus.BLOCKED

    stmt = select(TicketFieldReport).where(TicketFieldReport.ticket_id == ticket.id)
    reports = (await db_session.execute(stmt)).scalars().all()
    assert len(reports) == 2

    from app.models import TicketHistory, TicketHistoryAction
    stmt = select(TicketHistory).where(
        TicketHistory.ticket_id == ticket.id,
        TicketHistory.action == TicketHistoryAction.BLOCKED,
    )
    history = await db_session.scalar(stmt)
    assert history is not None
    assert history.moderator_id == moderator.id


@pytest.mark.asyncio
async def test_soft_block_emits_event_to_b2b(
    client, db_session, auth_headers, moderator,
):
    reason = _create_soft_reason()
    db_session.add(reason)
    await db_session.flush()

    ticket = _create_ticket()
    db_session.add(ticket)
    await db_session.flush()

    r1 = await client.post("/api/v1/tickets/next", headers=auth_headers)
    assert r1.status_code == 200
    ticket_id = r1.json()["id"]

    mock_post = AsyncMock(return_value=AsyncMock(
        status_code=204, raise_for_status=lambda: None,
    ))
    mock_client = AsyncMock(
        __aenter__=AsyncMock(return_value=AsyncMock(post=mock_post)),
    )

    with patch(
        "app.services.ticket_block_service.httpx.AsyncClient",
        return_value=mock_client,
    ):
        r2 = await client.post(
            f"/api/v1/tickets/{ticket_id}/block",
            json={
                "blocking_reason_ids": [str(reason.id)],
                "comment": "Blocked",
                "field_reports": [
                    {"field_path": "description", "message": "Bad desc", "severity": "ERROR"},
                ],
            },
            headers=auth_headers,
        )

    assert r2.status_code == 200, r2.text

    mock_post.assert_awaited_once()
    call_args = mock_post.call_args
    assert call_args.args[0] == "http://b2b_app:8001/api/v1/moderation/events"
    call_kwargs = call_args.kwargs
    payload = call_kwargs["json"]
    assert payload["event_type"] == "BLOCKED"
    assert payload["hard_block"] is False
    assert payload["product_id"] == str(ticket.product_id)
    assert payload["moderator_id"] == str(moderator.id)
    assert payload["blocking_reason_id"] == str(reason.id)
    assert payload["field_reports"][0]["field_name"] == "description"
    assert payload["field_reports"][0]["comment"] == "Bad desc"


@pytest.mark.asyncio
async def test_soft_block_unknown_reason_returns_400(
    client, db_session, auth_headers,
):
    ticket = _create_ticket()
    db_session.add(ticket)
    await db_session.flush()

    r1 = await client.post("/api/v1/tickets/next", headers=auth_headers)
    assert r1.status_code == 200
    ticket_id = r1.json()["id"]

    fake_id = str(uuid.uuid4())
    with _mock_b2b_post():
        r2 = await client.post(
            f"/api/v1/tickets/{ticket_id}/block",
            json={"blocking_reason_ids": [fake_id]},
            headers=auth_headers,
        )

    assert r2.status_code == 400, r2.text
    assert "not found" in r2.json()["message"].lower()


@pytest.mark.asyncio
async def test_soft_block_others_card_returns_403(
    client, db_session, auth_headers, moderator,
    second_auth_headers, second_moderator,
):
    reason = _create_soft_reason()
    db_session.add(reason)
    await db_session.flush()

    ticket = _create_ticket()
    db_session.add(ticket)
    await db_session.flush()

    r1 = await client.post("/api/v1/tickets/next", headers=auth_headers)
    assert r1.status_code == 200
    ticket_id = r1.json()["id"]

    with _mock_b2b_post():
        r2 = await client.post(
            f"/api/v1/tickets/{ticket_id}/block",
            json={"blocking_reason_ids": [str(reason.id)]},
            headers=second_auth_headers,
        )

    assert r2.status_code == 403, r2.text
    await db_session.refresh(ticket)
    assert ticket.status == TicketStatus.IN_REVIEW


@pytest.mark.asyncio
async def test_soft_block_invalid_field_name_returns_400(
    client, db_session, auth_headers,
):
    reason = _create_soft_reason()
    db_session.add(reason)
    await db_session.flush()

    ticket = _create_ticket()
    db_session.add(ticket)
    await db_session.flush()

    r1 = await client.post("/api/v1/tickets/next", headers=auth_headers)
    assert r1.status_code == 200
    ticket_id = r1.json()["id"]

    with _mock_b2b_post():
        r2 = await client.post(
            f"/api/v1/tickets/{ticket_id}/block",
            json={
                "blocking_reason_ids": [str(reason.id)],
                "field_reports": [{"field_path": "", "message": "No field"}],
            },
            headers=auth_headers,
        )

    assert r2.status_code == 400, r2.text
    assert "field_path" in r2.json()["message"].lower()


@pytest.mark.asyncio
async def test_soft_block_hard_block_reason_returns_400(
    client, db_session, auth_headers,
):
    reason = _create_hard_reason()
    db_session.add(reason)
    await db_session.flush()

    ticket = _create_ticket()
    db_session.add(ticket)
    await db_session.flush()

    r1 = await client.post("/api/v1/tickets/next", headers=auth_headers)
    assert r1.status_code == 200
    ticket_id = r1.json()["id"]

    with _mock_b2b_post():
        r2 = await client.post(
            f"/api/v1/tickets/{ticket_id}/block",
            json={"blocking_reason_ids": [str(reason.id)]},
            headers=auth_headers,
        )

    assert r2.status_code == 400, r2.text
    assert "hard-block" in r2.json()["message"].lower()
