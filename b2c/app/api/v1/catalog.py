import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.services import b2b_client

router = APIRouter()


# ─── US-CAT-01: Каталог с фильтрами ─────────────────────────────────────────

ALLOWED_SORT = {"price_asc", "price_desc", "created_at_desc", "popular"}


@router.get("/products")
async def list_products(
    category_id: uuid.UUID | None = Query(default=None),
    min_price: int | None = Query(default=None),
    max_price: int | None = Query(default=None),
    in_stock: bool | None = Query(default=None),
    sort: str = Query(default="created_at_desc"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    # US-CAT-02: поиск
    search: str | None = Query(default=None, min_length=None),
) -> dict:
    # Валидация sort
    if sort not in ALLOWED_SORT:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "INVALID_SORT",
                "message": f"Invalid sort. Allowed: {sorted(ALLOWED_SORT)}",
            },
        )

    # US-CAT-02: поиск — минимальная длина 3 символа
    if search is not None and len(search) < 3:
        raise HTTPException(
            status_code=400,
            detail={"code": "QUERY_TOO_SHORT", "message": "search must be at least 3 characters"},
        )

    params: dict = {
        "sort": sort,
        "page": page,
        "page_size": page_size,
        "status": "MODERATED",
        "deleted": False,
        "active_quantity_gt": 0,
    }
    if category_id:
        params["category_id"] = str(category_id)
    if min_price is not None:
        params["min_price"] = min_price
    if max_price is not None:
        params["max_price"] = max_price
    if in_stock is not None:
        params["in_stock"] = in_stock
    if search:
        params["search"] = search

    return await b2b_client.get_products(params)


# ─── US-CAT-01: Фасеты ───────────────────────────────────────────────────────

@router.get("/catalog/facets")
async def get_facets(
    category_id: uuid.UUID | None = Query(default=None),
    search: str | None = Query(default=None),
) -> dict:
    params: dict = {"status": "MODERATED", "deleted": False, "active_quantity_gt": 0}
    if category_id:
        params["category_id"] = str(category_id)
    if search:
        params["search"] = search
    return await b2b_client.get_catalog_facets(params)