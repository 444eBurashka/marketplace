import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import CurrentSeller
from app.db.session import get_db
from app.schemas.invoices import InvoiceAcceptRequest, InvoiceCreateRequest, InvoiceResponse
from app.services.invoices import accept_invoice, create_invoice

router = APIRouter()

DB = Annotated[AsyncSession, Depends(get_db)]


@router.post("", response_model=InvoiceResponse, status_code=status.HTTP_201_CREATED)
async def create_invoice_endpoint(
    body: InvoiceCreateRequest,
    seller: CurrentSeller,
    db: DB,
) -> InvoiceResponse:
    """Создать накладную. Только SKU MODERATED товаров продавца."""
    try:
        invoice = await create_invoice(body, seller.id, db)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": str(exc)},
        )
    except PermissionError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "NOT_OWNER", "message": "One or more SKUs do not belong to the authenticated seller"},
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_REQUEST", "message": str(exc)},
        )
    return InvoiceResponse.model_validate(invoice)


@router.post("/{invoice_id}/accept", response_model=InvoiceResponse)
async def accept_invoice_endpoint(
    invoice_id: uuid.UUID,
    body: InvoiceAcceptRequest,
    db: DB,
) -> InvoiceResponse:
    """Приёмка накладной оператором/админом. Атомарно увеличивает active_quantity SKU."""
    try:
        invoice = await accept_invoice(invoice_id, body, db)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": str(exc)},
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "CONFLICT", "message": str(exc)},
        )
    return InvoiceResponse.model_validate(invoice)