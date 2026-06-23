import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import CurrentBuyer
from app.db.session import get_db
from app.models import Buyer, Favorite
from app.services import b2b_client

router = APIRouter()
DB = Annotated[AsyncSession, Depends(get_db)]


@router.put("/favorites/{product_id}", status_code=204)  # ← PUT + path-параметр + 204
async def add_to_favorites(
    product_id: uuid.UUID,
    buyer: CurrentBuyer,
    db: DB,
) -> None:                    # ← 204 No Content, тело не возвращаем
    existing = await db.scalar(
        select(Favorite).where(
            Favorite.buyer_id == buyer.id,
            Favorite.product_id == product_id,
        )
    )
    if existing:
        return   # идемпотентно

    fav = Favorite(buyer_id=buyer.id, product_id=product_id)
    db.add(fav)


@router.get("/favorites")
async def get_favorites(buyer: CurrentBuyer, db: DB, limit: int = 20, offset: int = 0) -> dict:
    result = await db.execute(
        select(Favorite).where(Favorite.buyer_id == buyer.id)
    )
    favs = result.scalars().all()

    # Обогащаем из B2B, фильтруем недоступные
    items = []
    for fav in favs:
        product = await b2b_client.get_product(str(fav.product_id))
        # Если товар заблокирован/удалён — пропускаем
        if not product or product.get("status") != "MODERATED" or product.get("deleted"):
            continue
        items.append({
            "id": str(fav.id),
            "product_id": str(fav.product_id),
            "title": product.get("title"),
            "min_price": min((s.get("price", 0) for s in product.get("skus", [])), default=None),
            "images": product.get("images", []),
        })

    return {
        "items": items,
        "total_count": len(items),
        "limit": limit,    # добавить limit: int = Query(default=20) в сигнатуру
        "offset": offset,  # добавить offset: int = Query(default=0) в сигнатуру
    }


@router.delete("/favorites/{product_id}", status_code=204)
async def remove_from_favorites(
    product_id: uuid.UUID,
    buyer: CurrentBuyer,
    db: DB,
) -> None:
    # Идемпотентно: если нет — просто 204
    await db.execute(
        delete(Favorite).where(
            Favorite.buyer_id == buyer.id,
            Favorite.product_id == product_id,
        )
    )
