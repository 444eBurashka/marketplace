import uuid
from typing import Any
from pydantic import BaseModel


class CatalogSku(BaseModel):
    """Схема SKU для карточки товара (b2c/openapi.yaml: CatalogSku).

    Обязательные поля: id, price, available_quantity.
    Опциональные: sku_code, name, old_price, attributes, images.
    Поля in_stock и discount контрактом не предусмотрены — не включаем.
    """
    id: uuid.UUID
    price: int                              # копейки
    available_quantity: int                 # из B2B active_quantity
    sku_code: str | None = None             # из B2B article
    name: str | None = None
    old_price: int | None = None
    attributes: list[dict[str, Any]] = []
    images: list[str] = []


class ProductOut(BaseModel):
    id: uuid.UUID
    name: str                               # B2B title → B2C name
    description: str
    category_id: uuid.UUID | None
    slug: str | None = None
    images: list[str]
    min_price: int
    has_stock: bool
    skus: list[CatalogSku]


class ProductListOut(BaseModel):
    id: uuid.UUID
    title: str
    slug: str
    category_id: uuid.UUID | None
    min_price: int
    images: list[str]


class FacetValue(BaseModel):
    value: str
    count: int


class Facet(BaseModel):
    field: str
    values: list[FacetValue]


class FacetsResponse(BaseModel):
    facets: list[Facet]


class PaginatedProducts(BaseModel):
    items: list[ProductListOut]
    total: int
    page: int
    page_size: int


class CategoryOut(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    parent_id: uuid.UUID | None
    children: list["CategoryOut"] = []


class BreadcrumbItem(BaseModel):
    id: uuid.UUID
    name: str
    slug: str


class BreadcrumbsResponse(BaseModel):
    breadcrumbs: list[BreadcrumbItem]