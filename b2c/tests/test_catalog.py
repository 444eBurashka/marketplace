import pytest
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient


PRODUCT_ID = "00000000-0000-0000-0000-000000000011"
CATEGORY_ID = "00000000-0000-0000-0000-000000000002"
SKU_ID = "00000000-0000-0000-0000-000000000010"

MOCK_PRODUCTS = {
    "items": [
        {"id": PRODUCT_ID, "title": "Test Product", "slug": "test", "category_id": CATEGORY_ID, "min_price": 1000, "images": []}
    ],
    "total": 1, "page": 1, "page_size": 20,
}


@pytest.mark.asyncio
async def test_catalog_returns_filtered_sorted_products(client: AsyncClient):
    with patch("app.services.b2b_client.get_products", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = MOCK_PRODUCTS
        # Фильтры в deepObject-стиле: filter[category_id] согласно контракту (openapi.yaml:316-321)
        r = await client.get(f"/api/v1/catalog/products?filter[category_id]={CATEGORY_ID}&sort=price_asc")
        assert r.status_code == 200
        data = r.json()
        assert "items" in data
        # Убеждаемся, что параметры переданы в B2B
        call_params = mock_get.call_args[0][0]
        assert call_params["sort"] == "price_asc"
        assert call_params["status"] == "MODERATED"
        # Проверяем обязательные поля карточки в каждом элементе (openapi.yaml:1040)
        assert len(data["items"]) > 0
        item = data["items"][0]
        assert "name" in item, "обязательное поле name отсутствует (маппинг title→name не применён)"
        assert item["name"] == "Test Product"
        assert "min_price" in item
        assert "has_stock" in item
        assert "images" in item


@pytest.mark.asyncio
async def test_invalid_sort_returns_400(client: AsyncClient):
    r = await client.get("/api/v1/catalog/products?sort=invalid_sort_value")
    assert r.status_code == 400
    data = r.json()
    assert data["code"] == "INVALID_SORT"


@pytest.mark.asyncio
async def test_b2b_unavailable_returns_502(client: AsyncClient):
    with patch("app.services.b2b_client.get_products", new_callable=AsyncMock) as mock_get:
        from fastapi import HTTPException
        mock_get.side_effect = HTTPException(status_code=502, detail="B2B service unavailable")
        r = await client.get("/api/v1/catalog/products")
        assert r.status_code == 502


@pytest.mark.asyncio
async def test_facets_return_counts_per_filter_value(client: AsyncClient):
    with patch("app.services.b2b_client.get_catalog_facets", new_callable=AsyncMock) as mock_facets:
        mock_facets.return_value = {
            "facets": [{"field": "category", "values": [{"value": "Electronics", "count": 5}]}]
        }
        r = await client.get("/api/v1/catalog/facets")
        assert r.status_code == 200


@pytest.mark.asyncio
async def test_short_query_returns_400(client: AsyncClient):
    r = await client.get("/api/v1/catalog/products?q=ab")
    assert r.status_code == 400
    assert r.json()["code"] == "QUERY_TOO_SHORT"


@pytest.mark.asyncio
async def test_special_chars_do_not_break_query(client: AsyncClient):
    with patch("app.services.b2b_client.get_products", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"items": [], "total": 0, "page": 1, "page_size": 20}
        r = await client.get("/api/v1/catalog/products?q=iPhone%2515")
        assert r.status_code == 200


@pytest.mark.asyncio
async def test_empty_results_returns_200(client: AsyncClient):
    with patch("app.services.b2b_client.get_products", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"items": [], "total": 0, "page": 1, "page_size": 20}
        r = await client.get("/api/v1/catalog/products?q=xyznotexists")
        assert r.status_code == 200
        assert r.json()["items"] == []


# Фикстуры повторяют форму настоящего B2B SKUPublicResponse (b2b/openapi.yaml:1536):
# active_quantity — остаток за вычетом резерва, article — артикул.
MOCK_PRODUCT = {
    "id": PRODUCT_ID, "title": "Phone", "slug": "phone", "description": "desc",
    "category_id": CATEGORY_ID, "status": "MODERATED", "deleted": False,
    "images": [{"id": "img-1", "url": "https://example.com/img.jpg", "ordering": 1}],
    "skus": [{"id": SKU_ID, "article": "SKU1", "name": "Phone Red", "price": 1000,
              "discount": 0, "active_quantity": 10, "stock_quantity": 10,
              "images": [], "characteristics": []}],
}

MOCK_PRODUCT_NO_STOCK = {
    "id": PRODUCT_ID, "title": "Phone", "slug": "phone", "description": "desc",
    "category_id": CATEGORY_ID, "status": "MODERATED", "deleted": False,
    "images": [],
    "skus": [{"id": SKU_ID, "article": "SKU1", "name": "Phone Red", "price": 1000,
              "discount": 10, "active_quantity": 0, "stock_quantity": 5,
              "images": [], "characteristics": []}],
}


@pytest.mark.asyncio
async def test_product_card_returns_full_data_with_skus(client: AsyncClient):
    with patch("app.services.b2b_client.get_product", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = MOCK_PRODUCT
        r = await client.get(f"/api/v1/catalog/products/{PRODUCT_ID}")
        assert r.status_code == 200
        data = r.json()
        # Поля карточки товара
        assert data["name"] == "Phone"
        assert data["min_price"] == 1000
        assert data["has_stock"] is True
        assert data["slug"] == "phone"
        assert "description" in data
        assert "images" in data
        # SKU с полным набором полей
        assert "skus" in data
        sku = data["skus"][0]
        assert sku["id"] == SKU_ID
        assert sku["price"] == 1000
        assert sku["sku_code"] == "SKU1"
        assert sku["available_quantity"] == 10
        assert "in_stock" not in sku


@pytest.mark.asyncio
async def test_cost_price_absent_in_response(client: AsyncClient):
    with patch("app.services.b2b_client.get_product", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = MOCK_PRODUCT
        r = await client.get(f"/api/v1/catalog/products/{PRODUCT_ID}")
        assert r.status_code == 200
        skus = r.json().get("skus", [])
        assert len(skus) > 0
        assert "cost_price" not in skus[0]
        assert "reserved_quantity" not in skus[0]


@pytest.mark.asyncio
async def test_blocked_product_returns_404(client: AsyncClient):
    with patch("app.services.b2b_client.get_product", new_callable=AsyncMock) as mock_get:
        blocked = {**MOCK_PRODUCT, "status": "BLOCKED"}
        mock_get.return_value = blocked
        r = await client.get(f"/api/v1/catalog/products/{PRODUCT_ID}")
        assert r.status_code == 404


@pytest.mark.asyncio
async def test_sku_without_stock_is_shown_as_unavailable(client: AsyncClient):
    """SKU без остатка показывается в списке, но с in_stock=false."""
    with patch("app.services.b2b_client.get_product", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = MOCK_PRODUCT_NO_STOCK
        r = await client.get(f"/api/v1/catalog/products/{PRODUCT_ID}")
        assert r.status_code == 200
        data = r.json()
        assert data["has_stock"] is False
        assert len(data["skus"]) == 1
        sku = data["skus"][0]
        assert sku["in_stock"] is False
        assert sku["available_quantity"] == 0
        # Скидка > 0 — признак скидки
        assert sku["discount"] == 10