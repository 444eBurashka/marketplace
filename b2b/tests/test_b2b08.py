"""
Тесты для B2B-08: Reserve / Unreserve SKU.

Сценарии:
  happy:
    - reserve_all_skus_succeeds
    - idempotent_reserve_returns_200_without_double_deduction
    - unreserve_restores_quantities
  unhappy:
    - partial_insufficient_stock_returns_409_all_rollback
    - sku_out_of_stock_event_emitted
"""
import uuid
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select

from app.core.config import settings
from app.core.security import hash_password
from app.models import Category, Product, ProductStatus, Seller, SKU


# ─── Фикстуры ────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def seller(db_session):
    s = Seller(
        email="seller_b2b08@example.com",
        hashed_password=hash_password("password123"),
        company_name="Reserve Co B2B08",
        inn="8888888888",
        is_active=True,
    )
    db_session.add(s)
    await db_session.flush()
    return s


@pytest_asyncio.fixture
async def category(db_session):
    c = Category(name="Гаджеты B2B08", slug="gadgets-b2b08", is_active=True, sort_order=0)
    db_session.add(c)
    await db_session.flush()
    return c


@pytest.fixture
def service_headers():
    return {"X-Service-Key": settings.service_key}


async def make_product(db_session, seller, category, *, status=ProductStatus.MODERATED):
    p = Product(
        seller_id=seller.id,
        category_id=category.id,
        title="Test Product B2B08",
        slug=f"product-b2b08-{uuid.uuid4().hex[:8]}",
        description="Test description",
        status=status,
        deleted=False,
        blocked=False,
    )
    db_session.add(p)
    await db_session.flush()
    return p


async def make_sku(db_session, product, *, quantity=10, reserved_quantity=0):
    sku = SKU(
        product_id=product.id,
        name=f"SKU-{uuid.uuid4().hex[:6]}",
        price=10000,
        cost_price=7000,
        discount=0,
        quantity=quantity,
        reserved_quantity=reserved_quantity,
        is_active=True,
    )
    db_session.add(sku)
    await db_session.flush()
    return sku


# ─── Тесты ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_reserve_all_skus_succeeds(client: AsyncClient, db_session, seller, category, service_headers):
    """
    happy: reserve_all_skus_succeeds
    Все SKU с достаточными остатками резервируются успешно.
    active_quantity уменьшился, reserved_quantity вырос.
    """
    product = await make_product(db_session, seller, category)
    sku1 = await make_sku(db_session, product, quantity=10)
    sku2 = await make_sku(db_session, product, quantity=5)
    await db_session.commit()

    payload = {
        "idempotency_key": str(uuid.uuid4()),
        "order_id": str(uuid.uuid4()),
        "items": [
            {"sku_id": str(sku1.id), "quantity": 3},
            {"sku_id": str(sku2.id), "quantity": 2},
        ],
    }

    resp = await client.post("/api/v1/inventory/reserve", json=payload, headers=service_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "RESERVED"

    await db_session.refresh(sku1)
    await db_session.refresh(sku2)
    # active_quantity = quantity - reserved_quantity
    assert sku1.reserved_quantity == 3
    assert sku1.active_quantity == 7   # 10 - 3
    assert sku2.reserved_quantity == 2
    assert sku2.active_quantity == 3   # 5 - 2


@pytest.mark.asyncio
async def test_partial_insufficient_stock_returns_409_all_rollback(
    client: AsyncClient, db_session, seller, category, service_headers
):
    """
    unhappy: partial_insufficient_stock_returns_409_all_rollback
    Один SKU не проходит по остаткам → 409, ни один не зарезервирован.
    """
    product = await make_product(db_session, seller, category)
    sku1 = await make_sku(db_session, product, quantity=10)
    sku2 = await make_sku(db_session, product, quantity=2)  # только 2 в наличии
    await db_session.commit()

    payload = {
        "idempotency_key": str(uuid.uuid4()),
        "order_id": str(uuid.uuid4()),
        "items": [
            {"sku_id": str(sku1.id), "quantity": 3},
            {"sku_id": str(sku2.id), "quantity": 5},  # запрашиваем 5, есть только 2
        ],
    }

    resp = await client.post("/api/v1/inventory/reserve", json=payload, headers=service_headers)
    assert resp.status_code == 409

    # Rollback: sku1 тоже не должен быть зарезервирован
    await db_session.refresh(sku1)
    await db_session.refresh(sku2)
    assert sku1.reserved_quantity == 0
    assert sku2.reserved_quantity == 0


@pytest.mark.asyncio
async def test_idempotent_reserve_returns_200_without_double_deduction(
    client: AsyncClient, db_session, seller, category, service_headers
):
    """
    happy: idempotent_reserve_returns_200_without_double_deduction
    Повторный запрос с тем же idempotency_key → 200, данные не изменились.
    """
    product = await make_product(db_session, seller, category)
    sku = await make_sku(db_session, product, quantity=10)
    await db_session.commit()

    idempotency_key = str(uuid.uuid4())
    payload = {
        "idempotency_key": idempotency_key,
        "order_id": str(uuid.uuid4()),
        "items": [{"sku_id": str(sku.id), "quantity": 3}],
    }

    resp1 = await client.post("/api/v1/inventory/reserve", json=payload, headers=service_headers)
    assert resp1.status_code == 200

    resp2 = await client.post("/api/v1/inventory/reserve", json=payload, headers=service_headers)
    assert resp2.status_code == 200

    await db_session.refresh(sku)
    # Несмотря на два запроса, резерв взят только один раз
    assert sku.reserved_quantity == 3
    assert sku.active_quantity == 7  # 10 - 3


@pytest.mark.asyncio
async def test_sku_out_of_stock_event_emitted(
    client: AsyncClient, db_session, seller, category, service_headers
):
    """
    unhappy/happy: sku_out_of_stock_event_emitted
    После резервирования всего остатка (active_quantity → 0)
    отправляется событие SKU_OUT_OF_STOCK в B2C с sku_id и available_quantity=0.
    """
    product = await make_product(db_session, seller, category)
    sku = await make_sku(db_session, product, quantity=3)  # ровно 3 штуки
    await db_session.commit()

    payload = {
        "idempotency_key": str(uuid.uuid4()),
        "order_id": str(uuid.uuid4()),
        "items": [{"sku_id": str(sku.id), "quantity": 3}],  # забираем всё
    }

    with patch(
        "app.services.inventory._send_b2c_sku_out_of_stock",
        new_callable=AsyncMock,
    ) as mock_send:
        resp = await client.post("/api/v1/inventory/reserve", json=payload, headers=service_headers)
        assert resp.status_code == 200

    mock_send.assert_called_once()
    call_kwargs = mock_send.call_args
    called_sku_id = call_kwargs.args[0] if call_kwargs.args else call_kwargs.kwargs.get("sku_id")
    called_available = call_kwargs.kwargs.get("available_quantity", call_kwargs.args[2] if len(call_kwargs.args) > 2 else None)
    assert str(called_sku_id) == str(sku.id)
    assert called_available == 0


@pytest.mark.asyncio
async def test_unreserve_restores_quantities(
    client: AsyncClient, db_session, seller, category, service_headers
):
    """
    happy: unreserve_restores_quantities
    unreserve корректно восстанавливает active_quantity и reserved_quantity.
    """
    product = await make_product(db_session, seller, category)
    sku = await make_sku(db_session, product, quantity=10)
    await db_session.commit()

    order_id = str(uuid.uuid4())

    # Сначала резервируем
    reserve_payload = {
        "idempotency_key": str(uuid.uuid4()),
        "order_id": order_id,
        "items": [{"sku_id": str(sku.id), "quantity": 4}],
    }
    resp = await client.post("/api/v1/inventory/reserve", json=reserve_payload, headers=service_headers)
    assert resp.status_code == 200

    await db_session.refresh(sku)
    assert sku.reserved_quantity == 4
    assert sku.active_quantity == 6

    # Теперь снимаем резерв
    unreserve_payload = {
        "order_id": order_id,
        "items": [{"sku_id": str(sku.id), "quantity": 4}],
    }
    resp = await client.post("/api/v1/inventory/unreserve", json=unreserve_payload, headers=service_headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "UNRESERVED"

    await db_session.refresh(sku)
    assert sku.reserved_quantity == 0
    assert sku.active_quantity == 10  # вернулось к исходному