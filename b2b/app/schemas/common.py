"""
Общие схемы, используемые в products.py и skus.py.
Вынесены сюда чтобы избежать циклического импорта.
"""
import uuid

from pydantic import BaseModel, Field


class ImageOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    url: str
    ordering: int


class CharacteristicOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    name: str
    value: str