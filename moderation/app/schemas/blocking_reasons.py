import uuid
from pydantic import BaseModel, Field


class BlockingReasonCreateRequest(BaseModel):
    code: str = Field(pattern=r"^[A-Z_]+$", max_length=64)
    title: str = Field(max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    hard_block: bool


class BlockingReasonUpdateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    is_active: bool | None = None


class BlockingReasonResponse(BaseModel):
    model_config = {"from_attributes": True}
    id: uuid.UUID
    code: str
    title: str
    description: str | None = None
    hard_block: bool
    is_active: bool


class BlockingReasonListResponse(BaseModel):
    items: list[BlockingReasonResponse]
    total_count: int


# Alias for public-facing read API — slight variant with minimal safe fields
class ProductBlockingReasonOut(BaseModel):
    model_config = {"from_attributes": True}
    id: uuid.UUID
    title: str
    hard_block: bool