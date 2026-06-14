"""
Тесты для POST /api/v1/invoices (B2B-6: Создание накладной).

Сценарии:
  happy:
    - create_invoice_with_moderated_sku_returns_201
  unhappy:
    - empty_items_returns_400
    - non_moderated_sku_returns_400
    - others_sku_returns_403
"""
import uuid

import pytest
import pytest_asyncio
from httpx import AsyncClient

from app.core.config import settings
from app.core.security import hash_password
from app.models import Category, Product, ProductStatus, Seller, SKU
from shared.auth.jwt import create_access_token


# ─── Фикстуры ────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def seller(db_session):
    s = Seller(
        email="seller_b2b06@example.com",
        hashed_password=hash_password("password123"),
        company_name="Test Co",
        inn="1234567890",
        is_active=True,
    )
    db_session.add(s)
    await db_session.flush()
    return s


@pytest_asyncio.fixture
async def other_seller(db_session):
    s = Seller(
        email="other_b2b06@example.com",
        hashed_password=hash_password("password123"),
        company_name="Other Co",
        inn="0987654321",
        is_active=True,
    )
    db_session.add(s)
    await db_session.flush()
    return s


@pytest_asyncio.fixture
async def category(db_session):
    c = Category(
        name="Смартфоны b2b06",
        slug="smartphones-b2b06",
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


@pytest_asyncio.fixture
async def moderated_product(db_session, seller, category):
    p = Product(
        seller_id=seller.id,
        category_id=category.id,
        title="iPhone 15",
        slug="iphone-15-b2b06",
        description="Смартфон Apple",
        status=ProductStatus.MODERATED,
        deleted=False,
        blocked=False,
    )
    db_session.add(p)
    await db_session.flush()
    return p


@pytest_asyncio.fixture
async def created_product(db_session, seller, category):
    p = Product(
        seller_id=seller.id,
        category_id=category.id,
        title="Galaxy S24",
        slug="galaxy-s24-b2b06",
        description="Смартфон Samsung",
        status=ProductStatus.CREATED,
        deleted=False,
        blocked=False,
    )
    db_session.add(p)
    await db_session.flush()
    return p


@pytest_asyncio.fixture
async def moderated_sku(db_session, moderated_product):
    sku = SKU(
        product_id=moderated_product.id,
        name="128GB Black",
        price=9999000,
        cost_price=7000000,
        discount=0,
        quantity=0,
        reserved_quantity=0,
    )
    db_session.add(sku)
    await db_session.flush()
    return sku


@pytest_asyncio.fixture
async def non_moderated_sku(db_session, created_product):
    sku = SKU(
        product_id=created_product.id,
        name="128GB White",
        price=8999000,
        cost_price=6000000,
        discount=0,
        quantity=0,
        reserved_quantity=0,
    )
    db_session.add(sku)
    await db_session.flush()
    return sku


@pytest_asyncio.fixture
async def other_sellers_moderated_product(db_session, other_seller, category):
    p = Product(
        seller_id=other_seller.id,
        category_id=category.id,
        title="Pixel 8",
        slug="pixel-8-b2b06",
        description="Смартфон Google",
        status=ProductStatus.MODERATED,
        deleted=False,
        blocked=False,
    )
    db_session.add(p)
    await db_session.flush()
    return p


@pytest_asyncio.fixture
async def other_sellers_sku(db_session, other_sellers_moderated_product):
    sku = SKU(
        product_id=other_sellers_moderated_product.id,
        name="256GB",
        price=7999000,
        cost_price=5000000,
        discount=0,
        quantity=0,
        reserved_quantity=0,
    )
    db_session.add(sku)
    await db_session.flush()
    return sku


# ─── Тесты ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_invoice_with_moderated_sku_returns_201(
    client: AsyncClient, moderated_sku, auth_headers
):
    response = await client.post(
        "/api/v1/invoices",
        json={"items": [{"sku_id": str(moderated_sku.id), "quantity": 10}]},
        headers=auth_headers,
    )
    assert response.status_code == 201
    data = response.json()
    assert data["status"] == "CREATED"
    assert len(data["items"]) == 1
    assert data["items"][0]["quantity"] == 10
    assert data["items"][0]["accepted_quantity"] is None


@pytest.mark.asyncio
async def test_empty_items_returns_400(
    client: AsyncClient, auth_headers
):
    response = await client.post(
        "/api/v1/invoices",
        json={"items": []},
        headers=auth_headers,
    )
    assert response.status_code == 422  # Pydantic min_length=1


@pytest.mark.asyncio
async def test_non_moderated_sku_returns_400(
    client: AsyncClient, non_moderated_sku, auth_headers
):
    response = await client.post(
        "/api/v1/invoices",
        json={"items": [{"sku_id": str(non_moderated_sku.id), "quantity": 5}]},
        headers=auth_headers,
    )
    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_REQUEST"


@pytest.mark.asyncio
async def test_others_sku_returns_403(
    client: AsyncClient, other_sellers_sku, auth_headers
):
    response = await client.post(
        "/api/v1/invoices",
        json={"items": [{"sku_id": str(other_sellers_sku.id), "quantity": 3}]},
        headers=auth_headers,
    )
    assert response.status_code == 403
    assert response.json()["code"] == "NOT_OWNER"