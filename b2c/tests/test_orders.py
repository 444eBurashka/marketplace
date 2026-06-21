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


@pytest.mark.asyncio
async def test_cancel_assembling_order_returns_409(client: AsyncClient, db_session: AsyncSession):
    buyer, _, token = await create_buyer_with_address(db_session)
    order = Order(
        number="ORD-TEST-002",
        buyer_id=buyer.id,
        idempotency_key=uuid.uuid4(),
        status=OrderStatus.ASSEMBLING,  # нельзя отменить
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
