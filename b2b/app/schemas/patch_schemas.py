import uuid
from pydantic import BaseModel, Field
from app.schemas.products import CharacteristicIn, ImageIn


class ProductPatchRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, min_length=1, max_length=5000)
    category_id: uuid.UUID | None = None
    images: list[ImageIn] | None = None
    characteristics: list[CharacteristicIn] | None = None


class SKUCharacteristicIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    value: str = Field(min_length=1, max_length=500)


class SKUPatchRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    price: int | None = Field(default=None, gt=0)
    discount: int | None = Field(default=None, ge=0)
    cost_price: int | None = Field(default=None, gt=0)
    article: str | None = Field(default=None, max_length=255)
    characteristics: list[SKUCharacteristicIn] | None = None