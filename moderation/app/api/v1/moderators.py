import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi import status as http_status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import AdminOnly, CurrentModerator
from app.core.security import hash_password
from app.db.session import get_db
from app.models import Moderator, ModeratorRole
from app.schemas.common import PaginatedResponse
from app.schemas.moderators import (
    ModeratorCreateRequest,
    ModeratorResponse,
    ModeratorUpdateRequest,
)
from shared.errors.http import ConflictError

router = APIRouter()
DB = Annotated[AsyncSession, Depends(get_db)]


@router.get("/me", response_model=ModeratorResponse)
async def get_current_moderator_profile(
    current: CurrentModerator,
) -> ModeratorResponse:
    """Profile of the current moderator."""
    return ModeratorResponse.model_validate(current)


@router.get("", response_model=PaginatedResponse)
async def list_moderators(
    admin: AdminOnly,
    db: DB,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    is_active: bool | None = None,
) -> PaginatedResponse:
    """List moderators (admin only)."""
    conditions = []
    if is_active is not None:
        conditions.append(Moderator.is_active == is_active)

    total = await db.scalar(
        select(func.count()).select_from(Moderator).where(*conditions)
    ) or 0

    rows = (
        (await db.execute(
            select(Moderator).where(*conditions).order_by(Moderator.created_at.desc())
            .limit(limit).offset(offset)
        )).scalars().all()
    )

    return PaginatedResponse(
        items=[ModeratorResponse.model_validate(m) for m in rows],
        total_count=total, limit=limit, offset=offset,
    )


@router.post("", response_model=ModeratorResponse, status_code=http_status.HTTP_201_CREATED)
async def create_moderator(
    body: ModeratorCreateRequest,
    admin: AdminOnly,
    db: DB,
) -> ModeratorResponse:
    """Create moderator (admin only)."""
    exists = await db.scalar(select(Moderator.id).where(Moderator.email == body.email))
    if exists:
        raise ConflictError(detail="Email already registered")

    mod = Moderator(
        email=body.email,
        hashed_password=hash_password(body.password),
        first_name=body.first_name,
        last_name=body.last_name,
        role=ModeratorRole(body.role),
    )
    db.add(mod)
    await db.flush()
    return ModeratorResponse.model_validate(mod)


@router.get("/{moderator_id}", response_model=ModeratorResponse)
async def get_moderator(
    moderator_id: uuid.UUID,
    admin: AdminOnly,
    db: DB,
) -> ModeratorResponse:
    """Get moderator by ID (admin only)."""
    mod = await db.get(Moderator, moderator_id)
    if mod is None:
        raise HTTPException(status_code=404, detail="Moderator not found")
    return ModeratorResponse.model_validate(mod)


@router.patch("/{moderator_id}", response_model=ModeratorResponse)
async def update_moderator(
    moderator_id: uuid.UUID,
    body: ModeratorUpdateRequest,
    admin: AdminOnly,
    db: DB,
) -> ModeratorResponse:
    """Update moderator (admin only)."""
    mod = await db.get(Moderator, moderator_id)
    if mod is None:
        raise HTTPException(status_code=404, detail="Moderator not found")

    for field, value in body.model_dump(exclude_unset=True).items():
        if value is not None:
            if field == "role":
                setattr(mod, field, ModeratorRole(value))
            else:
                setattr(mod, field, value)
    await db.flush()
    return ModeratorResponse.model_validate(mod)


@router.delete("/{moderator_id}", status_code=http_status.HTTP_204_NO_CONTENT)
async def deactivate_moderator(
    moderator_id: uuid.UUID,
    admin: AdminOnly,
    db: DB,
) -> None:
    """Deactivate moderator (admin only)."""
    mod = await db.get(Moderator, moderator_id)
    if mod is None:
        raise HTTPException(status_code=404, detail="Moderator not found")
    mod.is_active = False