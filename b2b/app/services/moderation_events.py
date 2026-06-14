import uuid
from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.models import (
    ModerationEventInbox, Product, ProductStatus, SKU,
)
from app.schemas.moderation_events import ModerationEventRequest


async def _send_b2c_product_blocked(product: Product) -> None:
    """Fire-and-forget: уведомить B2C о блокировке товара."""
    sku_ids = [str(sku.id) for sku in product.skus]
    payload = {
        "idempotency_key": str(uuid.uuid4()),
        "event_type": "PRODUCT_BLOCKED",
        "occurred_at": datetime.now(UTC).isoformat(),
        "payload": {
            "product_id": str(product.id),
            "sku_ids": sku_ids,
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


async def process_moderation_event(
    body: ModerationEventRequest,
    db: AsyncSession,
) -> None:
    # ── Идемпотентность: проверяем не обрабатывали ли уже этот ключ ──────────
    existing = await db.scalar(
        select(ModerationEventInbox).where(
            ModerationEventInbox.idempotency_key == body.idempotency_key
        )
    )
    if existing is not None:
        # Дубликат — возвращаем 200/204 без каких-либо side effects
        return

    # ── Загружаем товар со SKU (нужны для каскада в B2C) ─────────────────────
    result = await db.execute(
        select(Product)
        .where(Product.id == body.product_id)
        .options(selectinload(Product.skus))
    )
    product = result.scalar_one_or_none()
    if product is None:
        raise LookupError(f"Product {body.product_id} not found")

    # ── Сохраняем запись в inbox (до обработки — защита от race condition) ────
    inbox = ModerationEventInbox(
        idempotency_key=body.idempotency_key,
        event_type=body.event_type,
        product_id=body.product_id,
        raw_payload=body.model_dump(mode="json"),
        processed_at=datetime.now(UTC),
    )
    db.add(inbox)
    await db.flush()

    # ── Применяем решение ─────────────────────────────────────────────────────
    if body.event_type == "MODERATED":
        product.status = ProductStatus.MODERATED
        product.blocked = False
        product.blocking_reason_id = None
        product.moderator_comment = None

    elif body.event_type == "BLOCKED":
        if body.hard_block:
            product.status = ProductStatus.HARD_BLOCKED
        else:
            product.status = ProductStatus.BLOCKED

        product.blocked = True
        product.blocking_reason_id = body.blocking_reason_id
        product.moderator_comment = body.moderator_comment

        await db.flush()
        # Каскадное событие в B2C — fire-and-forget
        await _send_b2c_product_blocked(product)

    else:
        raise ValueError(f"Unknown event_type: {body.event_type}")

    await db.flush()