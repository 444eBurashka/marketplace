import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import CurrentBuyer
from app.db.session import get_db
from app.models import ProductSubscription, SubscriptionType
from app.schemas.cart import SubscribeRequest
from app.services import b2b_client

router = APIRouter()
DB = Annotated[AsyncSession, Depends(get_db)]

VALID_NOTIFY_ON = {t.value for t in SubscriptionType}


@router.post("/subscriptions", status_code=201)
async def subscribe(body: SubscribeRequest, buyer: CurrentBuyer, db: DB) -> dict:
    if not body.notify_on:
        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_NOTIFY_ON", "message": "notify_on must not be empty"},
        )

    invalid = set(body.notify_on) - VALID_NOTIFY_ON
    if invalid:
        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_NOTIFY_ON", "message": f"Invalid values: {invalid}. Allowed: {VALID_NOTIFY_ON}"},
        )

    # Проверяем существование SKU через B2B (опционально — через products)
    # Можно упростить: просто сохранить подписку
    created = []
    for notify_type in body.notify_on:
        # Проверяем дубликат
        existing = await db.scalar(
            select(ProductSubscription).where(
                ProductSubscription.buyer_id == buyer.id,
                ProductSubscription.sku_id == body.sku_id,
                ProductSubscription.type == notify_type,
            )
        )
        if existing:
            raise HTTPException(
                status_code=409,
                detail={"code": "DUPLICATE_SUBSCRIPTION", "message": "Already subscribed"},
            )

        sub = ProductSubscription(
            buyer_id=buyer.id,
            sku_id=body.sku_id,
            type=notify_type,
        )
        db.add(sub)
        await db.flush()
        created.append({"id": str(sub.id), "sku_id": str(body.sku_id), "type": notify_type})

    return {"subscriptions": created, "notify_on": body.notify_on}


@router.delete("/subscriptions", status_code=204)
async def unsubscribe(
    sku_id: uuid.UUID,
    buyer: CurrentBuyer,
    db: DB,
) -> None:
    await db.execute(
        delete(ProductSubscription).where(
            ProductSubscription.buyer_id == buyer.id,
            ProductSubscription.sku_id == sku_id,
        )
    )
