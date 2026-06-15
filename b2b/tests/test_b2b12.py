"""
Тесты для DELETE /api/v1/skus/{sku_id} (B2B-12: Удаление SKU).

Сценарии:
  happy:
    - delete_sku_succeeds
    - last_sku_on_moderation_transitions_product_to_created
  unhappy:
    - delete_sku_with_active_reserves_returns_409
    - delete_sku_hard_blocked_product_returns_403
    - sku_out_of_stock_event_on_moderated_product
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
        email="seller_b2b12@example.com",
        hashed_password=hash_password("password123"),
        company_name="Delete SKU Co",
        inn="3333333333",
        is_active=True,
    )
    db_session.add(s)
    await db_session.flush()
    return s


@pytest_asyncio.fixture
async def other_seller(db_session):
    s = Seller(
        email="other_b2b12@example.com",
        hashed_password=hash_password("password123"),
        company_name="Other Co B2B12",
        inn="4444444444",
        is_active=True,
    )
    db_session.add(s)
    await db_session.flush()
    return s


@pytest_asyncio.fixture
async def category(db_session):
    c = Category(name="Гаджеты", slug="gadgets-b2b12", is_active=True, sort_order=0)
    db_session.add(c)
    await db_session.flush()
    return c


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
def other_auth_headers(other_seller):
    token = create_access_token(
        subject=str(other_seller.id),
        secret_key=settings.secret_key,
        expires_minutes=30,
        extra_claims={"role": "seller"},
    )
    return {"Authorization": f"Bearer {token}"}


async def make_product(db_session, seller, category, *, status=ProductStatus.MODERATED, slug_suffix=""):
    slug = f"product-b2b12-{slug_suffix or str(uuid.uuid4())[:8]}"
    p = Product(
        seller_id=seller.id,
        category_id=category.id,
        title="Test Product B2B12",
        slug=slug,
        description="Test description",
        status=status,
        deleted=False,
        blocked=False,
    )
    db_session.add(p)
    await db_session.flush()
    return p


async def make_sku(db_session, product, *, reserved_quantity=0, quantity=10, is_active=True):
    sku = SKU(
        product_id=product.id,
        name="Test SKU",
        price=9990000,
        cost_price=5000000,
        discount=0,
        quantity=quantity,
        reserved_quantity=reserved_quantity,
        is_active=is_active,
    )
    db_session.add(sku)
    # Добавляем изображение SKU
    await db_session.flush()
    img = Image(
        entity_type=ImageEntityType.SKU,
        entity_id=sku.id,
        url=f"/s3/sku-{sku.id}.jpg",
        ordering=0,
    )
    db_session.add(img)
    await db_session.flush()
    return sku


# ─── Тесты ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_delete_sku_succeeds(client: AsyncClient, db_session, seller, category, auth_headers):
    """delete_sku_succeeds — happy path, SKU помечается неактивным."""
    product = await make_product(db_session, seller, category, slug_suffix="del-ok")
    sku = await make_sku(db_session, product)

    resp = await client.delete(f"/api/v1/skus/{sku.id}", headers=auth_headers)
    assert resp.status_code == 204

    # SKU помечен is_active=False
    await db_session.refresh(sku)
    assert sku.is_active is False


@pytest.mark.asyncio
async def test_delete_sku_with_active_reserves_returns_409(
    client: AsyncClient, db_session, seller, category, auth_headers
):
    """delete_sku_with_active_reserves_returns_409 — reserved_quantity > 0 → 409."""
    product = await make_product(db_session, seller, category, slug_suffix="del-res")
    sku = await make_sku(db_session, product, reserved_quantity=3, quantity=10)

    resp = await client.delete(f"/api/v1/skus/{sku.id}", headers=auth_headers)
    assert resp.status_code == 409
    assert resp.json()["code"] == "CONFLICT"

    # SKU не удалён
    await db_session.refresh(sku)
    assert sku.is_active is True


@pytest.mark.asyncio
async def test_last_sku_on_moderation_transitions_product_to_created(
    client: AsyncClient, db_session, seller, category, auth_headers
):
    """last_sku_on_moderation_transitions_product_to_created — последний SKU удалён + ON_MODERATION → CREATED + событие PRODUCT_DELETED в Moderation."""
    product = await make_product(
        db_session, seller, category,
        status=ProductStatus.ON_MODERATION,
        slug_suffix="del-last",
    )
    sku = await make_sku(db_session, product, quantity=5, reserved_quantity=0)

    with patch("app.services.skus._send_moderation_deleted_event", new_callable=AsyncMock) as mock_mod:
        resp = await client.delete(f"/api/v1/skus/{sku.id}", headers=auth_headers)

    assert resp.status_code == 204

    # Товар вернулся в CREATED
    await db_session.refresh(product)
    assert product.status == ProductStatus.CREATED

    # Событие отправлено в Moderation с верным продуктом
    mock_mod.assert_awaited_once()
    assert mock_mod.call_args.args[0].id == product.id


@pytest.mark.asyncio
async def test_moderation_deleted_event_payload_structure(
    client: AsyncClient, db_session, seller, category, auth_headers
):
    """Проверяем структуру тела события PRODUCT_DELETED в Moderation: путь и IncomingB2BEvent."""
    from unittest.mock import AsyncMock, MagicMock, patch

    product = await make_product(
        db_session, seller, category,
        status=ProductStatus.ON_MODERATION,
        slug_suffix="del-payload",
    )
    sku = await make_sku(db_session, product, quantity=3, reserved_quantity=0)

    captured = {}

    mock_response = MagicMock()
    mock_response.status_code = 200

    mock_post = AsyncMock(return_value=mock_response)
    mock_http_client = AsyncMock()
    mock_http_client.post = mock_post
    mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
    mock_http_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_http_client):
        resp = await client.delete(f"/api/v1/skus/{sku.id}", headers=auth_headers)

    assert resp.status_code == 204
    mock_post.assert_awaited_once()

    call_url = mock_post.call_args.args[0]
    body = mock_post.call_args.kwargs.get("json", {})

    assert call_url.endswith("/api/v1/b2b/events")
    assert body["event_type"] == "PRODUCT_DELETED"
    assert "occurred_at" in body
    assert "idempotency_key" in body
    assert body["payload"]["product_id"] == str(product.id)
    assert body["payload"]["seller_id"] == str(product.seller_id)


@pytest.mark.asyncio
async def test_delete_sku_hard_blocked_product_returns_403(
    client: AsyncClient, db_session, seller, category, auth_headers
):
    """delete_sku_hard_blocked_product_returns_403 — товар HARD_BLOCKED → 403."""
    product = await make_product(
        db_session, seller, category,
        status=ProductStatus.HARD_BLOCKED,
        slug_suffix="del-hb",
    )
    sku = await make_sku(db_session, product)

    resp = await client.delete(f"/api/v1/skus/{sku.id}", headers=auth_headers)
    assert resp.status_code == 403
    assert resp.json()["code"] == "FORBIDDEN"


@pytest.mark.asyncio
async def test_sku_out_of_stock_event_on_moderated_product(
    client: AsyncClient, db_session, seller, category, auth_headers
):
    """sku_out_of_stock_event_on_moderated_product — active_quantity > 0 + MODERATED → SKU_OUT_OF_STOCK в B2C."""
    product = await make_product(
        db_session, seller, category,
        status=ProductStatus.MODERATED,
        slug_suffix="del-oos",
    )
    sku = await make_sku(db_session, product, quantity=10, reserved_quantity=0)

    with patch("app.services.skus._send_b2c_sku_out_of_stock", new_callable=AsyncMock) as mock_b2c:
        resp = await client.delete(f"/api/v1/skus/{sku.id}", headers=auth_headers)

    assert resp.status_code == 204
    mock_b2c.assert_awaited_once()
    assert mock_b2c.call_args.args[1] == sku.id


@pytest.mark.asyncio
async def test_b2c_out_of_stock_event_payload_structure(
    client: AsyncClient, db_session, seller, category, auth_headers
):
    """Проверяем структуру тела события SKU_OUT_OF_STOCK в B2C: путь и IncomingB2BEvent."""
    from unittest.mock import AsyncMock, MagicMock, patch

    product = await make_product(
        db_session, seller, category,
        status=ProductStatus.MODERATED,
        slug_suffix="del-oos-payload",
    )
    sku = await make_sku(db_session, product, quantity=5, reserved_quantity=0)

    mock_response = MagicMock()
    mock_response.status_code = 200

    mock_post = AsyncMock(return_value=mock_response)
    mock_http_client = AsyncMock()
    mock_http_client.post = mock_post
    mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
    mock_http_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_http_client):
        resp = await client.delete(f"/api/v1/skus/{sku.id}", headers=auth_headers)

    assert resp.status_code == 204
    mock_post.assert_awaited_once()

    call_url = mock_post.call_args.args[0]
    body = mock_post.call_args.kwargs.get("json", {})

    assert call_url.endswith("/api/v1/b2b/events")
    assert body["event_type"] == "SKU_OUT_OF_STOCK"
    assert "occurred_at" in body
    assert "idempotency_key" in body
    assert body["payload"]["product_id"] == str(product.id)
    assert body["payload"]["sku_id"] == str(sku.id)
    assert body["payload"]["available_quantity"] == 0


@pytest.mark.asyncio
async def test_delete_sku_not_found(client: AsyncClient, db_session, seller, auth_headers):
    """Несуществующий SKU → 404."""
    resp = await client.delete(f"/api/v1/skus/{uuid.uuid4()}", headers=auth_headers)
    assert resp.status_code == 404
    assert resp.json()["code"] == "NOT_FOUND"


@pytest.mark.asyncio
async def test_delete_sku_others_product_returns_403(
    client: AsyncClient, db_session, seller, other_seller, category, other_auth_headers
):
    """Чужой SKU → 403 NOT_OWNER."""
    product = await make_product(db_session, seller, category, slug_suffix="del-idor")
    sku = await make_sku(db_session, product)

    # other_seller пытается удалить SKU seller-а
    resp = await client.delete(f"/api/v1/skus/{sku.id}", headers=other_auth_headers)
    assert resp.status_code == 403
    assert resp.json()["code"] == "NOT_OWNER"


@pytest.mark.asyncio
async def test_delete_last_sku_moderated_no_transition(
    client: AsyncClient, db_session, seller, category, auth_headers
):
    """Последний SKU MODERATED-товара: товар НЕ уходит в CREATED (только ON_MODERATION триггерит)."""
    product = await make_product(
        db_session, seller, category,
        status=ProductStatus.MODERATED,
        slug_suffix="del-mod-last",
    )
    sku = await make_sku(db_session, product, quantity=0, reserved_quantity=0)

    resp = await client.delete(f"/api/v1/skus/{sku.id}", headers=auth_headers)
    assert resp.status_code == 204

    # Статус товара не изменился — только ON_MODERATION триггерит переход
    await db_session.refresh(product)
    assert product.status == ProductStatus.MODERATED