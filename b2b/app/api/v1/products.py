from typing import Annotated
import uuid
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import CurrentSeller
from app.db.session import get_db
from app.models import ProductStatus
from app.schemas.products import ProductCreateRequest,ProductResponse
from app.services.products import create_product
from app.services.delete_service import delete_product


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

@router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_product_endpoint(
    product_id: uuid.UUID,
    seller: CurrentSeller,
    db: DB,
) -> None:
    """Мягкое удаление товара. 204 No Content."""
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
            detail={
                "code": "NOT_OWNER",
                "message": "Product does not belong to the authenticated seller",
            },
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_REQUEST", "message": str(exc)},
        )
