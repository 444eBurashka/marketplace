import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import CurrentSeller
from app.db.session import get_db
from app.schemas.patch_schemas import ProductPatchRequest, SKUPatchRequest
from app.schemas.products import ProductResponse
from app.schemas.skus import SKUResponse
from app.services.edit_service import patch_product, patch_sku

router = APIRouter()
DB = Annotated[AsyncSession, Depends(get_db)]


@router.patch("/products/{product_id}", response_model=ProductResponse)
async def patch_product_endpoint(
    product_id: uuid.UUID,
    body: ProductPatchRequest,
    seller: CurrentSeller,
    db: DB,
) -> ProductResponse:
    try:
        return await patch_product(product_id, body, seller.id, db)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": str(exc)},
        )
    except PermissionError as exc:
        code = "FORBIDDEN" if str(exc) == "HARD_BLOCKED" else "NOT_OWNER"
        msg = (
            "Cannot edit hard-blocked product"
            if code == "FORBIDDEN"
            else "Product does not belong to the authenticated seller"
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": code, "message": msg},
        )


@router.patch("/skus/{sku_id}", response_model=SKUResponse)
async def patch_sku_endpoint(
    sku_id: uuid.UUID,
    body: SKUPatchRequest,
    seller: CurrentSeller,
    db: DB,
) -> SKUResponse:
    try:
        return await patch_sku(sku_id, body, seller.id, db)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": str(exc)},
        )
    except PermissionError as exc:
        code = "FORBIDDEN" if str(exc) == "HARD_BLOCKED" else "NOT_OWNER"
        msg = (
            "Cannot edit hard-blocked product"
            if code == "FORBIDDEN"
            else "Product does not belong to the authenticated seller"
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": code, "message": msg},
        )