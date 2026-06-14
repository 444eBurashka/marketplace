import uuid
from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.models import Product, SKU


async def _send_moderation_deleted(product: Product) -> None:
    payload = {
        "idempotency_key": str(uuid.uuid4()),
        "event_type": "PRODUCT_DELETED",
        "occurred_at": datetime.now(UTC).isoformat(),
        "product_id": str(product.id),
        "seller_id": str(product.seller_id),
    }
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                f"{settings.moderation_internal_url}/api/v1/b2b/events",
                json=payload,
                headers={"X-Service-Key": settings.service_key},
            )
    except Exception:
        pass


async def _send_b2c_deleted(product: Product, sku_ids: list[uuid.UUID]) -> None:
    payload = {
        "event_type": "PRODUCT_DELETED",
        "occurred_at": datetime.now(UTC).isoformat(),
        "payload": {
            "idempotency_key": str(uuid.uuid4()),
            "product_id": str(product.id),
            "sku_ids": [str(s) for s in sku_ids],
        },
    }
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                f"{settings.b2c_internal_url}/api/v1/b2b/events",
                json=payload,
                headers={"X-Service-Key": settings.service_key},
            )
    except Exception:
        pass


async def delete_product(
    product_id: uuid.UUID,
    seller_id: uuid.UUID,
    db: AsyncSession,
) -> None:
    result = await db.execute(
        select(Product)
        .where(Product.id == product_id)
        .options(selectinload(Product.skus))
    )
    product = result.scalar_one_or_none()

    if product is None:
        raise LookupError("Product not found")
    if product.seller_id != seller_id:
        raise PermissionError("NOT_OWNER")
    if product.deleted:
        raise ValueError("Product already deleted")

    product.deleted = True
    await db.flush()

    sku_ids = [sku.id for sku in product.skus]
    await _send_moderation_deleted(product)
    await _send_b2c_deleted(product, sku_ids)