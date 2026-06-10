import uuid
from datetime import datetime

from pydantic import BaseModel, Field


# ─── Вложенные схемы ────────────────────────────────────────────────────────


class ImageIn(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    ordering: int = Field(ge=0)


class ImageOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    url: str
    ordering: int


class CharacteristicIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    value: str = Field(min_length=1, max_length=500)


class CharacteristicOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    name: str
    value: str


class CategoryOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    name: str


class SKUOut(BaseModel):
    """
    SKU в ответе продавцу.
    Поля приведены к реальной модели БД (code, quantity, reserved_quantity).
    OpenAPI использует алиасы (name → code, stock_quantity → quantity и т.д.) —
    маппинг через Field(alias=...) для совместимости.
    """
    model_config = {"from_attributes": True, "populate_by_name": True}

    id: uuid.UUID
    product_id: uuid.UUID
    # OpenAPI называет поле "name", в модели — "code"
    name: str = Field(alias="code", serialization_alias="name")
    price: int
    cost_price: int
    # quantity в модели = stock_quantity в API
    stock_quantity: int = Field(alias="quantity", serialization_alias="stock_quantity")
    reserved_quantity: int
    images: list[ImageOut]
    characteristics: list["CharacteristicOut"] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


# ─── Request ─────────────────────────────────────────────────────────────────


class ProductCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    description: str = Field(min_length=1, max_length=5000)
    category_id: uuid.UUID
    slug: str | None = Field(default=None, min_length=1, max_length=500)
    images: list[ImageIn] = Field(min_length=1)
    characteristics: list[CharacteristicIn] = Field(default_factory=list)


# ─── Response ────────────────────────────────────────────────────────────────




class ProductResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    seller_id: uuid.UUID
    category_id: uuid.UUID | None
    title: str
    slug: str
    description: str
    status: str
    deleted: bool
    blocking_reason_id: uuid.UUID | None = None
    moderator_comment: str | None = None
    images: list[ImageOut]
    characteristics: list[CharacteristicOut]
    skus: list[SKUOut]
    created_at: datetime
    updated_at: datetime