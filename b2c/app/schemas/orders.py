import uuid
from pydantic import BaseModel


class CheckoutItemIn(BaseModel):
    sku_id: uuid.UUID
    quantity: int


class CheckoutRequest(BaseModel):
    idempotency_key: uuid.UUID
    items: list[CheckoutItemIn]
    address_id: uuid.UUID
    payment_method_id: uuid.UUID | None = None


class OrderItemOut(BaseModel):
    sku_id: uuid.UUID
    product_id: uuid.UUID
    name: str
    unit_price: int
    quantity: int
    line_total: int


class OrderOut(BaseModel):
    id: uuid.UUID
    number: str
    status: str
    subtotal: int
    delivery_cost: int
    total: int
    items: list[OrderItemOut] = []
    created_at: str


class CancelRequest(BaseModel):
    reason: str | None = None


class ProductEventRequest(BaseModel):
    idempotency_key: uuid.UUID
    event_type: str   # "PRODUCT_BLOCKED" | "PRODUCT_DELETED" | "OUT_OF_STOCK"
    product_id: uuid.UUID
    sku_ids: list[uuid.UUID] = []
