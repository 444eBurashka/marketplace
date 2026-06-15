import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user_id: uuid.UUID
    role: str


class LoginRequest(BaseModel):
    email: str = Field(max_length=255)
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str