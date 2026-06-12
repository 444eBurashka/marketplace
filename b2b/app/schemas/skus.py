import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.common import CharacteristicOut, ImageOut  # ← теперь из common


# ─── Request ─────────────────────────────────────────────────────────────────

class SKUCharacteristicIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    value: str = Field(min_length=1, max_length=500)


class SKUImageIn(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    ordering: int = Field(ge=0)


class SKUCreateRequest(BaseModel):
    product_id: uuid.UUID
    name: str = Field(min_length=1, max_length=255)
    price: int = Field(gt=0)
    discount: int = Field(default=0, ge=0)
    cost_price: int = Field(gt=0)
    article: str | None = Field(default=None, max_length=255)
    images: list[SKUImageIn] = Field(min_length=1)
    characteristics: list[SKUCharacteristicIn] = Field(default_factory=list)


# ─── Response ────────────────────────────────────────────────────────────────

class SKUResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    product_id: uuid.UUID
    name: str
    price: int
    discount: int
    cost_price: int
    stock_quantity: int
    active_quantity: int
    reserved_quantity: int
    article: str | None
    images: list[ImageOut]
    characteristics: list[CharacteristicOut]
    created_at: datetime
    updated_at: datetime