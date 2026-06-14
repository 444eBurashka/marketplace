from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_db
from app.schemas.inventory import ReserveRequest, ReserveResponse, UnreserveRequest, UnreserveResponse
from app.services.inventory import InsufficientStockError, reserve, unreserve

router = APIRouter()

DB = Annotated[AsyncSession, Depends(get_db)]


def _verify_service_key(x_service_key: str | None = Header(default=None)) -> None:
    """Только B2C-сервис может вызывать inventory endpoints."""
    if x_service_key != settings.service_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "Invalid or missing service key"},
        )


ServiceKeyDep = Annotated[None, Depends(_verify_service_key)]


@router.post("/reserve", response_model=ReserveResponse, status_code=status.HTTP_200_OK)
async def reserve_endpoint(
    body: ReserveRequest,
    _: ServiceKeyDep,
    db: DB,
) -> ReserveResponse:
    """
    All-or-nothing резервирование SKU (вызывается B2C при checkout).
    SELECT FOR UPDATE по всем sku_id.
    При active_quantity=0 → событие SKU_OUT_OF_STOCK в B2C.
    Идемпотентно по idempotency_key.
    """
    try:
        reservation = await reserve(body, db)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": str(exc)},
        )
    except InsufficientStockError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "INSUFFICIENT_STOCK",
                "message": "One or more SKUs have insufficient stock",
                "details": {"failed_items": exc.failed_items},
            },
        )

    return ReserveResponse(
        order_id=reservation.order_id,
        status="RESERVED",
        reserved_at=reservation.created_at,
    )


@router.post("/unreserve", response_model=UnreserveResponse, status_code=status.HTTP_200_OK)
async def unreserve_endpoint(
    body: UnreserveRequest,
    _: ServiceKeyDep,
    db: DB,
) -> UnreserveResponse:
    """Снять резерв (при отмене заказа). Идемпотентно по order_id."""
    try:
        reservation = await unreserve(body, db)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": str(exc)},
        )

    return UnreserveResponse(
        order_id=reservation.order_id,
        status="UNRESERVED",
        processed_at=datetime.now(UTC),
    )