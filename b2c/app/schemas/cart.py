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
    product_id: uuid.UUID | None = None
    quantity: int = 1


class CartItemOut(BaseModel):
    id: uuid.UUID
    sku_id: uuid.UUID
    product_id: uuid.UUID
    quantity: int
    # из B2B:
    title: str | None = None
    price: int | None = None
    available: bool = True
    unavailable_reason: str | None = None


class CartOut(BaseModel):
    items: list[CartItemOut]
    total_amount: int       # сумма только доступных позиций
