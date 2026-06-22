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

class BlockingReasonOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    title: str
    comment: str | None = None


class FieldReportOut(BaseModel):
    field_name: str
    sku_id: uuid.UUID | None = None
    comment: str


class ProductDetailResponse(BaseModel):
    """Схема GET /products/{id} по OpenAPI — включает blocked, blocking_reason, field_reports."""
    model_config = {"from_attributes": True}

    id: uuid.UUID
    seller_id: uuid.UUID
    category_id: uuid.UUID | None
    title: str
    slug: str
    description: str
    status: str
    deleted: bool
    blocked: bool
    images: list[ImageOut]
    characteristics: list[CharacteristicOut]
    skus: list[SKUResponse]
    created_at: datetime
    updated_at: datetime
    blocking_reason: BlockingReasonOut | None = None
    field_reports: list[FieldReportOut] = []


# ─── B2B-11: Список товаров продавца ─────────────────────────────────────────

class ProductListItem(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    title: str
    slug: str
    status: str
    category_id: uuid.UUID | None
    deleted: bool
    created_at: datetime
    min_price: int | None = None
    cover_image: str | None = None


class ProductListResponse(BaseModel):
    items: list[ProductListItem]
    total_count: int
    limit: int
    offset: int


# ─── B2B-07: Каталог для B2C ─────────────────────────────────────────────────

class CatalogSKUResponse(BaseModel):
    """SKU в каталоге B2C — без cost_price и reserved_quantity."""
    model_config = {"from_attributes": True, "populate_by_name": True}

    id: uuid.UUID
    product_id: uuid.UUID
    name: str
    price: int
    discount: int
    stock_quantity: int = Field(validation_alias="quantity")
    active_quantity: int
    article: str | None = None
    images: list[ImageOut]
    characteristics: list[CharacteristicOut] = Field(validation_alias="attributes")
    created_at: datetime
    updated_at: datetime


class CatalogProductResponse(BaseModel):
    """Товар в каталоге B2C — полный, без seller-only полей (список)."""
    model_config = {"from_attributes": True}

    id: uuid.UUID
    seller_id: uuid.UUID
    category_id: uuid.UUID | None
    title: str
    slug: str
    description: str
    status: str
    deleted: bool
    images: list[ImageOut]
    characteristics: list[CharacteristicOut]
    skus: list[CatalogSKUResponse]
    created_at: datetime
    updated_at: datetime


class CatalogProductDetailResponse(BaseModel):
    """GET /products/{id} через X-Service-Key — полная карточка по OpenAPI.

    Возвращает те же поля, что ProductDetailResponse, но без IDOR-проверки
    (Moderation и B2C видят любой товар). SKU без cost_price/reserved_quantity.
    """
    model_config = {"from_attributes": True}

    id: uuid.UUID
    seller_id: uuid.UUID
    category_id: uuid.UUID | None
    title: str
    slug: str
    description: str
    status: str
    deleted: bool
    images: list[ImageOut]
    characteristics: list[CharacteristicOut]
    skus: list[CatalogSKUResponse]
    created_at: datetime
    updated_at: datetime
    blocked: bool = False
    blocking_reason: BlockingReasonOut | None = None
    field_reports: list[FieldReportOut] = []


class CatalogListResponse(BaseModel):
    items: list[CatalogProductResponse]
    total_count: int
    limit: int
    offset: int