import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=100)
    company_name: str = Field(min_length=2, max_length=255)
    inn: str = Field(min_length=10, max_length=12, pattern=r"^\d+$")
    phone: str | None = Field(default=None, max_length=20)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class SellerResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    email: str
    company_name: str
    inn: str
    phone: str | None
    description: str | None
    is_active: bool
    created_at: datetime


class UpdateSellerRequest(BaseModel):
    company_name: str | None = Field(default=None, min_length=2, max_length=255)
    phone: str | None = Field(default=None, max_length=20)
    description: str | None = None
