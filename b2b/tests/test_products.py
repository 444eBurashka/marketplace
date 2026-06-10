"""
Тесты для POST /api/v1/products (B2B-1: Создание товара).

Покрытые сценарии:
  happy:
    - create_product_returns_201_with_created_status
    - seller_id_taken_from_jwt
  unhappy:
    - missing_images_returns_400
    - missing_category_returns_400
    - invalid_category_id_returns_400
"""
import uuid

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select

from app.core.config import settings
from app.models import Category, Product, Seller
from shared.auth.jwt import create_access_token
from shared.auth.jwt import hash_token
from app.core.security import hash_password


# ─── Вспомогательные фикстуры ────────────────────────────────────────────────


@pytest_asyncio.fixture
async def seller(db_session):
    """Создаёт тестового продавца и возвращает его."""
    s = Seller(
        email="test_seller@example.com",
        hashed_password=hash_password("password123"),
        company_name="Test Company",
        inn="1234567890",
        is_active=True,
    )
    db_session.add(s)
    await db_session.flush()
    return s


@pytest_asyncio.fixture
async def category(db_session):
    """Создаёт тестовую категорию."""
    c = Category(
        name="Смартфоны",
        slug="smartphones",
        is_active=True,
        sort_order=0,
    )
    db_session.add(c)
    await db_session.flush()
    return c


@pytest.fixture
def auth_headers(seller):
    """JWT-заголовок для тестового продавца."""
    token = create_access_token(
        subject=str(seller.id),
        secret_key=settings.secret_key,
        expires_minutes=30,
        extra_claims={"role": "seller"},
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def valid_payload(category):
    """Минимально валидный payload для создания товара."""
    return {
        "title": "iPhone 15 Pro Max",
        "description": "Флагманский смартфон Apple 2024 года",
        "category_id": str(category.id),
        "images": [
            {"url": "/s3/iphone15-front.jpg", "ordering": 0},
        ],
        "characteristics": [
            {"name": "Бренд", "value": "Apple"},
        ],
    }


# ─── Happy path ───────────────────────────────────────────────────────────────


async def test_create_product_returns_201_with_created_status(
    client: AsyncClient,
    auth_headers: dict,
    valid_payload: dict,
):
    """Товар создаётся с status=CREATED и пустым списком skus."""
    response = await client.post(
        "/api/v1/products",
        json=valid_payload,
        headers=auth_headers,
    )
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["status"] == "CREATED"
    assert data["skus"] == []
    assert data["deleted"] is False
    assert "id" in data


async def test_seller_id_taken_from_jwt(
    client: AsyncClient,
    auth_headers: dict,
    valid_payload: dict,
    seller,
):
    """seller_id в созданном товаре берётся из JWT, а не из тела запроса."""
    # Добавляем чужой seller_id в тело — он должен быть проигнорирован
    payload_with_fake_seller = {
        **valid_payload,
        "seller_id": str(uuid.uuid4()),  # чужой ID — не должен попасть в БД
    }
    response = await client.post(
        "/api/v1/products",
        json=payload_with_fake_seller,
        headers=auth_headers,
    )
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["seller_id"] == str(seller.id)


async def test_slug_generated_from_title_if_not_provided(
    client: AsyncClient,
    auth_headers: dict,
    valid_payload: dict,
):
    """Если slug не передан — генерируется из title."""
    response = await client.post(
        "/api/v1/products",
        json=valid_payload,
        headers=auth_headers,
    )
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["slug"]
    assert "iphone" in data["slug"].lower()


async def test_explicit_slug_is_used_when_provided(
    client: AsyncClient,
    auth_headers: dict,
    valid_payload: dict,
):
    """Если slug передан явно — используется он."""
    payload = {**valid_payload, "slug": "my-custom-slug"}
    response = await client.post(
        "/api/v1/products",
        json=payload,
        headers=auth_headers,
    )
    assert response.status_code == 201, response.text
    assert response.json()["slug"] == "my-custom-slug"


async def test_duplicate_slug_gets_suffix(
    client: AsyncClient,
    auth_headers: dict,
    valid_payload: dict,
):
    """При коллизии slug автоматически получает числовой суффикс."""
    r1 = await client.post("/api/v1/products", json=valid_payload, headers=auth_headers)
    assert r1.status_code == 201
    slug1 = r1.json()["slug"]

    r2 = await client.post("/api/v1/products", json=valid_payload, headers=auth_headers)
    assert r2.status_code == 201
    slug2 = r2.json()["slug"]

    assert slug1 != slug2
    assert slug2.startswith(slug1)


# ─── Unhappy path ─────────────────────────────────────────────────────────────


async def test_missing_images_returns_400(
    client: AsyncClient,
    auth_headers: dict,
    valid_payload: dict,
):
    """Запрос без images → 422 с указанием поля."""
    payload = {**valid_payload, "images": []}
    response = await client.post(
        "/api/v1/products",
        json=payload,
        headers=auth_headers,
    )
    assert response.status_code == 422
    body = response.json()
    assert "images" in str(body).lower() or "image" in str(body).lower()


async def test_missing_category_returns_400(
    client: AsyncClient,
    auth_headers: dict,
    valid_payload: dict,
):
    """Запрос без category_id → 422."""
    payload = {k: v for k, v in valid_payload.items() if k != "category_id"}
    response = await client.post(
        "/api/v1/products",
        json=payload,
        headers=auth_headers,
    )
    assert response.status_code == 422


async def test_invalid_category_id_returns_400(
    client: AsyncClient,
    auth_headers: dict,
    valid_payload: dict,
):
    """Несуществующий category_id → 422."""
    payload = {**valid_payload, "category_id": str(uuid.uuid4())}
    response = await client.post(
        "/api/v1/products",
        json=payload,
        headers=auth_headers,
    )
    assert response.status_code == 422
    body = response.json()
    assert "category" in str(body).lower()


async def test_missing_service_without_auth_returns_401(
    client: AsyncClient,
    valid_payload: dict,
):
    """Запрос без JWT → 401."""
    response = await client.post("/api/v1/products", json=valid_payload)
    assert response.status_code == 401
