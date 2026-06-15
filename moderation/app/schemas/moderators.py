import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


class ModeratorCreateRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=100)
    first_name: str = Field(max_length=100)
    last_name: str | None = Field(default=None, max_length=100)
    role: str = Field(default="MODERATOR", pattern=r"^(MODERATOR|ADMIN)$")
    category_specializations: list[uuid.UUID] = Field(default_factory=list)


class ModeratorUpdateRequest(BaseModel):
    first_name: str | None = Field(default=None, max_length=100)
    last_name: str | None = Field(default=None, max_length=100)
    role: str | None = Field(default=None, pattern=r"^(MODERATOR|ADMIN)$")
    is_active: bool | None = None
    category_specializations: list[uuid.UUID] | None = None


class ModeratorResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    email: str
    first_name: str
    last_name: str | None = None
    role: str
    is_active: bool
    category_specializations: list[uuid.UUID] = Field(default_factory=list)
    created_at: datetime
    last_login_at: datetime | None = None