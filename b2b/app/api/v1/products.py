from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import CurrentSeller
from app.db.session import get_db
from app.schemas.products import ProductCreateRequest, ProductResponse
from app.services.products import create_product, get_product
import uuid

router = APIRouter()

DB = Annotated[AsyncSession, Depends(get_db)]


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