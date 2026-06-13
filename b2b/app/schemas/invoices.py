import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models import InvoiceStatus


# ─── Request ─────────────────────────────────────────────────────────────────

class InvoiceItemIn(BaseModel):
    sku_id: uuid.UUID
    quantity: int = Field(gt=0)


class InvoiceCreateRequest(BaseModel):
    items: list[InvoiceItemIn] = Field(min_length=1)


class AcceptItemIn(BaseModel):
    invoice_item_id: uuid.UUID
    accepted_quantity: int = Field(ge=0)


class InvoiceAcceptRequest(BaseModel):
    accepted_items: list[AcceptItemIn] = Field(min_length=1)


# ─── Response ────────────────────────────────────────────────────────────────

class InvoiceItemOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    sku_id: uuid.UUID
    quantity: int
    accepted_quantity: int | None = None


class InvoiceResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    seller_id: uuid.UUID
    status: InvoiceStatus
    items: list[InvoiceItemOut]
    created_at: datetime
    updated_at: datetime
    accepted_at: datetime | None = None
    accepted_by: uuid.UUID | None = None