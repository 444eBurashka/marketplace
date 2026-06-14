import uuid
from datetime import datetime

from pydantic import BaseModel, Field


# ─── Reserve ─────────────────────────────────────────────────────────────────

class ReserveItem(BaseModel):
    sku_id: uuid.UUID
    quantity: int = Field(gt=0)


class ReserveRequest(BaseModel):
    idempotency_key: uuid.UUID
    order_id: uuid.UUID
    items: list[ReserveItem] = Field(min_length=1)


class ReservedItemOut(BaseModel):
    sku_id: uuid.UUID
    reserved_quantity: int
    remaining_stock: int


class ReserveResponse(BaseModel):
    order_id: uuid.UUID
    status: str  # RESERVED
    reserved_at: datetime


# ─── Unreserve ───────────────────────────────────────────────────────────────

class UnreserveItem(BaseModel):
    sku_id: uuid.UUID
    quantity: int = Field(gt=0)


class UnreserveRequest(BaseModel):
    order_id: uuid.UUID
    items: list[UnreserveItem] = Field(min_length=1)


class UnreserveResponse(BaseModel):
    order_id: uuid.UUID
    status: str  # UNRESERVED
    processed_at: datetime


# ─── Fulfill ─────────────────────────────────────────────────────────────────

class FulfillItem(BaseModel):
    sku_id: uuid.UUID
    quantity: int = Field(gt=0)


class FulfillRequest(BaseModel):
    order_id: uuid.UUID
    items: list[FulfillItem] = Field(min_length=1)


class FulfillResponse(BaseModel):
    order_id: uuid.UUID
    status: str  # FULFILLED
    processed_at: datetime