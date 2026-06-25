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


# ─── Хелпер: ожидаемый AddressResponse из address-объекта ────────────────────

def expected_address_response(address: Address) -> dict:
    """Собирает ожидаемый AddressResponse по контракту B2C для сравнения в тестах."""
    return {
        "id": str(address.id),
        "created_at": address.created_at.isoformat(),
        "country": "",  # пока поля нет в модели — пустая строка
        "city": address.city,
        "street": address.street,
        "building": address.building,
        "apartment": address.apartment,
        "postal_code": address.zip_code,
        "is_default": False,
    }


# ─── US-ORD-01: Checkout ─────────────────────────────────────────────────────

MOCK_SKU = {
    "id": "00000000-0000-0000-0000-000000000010",
    "product_id": "00000000-0000-0000-0000-000000000099",
    "price": 1000,
    "attributes": [],
    "active_quantity": 10,
}

MOCK_PRODUCT = {
    "id": "00000000-0000-0000-0000-000000000099",
    "title": "Phone",
    "skus": [
        {"id": "00000000-0000-0000-0000-000000000010", "price": 1000, "attributes": []}
    ],
}

MOCK_RESERVE_OK = {"status_code": 200, "body": {}}
MOCK_RESERVE_409 = {
    "status_code": 409,
    "body": {"failed_items": [{"sku_id": "00000000-0000-0000-0000-000000000010", "requested": 2, "available": 0}]},
}
MOCK_RESERVE_503 = {"status_code": 503, "body": {}}


@pytest.mark.asyncio
async def test_checkout_happy_path_returns_201_with_full_order(client: AsyncClient, db_session: AsyncSession):
    """Happy path: заказ создаётся, ответ содержит все обязательные поля OrderResponse."""
    buyer, address, token = await create_buyer_with_address(db_session)
    payment_method_id = uuid.uuid4()

    with (
        patch("app.services.b2b_client.get_sku_public", AsyncMock(return_value=MOCK_SKU)),
        patch("app.services.b2b_client.get_product", AsyncMock(return_value=MOCK_PRODUCT)),
        patch("app.services.b2b_client.reserve", AsyncMock(return_value=MOCK_RESERVE_OK)),
    ):
        r = await client.post(
            "/api/v1/orders",
            json={
                "address_id": str(address.id),
                "payment_method_id": str(payment_method_id),
                "items": [{"sku_id": "00000000-0000-0000-0000-000000000010", "quantity": 1}]
            },
            headers={"Authorization": f"Bearer {token}", "Idempotency-Key": str(uuid.uuid4())},
        )

    assert r.status_code == 201
    data = r.json()

    # Обязательные поля OrderResponse
    assert "id" in data
    assert "buyer_id" in data
    assert "status" in data
    assert "items" in data
    assert "subtotal" in data
    assert "total" in data
    assert "address" in data
    assert "created_at" in data

    assert data["status"] == "PAID"
    assert data["total"] == 1000

    # items: обязательные поля OrderItem
    item = data["items"][0]
    assert "sku_id" in item
    assert "product_id" in item
    assert "name" in item
    assert "quantity" in item
    assert "unit_price" in item
    assert "line_total" in item


@pytest.mark.asyncio
async def test_checkout_address_response_matches_contract(client: AsyncClient, db_session: AsyncSession):
    """Адрес в ответе — объект AddressResponse с id, created_at, country, postal_code."""
    buyer, address, token = await create_buyer_with_address(db_session)
    payment_method_id = uuid.uuid4()

    with (
        patch("app.services.b2b_client.get_sku_public", AsyncMock(return_value=MOCK_SKU)),
        patch("app.services.b2b_client.get_product", AsyncMock(return_value=MOCK_PRODUCT)),
        patch("app.services.b2b_client.reserve", AsyncMock(return_value=MOCK_RESERVE_OK)),
    ):
        r = await client.post(
            "/api/v1/orders",
            json={
                "address_id": str(address.id),
                "payment_method_id": str(payment_method_id),
                "items": [{"sku_id": "00000000-0000-0000-0000-000000000010", "quantity": 1}]
            },
            headers={"Authorization": f"Bearer {token}", "Idempotency-Key": str(uuid.uuid4())},
        )

    assert r.status_code == 201
    addr = r.json()["address"]

    # Обязательные поля AddressResponse
    assert "id" in addr, "address.id отсутствует"
    assert "created_at" in addr, "address.created_at отсутствует"
    assert "country" in addr, "address.country отсутствует (обязательное)"
    assert "city" in addr
    assert "street" in addr
    assert "building" in addr

    # Имя поля почтового индекса
    assert "postal_code" in addr, "должно быть postal_code, не zip_code"
    assert "zip_code" not in addr, "zip_code не должен попадать в ответ"

    assert addr["city"] == "Moscow"
    assert addr["postal_code"] == "101000"


@pytest.mark.asyncio
async def test_idempotent_repeat_returns_201_with_full_order(client: AsyncClient, db_session: AsyncSession):
    """Повторный запрос с тем же Idempotency-Key возвращает 201 и полный OrderResponse."""
    buyer, address, token = await create_buyer_with_address(db_session)
    idem_key = str(uuid.uuid4())
    payment_method_id = uuid.uuid4()

    with (
        patch("app.services.b2b_client.get_sku_public", AsyncMock(return_value=MOCK_SKU)),
        patch("app.services.b2b_client.get_product", AsyncMock(return_value=MOCK_PRODUCT)),
        patch("app.services.b2b_client.reserve", AsyncMock(return_value=MOCK_RESERVE_OK)),
    ):
        r1 = await client.post(
            "/api/v1/orders",
            json={
                "address_id": str(address.id),
                "payment_method_id": str(payment_method_id),
                "items": [{"sku_id": "00000000-0000-0000-0000-000000000010", "quantity": 1}]
            },
            headers={"Authorization": f"Bearer {token}", "Idempotency-Key": idem_key},
        )
        assert r1.status_code == 201

        # Повтор — reserve не должен вызываться второй раз
        r2 = await client.post(
            "/api/v1/orders",
            json={
                "address_id": str(address.id),
                "payment_method_id": str(payment_method_id),
                "items": [{"sku_id": "00000000-0000-0000-0000-000000000010", "quantity": 1}]
            },
            headers={"Authorization": f"Bearer {token}", "Idempotency-Key": idem_key},
        )

    assert r2.status_code == 201, "идемпотентный повтор должен возвращать 201, не 200"

    data = r2.json()
    # Полный набор обязательных полей — не усечённый {id, number, status}
    assert "buyer_id" in data, "buyer_id отсутствует в идемпотентном ответе"
    assert "items" in data, "items отсутствует в идемпотентном ответе"
    assert "subtotal" in data, "subtotal отсутствует в идемпотентном ответе"
    assert "total" in data, "total отсутствует в идемпотентном ответе"
    assert "address" in data, "address отсутствует в идемпотентном ответе"
    assert "created_at" in data, "created_at отсутствует в идемпотентном ответе"

    # id заказа совпадает с первым ответом
    assert data["id"] == r1.json()["id"]


@pytest.mark.asyncio
async def test_reserve_conflict_returns_409_with_failed_items(client: AsyncClient, db_session: AsyncSession):
    """Нехватка остатков → 409 RESERVE_FAILED с failed_items."""
    buyer, address, token = await create_buyer_with_address(db_session)
    payment_method_id = uuid.uuid4()

    with (
        patch("app.services.b2b_client.get_sku_public", AsyncMock(return_value=MOCK_SKU)),
        patch("app.services.b2b_client.get_product", AsyncMock(return_value=MOCK_PRODUCT)),
        patch("app.services.b2b_client.reserve", AsyncMock(return_value=MOCK_RESERVE_409)),
    ):
        r = await client.post(
            "/api/v1/orders",
            json={
                "address_id": str(address.id),
                "payment_method_id": str(payment_method_id),
                "items": [{"sku_id": "00000000-0000-0000-0000-000000000010", "quantity": 2}]
            },
            headers={"Authorization": f"Bearer {token}", "Idempotency-Key": str(uuid.uuid4())},
        )

    assert r.status_code == 409
    data = r.json()
    assert data["code"] == "RESERVE_FAILED"
    assert "failed_items" in data


@pytest.mark.asyncio
async def test_b2b_unavailable_returns_503(client: AsyncClient, db_session: AsyncSession):
    """B2B недоступен → 503 B2B_UNAVAILABLE."""
    buyer, address, token = await create_buyer_with_address(db_session)
    payment_method_id = uuid.uuid4()

    with (
        patch("app.services.b2b_client.get_sku_public", AsyncMock(return_value=MOCK_SKU)),
        patch("app.services.b2b_client.get_product", AsyncMock(return_value=MOCK_PRODUCT)),
        patch("app.services.b2b_client.reserve", AsyncMock(return_value=MOCK_RESERVE_503)),
    ):
        r = await client.post(
            "/api/v1/orders",
            json={
                "address_id": str(address.id),
                "payment_method_id": str(payment_method_id),
                "items": [{"sku_id": "00000000-0000-0000-0000-000000000010", "quantity": 1}]
            },
            headers={"Authorization": f"Bearer {token}", "Idempotency-Key": str(uuid.uuid4())},
        )

    assert r.status_code == 503
    assert r.json()["code"] == "B2B_UNAVAILABLE"


@pytest.mark.asyncio
async def test_payment_method_required(client: AsyncClient, db_session: AsyncSession):
    """Проверяем, что payment_method_id обязателен."""
    buyer, address, token = await create_buyer_with_address(db_session)

    r = await client.post(
        "/api/v1/orders",
        json={
            "address_id": str(address.id),
            "items": [{"sku_id": "00000000-0000-0000-0000-000000000010", "quantity": 1}]
            # payment_method_id отсутствует
        },
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": str(uuid.uuid4())},
    )

    assert r.status_code == 422  # Validation error


# ─── US-ORD-02: Просмотр заказов ─────────────────────────────────────────────

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
        address_snapshot={"id": str(uuid.uuid4()), "created_at": "2024-01-01T00:00:00", "country": "", "city": "Moscow",
                          "street": "Test", "building": "1", "postal_code": "101000", "is_default": False},
        subtotal=1000,
        total=1000,
    )
    db_session.add(order)
    await db_session.flush()

    r = await client.get(f"/api/v1/orders/{order.id}", headers={"Authorization": f"Bearer {token1}"})
    assert r.status_code == 404


# ─── US-ORD-03: Cancel ───────────────────────────────────────────────────────

def make_order_snapshot():
    return {
        "id": str(uuid.uuid4()),
        "created_at": "2024-01-01T00:00:00+00:00",
        "country": "",
        "city": "Moscow",
        "street": "Test",
        "building": "1",
        "postal_code": "101000",
        "is_default": False,
    }


def assert_full_order_response(data: dict) -> None:
    """Проверяет, что тело ответа содержит все обязательные поля OrderResponse."""
    required = ("id", "buyer_id", "status", "items", "subtotal", "total", "address", "created_at")
    for field in required:
        assert field in data, f"Обязательное поле '{field}' отсутствует в ответе отмены"


@pytest.mark.asyncio
async def test_cancel_paid_order_transitions_to_cancelled(client: AsyncClient, db_session: AsyncSession):
    """Happy path PAID → CANCELLED; ответ — полный OrderResponse."""
    buyer, _, token = await create_buyer_with_address(db_session)
    order = Order(
        number="ORD-CANCEL-001",
        buyer_id=buyer.id,
        idempotency_key=uuid.uuid4(),
        status=OrderStatus.PAID,
        address_snapshot=make_order_snapshot(),
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
    assert_full_order_response(data)


@pytest.mark.asyncio
async def test_unreserve_failure_transitions_to_cancel_pending(client: AsyncClient, db_session: AsyncSession):
    """B2B недоступен → CANCEL_PENDING; ответ — полный OrderResponse."""
    buyer, _, token = await create_buyer_with_address(db_session)
    order = Order(
        number="ORD-CANCEL-002",
        buyer_id=buyer.id,
        idempotency_key=uuid.uuid4(),
        status=OrderStatus.PAID,
        address_snapshot=make_order_snapshot(),
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
    assert_full_order_response(data)


@pytest.mark.asyncio
async def test_cancel_assembling_order_succeeds(client: AsyncClient, db_session: AsyncSession):
    """ASSEMBLING теперь отменяем (контракт обновлён 2026-06-14)."""
    buyer, _, token = await create_buyer_with_address(db_session)
    order = Order(
        number="ORD-CANCEL-003",
        buyer_id=buyer.id,
        idempotency_key=uuid.uuid4(),
        status=OrderStatus.ASSEMBLING,
        address_snapshot=make_order_snapshot(),
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
    assert r.json()["status"] == "CANCELLED"


@pytest.mark.asyncio
async def test_cancel_delivered_order_returns_409(client: AsyncClient, db_session: AsyncSession):
    """DELIVERED → 409 CANCEL_NOT_ALLOWED (не отменяем)."""
    buyer, _, token = await create_buyer_with_address(db_session)
    order = Order(
        number="ORD-CANCEL-004",
        buyer_id=buyer.id,
        idempotency_key=uuid.uuid4(),
        status=OrderStatus.DELIVERED,
        address_snapshot={},
        subtotal=0,
        total=0,
    )
    db_session.add(order)
    await db_session.flush()

    r = await client.post(
        f"/api/v1/orders/{order.id}/cancel",
        json={"reason": "too late"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 409
    assert r.json()["code"] == "CANCEL_NOT_ALLOWED"
    assert r.json()["current_status"] == "DELIVERED"


@pytest.mark.asyncio
async def test_other_user_cancel_returns_404(client: AsyncClient, db_session: AsyncSession):
    """Чужой заказ при отмене → 404 (IDOR-защита)."""
    buyer1, _, token1 = await create_buyer_with_address(db_session)
    buyer2 = Buyer(email="other2@test.com", hashed_password=hash_password("pass1234"))
    db_session.add(buyer2)
    await db_session.flush()
    order = Order(
        number="ORD-CANCEL-005",
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


# ─── US-ORD-04: События от B2B ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_event_sku_out_of_stock_updates_cart(client: AsyncClient, db_session: AsyncSession):
    """Событие SKU_OUT_OF_STOCK помечает позиции в корзине как недоступные."""
    from app.models import Cart, CartItem

    buyer, address, token = await create_buyer_with_address(db_session)

    # Создаём корзину с товаром
    cart = Cart(buyer_id=buyer.id)
    db_session.add(cart)
    await db_session.flush()

    sku_id = uuid.uuid4()
    cart_item = CartItem(cart_id=cart.id, sku_id=sku_id, quantity=1)
    db_session.add(cart_item)
    await db_session.flush()

    # Отправляем событие о нехватке
    event_body = {
        "idempotency_key": str(uuid.uuid4()),
        "event_type": "SKU_OUT_OF_STOCK",
        "product_id": str(uuid.uuid4()),
        "sku_ids": [str(sku_id)]
    }

    # Используем сервисный ключ для аутентификации
    r = await client.post(
        "/api/v1/b2b/events",
        json=event_body,
        headers={"X-Service-Key": settings.service_key}
    )

    assert r.status_code == 202

    # Проверяем, что корзина обновлена
    await db_session.refresh(cart_item)
    assert cart_item.unavailable_reason == "SKU_OUT_OF_STOCK"


@pytest.mark.asyncio
async def test_event_wrong_path_returns_404(client: AsyncClient):
    """Старый путь /events/product больше не работает."""
    r = await client.post(
        "/api/v1/events/product",
        json={"idempotency_key": str(uuid.uuid4()), "event_type": "SKU_OUT_OF_STOCK", "sku_ids": []},
        headers={"X-Service-Key": settings.service_key}
    )
    assert r.status_code == 404