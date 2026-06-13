"""
Тесты для GET /api/v1/products (B2B-11: Список товаров продавца).

Сценарии:
  happy:
    - list_returns_only_own_products
    - deleted_products_visible_with_deleted_flag
  unhappy:
    - idor_query_param_seller_id_ignored
    - status_filter_works_correctly
    - search_by_title_case_insensitive
"""
import uuid

import pytest
import pytest_asyncio
from httpx import AsyncClient

from app.core.config import settings
from app.models import Category, Product, ProductStatus, Seller, SKU, Image, ImageEntityType
from app.core.security import hash_password
from shared.auth.jwt import create_access_token


# ─── Фикстуры ────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def seller(db_session):
    s = Seller(
        email="seller_b2b11@example.com",
        hashed_password=hash_password("password123"),
        company_name="Test Co B2B11",
        inn="1111111111",
        is_active=True,
    )
    db_session.add(s)
    await db_session.flush()
    return s


@pytest_asyncio.fixture
async def other_seller(db_session):
    s = Seller(
        email="other_b2b11@example.com",
        hashed_password=hash_password("password123"),
        company_name="Other Co B2B11",
        inn="2222222222",
        is_active=True,
    )
    db_session.add(s)
    await db_session.flush()
    return s


@pytest_asyncio.fixture
async def category(db_session):
    c = Category(name="Электроника", slug="electronics-b2b11", is_active=True, sort_order=0)
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


async def make_product(db_session, seller, category, *, title="Test Product",
                        status=ProductStatus.MODERATED, deleted=False, slug_suffix=""):
    slug = f"test-{title.lower().replace(' ', '-')}-{slug_suffix or str(uuid.uuid4())[:8]}"
    p = Product(
        seller_id=seller.id,
        category_id=category.id,
        title=title,
        slug=slug,
        description="Test description",
        status=status,
        deleted=deleted,
        blocked=False,
    )
    db_session.add(p)
    await db_session.flush()

    # Добавляем обложку
    img = Image(
        entity_type=ImageEntityType.PRODUCT,
        entity_id=p.id,
        url=f"/s3/{slug}-cover.jpg",
        ordering=0,
    )
    db_session.add(img)

    # Добавляем SKU с ценой
    sku = SKU(
        product_id=p.id,
        name="Default SKU",
        price=9990000,
        cost_price=5000000,
        discount=0,
        quantity=10,
        reserved_quantity=0,
    )
    db_session.add(sku)
    await db_session.flush()

    return p


# ─── Тесты ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_returns_only_own_products(client: AsyncClient, db_session, seller, other_seller, category, auth_headers, other_auth_headers):
    """list_returns_only_own_products — список содержит только товары из JWT."""
    # Создаём товар продавца и товар другого продавца
    own = await make_product(db_session, seller, category, title="My iPhone", slug_suffix="own")
    alien = await make_product(db_session, other_seller, category, title="Alien Phone", slug_suffix="alien")

    resp = await client.get("/api/v1/products", headers=auth_headers)
    assert resp.status_code == 200

    data = resp.json()
    ids = [item["id"] for item in data["items"]]
    assert str(own.id) in ids
    assert str(alien.id) not in ids


@pytest.mark.asyncio
async def test_idor_query_param_seller_id_ignored(client: AsyncClient, db_session, seller, other_seller, category, auth_headers):
    """idor_query_param_seller_id_ignored — ?seller_id= в query не меняет выборку."""
    own = await make_product(db_session, seller, category, title="Own Product", slug_suffix="idor-own")
    alien = await make_product(db_session, other_seller, category, title="Alien Product", slug_suffix="idor-alien")

    # Передаём seller_id другого продавца в query — должно быть проигнорировано
    resp = await client.get(
        f"/api/v1/products?seller_id={other_seller.id}",
        headers=auth_headers,
    )
    assert resp.status_code == 200

    data = resp.json()
    ids = [item["id"] for item in data["items"]]
    assert str(own.id) in ids
    assert str(alien.id) not in ids


@pytest.mark.asyncio
async def test_deleted_products_visible_with_deleted_flag(client: AsyncClient, db_session, seller, category, auth_headers):
    """deleted_products_visible_with_deleted_flag — удалённые видны с include_deleted=true."""
    active = await make_product(db_session, seller, category, title="Active Product", slug_suffix="del-active")
    deleted = await make_product(db_session, seller, category, title="Deleted Product", deleted=True, slug_suffix="del-deleted")

    # По умолчанию удалённые не видны
    resp = await client.get("/api/v1/products", headers=auth_headers)
    assert resp.status_code == 200
    ids_default = [item["id"] for item in resp.json()["items"]]
    assert str(active.id) in ids_default
    assert str(deleted.id) not in ids_default

    # С include_deleted=true — видны оба
    resp2 = await client.get("/api/v1/products?include_deleted=true", headers=auth_headers)
    assert resp2.status_code == 200
    ids_with_deleted = [item["id"] for item in resp2.json()["items"]]
    assert str(active.id) in ids_with_deleted
    assert str(deleted.id) in ids_with_deleted

    # Удалённый товар имеет deleted=true в ответе
    deleted_item = next(i for i in resp2.json()["items"] if i["id"] == str(deleted.id))
    assert deleted_item["deleted"] is True


@pytest.mark.asyncio
async def test_status_filter_works_correctly(client: AsyncClient, db_session, seller, category, auth_headers):
    """status_filter_works_correctly — ?status=BLOCKED возвращает только BLOCKED."""
    moderated = await make_product(db_session, seller, category, title="Moderated P", status=ProductStatus.MODERATED, slug_suffix="sf-mod")
    blocked = await make_product(db_session, seller, category, title="Blocked P", status=ProductStatus.BLOCKED, slug_suffix="sf-blk")

    resp = await client.get("/api/v1/products?status=BLOCKED", headers=auth_headers)
    assert resp.status_code == 200

    data = resp.json()
    ids = [item["id"] for item in data["items"]]
    assert str(blocked.id) in ids
    assert str(moderated.id) not in ids

    # Проверяем что у отфильтрованных статус верный
    for item in data["items"]:
        assert item["status"] == "BLOCKED"


@pytest.mark.asyncio
async def test_search_by_title_case_insensitive(client: AsyncClient, db_session, seller, category, auth_headers):
    """search_by_title_case_insensitive — поиск нечувствителен к регистру."""
    iphone = await make_product(db_session, seller, category, title="iPhone 15 Pro Max", slug_suffix="srch-1")
    samsung = await make_product(db_session, seller, category, title="Samsung Galaxy", slug_suffix="srch-2")

    # Поиск в нижнем регистре
    resp = await client.get("/api/v1/products?search=iphone", headers=auth_headers)
    assert resp.status_code == 200
    ids = [item["id"] for item in resp.json()["items"]]
    assert str(iphone.id) in ids
    assert str(samsung.id) not in ids

    # Поиск в верхнем регистре
    resp2 = await client.get("/api/v1/products?search=IPHONE", headers=auth_headers)
    assert resp2.status_code == 200
    ids2 = [item["id"] for item in resp2.json()["items"]]
    assert str(iphone.id) in ids2


@pytest.mark.asyncio
async def test_list_response_structure(client: AsyncClient, db_session, seller, category, auth_headers):
    """Проверяем структуру ответа: поля согласно OpenAPI."""
    product = await make_product(db_session, seller, category, title="Structure Test", slug_suffix="struct")

    resp = await client.get("/api/v1/products", headers=auth_headers)
    assert resp.status_code == 200

    data = resp.json()
    assert "items" in data
    assert "total_count" in data
    assert "limit" in data
    assert "offset" in data

    item = next(i for i in data["items"] if i["id"] == str(product.id))
    for field in ("id", "title", "slug", "status", "category_id", "deleted", "created_at"):
        assert field in item, f"Missing field: {field}"

    # min_price должен быть заполнен (SKU создан в фикстуре)
    assert item["min_price"] == 9990000
    # cover_image тоже
    assert item["cover_image"] is not None


@pytest.mark.asyncio
async def test_pagination_limit_offset(client: AsyncClient, db_session, seller, category, auth_headers):
    """Пагинация: limit и offset работают корректно."""
    # Создаём 3 товара
    for i in range(3):
        await make_product(db_session, seller, category, title=f"Paged Product {i}", slug_suffix=f"page-{i}")

    resp = await client.get("/api/v1/products?limit=2&offset=0", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) <= 2
    assert data["limit"] == 2
    assert data["offset"] == 0
    assert data["total_count"] >= 3