import uuid
import pytest
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Buyer, Order, OrderStatus, Address
from app.core.security import hash_password
from shared.auth.jwt import create_access_token
from app.core.config import settings


async def create_buyer_with_address(db: AsyncSession):
    buyer = Buyer(email="order@test.com", hashed_password=hash_password("pass1234"))
    db.add(buyer)
    await db.flush()
    address = Address(
        buyer_id=buyer.id,
        label="Home",
        city="Moscow",
        street="Lenina",
        building="1",
        zip_code="101000",
    )
    db.add(address)
    await db.flush()
    token = create_access_token(subject=str(buyer.id), secret_key=settings.secret_key, extra_claims={"role": "buyer"})
    return buyer, address, token


@pytest.mark.asyncio
async def test_orders_list_returns_own_orders_paginated(client: AsyncClient, db_session: AsyncSession):
    buyer, address, token = await create_buyer_with_address(db_session)
    r = await client.get("/api/v1/orders", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    data = r.json()
    assert "items" in data
    assert data["total_count"] == 0
    assert data["limit"] == 20
    assert data["offset"] == 0


@pytest.mark.asyncio
async def test_other_user_order_returns_404_not_403(client: AsyncClient, db_session: AsyncSession):
    buyer1, _, token1 = await create_buyer_with_address(db_session)
    buyer2 = Buyer(email="other@test.com", hashed_password=hash_password("pass1234"))
    db_session.add(buyer2)
    await db_session.flush()
    order = Order(
        number="ORD-TEST-001",
        buyer_id=buyer2.id,
        idempotency_key=uuid.uuid4(),
        status=OrderStatus.PAID,
        address_snapshot={"city": "Moscow", "street": "Test", "building": "1", "zip_code": "101000"},
        subtotal=1000,
        total=1000,
    )
    db_session.add(order)
    await db_session.flush()

    # buyer1 пытается получить заказ buyer2 → 404, не 403
    r = await client.get(f"/api/v1/orders/{order.id}", headers={"Authorization": f"Bearer {token1}"})
    assert r.status_code == 404


# ─── US-ORD-03: Cancel ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cancel_paid_order_transitions_to_cancelled(client: AsyncClient, db_session: AsyncSession):
    """Happy path: отмена PAID заказа → CANCELLED."""
    buyer, _, token = await create_buyer_with_address(db_session)
    order = Order(
        number="ORD-CANCEL-001",
        buyer_id=buyer.id,
        idempotency_key=uuid.uuid4(),
        status=OrderStatus.PAID,
        address_snapshot={"city": "Moscow", "street": "Test", "building": "1", "zip_code": "101000"},
        subtotal=1000,
        total=1000,
    )
    db_session.add(order)
    await db_session.flush()

    with patch("app.services.b2b_client.unreserve", AsyncMock(return_value=200)):
        r = await client.post(
            f"/api/v1/orders/{order.id}/cancel",
            json={"reason": "changed mind"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "CANCELLED"


@pytest.mark.asyncio
async def test_unreserve_failure_transitions_to_cancel_pending(client: AsyncClient, db_session: AsyncSession):
    """B2B недоступен → CANCEL_PENDING."""
    buyer, _, token = await create_buyer_with_address(db_session)
    order = Order(
        number="ORD-CANCEL-002",
        buyer_id=buyer.id,
        idempotency_key=uuid.uuid4(),
        status=OrderStatus.PAID,
        address_snapshot={"city": "Moscow", "street": "Test", "building": "1", "zip_code": "101000"},
        subtotal=1000,
        total=1000,
    )
    db_session.add(order)
    await db_session.flush()

    with patch("app.services.b2b_client.unreserve", AsyncMock(return_value=503)):
        r = await client.post(
            f"/api/v1/orders/{order.id}/cancel",
            json={"reason": "changed mind"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "CANCEL_PENDING"


@pytest.mark.asyncio
async def test_cancel_assembling_order_returns_409(client: AsyncClient, db_session: AsyncSession):
    """ASSEMBLING → 409 CANCEL_NOT_ALLOWED."""
    buyer, _, token = await create_buyer_with_address(db_session)
    order = Order(
        number="ORD-CANCEL-003",
        buyer_id=buyer.id,
        idempotency_key=uuid.uuid4(),
        status=OrderStatus.ASSEMBLING,
        address_snapshot={},
        subtotal=0,
        total=0,
    )
    db_session.add(order)
    await db_session.flush()

    r = await client.post(
        f"/api/v1/orders/{order.id}/cancel",
        json={"reason": "changed mind"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 409
    assert r.json()["code"] == "CANCEL_NOT_ALLOWED"
    assert r.json()["current_status"] == "ASSEMBLING"


@pytest.mark.asyncio
async def test_other_user_cancel_returns_404(client: AsyncClient, db_session: AsyncSession):
    """Чужой заказ при отмене → 404 (IDOR)."""
    buyer1, _, token1 = await create_buyer_with_address(db_session)
    buyer2 = Buyer(email="other2@test.com", hashed_password=hash_password("pass1234"))
    db_session.add(buyer2)
    await db_session.flush()
    order = Order(
        number="ORD-CANCEL-004",
        buyer_id=buyer2.id,
        idempotency_key=uuid.uuid4(),
        status=OrderStatus.PAID,
        address_snapshot={},
        subtotal=1000,
        total=1000,
    )
    db_session.add(order)
    await db_session.flush()

    r = await client.post(
        f"/api/v1/orders/{order.id}/cancel",
        json={"reason": "hack attempt"},
        headers={"Authorization": f"Bearer {token1}"},
    )
    assert r.status_code == 404
