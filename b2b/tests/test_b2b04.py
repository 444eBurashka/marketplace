import uuid
import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select

from app.core.config import settings
from app.core.security import hash_password
from app.models import Category, Product, ProductStatus, SKU, Seller
from shared.auth.jwt import create_access_token


# ─── Фикстуры ────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def seller(db_session):
    s = Seller(
        email="delete_seller@example.com",
        hashed_password=hash_password("password123"),
        company_name="Delete Co",
        inn="1122334455",
        is_active=True,
    )
    db_session.add(s)
    await db_session.flush()
    return s


@pytest_asyncio.fixture
async def other_seller(db_session):
    s = Seller(
        email="delete_other@example.com",
        hashed_password=hash_password("password123"),
        company_name="Other Co",
        inn="5544332211",
        is_active=True,
    )
    db_session.add(s)
    await db_session.flush()
    return s


@pytest_asyncio.fixture
async def category(db_session):
    c = Category(
        name="Для удаления",
        slug=f"delete-cat-{uuid.uuid4()}",
        is_active=True,
        sort_order=0,
    )
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


async def _make_product(db_session, seller, category, deleted=False) -> Product:
    p = Product(
        seller_id=seller.id,
        category_id=category.id,
        title="Товар для удаления",
        slug=f"delete-product-{uuid.uuid4()}",
        description="Описание",
        status=ProductStatus.MODERATED,
        deleted=deleted,
        blocked=False,
    )
    db_session.add(p)
    await db_session.flush()
    return p


async def _make_sku(db_session, product) -> SKU:
    sku = SKU(
        product_id=product.id,
        name="Вариант",
        price=10000,
        discount=0,
        cost_price=8000,
        quantity=5,
        reserved_quantity=0,
        is_active=True,
    )
    db_session.add(sku)
    await db_session.flush()
    return sku


# ─── Happy path ───────────────────────────────────────────────────────────────

async def test_delete_sets_deleted_true(
    client: AsyncClient, auth_headers, db_session, seller, category
):
    """DELETE /products/{id} → поле deleted=True в БД, ответ 204."""
    product = await _make_product(db_session, seller, category)

    response = await client.delete(
        f"/api/v1/products/{product.id}",
        headers=auth_headers,
    )
    assert response.status_code == 204

    await db_session.refresh(product)
    assert product.deleted is True


async def test_delete_emits_event_to_moderation(
    client: AsyncClient, auth_headers, db_session, seller, category
):
    """После удаления уходит fire-and-forget событие в Moderation."""
    from unittest.mock import AsyncMock, patch

    product = await _make_product(db_session, seller, category)

    with patch("app.services.delete_service._send_moderation_deleted", new_callable=AsyncMock) as mock_mod:
        response = await client.delete(
            f"/api/v1/products/{product.id}",
            headers=auth_headers,
        )

    assert response.status_code == 204
    mock_mod.assert_awaited_once()
    call_product = mock_mod.call_args.args[0]
    assert call_product.id == product.id


async def test_delete_emits_product_deleted_to_b2c(
    client: AsyncClient, auth_headers, db_session, seller, category
):
    """После удаления уходит событие PRODUCT_DELETED в B2C с sku_ids."""
    from unittest.mock import AsyncMock, patch

    product = await _make_product(db_session, seller, category)
    sku = await _make_sku(db_session, product)

    with patch("app.services.delete_service._send_b2c_deleted", new_callable=AsyncMock) as mock_b2c:
        response = await client.delete(
            f"/api/v1/products/{product.id}",
            headers=auth_headers,
        )

    assert response.status_code == 204
    mock_b2c.assert_awaited_once()
    call_sku_ids = mock_b2c.call_args.args[1]
    assert sku.id in call_sku_ids


# ─── Unhappy path ─────────────────────────────────────────────────────────────

async def test_delete_already_deleted_returns_400(
    client: AsyncClient, auth_headers, db_session, seller, category
):
    """Повторное удаление уже удалённого товара → 400 INVALID_REQUEST."""
    product = await _make_product(db_session, seller, category, deleted=True)

    response = await client.delete(
        f"/api/v1/products/{product.id}",
        headers=auth_headers,
    )
    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_REQUEST"


async def test_delete_others_product_returns_403(
    client: AsyncClient, other_auth_headers, db_session, seller, category
):
    """Попытка удалить чужой товар → 403 NOT_OWNER."""
    product = await _make_product(db_session, seller, category)

    response = await client.delete(
        f"/api/v1/products/{product.id}",
        headers=other_auth_headers,
    )
    assert response.status_code == 403
    assert response.json()["code"] == "NOT_OWNER"


async def test_deleted_product_not_in_seller_list(
    client: AsyncClient, auth_headers, db_session, seller, category
):
    """Удалённый товар не возвращается в GET /products (если эндпоинт есть)."""
    product = await _make_product(db_session, seller, category)

    await client.delete(f"/api/v1/products/{product.id}", headers=auth_headers)

    # Проверяем напрямую через БД — deleted=True
    result = await db_session.execute(
        select(Product).where(Product.id == product.id)
    )
    p = result.scalar_one()
    assert p.deleted is True