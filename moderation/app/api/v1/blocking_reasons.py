import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi import status as http_status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import AdminOnly, CurrentModerator
from app.db.session import get_db
from app.models import BlockingReason, TicketBlockingReason
from app.schemas.blocking_reasons import (
    BlockingReasonCreateRequest,
    BlockingReasonListResponse,
    BlockingReasonResponse,
    BlockingReasonUpdateRequest,
    ProductBlockingReasonOut,
)
from shared.errors.http import ConflictError

router = APIRouter()
DB = Annotated[AsyncSession, Depends(get_db)]


async def _validate_code_unique(code: str, db: AsyncSession, exclude_id: uuid.UUID | None = None) -> None:
    """Check that code is unique; raise 409 if taken."""
    stmt = select(BlockingReason.id).where(BlockingReason.code == code)
    if exclude_id is not None:
        stmt = stmt.where(BlockingReason.id != exclude_id)
    existing = await db.scalar(stmt)
    if existing is not None:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail=f"Blocking reason with code '{code}' already exists",
        )


async def _get_or_404(reason_id: uuid.UUID, db: AsyncSession) -> BlockingReason:
    reason = await db.get(BlockingReason, reason_id)
    if reason is None:
        raise HTTPException(status_code=404, detail="Blocking reason not found")
    return reason


# ── Public / Moderator-facing ─────────────────────────────────────────────

@router.get(
    "/product-blocking-reasons",
    response_model=list[ProductBlockingReasonOut],
    summary="List active blocking reasons for product moderation",
    description=(
        "Returns active blocking reasons that moderators can select "
        "when declining a ticket. Optionally filter by hard_block flag."
    ),
)
async def list_product_blocking_reasons(
    moderator: CurrentModerator,
    db: DB,
    hard_block: bool | None = Query(
        default=None,
        description="Filter by hard_block flag: true/false. Omit to return all active reasons.",
    ),
) -> list[ProductBlockingReasonOut]:
    """List active blocking reasons. Any authenticated moderator can access."""
    conditions = [BlockingReason.is_active == True]
    if hard_block is not None:
        conditions.append(BlockingReason.hard_block == hard_block)

    rows = (
        (await db.execute(
            select(BlockingReason).where(*conditions).order_by(BlockingReason.code)
        )).scalars().all()
    )
    return [ProductBlockingReasonOut.model_validate(r) for r in rows]


@router.get("", response_model=BlockingReasonListResponse, summary="List blocking reasons (admin)")
async def list_blocking_reasons(
    admin: AdminOnly,
    db: DB,
    is_active: bool | None = Query(default=True, description="Filter by active status (default: only active)"),
    hard_block: bool | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> BlockingReasonListResponse:
    """List all blocking reasons with optional filters. Admin only."""
    conditions = []
    if is_active is not None:
        conditions.append(BlockingReason.is_active == is_active)
    if hard_block is not None:
        conditions.append(BlockingReason.hard_block == hard_block)

    total = await db.scalar(
        select(func.count()).select_from(BlockingReason).where(*conditions)
    ) or 0

    rows = (
        (await db.execute(
            select(BlockingReason).where(*conditions).order_by(BlockingReason.code)
            .limit(limit).offset(offset)
        )).scalars().all()
    )
    return BlockingReasonListResponse(
        items=[BlockingReasonResponse.model_validate(r) for r in rows],
        total_count=total,
    )


@router.get("/{reason_id}", response_model=BlockingReasonResponse)
async def get_blocking_reason(
    reason_id: uuid.UUID,
    admin: AdminOnly,
    db: DB,
) -> BlockingReasonResponse:
    """Get a single blocking reason by ID. Admin only."""
    reason = await _get_or_404(reason_id, db)
    return BlockingReasonResponse.model_validate(reason)


@router.post(
    "",
    response_model=BlockingReasonResponse,
    status_code=http_status.HTTP_201_CREATED,
)
async def create_blocking_reason(
    body: BlockingReasonCreateRequest,
    admin: AdminOnly,
    db: DB,
) -> BlockingReasonResponse:
    """Create a new blocking reason. Admin only."""
    await _validate_code_unique(body.code, db)

    reason = BlockingReason(
        code=body.code,
        title=body.title,
        description=body.description,
        hard_block=body.hard_block,
        is_active=True,
    )
    db.add(reason)
    await db.flush()
    await db.refresh(reason)
    return BlockingReasonResponse.model_validate(reason)


@router.patch("/{reason_id}", response_model=BlockingReasonResponse)
async def update_blocking_reason(
    reason_id: uuid.UUID,
    body: BlockingReasonUpdateRequest,
    admin: AdminOnly,
    db: DB,
) -> BlockingReasonResponse:
    """Update a blocking reason. Admin only.
    Use is_active=false to soft-deactivate a reason without breaking FK references.
    """
    reason = await _get_or_404(reason_id, db)

    for field, value in body.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(reason, field, value)
    await db.flush()
    await db.refresh(reason)
    return BlockingReasonResponse.model_validate(reason)


@router.delete("/{reason_id}", status_code=http_status.HTTP_204_NO_CONTENT)
async def deactivate_blocking_reason(
    reason_id: uuid.UUID,
    admin: AdminOnly,
    db: DB,
) -> None:
    """Deactivate a blocking reason (soft-delete).

    If any moderation ticket references this reason, the delete is rejected
    with 409 Conflict. In that case use PATCH is_active=false instead.

    Never physically delete records — historical FK references must be preserved.
    """
    reason = await _get_or_404(reason_id, db)

    # Check referential integrity: is this reason referenced by any ticket?
    ref_count = await db.scalar(
        select(func.count()).select_from(TicketBlockingReason).where(
            TicketBlockingReason.blocking_reason_id == reason_id,
        )
    ) or 0

    if ref_count > 0:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail=(
                "Cannot delete blocking reason that is referenced by tickets. "
                "Use PATCH to set is_active=false instead."
            ),
        )

    # Safe to soft-delete (no references)
    reason.is_active = False
    await db.flush()
