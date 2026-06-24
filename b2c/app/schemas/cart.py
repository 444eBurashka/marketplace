import uuid
from pydantic import BaseModel


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
    sku_id: uuid.UUID
    product_id: uuid.UUID
    quantity: int = 1


class CartItemOut(BaseModel):
    id: uuid.UUID
    sku_id: uuid.UUID
    product_id: uuid.UUID
    quantity: int
    # из B2B:
    name: str | None = None
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
    """Body for PATCH /cart/items/{sku_id}"""
    quantity: int
