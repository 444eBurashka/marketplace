import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import CurrentSeller
from app.db.session import get_db
from app.schemas.skus import SKUCreateRequest, SKUResponse
from app.services.skus import BlockedError, ConflictError, create_sku, delete_sku

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


@router.delete("/skus/{sku_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_sku_endpoint(
    sku_id: uuid.UUID,
    seller: CurrentSeller,
    db: DB,
) -> None:
    """Удалить SKU. Запрещено если reserved_quantity > 0."""
    try:
        await delete_sku(sku_id, seller.id, db)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": str(exc)},
        )
    except PermissionError as exc:
        msg = str(exc)
        if msg == "NOT_OWNER":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "NOT_OWNER", "message": "SKU does not belong to the authenticated seller"},
            )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "FORBIDDEN", "message": msg},
        )
    except BlockedError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "FORBIDDEN", "message": str(exc)},
        )
    except ConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "CONFLICT", "message": str(exc)},
        )