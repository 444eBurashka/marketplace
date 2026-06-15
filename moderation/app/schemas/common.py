import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class Error(BaseModel):
    code: str
    message: str
    details: dict | None = None


class PaginatedResponse(BaseModel):
    items: list
    total_count: int
    limit: int
    offset: int


class PaginationParams(BaseModel):
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)