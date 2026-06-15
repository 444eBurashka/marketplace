from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.auth import LoginRequest, RefreshRequest, TokenResponse
from app.services.auth_service import authenticate_moderator, logout_moderator, refresh_tokens

router = APIRouter()
DB = Annotated[AsyncSession, Depends(get_db)]


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, db: DB) -> TokenResponse:
    """Login moderator."""
    return await authenticate_moderator(body.email, body.password, db)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(body: RefreshRequest, db: DB) -> TokenResponse:
    """Refresh access token."""
    return await refresh_tokens(body.refresh_token, db)


@router.post("/logout", status_code=204)
async def logout(body: RefreshRequest, db: DB) -> None:
    """Logout (revoke refresh token)."""
    await logout_moderator(body.refresh_token, db)