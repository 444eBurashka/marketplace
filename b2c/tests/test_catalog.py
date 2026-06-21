import pytest
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient


MOCK_PRODUCTS = {
    "items": [
        {"id": "aaa", "title": "Test Product", "slug": "test", "category_id": "ccc", "min_price": 1000, "images": []}
    ],
    "total": 1, "page": 1, "page_size": 20,
}


@pytest.mark.asyncio
async def test_catalog_returns_filtered_sorted_products(client: AsyncClient):
    with patch("app.services.b2b_client.get_products", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = MOCK_PRODUCTS
        r = await client.get("/api/v1/products?category_id=ccc&sort=price_asc")
        assert r.status_code == 200
        data = r.json()
        assert "items" in data
        # Убеждаемся, что параметры переданы в B2B
        call_params = mock_get.call_args[0][0]
        assert call_params["sort"] == "price_asc"
        assert call_params["status"] == "MODERATED"


@pytest.mark.asyncio
async def test_invalid_sort_returns_400(client: AsyncClient):
    r = await client.get("/api/v1/products?sort=invalid_sort_value")
    assert r.status_code == 400
    data = r.json()
    assert data["code"] == "INVALID_SORT"


@pytest.mark.asyncio
async def test_b2b_unavailable_returns_502(client: AsyncClient):
    with patch("app.services.b2b_client.get_products", new_callable=AsyncMock) as mock_get:
        from fastapi import HTTPException
        mock_get.side_effect = HTTPException(status_code=502, detail="B2B service unavailable")
        r = await client.get("/api/v1/products")
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
    r = await client.get("/api/v1/products?search=ab")
    assert r.status_code == 400
    assert r.json()["code"] == "QUERY_TOO_SHORT"


@pytest.mark.asyncio
async def test_special_chars_do_not_break_query(client: AsyncClient):
    with patch("app.services.b2b_client.get_products", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"items": [], "total": 0, "page": 1, "page_size": 20}
        r = await client.get("/api/v1/products?search=iPhone%2515")
        assert r.status_code == 200


@pytest.mark.asyncio
async def test_empty_results_returns_200(client: AsyncClient):
    with patch("app.services.b2b_client.get_products", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"items": [], "total": 0, "page": 1, "page_size": 20}
        r = await client.get("/api/v1/products?search=xyznotexists")
        assert r.status_code == 200
        assert r.json()["items"] == []


MOCK_PRODUCT = {
    "id": "aaa", "title": "Phone", "slug": "phone", "description": "desc",
    "category_id": "ccc", "status": "MODERATED", "deleted": False,
    "images": [],
    "skus": [{"id": "sku1", "code": "SKU1", "price": 1000, "cost_price": 500,
              "discount": 0, "quantity": 10, "reserved_quantity": 0,
              "is_active": True, "attributes": []}],
}


@pytest.mark.asyncio
async def test_product_card_returns_full_data_with_skus(client: AsyncClient):
    with patch("app.services.b2b_client.get_product", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = MOCK_PRODUCT
        r = await client.get("/api/v1/products/aaa")
        assert r.status_code == 200
        data = r.json()
        assert "skus" in data


@pytest.mark.asyncio
async def test_cost_price_absent_in_response(client: AsyncClient):
    with patch("app.services.b2b_client.get_product", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = MOCK_PRODUCT
        r = await client.get("/api/v1/products/aaa")
        assert r.status_code == 200
        skus = r.json().get("skus", [])
        assert len(skus) > 0
        assert "cost_price" not in skus[0]


@pytest.mark.asyncio
async def test_blocked_product_returns_404(client: AsyncClient):
    with patch("app.services.b2b_client.get_product", new_callable=AsyncMock) as mock_get:
        blocked = {**MOCK_PRODUCT, "status": "BLOCKED"}
        mock_get.return_value = blocked
        r = await client.get("/api/v1/products/aaa")
        assert r.status_code == 404
