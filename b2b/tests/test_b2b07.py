"""
Тесты для GET /api/v1/products в режиме B2C каталога (B2B-07).

Сценарии:
  happy:
    - catalog_returns_moderated_in_stock_products
    - batch_ids_returns_visible_subset
  unhappy:
    - catalog_excludes_hard_blocked
    - catalog_missing_service_key_returns_401
    - catalog_response_has_no_cost_price
"""
import uuid

import pytest
import pytest_asyncio
from httpx import AsyncClient

from app.core.config import settings
from app.core.security import hash_password
from app.models import Category, Image, ImageEntityType, Product, ProductStatus, Seller, SKU

SERVICE_HEADERS = {"X-Service-Key": settings.service_key}
CATALOG_URL = "/api/v1/products"


# ─── Фикстуры ────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def seller(db_session):
    s = Seller(
        email="seller_b2b07@example.com",
        hashed_password=hash_password("password123"),
        company_name="Catalog Co",
        inn="7777777777",
        is_active=True,
    )
    db_session.add(s)
    await db_session.flush()
    return s


@pytest_asyncio.fixture
async def category(db_session):
    c = Category(name="Каталог", slug="catalog-b2b07", is_active=True, sort_order=0)
    db_session.add(c)
    await db_session.flush()
    return c


async def make_product(db_session, seller, category, *, title="Test", status=ProductStatus.MODERATED,
                        deleted=False, slug_suffix=""):
    slug = f"catalog-{slug_suffix or str(uuid.uuid4())[:8]}"
    p = Product(
        seller_id=seller.id,
        category_id=category.id,
        title=title,
        slug=slug,
        description="desc",
        status=status,
        deleted=deleted,
        blocked=False,
    )
    db_session.add(p)
    await db_session.flush()

    img = Image(
        entity_type=ImageEntityType.PRODUCT,
        entity_id=p.id,
        url=f"/s3/{slug}.jpg",
        ordering=0,
    )
    db_session.add(img)
    await db_session.flush()
    return p


async def make_sku(db_session, product, *, quantity=10, reserved_quantity=0):
    sku = SKU(
        product_id=product.id,
        name="SKU",
        price=9990000,
        cost_price=5000000,
        discount=0,
        quantity=quantity,
        reserved_quantity=reserved_quantity,
        is_active=True,
    )
    db_session.add(sku)
    await db_session.flush()
    return sku


# ─── Тесты ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_catalog_returns_moderated_in_stock_products(
    client: AsyncClient, db_session, seller, category
):
    """catalog_returns_moderated_in_stock_products — только MODERATED+deleted=false+active_quantity>0."""
    # Подходящий товар
    good = await make_product(db_session, seller, category, title="Good Product", slug_suffix="good")
    await make_sku(db_session, good, quantity=5)

    # Товар без остатков — не должен попасть
    no_stock = await make_product(db_session, seller, category, title="No Stock", slug_suffix="nostock")
    await make_sku(db_session, no_stock, quantity=0)

    # ON_MODERATION — не должен попасть
    on_mod = await make_product(db_session, seller, category, title="On Mod", status=ProductStatus.ON_MODERATION, slug_suffix="onmod")
    await make_sku(db_session, on_mod, quantity=5)

    # Удалённый — не должен попасть
    deleted = await make_product(db_session, seller, category, title="Deleted", deleted=True, slug_suffix="del")
    await make_sku(db_session, deleted, quantity=5)

    resp = await client.get(CATALOG_URL, headers=SERVICE_HEADERS)
    assert resp.status_code == 200

    data = resp.json()
    assert "items" in data
    assert "total_count" in data
    ids = [item["id"] for item in data["items"]]

    assert str(good.id) in ids
    assert str(no_stock.id) not in ids
    assert str(on_mod.id) not in ids
    assert str(deleted.id) not in ids


@pytest.mark.asyncio
async def test_catalog_excludes_hard_blocked(
    client: AsyncClient, db_session, seller, category
):
    """catalog_excludes_hard_blocked — HARD_BLOCKED не попадает в выдачу."""
    hard_blocked = await make_product(
        db_session, seller, category,
        title="Hard Blocked",
        status=ProductStatus.HARD_BLOCKED,
        slug_suffix="hb",
    )
    await make_sku(db_session, hard_blocked, quantity=10)

    resp = await client.get(CATALOG_URL, headers=SERVICE_HEADERS)
    assert resp.status_code == 200

    ids = [item["id"] for item in resp.json()["items"]]
    assert str(hard_blocked.id) not in ids


@pytest.mark.asyncio
async def test_catalog_missing_service_key_returns_401(client: AsyncClient):
    """catalog_missing_service_key_returns_401 — без X-Service-Key → 401."""
    resp = await client.get(CATALOG_URL)
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_catalog_response_has_no_cost_price(
    client: AsyncClient, db_session, seller, category
):
    """catalog_response_has_no_cost_price — в ответе нет cost_price и reserved_quantity."""
    product = await make_product(db_session, seller, category, title="iPhone", slug_suffix="iphone")
    await make_sku(db_session, product, quantity=10)

    resp = await client.get(CATALOG_URL, headers=SERVICE_HEADERS)
    assert resp.status_code == 200

    items = resp.json()["items"]
    found = next((i for i in items if i["id"] == str(product.id)), None)
    assert found is not None

    for sku in found["skus"]:
        assert "cost_price" not in sku
        assert "reserved_quantity" not in sku


@pytest.mark.asyncio
async def test_batch_ids_returns_visible_subset(
    client: AsyncClient, db_session, seller, category
):
    """batch_ids_returns_visible_subset — ?ids= возвращает только видимые, без 404 для скрытых."""
    visible = await make_product(db_session, seller, category, title="Visible", slug_suffix="vis")
    await make_sku(db_session, visible, quantity=5)

    hidden = await make_product(
        db_session, seller, category, title="Hidden",
        status=ProductStatus.BLOCKED, slug_suffix="hid",
    )
    await make_sku(db_session, hidden, quantity=5)

    nonexistent_id = uuid.uuid4()

    ids_param = f"{visible.id},{hidden.id},{nonexistent_id}"
    resp = await client.get(f"{CATALOG_URL}?ids={ids_param}", headers=SERVICE_HEADERS)
    assert resp.status_code == 200  # не 404

    data = resp.json()
    ids = [item["id"] for item in data["items"]]
    assert str(visible.id) in ids
    assert str(hidden.id) not in ids
    assert str(nonexistent_id) not in ids


@pytest.mark.asyncio
async def test_catalog_search_filter(client: AsyncClient, db_session, seller, category):
    """Поиск по названию работает в каталоге."""
    iphone = await make_product(db_session, seller, category, title="iPhone 15 Pro", slug_suffix="srch-ip")
    await make_sku(db_session, iphone, quantity=5)

    samsung = await make_product(db_session, seller, category, title="Samsung Galaxy", slug_suffix="srch-sg")
    await make_sku(db_session, samsung, quantity=5)

    resp = await client.get(f"{CATALOG_URL}?search=iphone", headers=SERVICE_HEADERS)
    assert resp.status_code == 200

    ids = [item["id"] for item in resp.json()["items"]]
    assert str(iphone.id) in ids
    assert str(samsung.id) not in ids


@pytest.mark.asyncio
async def test_catalog_pagination(client: AsyncClient, db_session, seller, category):
    """Пагинация: limit и offset корректно работают."""
    for i in range(5):
        p = await make_product(db_session, seller, category, title=f"Product {i}", slug_suffix=f"pag-{i}")
        await make_sku(db_session, p, quantity=3)

    resp = await client.get(f"{CATALOG_URL}?limit=2&offset=0", headers=SERVICE_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) <= 2
    assert data["total_count"] >= 5
    assert data["limit"] == 2
    assert data["offset"] == 0


@pytest.mark.asyncio
async def test_catalog_reserved_fully_hides_product(
    client: AsyncClient, db_session, seller, category
):
    """Товар у которого весь остаток зарезервирован — не виден в каталоге."""
    product = await make_product(db_session, seller, category, title="Full Reserved", slug_suffix="fres")
    # quantity=5, reserved=5 → active_quantity=0
    await make_sku(db_session, product, quantity=5, reserved_quantity=5)

    resp = await client.get(CATALOG_URL, headers=SERVICE_HEADERS)
    ids = [item["id"] for item in resp.json()["items"]]
    assert str(product.id) not in ids