import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models import Banner, BannerEvent, Collection
from app.services import b2b_client

router = APIRouter()
DB = Annotated[AsyncSession, Depends(get_db)]


# ─── US-CART-04: Баннеры ─────────────────────────────────────────────────────

@router.get("/banners")
async def get_banners(db: DB) -> dict:
    now = datetime.now(UTC)
    result = await db.execute(
        select(Banner).where(
            Banner.is_active == True,  # noqa: E712
            (Banner.active_from.is_(None)) | (Banner.active_from <= now),
            (Banner.active_to.is_(None)) | (Banner.active_to >= now),
        ).order_by(Banner.priority)
    )
    banners = result.scalars().all()
    return [
            {
                "id": str(b.id),
                "title": b.title,
                "image_url": b.image_url,
                "link": b.link_url,
                "priority": b.priority,
            }
            for b in banners
        ]


@router.post("/banner-events", status_code=201)
async def register_banner_event(
    banner_id: uuid.UUID,
    event_type: str,
    db: DB,
) -> dict:
    banner = await db.get(Banner, banner_id)
    if not banner:
        raise HTTPException(
            status_code=400,
            detail={"code": "UNKNOWN_BANNER", "message": "Banner not found"},
        )
    event = BannerEvent(banner_id=banner_id, event_type=event_type)
    db.add(event)
    await db.flush()
    return {"id": str(event.id)}


# ─── US-CART-05: Подборки ────────────────────────────────────────────────────

@router.get("/collections")
async def get_collections(db: DB) -> dict:
    result = await db.execute(
        select(Collection).where(Collection.is_active == True)  # noqa: E712
    )
    collections = result.scalars().all()
    return {
        "items": [
            {"id": str(c.id), "title": c.title, "slug": c.slug}
            for c in collections
        ]
    }


@router.get("/collections/{collection_id}/products")
async def get_collection_products(collection_id: uuid.UUID, db: DB) -> dict:
    collection = await db.get(Collection, collection_id)
    if not collection:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Collection not found"})

    product_ids: list = collection.product_ids or []
    available = []
    unavailable_ids = []

    for pid in product_ids:
        product = await b2b_client.get_product(str(pid))
        if not product or product.get("status") != "MODERATED" or product.get("deleted"):
            unavailable_ids.append(str(pid))
        else:
            available.append({
                "id": product["id"],
                "title": product.get("title"),
                "min_price": min((s.get("price", 0) for s in product.get("skus", [])), default=None),
                "images": product.get("images", []),
            })

    return {"items": available, "unavailable_ids": unavailable_ids}
