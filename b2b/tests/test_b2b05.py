"""
Тесты для GET /api/v1/products/{id} (B2B-5: Просмотр карточки товара).

Сценарии:
  happy:
    - get_moderated_product_returns_full_payload
    - get_blocked_product_returns_blocking_reason_and_field_reports
  unhappy:
    - get_others_product_returns_404
    - get_nonexistent_returns_404
"""
import uuid

import pytest
import pytest_asyncio
from httpx import AsyncClient

from app.core.config import settings
from app.models import BlockingReason, Category, Product, ProductStatus, Seller, SKU
from app.core.security import hash_password
from shared.auth.jwt import create_access_token


# ─── Фикстуры ────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def seller(db_session):
    s = Seller(
        email="seller_b2b05@example.com",
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
        email="other_b2b05@example.com",
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
    c = Category(name="Смартфоны", slug="smartphones-b2b05", is_active=True, sort_order=0)
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
        title="iPhone 15 Pro Max",
        slug="iphone-15-pro-max-b2b05",
        description="Флагман Apple",
        status=ProductStatus.MODERATED,
        deleted=False,
        blocked=False,
    )
    db_session.add(p)
    await db_session.flush()
    return p


@pytest_asyncio.fixture
async def blocking_reason(db_session):
    br = BlockingReason(
        title="Описание не соответствует товару",
        comment="Фото не совпадает с описанием",
        is_active=True,
    )
    db_session.add(br)
    await db_session.flush()
    return br


@pytest_asyncio.fixture
async def blocked_product(db_session, seller, category, blocking_reason):
    p = Product(
        seller_id=seller.id,
        category_id=category.id,
        title="Levi's 501",
        slug="levis-501-b2b05",
        description="Джинсы",
        status=ProductStatus.BLOCKED,
        deleted=False,
        blocked=True,
        blocking_reason_id=blocking_reason.id,
        moderator_comment="Фото не совпадает",
    )
    db_session.add(p)
    await db_session.flush()
    return p


# ─── Тесты ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_moderated_product_returns_full_payload(
    client: AsyncClient, moderated_product, auth_headers
):
    response = await client.get(
        f"/api/v1/products/{moderated_product.id}",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == str(moderated_product.id)
    assert data["status"] == "MODERATED"
    # ProductDetailResponse — обязательные поля по OpenAPI
    assert data["blocked"] is False
    assert data["blocking_reason"] is None
    assert data["field_reports"] == []
    assert isinstance(data["skus"], list)


@pytest.mark.asyncio
async def test_get_blocked_product_returns_blocking_reason_and_field_reports(
    client: AsyncClient, blocked_product, blocking_reason, auth_headers
):
    response = await client.get(
        f"/api/v1/products/{blocked_product.id}",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "BLOCKED"
    assert data["blocked"] is True
    # blocking_reason — объект, не просто id
    assert data["blocking_reason"] is not None
    assert data["blocking_reason"]["id"] == str(blocking_reason.id)
    assert data["blocking_reason"]["title"] == blocking_reason.title
    # нет legacy-полей
    assert "blocking_reason_id" not in data
    assert "moderator_comment" not in data
    # field_reports — массив
    assert isinstance(data["field_reports"], list)


@pytest.mark.asyncio
async def test_get_others_product_returns_404(
    client: AsyncClient, moderated_product, other_auth_headers
):
    response = await client.get(
        f"/api/v1/products/{moderated_product.id}",
        headers=other_auth_headers,
    )
    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"


@pytest.mark.asyncio
async def test_get_nonexistent_returns_404(
    client: AsyncClient, auth_headers
):
    response = await client.get(
        f"/api/v1/products/{uuid.uuid4()}",
        headers=auth_headers,
    )
    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"