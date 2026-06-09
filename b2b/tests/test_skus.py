"""
Тесты для POST /api/v1/skus (US-B2B-02: Создание SKU).

Покрытые сценарии:
  happy:
    - first_sku_transitions_product_to_on_moderation
    - first_sku_emits_created_event_to_moderation
    - second_sku_no_state_change
  unhappy:
    - add_sku_to_hard_blocked_returns_403
    - missing_image_returns_400
"""
import uuid
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select

from app.core.config import settings
from app.core.security import hash_password
from app.models import Category, Image, ImageEntityType, Product, ProductStatus, Seller, SKU
from shared.auth.jwt import create_access_token


# ─── Фикстуры ────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def seller(db_session):
    s = Seller(
        email="sku_seller@example.com",
        hashed_password=hash_password("password123"),
        company_name="SKU Test Co",
        inn="9876543210",
        is_active=True,
    )
    db_session.add(s)
    await db_session.flush()
    return s


@pytest_asyncio.fixture
async def category(db_session):
    c = Category(name="Электроника", slug="electronics", is_active=True, sort_order=0)
    db_session.add(c)
    await db_session.flush()
    return c


@pytest_asyncio.fixture
async def product(db_session, seller, category):
    p = Product(
        seller_id=seller.id,
        category_id=category.id,
        title="Тестовый товар",
        slug="test-product",
        description="Описание",
        status=ProductStatus.CREATED,
        deleted=False,
        blocked=False,
    )
    # product image (required for product creation, not SKU)
    db_session.add(p)
    await db_session.flush()
    img = Image(entity_type=ImageEntityType.PRODUCT, entity_id=p.id, url="/s3/cover.jpg", ordering=0)
    db_session.add(img)
    await db_session.flush()
    return p


@pytest.fixture
def auth_headers(seller):
    token = create_access_token(
        subject=str(seller.id),
        secret_key=settings.secret_key,
        expires_minutes=30,
        extra_claims={"role": "seller"},
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def valid_sku_payload(product):
    return {
        "product_id": str(product.id),
        "name": "256GB Black",
        "price": 12999000,
        "cost_price": 9500000,
        "discount": 0,
        "images": [{"url": "/s3/iphone-black.jpg", "ordering": 0}],
        "characteristics": [{"name": "Цвет", "value": "Чёрный"}],
    }


# ─── Тесты ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_first_sku_transitions_product_to_on_moderation(
    client: AsyncClient, db_session, auth_headers, valid_sku_payload, product
):
    """Первый SKU переводит товар из CREATED в ON_MODERATION."""
    with patch("app.services.skus._send_moderation_event", new_callable=AsyncMock):
        response = await client.post(
            "/api/v1/skus", json=valid_sku_payload, headers=auth_headers
        )

    assert response.status_code == 201, response.text
    await db_session.refresh(product)
    assert product.status == ProductStatus.ON_MODERATION


@pytest.mark.asyncio
async def test_first_sku_emits_created_event_to_moderation(
    client: AsyncClient, db_session, auth_headers, valid_sku_payload, product
):
    """При первом SKU отправляется событие CREATED в Moderation."""
    with patch("app.services.skus._send_moderation_event", new_callable=AsyncMock) as mock_send:
        response = await client.post(
            "/api/v1/skus", json=valid_sku_payload, headers=auth_headers
        )

    assert response.status_code == 201
    mock_send.assert_called_once()
    call_kwargs = mock_send.call_args
    assert call_kwargs.args[1] == "CREATED"  # event_type


@pytest.mark.asyncio
async def test_second_sku_no_state_change(
    client: AsyncClient, db_session, auth_headers, valid_sku_payload, product
):
    """Второй SKU не меняет статус товара (уже ON_MODERATION) и не шлёт CREATED снова."""
    # Переводим товар вручную в ON_MODERATION (как после первого SKU)
    product.status = ProductStatus.ON_MODERATION
    await db_session.flush()

    with patch("app.services.skus._send_moderation_event", new_callable=AsyncMock) as mock_send:
        second_payload = {**valid_sku_payload, "name": "512GB White"}
        response = await client.post(
            "/api/v1/skus", json=second_payload, headers=auth_headers
        )

    assert response.status_code == 201
    await db_session.refresh(product)
    assert product.status == ProductStatus.ON_MODERATION
    mock_send.assert_not_called()


@pytest.mark.asyncio
async def test_add_sku_to_hard_blocked_returns_403(
    client: AsyncClient, db_session, auth_headers, valid_sku_payload, product
):
    """Нельзя добавить SKU к HARD_BLOCKED товару — возвращает 403."""
    product.status = ProductStatus.HARD_BLOCKED
    await db_session.flush()

    response = await client.post(
        "/api/v1/skus", json=valid_sku_payload, headers=auth_headers
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "FORBIDDEN"


@pytest.mark.asyncio
async def test_missing_image_returns_400(
    client: AsyncClient, auth_headers, valid_sku_payload
):
    """Запрос без images возвращает 422."""
    payload = {**valid_sku_payload, "images": []}
    response = await client.post("/api/v1/skus", json=payload, headers=auth_headers)
    assert response.status_code == 422