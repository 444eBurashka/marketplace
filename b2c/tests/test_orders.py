import uuid
import pytest
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Buyer, Order, OrderStatus, Address, PaymentMethod
from app.core.security import hash_password
from shared.auth.jwt import create_access_token
from app.core.config import settings


SKU_ID = "00000000-0000-0000-0000-000000000010"
PRODUCT_ID = "00000000-0000-0000-0000-000000000011"


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
    pm = PaymentMethod(buyer_id=buyer.id, label="Card", card_last4="1234")
    db.add(pm)
    await db.flush()
    token = create_access_token(subject=str(buyer.id), secret_key=settings.secret_key, extra_claims={"role": "buyer"})
    return buyer, address, pm, token


def _mock_product():
    return {
        "id": PRODUCT_ID,
        "title": "Test Product",
        "status": "MODERATED",
        "deleted": False,
        "skus": [{"id": SKU_ID, "price": 1000, "quantity": 10}],
    }


# ─── US-ORD-01: Checkout ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_checkout_creates_paid_order_with_fixed_prices(client: AsyncClient, db_session: AsyncSession):
    """Happy path: checkout creates PAID order with fixed prices."""
    buyer, address, pm, token = await create_buyer_with_address(db_session)

    with patch("app.services.b2b_client.get_product", AsyncMock(return_value=_mock_product())), \
         patch("app.services.b2b_client.reserve", AsyncMock(return_value={"status_code": 200, "body": {}})):
        r = await client.post(
            "/api/v1/orders",
            json={
                "items": [{"sku_id": SKU_ID, "product_id": PRODUCT_ID, "quantity": 2}],
                "address_id": str(address.id),
                "payment_method_id": str(pm.id),
            },
            headers={"Authorization": f"Bearer {token}", "Idempotency-Key": str(uuid.uuid4())},
        )

    assert r.status_code == 201
    data = r.json()
    assert data["status"] == "PAID"
    assert data["subtotal"] == 2000
    assert data["total"] == 2000
    assert data["buyer_id"] == str(buyer.id)
    assert data["number"].startswith("ORD-")
    assert len(data["items"]) == 1
    item = data["items"][0]
    assert item["unit_price"] == 1000
    assert item["quantity"] == 2
    assert item["line_total"] == 2000
    assert item["product_id"] == PRODUCT_ID


@pytest.mark.asyncio
async def test_partial_reserve_failure_returns_409(client: AsyncClient, db_session: AsyncSession):
    """At least one SKU not reserved -> 409 RESERVE_FAILED with failed_items."""
    buyer, address, pm, token = await create_buyer_with_address(db_session)

    with patch("app.services.b2b_client.get_product", AsyncMock(return_value=_mock_product())), \
         patch("app.services.b2b_client.reserve", AsyncMock(return_value={
            "status_code": 409,
            "body": {
                "code": "RESERVE_FAILED",
                "failed_items": [{"sku_id": SKU_ID, "reason": "INSUFFICIENT_STOCK"}],
            },
        })):
        r = await client.post(
            "/api/v1/orders",
            json={
                "items": [{"sku_id": SKU_ID, "product_id": PRODUCT_ID, "quantity": 99}],
                "address_id": str(address.id),
                "payment_method_id": str(pm.id),
            },
            headers={"Authorization": f"Bearer {token}", "Idempotency-Key": str(uuid.uuid4())},
        )

    assert r.status_code == 409
    data = r.json()
    assert data["code"] == "RESERVE_FAILED"
    assert len(data["failed_items"]) == 1


@pytest.mark.asyncio
async def test_idempotency_returns_existing_order(client: AsyncClient, db_session: AsyncSession):
    """Repeat POST with same idempotency_key returns existing order."""
    buyer, address, pm, token = await create_buyer_with_address(db_session)
    key = str(uuid.uuid4())

    with patch("app.services.b2b_client.get_product", AsyncMock(return_value=_mock_product())), \
         patch("app.services.b2b_client.reserve", AsyncMock(return_value={"status_code": 200, "body": {}})):
        r1 = await client.post(
            "/api/v1/orders",
            json={
                "items": [{"sku_id": SKU_ID, "product_id": PRODUCT_ID, "quantity": 1}],
                "address_id": str(address.id),
                "payment_method_id": str(pm.id),
            },
            headers={"Authorization": f"Bearer {token}", "Idempotency-Key": key},
        )
        assert r1.status_code == 201

        r2 = await client.post(
            "/api/v1/orders",
            json={
                "items": [{"sku_id": SKU_ID, "product_id": PRODUCT_ID, "quantity": 1}],
                "address_id": str(address.id),
                "payment_method_id": str(pm.id),
            },
            headers={"Authorization": f"Bearer {token}", "Idempotency-Key": key},
        )

    assert r2.status_code == 200
    assert r2.json()["id"] == r1.json()["id"]
    assert r2.json()["status"] == "PAID"


@pytest.mark.asyncio
async def test_b2b_unavailable_returns_503(client: AsyncClient, db_session: AsyncSession):
    """B2B unavailable -> 503."""
    buyer, address, pm, token = await create_buyer_with_address(db_session)

    with patch("app.services.b2b_client.get_product", AsyncMock(return_value=_mock_product())), \
         patch("app.services.b2b_client.reserve", AsyncMock(return_value={"status_code": 503, "body": {}})):
        r = await client.post(
            "/api/v1/orders",
            json={
                "items": [{"sku_id": SKU_ID, "product_id": PRODUCT_ID, "quantity": 1}],
                "address_id": str(address.id),
                "payment_method_id": str(pm.id),
            },
            headers={"Authorization": f"Bearer {token}", "Idempotency-Key": str(uuid.uuid4())},
        )

    assert r.status_code == 503
    assert r.json()["code"] == "B2B_UNAVAILABLE"


# ─── US-ORD-02: List / Get Orders ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_orders_list_returns_own_orders_paginated(client: AsyncClient, db_session: AsyncSession):
    buyer, address, pm, token = await create_buyer_with_address(db_session)
    r = await client.get("/api/v1/orders", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    data = r.json()
    assert "items" in data
    assert data["total_count"] == 0


@pytest.mark.asyncio
async def test_other_user_order_returns_404_not_403(client: AsyncClient, db_session: AsyncSession):
    buyer1, _, _, token1 = await create_buyer_with_address(db_session)
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

    r = await client.get(f"/api/v1/orders/{order.id}", headers={"Authorization": f"Bearer {token1}"})
    assert r.status_code == 404


# ─── US-ORD-03: Cancel ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cancel_paid_order_transitions_to_cancelled(client: AsyncClient, db_session: AsyncSession):
    """Happy path: отмена PAID заказа → CANCELLED."""
    buyer, _, _, token = await create_buyer_with_address(db_session)
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
    buyer, _, _, token = await create_buyer_with_address(db_session)
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
    buyer, _, _, token = await create_buyer_with_address(db_session)
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
    buyer1, _, _, token1 = await create_buyer_with_address(db_session)
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
