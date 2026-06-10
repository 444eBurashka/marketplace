from fastapi import APIRouter, HTTPException, status

from app.core.dependencies import CurrentSeller
from app.db.session import get_db
from app.schemas.skus import SKUCreateRequest, SKUResponse
from app.services.skus import create_sku
from typing import Annotated
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()

DB = Annotated[AsyncSession, Depends(get_db)]


@router.post("/skus", response_model=SKUResponse, status_code=status.HTTP_201_CREATED)
async def create_sku_endpoint(
    body: SKUCreateRequest,
    seller: CurrentSeller,
    db: DB,
) -> SKUResponse:
    """Создать SKU. Первый SKU товара → товар ON_MODERATION + событие CREATED."""
    try:
        return await create_sku(body, seller.id, db)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": str(exc)},
        )
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "FORBIDDEN", "message": str(exc)},
        )
