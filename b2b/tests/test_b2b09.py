"""
Тесты для POST /api/v1/moderation/events (B2B-9: Обработка событий от Moderation).

Сценарии:
  happy:
    - moderated_event_clears_blocking_data
    - blocked_soft_saves_field_reports
    - blocked_hard_sets_terminal_status
  unhappy:
    - duplicate_event_same_idempotency_key_no_side_effects
    - missing_service_key_returns_401
"""
import json
import uuid

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select

from app.core.config import settings
from app.core.security import hash_password
from app.models import BlockingReason, Category, Product, ProductStatus, Seller, SKU


SERVICE_HEADERS = {"X-Service-Key": settings.service_key}
MODERATION_URL = "/api/v1/moderation/events"


# ─── Фикстуры ────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def seller(db_session):
    s = Seller(
        email="mod_events_seller@example.com",
        hashed_password=hash_password("password123"),
        company_name="Mod Events Co",
        inn="9988776655",
        is_active=True,
    )
    db_session.add(s)
    await db_session.flush()
    return s


@pytest_asyncio.fixture
async def category(db_session):
    c = Category(
        name="Категория b2b09",
        slug=f"cat-b2b09-{uuid.uuid4()}",
        is_active=True,
        sort_order=0,
    )
    db_session.add(c)
    await db_session.flush()
    return c


@pytest_asyncio.fixture
async def blocking_reason(db_session):
    br = BlockingReason(
        title="Описание не соответствует товару",
        comment="Фото не совпадает",
        is_active=True,
    )
    db_session.add(br)
    await db_session.flush()
    return br


async def _make_product(db_session, seller, category, status=ProductStatus.ON_MODERATION):
    p = Product(
        seller_id=seller.id,
        category_id=category.id,
        title="Тестовый товар",
        slug=f"test-product-{uuid.uuid4()}",
        description="Описание",
        status=status,
        deleted=False,
        blocked=False,
    )
    db_session.add(p)
    await db_session.flush()
    return p


async def _make_sku(db_session, product):
    sku = SKU(
        product_id=product.id,
        name="Вариант",
        price=10000,
        cost_price=8000,
        discount=0,
        quantity=5,
        reserved_quantity=0,
    )
    db_session.add(sku)
    await db_session.flush()
    return sku


# ─── Happy path ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_moderated_event_clears_blocking_data(
    client: AsyncClient, db_session, seller, category, blocking_reason
):
    """MODERATED: товар переходит в MODERATED, blocking_reason и moderator_comment очищены."""
    product = await _make_product(db_session, seller, category, status=ProductStatus.BLOCKED)
    product.blocked = True
    product.blocking_reason_id = blocking_reason.id
    product.moderator_comment = "Старый комментарий"
    await db_session.flush()

    response = await client.post(
        MODERATION_URL,
        json={
            "idempotency_key": str(uuid.uuid4()),
            "product_id": str(product.id),
            "event_type": "MODERATED",
            "occurred_at": "2026-06-13T10:00:00Z",
        },
        headers=SERVICE_HEADERS,
    )
    assert response.status_code == 204

    await db_session.refresh(product)
    assert product.status == ProductStatus.MODERATED
    assert product.blocked is False
    assert product.blocking_reason_id is None
    assert product.moderator_comment is None


@pytest.mark.asyncio
async def test_blocked_soft_saves_field_reports(
    client: AsyncClient, db_session, seller, category, blocking_reason, httpx_mock
):
    """BLOCKED soft: статус BLOCKED, blocking_reason_id сохранён, каскад в B2C."""
    httpx_mock.add_response(method="POST", status_code=200)

    product = await _make_product(db_session, seller, category)
    await _make_sku(db_session, product)

    idempotency_key = str(uuid.uuid4())
    response = await client.post(
        MODERATION_URL,
        json={
            "idempotency_key": idempotency_key,
            "product_id": str(product.id),
            "event_type": "BLOCKED",
            "hard_block": False,
            "blocking_reason_id": str(blocking_reason.id),
            "moderator_comment": "Фото не совпадает с описанием",
            "field_reports": [
                {"field_name": "description", "sku_id": None, "comment": "Текст скопирован"}
            ],
            "occurred_at": "2026-06-13T10:00:00Z",
        },
        headers=SERVICE_HEADERS,
    )
    assert response.status_code == 204

    await db_session.refresh(product)
    assert product.status == ProductStatus.BLOCKED
    assert product.blocked is True
    assert product.blocking_reason_id == blocking_reason.id
    assert product.moderator_comment == "Фото не совпадает с описанием"

    # Проверяем каскад в B2C
    b2c_requests = [r for r in httpx_mock.get_requests() if "b2c" in str(r.url)]
    assert len(b2c_requests) == 1
    data = json.loads(b2c_requests[0].read())
    assert data["payload"]["product_id"] == str(product.id)


@pytest.mark.asyncio
async def test_blocked_hard_sets_terminal_status(
    client: AsyncClient, db_session, seller, category, blocking_reason, httpx_mock
):
    """BLOCKED hard: статус HARD_BLOCKED, каскад в B2C."""
    httpx_mock.add_response(method="POST", status_code=200)

    product = await _make_product(db_session, seller, category)
    await _make_sku(db_session, product)

    response = await client.post(
        MODERATION_URL,
        json={
            "idempotency_key": str(uuid.uuid4()),
            "product_id": str(product.id),
            "event_type": "BLOCKED",
            "hard_block": True,
            "blocking_reason_id": str(blocking_reason.id),
            "moderator_comment": "Грубое нарушение",
            "occurred_at": "2026-06-13T10:00:00Z",
        },
        headers=SERVICE_HEADERS,
    )
    assert response.status_code == 204

    await db_session.refresh(product)
    assert product.status == ProductStatus.HARD_BLOCKED
    assert product.blocked is True

    b2c_requests = [r for r in httpx_mock.get_requests() if "b2c" in str(r.url)]
    assert len(b2c_requests) == 1


@pytest.mark.asyncio
async def test_hard_blocked_product_rejects_seller_edits(
    client: AsyncClient, db_session, seller, category
):
    """PUT от продавца на HARD_BLOCKED товар → 403."""
    from app.core.config import settings as cfg
    from shared.auth.jwt import create_access_token

    product = await _make_product(db_session, seller, category, status=ProductStatus.HARD_BLOCKED)

    token = create_access_token(
        subject=str(seller.id),
        secret_key=cfg.secret_key,
        expires_minutes=30,
        extra_claims={"role": "seller"},
    )
    headers = {"Authorization": f"Bearer {token}"}

    response = await client.patch(
        f"/api/v1/products/{product.id}",
        json={"title": "Новое название"},
        headers=headers,
    )
    assert response.status_code == 403


# ─── Unhappy path ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_duplicate_event_same_idempotency_key_no_side_effects(
    client: AsyncClient, db_session, seller, category
):
    """Повторное событие с тем же idempotency_key → 204, статус не меняется повторно."""
    product = await _make_product(db_session, seller, category)
    idempotency_key = str(uuid.uuid4())

    payload = {
        "idempotency_key": idempotency_key,
        "product_id": str(product.id),
        "event_type": "MODERATED",
        "occurred_at": "2026-06-13T10:00:00Z",
    }

    # Первый запрос
    r1 = await client.post(MODERATION_URL, json=payload, headers=SERVICE_HEADERS)
    assert r1.status_code == 204

    await db_session.refresh(product)
    assert product.status == ProductStatus.MODERATED

    # Принудительно меняем статус — второй запрос не должен его трогать
    product.status = ProductStatus.BLOCKED
    await db_session.flush()

    # Второй запрос с тем же ключом
    r2 = await client.post(MODERATION_URL, json=payload, headers=SERVICE_HEADERS)
    assert r2.status_code == 204

    await db_session.refresh(product)
    # Статус остался BLOCKED — дубликат не применился
    assert product.status == ProductStatus.BLOCKED


@pytest.mark.asyncio
async def test_missing_service_key_returns_401(
    client: AsyncClient, db_session, seller, category
):
    """Запрос без X-Service-Key → 401."""
    product = await _make_product(db_session, seller, category)

    response = await client.post(
        MODERATION_URL,
        json={
            "idempotency_key": str(uuid.uuid4()),
            "product_id": str(product.id),
            "event_type": "MODERATED",
            "occurred_at": "2026-06-13T10:00:00Z",
        },
        # заголовок НЕ передаём
    )
    assert response.status_code == 401