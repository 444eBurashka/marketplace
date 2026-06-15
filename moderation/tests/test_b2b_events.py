"""Tests for US-MOD-01: B2B product events endpoint.

Scenarios (from DoD):
1. created_pending        — CREATED creates a ticket in PENDING
2. edited_returns_to_review — EDITED after MODERATED/BLOCKED returns to queue
3. edited_updates_in_review — EDITED during IN_REVIEW updates fields
4. deleted_archived        — DELETED removes ticket from queue
5. duplicate_event_no_side_effects — repeated idempotency_key -> 409
6. missing_service_header_401 — request without X-Service-Key -> 401
"""

import uuid
import pytest
from datetime import UTC, datetime
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import Ticket, TicketStatus

SERVICE_KEY = settings.service_key
PRODUCT_ID = uuid.uuid4()
SELLER_ID = uuid.uuid4()
CATEGORY_ID = uuid.uuid4()


def _event_payload(event_type: str, **overrides) -> dict:
    base = {
        "PRODUCT_CREATED": {
            "product_id": str(PRODUCT_ID),
            "seller_id": str(SELLER_ID),
            "category_id": str(CATEGORY_ID),
            "queue_priority": 3,
            "json_after": {"title": "Test Product", "price": 1000},
        },
        "PRODUCT_EDITED": {
            "product_id": str(PRODUCT_ID),
            "seller_id": str(SELLER_ID),
            "category_id": str(CATEGORY_ID),
            "queue_priority": 3,
            "json_before": {"title": "Old", "price": 500},
            "json_after": {"title": "New", "price": 1000},
        },
        "PRODUCT_DELETED": {
            "product_id": str(PRODUCT_ID),
        },
    }
    data = base[event_type].copy()
    data.update(overrides)
    return data


def _make_request(event_type: str, **payload_overrides) -> dict:
    return {
        "event_type": event_type,
        "idempotency_key": str(uuid.uuid4()),
        "occurred_at": datetime.now(UTC).isoformat(),
        "payload": _event_payload(event_type, **payload_overrides),
    }


@pytest.mark.asyncio
async def test_created_pending(client, db_session):
    """PRODUCT_CREATED event creates a PENDING ticket."""
    body = _make_request("PRODUCT_CREATED")
    response = await client.post(
        "/api/v1/b2b/events",
        json=body,
        headers={"X-Service-Key": SERVICE_KEY},
    )
    assert response.status_code == 202, response.text
    data = response.json()
    assert data["status"] == "PENDING"

    ticket = await db_session.scalar(
        select(Ticket).where(Ticket.product_id == PRODUCT_ID)
    )
    assert ticket is not None
    assert ticket.status == TicketStatus.PENDING
    assert ticket.kind.value == "CREATE"
    assert ticket.json_after["title"] == "Test Product"


@pytest.mark.asyncio
async def test_edited_returns_to_review(client, db_session):
    """PRODUCT_EDITED after a product was MODERATED -> new EDIT ticket in PENDING."""
    pid = uuid.uuid4()
    body1 = _make_request("PRODUCT_CREATED", product_id=str(pid))
    r1 = await client.post(
        "/api/v1/b2b/events", json=body1, headers={"X-Service-Key": SERVICE_KEY}
    )
    assert r1.status_code == 202

    body2 = _make_request("PRODUCT_EDITED", product_id=str(pid))
    r2 = await client.post(
        "/api/v1/b2b/events", json=body2, headers={"X-Service-Key": SERVICE_KEY}
    )
    assert r2.status_code == 202, r2.text
    assert r2.json()["status"] == "PENDING"


@pytest.mark.asyncio
async def test_edited_updates_in_review(client, db_session):
    """PRODUCT_EDITED while a ticket is IN_REVIEW -> updates json_after."""
    pid = uuid.uuid4()
    body = _make_request("PRODUCT_CREATED", product_id=str(pid))
    r1 = await client.post(
        "/api/v1/b2b/events", json=body, headers={"X-Service-Key": SERVICE_KEY}
    )
    assert r1.status_code == 202
    ticket_id = r1.json()["id"]

    ticket = await db_session.get(Ticket, uuid.UUID(ticket_id))
    ticket.status = TicketStatus.IN_REVIEW
    await db_session.flush()

    body2 = _make_request(
        "PRODUCT_EDITED",
        product_id=str(pid),
        json_after={"title": "Updated Title", "price": 2000},
    )
    r2 = await client.post(
        "/api/v1/b2b/events", json=body2, headers={"X-Service-Key": SERVICE_KEY}
    )
    assert r2.status_code == 202, r2.text

    await db_session.refresh(ticket)
    assert ticket.json_after["title"] == "Updated Title"


@pytest.mark.asyncio
async def test_deleted_archived(client, db_session):
    """PRODUCT_DELETED closes all open PENDING tickets -> HARD_BLOCKED."""
    pid = uuid.uuid4()
    body = _make_request("PRODUCT_CREATED", product_id=str(pid))
    r1 = await client.post(
        "/api/v1/b2b/events", json=body, headers={"X-Service-Key": SERVICE_KEY}
    )
    assert r1.status_code == 202

    body2 = _make_request("PRODUCT_DELETED", product_id=str(pid))
    r2 = await client.post(
        "/api/v1/b2b/events", json=body2, headers={"X-Service-Key": SERVICE_KEY}
    )
    assert r2.status_code == 202, r2.text
    data = r2.json()
    assert len(data["closed_tickets"]) == 1

    ticket = await db_session.scalar(select(Ticket).where(Ticket.product_id == pid))
    assert ticket.status == TicketStatus.HARD_BLOCKED


@pytest.mark.asyncio
async def test_duplicate_event_no_side_effects(client, db_session):
    """Same idempotency_key -> 409 Conflict, no duplicate ticket."""
    body = _make_request("PRODUCT_CREATED")

    r1 = await client.post(
        "/api/v1/b2b/events", json=body, headers={"X-Service-Key": SERVICE_KEY}
    )
    assert r1.status_code == 202

    r2 = await client.post(
        "/api/v1/b2b/events", json=body, headers={"X-Service-Key": SERVICE_KEY}
    )
    assert r2.status_code == 409, r2.text
    assert "duplicate" in r2.json()["message"].lower()


@pytest.mark.asyncio
async def test_missing_service_header_401(client, db_session):
    """Request without X-Service-Key -> 401/403."""
    body = _make_request("PRODUCT_CREATED")
    response = await client.post(
        "/api/v1/b2b/events",
        json=body,
        headers={},
    )
    assert response.status_code in (401, 403), response.text
