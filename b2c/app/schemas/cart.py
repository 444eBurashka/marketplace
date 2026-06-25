import uuid
from typing import Literal
from pydantic import BaseModel, Field


class AddToFavoriteRequest(BaseModel):
    product_id: uuid.UUID


class FavoriteOut(BaseModel):
    id: uuid.UUID
    product_id: uuid.UUID
    # поля из B2B обогащения:
    title: str | None = None
    min_price: int | None = None
    images: list[str] = []
    available: bool = True


class SubscribeRequest(BaseModel):
    sku_id: uuid.UUID
    events: list[str]   # ["BACK_IN_STOCK", "PRICE_DROP"]


class SubscriptionOut(BaseModel):
    id: uuid.UUID
    sku_id: uuid.UUID
    type: str


class CartItemIn(BaseModel):
    """Body for POST /cart/items. Контракт: required только [sku_id, quantity]."""
    sku_id: uuid.UUID
    quantity: int = Field(default=1, ge=1)


class CartItemOut(BaseModel):
    id: uuid.UUID
    sku_id: uuid.UUID
    product_id: uuid.UUID
    quantity: int
    # из B2B; "name" обязателен и непуст по контракту даже для недоступных позиций
    name: str
    unit_price: int = 0
    line_total: int = 0
    available_quantity: int = 0
    is_available: bool = False
    available: bool = True
    unavailable_reason: str | None = None


class CartOut(BaseModel):
    items: list[CartItemOut]
    items_count: int
    subtotal: int
    is_valid: bool       # сумма только доступных позиций


class CartItemUpdate(BaseModel):
    """Body for PATCH /cart/items/{sku_id}. Контракт: quantity >= 1 (удаление — через DELETE)."""
    quantity: int = Field(ge=1)


# ── Валидация корзины перед чекаутом ──────────────────────────────────────────

CartValidationIssueType = Literal[
    "PRICE_CHANGED",
    "OUT_OF_STOCK",
    "QUANTITY_REDUCED",
    "PRODUCT_BLOCKED",
    "PRODUCT_DELETED",
]


class CartValidationIssue(BaseModel):
    sku_id: uuid.UUID
    type: CartValidationIssueType
    message: str
    old_value: int | str | None = None
    new_value: int | str | None = None


class CartValidationResponse(BaseModel):
    is_valid: bool
    cart: CartOut
    issues: list[CartValidationIssue]
