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
        # Видимость: только MODERATED, не deleted, in-stock
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


# ─── US-CAT-03: Карточка товара ──────────────────────────────────────────────

@router.get("/products/{product_id}")
async def get_product(product_id: uuid.UUID) -> dict:
    data = await b2b_client.get_product(str(product_id))
    if data is None:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Product not found"})

    # Проверяем видимость
    if data.get("status") != "MODERATED" or data.get("deleted"):
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Product not found"})

    # Убираем поля, запрещённые для покупателя
    for sku in data.get("skus", []):
        sku.pop("cost_price", None)
        sku.pop("reserved_quantity", None)
        # Добавляем in_stock
        qty = sku.get("quantity", 0)
        sku["in_stock"] = qty > 0

    return data


# ─── US-CAT-04: Похожие товары ───────────────────────────────────────────────

@router.get("/products/{product_id}/similar")
async def get_similar_products(product_id: uuid.UUID) -> dict:
    # Проверяем существование товара
    product = await b2b_client.get_product(str(product_id))
    if product is None or product.get("status") != "MODERATED" or product.get("deleted"):
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Product not found"})

    category_id = product.get("category_id")
    params: dict = {
        "status": "MODERATED",
        "deleted": False,
        "active_quantity_gt": 0,
        "page_size": 9,  # берём +1 чтобы потом отфильтровать текущий
    }
    if category_id:
        params["category_id"] = category_id

    result = await b2b_client.get_products(params)
    items = [p for p in result.get("items", []) if p["id"] != str(product_id)][:8]

    # Если меньше 8 — fallback на родительскую категорию (запрос без category_id)
    if len(items) < 8:
        parent_params = {**params}
        parent_params.pop("category_id", None)
        parent_params["page_size"] = 8 - len(items) + 1
        fallback = await b2b_client.get_products(parent_params)
        existing_ids = {p["id"] for p in items}
        for p in fallback.get("items", []):
            if p["id"] != str(product_id) and p["id"] not in existing_ids:
                items.append(p)
                if len(items) >= 8:
                    break

    return {"items": items[:8]}


# ─── US-CAT-05: Навигация по категориям ──────────────────────────────────────

@router.get("/categories")
async def get_category_tree() -> dict:
    flat = await b2b_client.get_categories()
    # Строим дерево из плоского списка
    by_id = {c["id"]: {**c, "children": []} for c in flat}
    roots = []
    for cat in flat:
        pid = cat.get("parent_id")
        if pid and pid in by_id:
            by_id[pid]["children"].append(by_id[cat["id"]])
        elif not pid:
            roots.append(by_id[cat["id"]])
    return {"categories": roots}


@router.get("/categories/{category_id}")
async def get_category(category_id: uuid.UUID) -> dict:
    flat = await b2b_client.get_categories()
    found = next((c for c in flat if c["id"] == str(category_id)), None)
    if not found:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Category not found"})
    return found


@router.get("/categories/breadcrumbs")
async def get_breadcrumbs(
    category_id: uuid.UUID | None = Query(default=None),
    product_id: uuid.UUID | None = Query(default=None),
) -> dict:
    if category_id and product_id:
        raise HTTPException(
            status_code=400,
            detail={"code": "AMBIGUOUS_PARAMS", "message": "Provide either category_id or product_id, not both"},
        )
    if not category_id and not product_id:
        raise HTTPException(
            status_code=400,
            detail={"code": "MISSING_PARAM", "message": "Provide category_id or product_id"},
        )

    flat = await b2b_client.get_categories()
    by_id = {c["id"]: c for c in flat}

    # Определяем стартовую категорию
    if product_id:
        product = await b2b_client.get_product(str(product_id))
        if not product:
            raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Product not found"})
        start_id = product.get("category_id")
    else:
        start_id = str(category_id)

    if not start_id or start_id not in by_id:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Category not found"})

    # Строим цепочку от текущей до корня
    breadcrumbs = []
    current_id = start_id
    visited = set()
    while current_id:
        if current_id in visited:
            raise HTTPException(
                status_code=422,
                detail={"code": "ORPHAN_NODE", "message": "Broken category hierarchy"},
            )
        visited.add(current_id)
        cat = by_id.get(current_id)
        if not cat:
            raise HTTPException(
                status_code=422,
                detail={"code": "ORPHAN_NODE", "message": "Broken category hierarchy"},
            )
        breadcrumbs.insert(0, {"id": cat["id"], "name": cat["name"], "slug": cat["slug"]})
        current_id = cat.get("parent_id")

    return {"breadcrumbs": breadcrumbs}
