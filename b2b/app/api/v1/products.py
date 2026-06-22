import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi import status as http_status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.dependencies import CurrentSeller, get_optional_seller
from app.db.session import get_db
from app.models import Seller
from app.schemas.products import (
    CatalogListResponse,
    CatalogProductDetailResponse,
    CatalogProductResponse,
    ProductCreateRequest,
    ProductDetailResponse,
    ProductListItem,
    ProductListResponse,
    ProductResponse,
)
from app.services.delete_service import delete_product
from app.services.products import (
    create_product,
    get_catalog_product,
    get_product,
    list_catalog,
    list_products,
)

router = APIRouter()

DB = Annotated[AsyncSession, Depends(get_db)]


def _is_service_key(x_service_key: str | None) -> bool:
    return x_service_key is not None and x_service_key == settings.service_key


# ─── GET /products — два режима по заголовку ─────────────────────────────────

@router.get("")
async def list_products_endpoint(
    db: DB,
    x_service_key: str | None = Header(default=None),
    seller: Seller | None = Depends(get_optional_seller),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    # seller-only
    product_status: str | None = Query(default=None, alias="status"),
    include_deleted: bool = Query(default=False),
    # catalog-only
    category: uuid.UUID | None = Query(default=None),
    sort: str | None = Query(default=None),
    ids: str | None = Query(default=None),
    # shared
    search: str | None = Query(default=None),
    # IDOR prevention — игнорируем
    seller_id: str | None = Query(default=None, include_in_schema=False),
    user_id: str | None = Query(default=None, include_in_schema=False),
    owner_id: str | None = Query(default=None, include_in_schema=False),
) -> CatalogListResponse | ProductListResponse:
    """
    Два режима:
    - X-Service-Key → B2C каталог (только MODERATED+in-stock, без cost_price/reserved_quantity)
    - Bearer JWT    → seller cabinet (только свои товары)
    """
    # ── Режим B2C каталога ───────────────────────────────────────────────────
    if _is_service_key(x_service_key):
        ids_list: list[uuid.UUID] | None = None
        if ids:
            try:
                ids_list = [uuid.UUID(i.strip()) for i in ids.split(",") if i.strip()]
            except ValueError:
                raise HTTPException(
                    status_code=422,
                    detail={"code": "VALIDATION_ERROR", "message": "Invalid UUID in ids parameter"},
                )

        products, total = await list_catalog(
            db,
            limit=limit,
            offset=offset,
            category_id=category,
            search=search,
            sort=sort,
            ids=ids_list,
        )
        return CatalogListResponse(
            items=[CatalogProductResponse.model_validate(p) for p in products],
            total_count=total,
            limit=limit,
            offset=offset,
        )

    # ── Режим seller cabinet ─────────────────────────────────────────────────
    if seller is None:
        raise HTTPException(
            status_code=http_status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "Authentication required"},
        )

    try:
        items, total = await list_products(
            seller.id,
            db,
            limit=limit,
            offset=offset,
            status=product_status,
            search=search,
            include_deleted=include_deleted,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "VALIDATION_ERROR", "message": str(exc), "details": {}},
        )

    return ProductListResponse(
        items=[ProductListItem.model_validate(item) for item in items],
        total_count=total,
        limit=limit,
        offset=offset,
    )


# ─── POST /products ───────────────────────────────────────────────────────────

@router.post("", response_model=ProductResponse, status_code=http_status.HTTP_201_CREATED)
async def create_product_endpoint(
    body: ProductCreateRequest,
    seller: CurrentSeller,
    db: DB,
) -> ProductResponse:
    """Создать товар (без SKU → статус CREATED, на модерацию НЕ идёт)."""
    try:
        product = await create_product(body, seller.id, db)
    except ValueError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "VALIDATION_ERROR", "message": str(exc), "details": {}},
        )
    return ProductResponse.model_validate(product)


# ─── GET /products/{product_id} — два режима ─────────────────────────────────

@router.get("/{product_id}")
async def get_product_endpoint(
    product_id: uuid.UUID,
    db: DB,
    x_service_key: str | None = Header(default=None),
    seller: Seller | None = Depends(get_optional_seller),
) -> ProductDetailResponse | CatalogProductResponse:
    """
    X-Service-Key → карточка без ownership-проверки (для Moderation/B2C).
    Bearer JWT    → seller-view, чужой товар → 404.
    """
    if _is_service_key(x_service_key):
        try:
            product = await get_catalog_product(product_id, db)
        except LookupError as exc:
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail={"code": "NOT_FOUND", "message": str(exc)},
            )
        return CatalogProductDetailResponse.model_validate(product)

    if seller is None:
        raise HTTPException(
            status_code=http_status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "Authentication required"},
        )

    try:
        data = await get_product(product_id, seller.id, db)
    except LookupError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": str(exc)},
        )
    return ProductDetailResponse.model_validate(data)


# ─── DELETE /products/{product_id} ───────────────────────────────────────────

@router.delete("/{product_id}", status_code=http_status.HTTP_204_NO_CONTENT)
async def delete_product_endpoint(
    product_id: uuid.UUID,
    seller: CurrentSeller,
    db: DB,
) -> None:
    """Мягкое удаление товара (deleted=True), события в Moderation и B2C."""
    try:
        await delete_product(product_id, seller.id, db)
    except LookupError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": str(exc)},
        )
    except PermissionError:
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail={"code": "NOT_OWNER", "message": "Product does not belong to the authenticated seller"},
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_REQUEST", "message": str(exc)},
        )