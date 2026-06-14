from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import CurrentSeller
from app.db.session import get_db
from app.schemas.products import (
    ProductCreateRequest,
    ProductListResponse,
    ProductListItem,
    ProductResponse,
)
from app.services.products import create_product, get_product, list_products
from app.services.delete_service import delete_product
import uuid

router = APIRouter()

DB = Annotated[AsyncSession, Depends(get_db)]


@router.get("", response_model=ProductListResponse)
async def list_products_endpoint(
    seller: CurrentSeller,
    db: DB,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    status: str | None = Query(default=None),
    include_deleted: bool = Query(default=False),
    search: str | None = Query(default=None),
    # IDOR prevention: любой seller_id / user_id / owner_id в query игнорируется
    seller_id: str | None = Query(default=None, include_in_schema=False),
    user_id: str | None = Query(default=None, include_in_schema=False),
    owner_id: str | None = Query(default=None, include_in_schema=False),
) -> ProductListResponse:
    """Список своих товаров продавца. seller_id берётся только из JWT."""
    try:
        items, total = await list_products(
            seller.id,
            db,
            limit=limit,
            offset=offset,
            status=status,
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


@router.post("", response_model=ProductResponse, status_code=status.HTTP_201_CREATED)
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
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "VALIDATION_ERROR",
                "message": str(exc),
                "details": {},
            },
        )
    return ProductResponse.model_validate(product)


@router.get("/{product_id}", response_model=ProductResponse)
async def get_product_endpoint(
    product_id: uuid.UUID,
    seller: CurrentSeller,
    db: DB,
) -> ProductResponse:
    """Карточка товара продавца. Чужой товар → 404 (не 403)."""
    try:
        product = await get_product(product_id, seller.id, db)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": str(exc)},
        )
    return ProductResponse.model_validate(product)

@router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
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
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": str(exc)},
        )
    except PermissionError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "NOT_OWNER", "message": "Product does not belong to the authenticated seller"},
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_REQUEST", "message": str(exc)},
        )