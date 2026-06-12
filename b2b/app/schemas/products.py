import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.common import CharacteristicOut, ImageOut
from app.schemas.skus import SKUResponse


# ─── Вложенные схемы ────────────────────────────────────────────────────────

class ImageIn(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    ordering: int = Field(ge=0)


class CharacteristicIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    value: str = Field(min_length=1, max_length=500)


class CategoryOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    name: str


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
    skus: list[SKUResponse]
    created_at: datetime
    updated_at: datetime