import uuid
from typing import Any
from pydantic import BaseModel


class SKUOut(BaseModel):
    id: uuid.UUID
    code: str
    price: int          # копейки
    discount: int       # копейки, 0 если нет скидки
    in_stock: bool
    attributes: list[dict[str, str]]


class ProductOut(BaseModel):
    id: uuid.UUID
    title: str
    description: str
    category_id: uuid.UUID | None
    slug: str
    images: list[str]
    skus: list[SKUOut]


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
