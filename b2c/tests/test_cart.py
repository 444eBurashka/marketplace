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


# ─────────────────────────────
# US-CART-03 DoD tests
# ─────────────────────────────

@pytest.mark.asyncio
async def test_add_sku_increments_quantity_if_already_in_cart(client: AsyncClient, db_session: AsyncSession):
    """Добавление одного SKU дважды увеличивает quantity."""
    buyer, token = await create_buyer(db_session, "inc@test.com")
    payload = {"sku_id": SKU, "product_id": PRODUCT, "quantity": 1}

    r1 = await client.post("/api/v1/cart/items", json=payload, headers={"Authorization": f"Bearer {token}"})
    assert r1.status_code == 201
    assert r1.json()["quantity"] == 1

    r2 = await client.post("/api/v1/cart/items", json=payload, headers={"Authorization": f"Bearer {token}"})
    assert r2.status_code == 201
    assert r2.json()["quantity"] == 2


@pytest.mark.asyncio
async def test_get_cart_enriched_with_b2b_data(client: AsyncClient, db_session: AsyncSession):
    """GET /cart возвращает обогащённые данные из B2B и правильную сумму."""
    buyer, token = await create_buyer(db_session, "enrich@test.com")
    await client.post(
        "/api/v1/cart/items",
        json={"sku_id": SKU, "product_id": PRODUCT, "quantity": 2},
        headers={"Authorization": f"Bearer {token}"},
    )

    mock_data = _mock_product(price=1500)
    with patch("app.services.b2b_client.get_product", AsyncMock(return_value=mock_data)):
        r = await client.get("/api/v1/cart", headers={"Authorization": f"Bearer {token}"})

    assert r.status_code == 200
    data = r.json()
    assert len(data["items"]) == 1
    item = data["items"][0]
    assert item["title"] == "Test Product"
    assert item["price"] == 1500
    assert item["available"] is True
    assert item["quantity"] == 2
    assert data["total_amount"] == 3000  # 1500 * 2
    # Нет лишних ключей из старой реализации
    assert "subtotal" not in data
    assert "items_count" not in data
    assert "is_valid" not in data


@pytest.mark.asyncio
async def test_unavailable_sku_shown_with_reason(client: AsyncClient, db_session: AsyncSession):
    """Товар c product.status != MODERATED помечается как недоступный."""
    buyer, token = await create_buyer(db_session, "unavail@test.com")
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
    assert data["items"][0]["available"] is False
    assert data["items"][0]["unavailable_reason"] == "PRODUCT_BLOCKED"
    assert data["total_amount"] == 0


@pytest.mark.asyncio
async def test_guest_cart_merged_on_login(client: AsyncClient, db_session: AsyncSession):
    """Гостевая корзина сливается с авторизованной при /cart/merge."""
    buyer, token = await create_buyer(db_session, "merge@test.com")
    session_id = "merge-session-001"

    # Шаг 1: авторизованный добавляет товар (quantity=1)
    await client.post(
        "/api/v1/cart/items",
        json={"sku_id": SKU, "product_id": PRODUCT, "quantity": 1},
        headers={"Authorization": f"Bearer {token}"},
    )

    # Шаг 2: гость на том же session_id добавляет тот же SKU (quantity=3)
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

    # Шаг 4: проверяем, что quantity стал 3 и гостевая корзина удалена
    mock_data = _mock_product(stock=20)
    with patch("app.services.b2b_client.get_product", AsyncMock(return_value=mock_data)):
        r = await client.get("/api/v1/cart", headers={"Authorization": f"Bearer {token}"})

    assert r.status_code == 200
    data = r.json()
    assert len(data["items"]) == 1
    assert data["items"][0]["quantity"] == 3

    # Сессионная корзина удалена — по session_id пусто
    with patch("app.services.b2b_client.get_product", AsyncMock(return_value=mock_data)):
        guest_r = await client.get("/api/v1/cart", headers={"X-Session-Id": session_id})
    assert guest_r.status_code == 200
    assert len(guest_r.json()["items"]) == 0
