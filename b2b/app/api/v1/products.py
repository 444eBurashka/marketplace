from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import CurrentSeller
from app.db.session import get_db
from app.models import ProductStatus
from app.schemas.products import ProductCreateRequest,ProductResponse
from app.services.products import create_product

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

