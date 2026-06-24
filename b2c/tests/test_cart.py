import pytest
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Buyer, Cart, CartItem
from app.core.security import hash_password
from shared.auth.jwt import create_access_token
from app.core.config import settings


async def create_buyer(db: AsyncSession, email: str = "test@test.com") -> tuple[Buyer, str]:
    buyer = Buyer(email=email, hashed_password=hash_password("password123"))
    db.add(buyer)
    await db.flush()
    token = create_access_token(
        subject=str(buyer.id),
        secret_key=settings.secret_key,
        extra_claims={"role": "buyer"},
    )
    return buyer, token


SKU = "00000000-0000-0000-0000-000000000010"
PRODUCT = "00000000-0000-0000-0000-000000000011"


def _mock_product(*, sku_id: str | None = None, price: int = 1000, stock: int = 10,
                  reserved: int = 0, title: str = "Test Product",
                  status: str = "MODERATED", deleted: bool = False) -> dict:
    sku_id = sku_id or SKU
    return {
        "id": PRODUCT,
        "title": title,
        "status": status,
        "deleted": deleted,
        "skus": [{
            "id": sku_id,
            "price": price,
            "quantity": stock,
            "reserved_quantity": reserved,
        }],
    }


def _mock_b2b(**kwargs):
    """Decorator/context-manager helper: patch b2b_client.get_product."""
    return patch("app.services.b2b_client.get_product", AsyncMock(return_value=_mock_product(**kwargs)))


# ─────────────────────────────
# US-CART-03 DoD tests
# ─────────────────────────────

@pytest.mark.asyncio
async def test_add_sku_increments_quantity_if_already_in_cart(client: AsyncClient, db_session: AsyncSession):
    """Добавление одного SKU дважды увеличивает quantity."""
    buyer, token = await create_buyer(db_session, "inc@test.com")
    payload = {"sku_id": SKU, "product_id": PRODUCT, "quantity": 1}

    with _mock_b2b(price=500):
        r1 = await client.post("/api/v1/cart/items", json=payload, headers={"Authorization": f"Bearer {token}"})
    assert r1.status_code == 200
    data1 = r1.json()
    assert data1["items"][0]["quantity"] == 1
    assert data1["items_count"] == 1
    assert data1["subtotal"] == 500
    assert data1["is_valid"] is True

    with _mock_b2b(price=500):
        r2 = await client.post("/api/v1/cart/items", json=payload, headers={"Authorization": f"Bearer {token}"})
    assert r2.status_code == 200
    data2 = r2.json()
    assert data2["items"][0]["quantity"] == 2
    assert data2["subtotal"] == 1000
    assert data2["items_count"] == 1


@pytest.mark.asyncio
async def test_get_cart_enriched_with_b2b_data(client: AsyncClient, db_session: AsyncSession):
    """GET /cart возвращает обогащённые данные из B2B и правильную сумму."""
    buyer, token = await create_buyer(db_session, "enrich@test.com")

    with _mock_b2b(price=1500):
        await client.post(
            "/api/v1/cart/items",
            json={"sku_id": SKU, "product_id": PRODUCT, "quantity": 2},
            headers={"Authorization": f"Bearer {token}"},
        )

    with _mock_b2b(price=1500):
        r = await client.get("/api/v1/cart", headers={"Authorization": f"Bearer {token}"})

    assert r.status_code == 200
    data = r.json()
    assert len(data["items"]) == 1
    item = data["items"][0]
    assert item["name"] == "Test Product"
    assert item["unit_price"] == 1500
    assert item["line_total"] == 3000
    assert item["available_quantity"] == 10
    assert item["is_available"] is True
    assert item["available"] is True
    assert data["items_count"] == 1
    assert data["subtotal"] == 3000
    assert data["is_valid"] is True


@pytest.mark.asyncio
async def test_unavailable_sku_shown_with_reason(client: AsyncClient, db_session: AsyncSession):
    """Товар c product.status != MODERATED помечается как недоступный."""
    buyer, token = await create_buyer(db_session, "unavail@test.com")

    with _mock_b2b():
        await client.post(
            "/api/v1/cart/items",
            json={"sku_id": SKU, "product_id": PRODUCT, "quantity": 1},
            headers={"Authorization": f"Bearer {token}"},
        )

    # B2B вернул None — товар не найден
    with patch("app.services.b2b_client.get_product", AsyncMock(return_value=None)):
        r = await client.get("/api/v1/cart", headers={"Authorization": f"Bearer {token}"})

    assert r.status_code == 200
    data = r.json()
    assert len(data["items"]) == 1
    item = data["items"][0]
    assert item["available"] is False
    assert item["unavailable_reason"] == "PRODUCT_BLOCKED"
    # Обязательные поля spec даже для недоступных
    assert "name" in item
    assert "unit_price" in item
    assert "line_total" in item
    assert "available_quantity" in item
    assert "is_available" in item
    assert data["subtotal"] == 0
    assert data["items_count"] == 1


@pytest.mark.asyncio
async def test_guest_cart_merged_on_login(client: AsyncClient, db_session: AsyncSession):
    """Гостевая корзина сливается с авторизованной при /cart/merge."""
    buyer, token = await create_buyer(db_session, "merge@test.com")
    session_id = "merge-session-001"

    # Шаг 1: авторизованный добавляет товар (quantity=1)
    with _mock_b2b():
        await client.post(
            "/api/v1/cart/items",
            json={"sku_id": SKU, "product_id": PRODUCT, "quantity": 1},
            headers={"Authorization": f"Bearer {token}"},
        )

    # Шаг 2: гость на том же session_id добавляет тот же SKU (quantity=3)
    with _mock_b2b():
        await client.post(
            "/api/v1/cart/items",
            json={"sku_id": SKU, "product_id": PRODUCT, "quantity": 3},
            headers={"X-Session-Id": session_id},
        )

    # Шаг 3: merge — берём MAX(1, 3) = 3
    r = await client.post(
        "/api/v1/cart/merge",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Session-Id": session_id,
        },
    )
    assert r.status_code == 200
    assert r.json()["merged"] == 1

    # Шаг 4: проверяем quantity стал 3
    with _mock_b2b(stock=20):
        r = await client.get("/api/v1/cart", headers={"Authorization": f"Bearer {token}"})

    assert r.status_code == 200
    data = r.json()
    assert len(data["items"]) == 1
    assert data["items"][0]["quantity"] == 3

    # Сессионная корзина удалена
    with _mock_b2b(stock=20):
        guest_r = await client.get("/api/v1/cart", headers={"X-Session-Id": session_id})
    assert guest_r.status_code == 200
    assert len(guest_r.json()["items"]) == 0


# ─────────────────────────────
# DELETE /cart/items/{sku_id} returns CartOut
# ─────────────────────────────

@pytest.mark.asyncio
async def test_delete_cart_item_returns_cart(client: AsyncClient, db_session: AsyncSession):
    """DELETE /cart/items/{sku_id} возвращает 200 с CartOut."""
    buyer, token = await create_buyer(db_session, "del@test.com")

    with _mock_b2b():
        await client.post(
            "/api/v1/cart/items",
            json={"sku_id": SKU, "product_id": PRODUCT, "quantity": 2},
            headers={"Authorization": f"Bearer {token}"},
        )

    with _mock_b2b():
        r = await client.delete(
            f"/api/v1/cart/items/{SKU}",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert r.status_code == 200
    data = r.json()
    assert len(data["items"]) == 0
    assert data["subtotal"] == 0
    assert data["items_count"] == 0
    assert data["is_valid"] is True
