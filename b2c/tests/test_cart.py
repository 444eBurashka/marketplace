import pytest
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Buyer, Cart, CartItem
from app.core.security import hash_password
from shared.auth.jwt import create_access_token
from app.core.config import settings


async def create_buyer(db: AsyncSession, email: str = "test@test.com") -> tuple[Buyer, str]:
    buyer = Buyer(email=email, hashed_password=hash_password("password123"))
    db.add(buyer)
    await db.flush()
    token = create_access_token(
        subject=str(buyer.id),
        secret_key=settings.secret_key,
        extra_claims={"role": "buyer"},
    )
    return buyer, token


@pytest.mark.asyncio
async def test_add_to_favorites_returns_201(client: AsyncClient, db_session: AsyncSession):
    buyer, token = await create_buyer(db_session)
    r = await client.post(
        "/api/v1/favorites",
        params={"product_id": "00000000-0000-0000-0000-000000000001"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 201


@pytest.mark.asyncio
async def test_repeat_add_returns_200_not_duplicate(client: AsyncClient, db_session: AsyncSession):
    buyer, token = await create_buyer(db_session, "dup@test.com")
    pid = "00000000-0000-0000-0000-000000000002"
    await client.post("/api/v1/favorites", params={"product_id": pid}, headers={"Authorization": f"Bearer {token}"})
    r = await client.post("/api/v1/favorites", params={"product_id": pid}, headers={"Authorization": f"Bearer {token}"})
    # Второй вызов — 201 (идемпотентный) или 200
    assert r.status_code in (200, 201)

    # Проверяем что дублей нет в БД
    from sqlalchemy import select, func
    from app.models import Favorite
    count = await db_session.scalar(
        select(func.count()).select_from(Favorite).where(
            Favorite.buyer_id == buyer.id,
            Favorite.product_id == "00000000-0000-0000-0000-000000000002"
        )
    )
    assert count == 1


@pytest.mark.asyncio
async def test_add_sku_increments_quantity_if_already_in_cart(client: AsyncClient, db_session: AsyncSession):
    buyer, token = await create_buyer(db_session, "cart@test.com")
    payload = {
        "sku_id": "00000000-0000-0000-0000-000000000010",
        "product_id": "00000000-0000-0000-0000-000000000011",
        "quantity": 1,
    }
    await client.post("/api/v1/cart/items", json=payload, headers={"Authorization": f"Bearer {token}"})
    r = await client.post("/api/v1/cart/items", json=payload, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 201
    assert r.json()["quantity"] == 2
