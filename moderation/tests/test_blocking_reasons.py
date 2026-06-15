"""Tests for US-MOD-06: Blocking Reasons Directory.

Scenarios (from DoD):
1. list_returns_active_reasons - happy path: active reasons returned with id, title, hard_block
2. inactive_reasons_not_visible - deactivated reasons not in list
3. referenced_reason_cannot_be_deleted - delete referenced reason -> 409
"""

import uuid
import pytest

from fastapi import status as http_status

from app.models import (
    BlockingReason, Ticket, TicketKind, TicketStatus, TicketBlockingReason,
)


def _create_reason(code="TEST_CODE", hard_block=False) -> BlockingReason:
    return BlockingReason(
        code=code,
        title="Test Reason",
        description="A test blocking reason",
        hard_block=hard_block,
        is_active=True,
    )


def _create_ticket() -> Ticket:
    return Ticket(
        product_id=uuid.uuid4(),
        seller_id=uuid.uuid4(),
        category_id=uuid.uuid4(),
        kind=TicketKind.CREATE,
        status=TicketStatus.PENDING,
        queue_priority=3,
        json_after={"title": "Test"},
    )


# ── DoD Test 1: Happy path ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_returns_active_reasons(
    client, db_session, admin_headers,
):
    """Active blocking reasons are returned with id, title, hard_block."""
    reason1 = _create_reason(code="COUNTERFEIT", hard_block=True)
    reason2 = _create_reason(code="INAPPROPRIATE", hard_block=False)
    db_session.add(reason1)
    db_session.add(reason2)
    await db_session.flush()

    r = await client.get(
        "/api/v1/blocking-reasons",
        headers=admin_headers,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["total_count"] == 2
    assert len(data["items"]) == 2

    items = data["items"]
    codes = {item["code"]: item for item in items}

    assert codes["COUNTERFEIT"]["title"] == "Test Reason"
    assert codes["COUNTERFEIT"]["hard_block"] is True
    assert codes["INAPPROPRIATE"]["hard_block"] is False


# ── DoD Test 2: Inactive hidden ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_inactive_reasons_not_visible(
    client, db_session, admin_headers,
):
    """Deactivated reasons are not returned in the list."""
    active = _create_reason(code="ACTIVE")
    inactive = _create_reason(code="INACTIVE")
    inactive.is_active = False
    db_session.add(active)
    db_session.add(inactive)
    await db_session.flush()

    r = await client.get(
        "/api/v1/blocking-reasons",
        headers=admin_headers,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["total_count"] == 1
    assert data["items"][0]["code"] == "ACTIVE"

    # Verify inactive is returned with ?is_active=false filter
    r2 = await client.get(
        "/api/v1/blocking-reasons?is_active=false",
        headers=admin_headers,
    )
    assert r2.status_code == 200
    assert r2.json()["total_count"] == 1
    assert r2.json()["items"][0]["code"] == "INACTIVE"


# ── DoD Test 3: Referential integrity ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_referenced_reason_cannot_be_deleted(
    client, db_session, admin_headers,
):
    """DELETE on a referenced blocking reason returns 409 Conflict."""
    reason = _create_reason(code="REFERENCED")
    db_session.add(reason)
    await db_session.flush()

    ticket = _create_ticket()
    db_session.add(ticket)
    await db_session.flush()

    # Link the reason to the ticket (simulates a decline/block decision)
    link = TicketBlockingReason(ticket_id=ticket.id, blocking_reason_id=reason.id)
    db_session.add(link)
    await db_session.flush()

    # Attempt DELETE -> 409 because referenced
    r = await client.delete(
        f"/api/v1/blocking-reasons/{reason.id}",
        headers=admin_headers,
    )
    assert r.status_code == 409, r.text

    # Verify the reason is still active (unchanged)
    await db_session.refresh(reason)
    assert reason.is_active is True

    # Ticket link is still intact (FK preserved)
    await db_session.refresh(ticket, attribute_names=["blocking_reasons"])


@pytest.mark.asyncio
async def test_delete_unreferenced_reason_soft_deactivates(
    client, db_session, admin_headers,
):
    """DELETE on unreferenced reason soft-deactivates it (is_active=false)."""
    reason = _create_reason(code="SAFE_TO_DELETE")
    db_session.add(reason)
    await db_session.flush()

    r = await client.delete(
        f"/api/v1/blocking-reasons/{reason.id}",
        headers=admin_headers,
    )
    assert r.status_code == 204, r.text

    # Verify soft-deleted: still in DB but inactive
    await db_session.refresh(reason)
    assert reason.is_active is False

    # Still exists physically
    still_there = await db_session.get(BlockingReason, reason.id)
    assert still_there is not None
    assert still_there.is_active is False


# ── Additional tests ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_blocking_reason(
    client, db_session, admin_headers,
):
    """Admin can create a new blocking reason."""
    payload = {
        "code": "COPYRIGHT",
        "title": "Copyright Violation",
        "description": "Product violates copyright",
        "hard_block": True,
    }
    r = await client.post(
        "/api/v1/blocking-reasons",
        json=payload,
        headers=admin_headers,
    )
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["code"] == "COPYRIGHT"
    assert data["title"] == "Copyright Violation"
    assert data["hard_block"] is True
    assert data["is_active"] is True
    assert "id" in data


@pytest.mark.asyncio
async def test_create_duplicate_code_returns_409(
    client, db_session, admin_headers,
):
    """Duplicate code returns 409."""
    reason = _create_reason(code="DUPE")
    db_session.add(reason)
    await db_session.flush()

    r = await client.post(
        "/api/v1/blocking-reasons",
        json={"code": "DUPE", "title": "Duplicate", "hard_block": False},
        headers=admin_headers,
    )
    assert r.status_code == 409, r.text


@pytest.mark.asyncio
async def test_update_blocking_reason(
    client, db_session, admin_headers,
):
    """Admin can update a blocking reason."""
    reason = _create_reason(code="UPDATE_ME")
    db_session.add(reason)
    await db_session.flush()

    r = await client.patch(
        f"/api/v1/blocking-reasons/{reason.id}",
        json={"title": "Updated Title"},
        headers=admin_headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["title"] == "Updated Title"


@pytest.mark.asyncio
async def test_deactivate_via_patch(
    client, db_session, admin_headers,
):
    """Admin can soft-deactivate via PATCH is_active=false."""
    reason = _create_reason(code="DEACTIVATE_ME")
    db_session.add(reason)
    await db_session.flush()

    r = await client.patch(
        f"/api/v1/blocking-reasons/{reason.id}",
        json={"is_active": False},
        headers=admin_headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["is_active"] is False

    await db_session.refresh(reason)
    assert reason.is_active is False


@pytest.mark.asyncio
async def test_list_filter_by_hard_block(
    client, db_session, admin_headers,
):
    """Filter list by hard_block flag."""
    soft = _create_reason(code="SOFT", hard_block=False)
    hard = _create_reason(code="HARD", hard_block=True)
    db_session.add(soft)
    db_session.add(hard)
    await db_session.flush()

    r = await client.get(
        "/api/v1/blocking-reasons?hard_block=true",
        headers=admin_headers,
    )
    assert r.status_code == 200
    assert r.json()["total_count"] == 1
    assert r.json()["items"][0]["code"] == "HARD"


@pytest.mark.asyncio
async def test_moderator_cannot_create_reason(
    client, db_session, auth_headers,
):
    """Regular moderator gets 403 on admin-only endpoints."""
    r = await client.post(
        "/api/v1/blocking-reasons",
        json={"code": "NOPE", "title": "Nope", "hard_block": False},
        headers=auth_headers,
    )
    assert r.status_code == 403, r.text


@pytest.mark.asyncio
async def test_product_blocking_reasons_endpoint(
    client, db_session, auth_headers,
):
    """GET /product-blocking-reasons returns active reasons with minimal fields."""
    reason1 = _create_reason(code="COUNTERFEIT", hard_block=True)
    reason2 = _create_reason(code="INAPPROPRIATE", hard_block=False)
    inactive = _create_reason(code="OLD")
    inactive.is_active = False
    db_session.add(reason1)
    db_session.add(reason2)
    db_session.add(inactive)
    await db_session.flush()

    r = await client.get(
        "/api/v1/blocking-reasons/product-blocking-reasons",
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert len(data) == 2

    # Check minimal fields
    for item in data:
        assert "id" in item
        assert "title" in item
        assert "hard_block" in item
        # code/description should NOT be in output
        assert "code" not in item
        assert "description" not in item

    # Filter by hard_block
    r2 = await client.get(
        "/api/v1/blocking-reasons/product-blocking-reasons?hard_block=true",
        headers=auth_headers,
    )
    assert r2.status_code == 200
    assert len(r2.json()) == 1
    assert r2.json()[0]["hard_block"] is True


@pytest.mark.asyncio
async def test_delete_nonexistent_reason_returns_404(
    client, db_session, admin_headers,
):
    """DELETE on non-existent reason returns 404."""
    fake_id = uuid.uuid4()
    r = await client.delete(
        f"/api/v1/blocking-reasons/{fake_id}",
        headers=admin_headers,
    )
    assert r.status_code == 404, r.text
