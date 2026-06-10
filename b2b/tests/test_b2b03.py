import uuid
import pytest
import pytest_asyncio
from httpx import AsyncClient
from app.core.config import settings
from app.core.security import hash_password
from app.models import Category, Product, ProductStatus, SKU, Seller
from shared.auth.jwt import create_access_token


# ─── Фикстуры ────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def seller(db_session):
    s = Seller(
        email="edit_seller@example.com",
        hashed_password=hash_password("password123"),
        company_name="Edit Co",
        inn="9876543210",
        is_active=True,
    )
    db_session.add(s)
    await db_session.flush()
    return s


@pytest_asyncio.fixture
async def other_seller(db_session):
    s = Seller(
        email="other_seller@example.com",
        hashed_password=hash_password("password123"),
        company_name="Other Co",
        inn="1111111111",
        is_active=True,
    )
    db_session.add(s)
    await db_session.flush()
    return s


@pytest_asyncio.fixture
async def category(db_session):
    c = Category(name="Электроника", slug="electronics-edit", is_active=True, sort_order=0)
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


async def _make_product(db_session, seller, category, status: ProductStatus) -> Product:
    p = Product(
        seller_id=seller.id,
        category_id=category.id,
        title="Test Product",
        slug=f"test-product-{uuid.uuid4()}",
        description="Описание",
        status=status,
        deleted=False,
        blocked=False,
    )
    db_session.add(p)
    await db_session.flush()
    return p


async def _make_sku(db_session, product, reserved: int = 0) -> SKU:
    sku = SKU(
        product_id=product.id,
        name="256GB Black",
        price=10000,
        discount=0,
        cost_price=8000,
        quantity=10,
        reserved_quantity=reserved,
        is_active=True,
    )
    db_session.add(sku)
    await db_session.flush()
    return sku


# ─── Happy path ───────────────────────────────────────────────────────────────

async def test_edit_moderated_product_returns_to_on_moderation(
    client: AsyncClient, auth_headers, db_session, seller, category
):
    """MODERATED → ON_MODERATION после PATCH /products/{id}"""
    product = await _make_product(db_session, seller, category, ProductStatus.MODERATED)

    response = await client.patch(
        f"/api/v1/products/{product.id}",
        json={"title": "Новое название"},
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "ON_MODERATION"


async def test_edit_blocked_product_returns_to_on_moderation(
    client: AsyncClient, auth_headers, db_session, seller, category
):
    """BLOCKED → ON_MODERATION после PATCH /products/{id}"""
    product = await _make_product(db_session, seller, category, ProductStatus.BLOCKED)

    response = await client.patch(
        f"/api/v1/products/{product.id}",
        json={"description": "Исправленное описание"},
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "ON_MODERATION"


async def test_reserves_preserved_after_sku_edit(
    client: AsyncClient, auth_headers, db_session, seller, category
):
    """reserved_quantity не меняется при PATCH /skus/{id}"""
    product = await _make_product(db_session, seller, category, ProductStatus.MODERATED)
    sku = await _make_sku(db_session, product, reserved=3)

    response = await client.patch(
        f"/api/v1/skus/{sku.id}",
        json={"name": "512GB Black"},
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["reserved_quantity"] == 3


# ─── Unhappy path ─────────────────────────────────────────────────────────────

async def test_edit_hard_blocked_returns_403(
    client: AsyncClient, auth_headers, db_session, seller, category
):
    """HARD_BLOCKED → 403 FORBIDDEN"""
    product = await _make_product(db_session, seller, category, ProductStatus.HARD_BLOCKED)

    response = await client.patch(
        f"/api/v1/products/{product.id}",
        json={"title": "Попытка изменить"},
        headers=auth_headers,
    )
    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"


async def test_edit_others_product_returns_403(
    client: AsyncClient, other_auth_headers, db_session, seller, category
):
    """Чужой товар → 403 NOT_OWNER"""
    product = await _make_product(db_session, seller, category, ProductStatus.MODERATED)

    response = await client.patch(
        f"/api/v1/products/{product.id}",
        json={"title": "Чужой товар"},
        headers=other_auth_headers,
    )
    assert response.status_code == 403
    assert response.json()["code"] == "NOT_OWNER"