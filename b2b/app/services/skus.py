import uuid
from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.models import Image, ImageEntityType, Product, ProductStatus, SKU, SKUAttribute
from app.schemas.skus import SKUCreateRequest, SKUResponse


# Статусы, при которых добавление SKU триггерит ON_MODERATION
_TRANSITION_STATUSES = {
    ProductStatus.CREATED,
    ProductStatus.MODERATED,
    ProductStatus.BLOCKED,
}


async def _send_moderation_event(
    product: Product,
    event_type: str,
    idempotency_key: uuid.UUID,
) -> None:
    payload = {
        "idempotency_key": str(idempotency_key),
        "event_type": event_type,
        "occurred_at": datetime.now(UTC).isoformat(),
        "payload": {
            "product_id": str(product.id),
            "seller_id": str(product.seller_id),
            "json_after": {
                "id": str(product.id),
                "seller_id": str(product.seller_id),
                "category_id": str(product.category_id) if product.category_id else None,
                "title": product.title,
                "description": product.description,
                "status": product.status.value,
            },
        },
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


async def create_sku(
    body: SKUCreateRequest,
    seller_id: uuid.UUID,
    db: AsyncSession,
) -> SKUResponse:
    # 1. Загружаем товар с подсчётом существующих SKU
    result = await db.execute(
        select(Product)
        .where(Product.id == body.product_id, Product.deleted == False)  # noqa: E712
        .options(selectinload(Product.skus))
    )
    product = result.scalar_one_or_none()

    if product is None:
        raise LookupError("Product not found")

    if product.seller_id != seller_id:
        raise PermissionError("Product belongs to another seller")

    if product.status == ProductStatus.HARD_BLOCKED:
        raise PermissionError("Cannot add SKU to hard-blocked product")

    # 2. Создаём SKU
    sku = SKU(
        product_id=product.id,
        name=body.name,
        article=body.article,
        price=body.price,
        discount=body.discount,
        cost_price=body.cost_price,
        quantity=0,
        reserved_quantity=0,
        is_active=True,
    )
    db.add(sku)
    await db.flush()  # получаем sku.id

    # 3. Изображения SKU
    for img in body.images:
        db.add(Image(
            entity_type=ImageEntityType.SKU,
            entity_id=sku.id,
            url=img.url,
            ordering=img.ordering,
        ))

    # 4. Характеристики SKU
    for char in body.characteristics:
        db.add(SKUAttribute(
            sku_id=sku.id,
            name=char.name,
            value=char.value,
        ))

    await db.flush()

    # 5. Переход статуса товара и событие в Moderation
    idempotency_key = uuid.uuid4()
    is_first_sku = len(product.skus) == 1  # flush уже добавил sku в коллекцию

    if product.status in _TRANSITION_STATUSES:
        event_type = "PRODUCT_CREATED" if product.status == ProductStatus.CREATED else "PRODUCT_EDITED"
        product.status = ProductStatus.ON_MODERATION
        await db.flush()
        await _send_moderation_event(product, event_type, idempotency_key)

    # 6. Перезагружаем SKU с relationships для сериализации
    await db.refresh(sku)
    result2 = await db.execute(
        select(SKU)
        .where(SKU.id == sku.id)
        .options(
            selectinload(SKU.images),
            selectinload(SKU.attributes),
        )
    )
    sku = result2.scalar_one()

    return SKUResponse(
        id=sku.id,
        product_id=sku.product_id,
        name=sku.name,
        price=sku.price,
        discount=sku.discount,
        cost_price=sku.cost_price,
        stock_quantity=sku.quantity,
        active_quantity=sku.active_quantity,
        reserved_quantity=sku.reserved_quantity,
        article=sku.article,
        images=[{"id": i.id, "url": i.url, "ordering": i.ordering} for i in sku.images],
        characteristics=[{"id": a.id, "name": a.name, "value": a.value} for a in sku.attributes],
        created_at=sku.created_at,
        updated_at=sku.updated_at,
    )

# ─── B2B-12: Удаление SKU ────────────────────────────────────────────────────

async def _send_moderation_deleted_event(product: Product) -> None:
    """Событие PRODUCT_DELETED в Moderation (товар ушёл из очереди — нет SKU)."""
    payload = {
        "idempotency_key": str(uuid.uuid4()),
        "event_type": "PRODUCT_DELETED",
        "occurred_at": datetime.now(UTC).isoformat(),
        "payload": {
            "product_id": str(product.id),
            "seller_id": str(product.seller_id),
        },
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


async def _send_b2c_sku_out_of_stock(product: Product, sku_id: uuid.UUID) -> None:
    """Событие SKU_OUT_OF_STOCK в B2C (MODERATED-товар, active_quantity > 0)."""
    payload = {
        "idempotency_key": str(uuid.uuid4()),
        "event_type": "SKU_OUT_OF_STOCK",
        "occurred_at": datetime.now(UTC).isoformat(),
        "payload": {
            "product_id": str(product.id),
            "sku_ids": [str(sku_id)],
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


async def delete_sku(
    sku_id: uuid.UUID,
    seller_id: uuid.UUID,
    db: AsyncSession,
) -> None:
    """
    Удаляет SKU. Порядок проверок строго по канону:
    1. Найти SKU
    2. IDOR — ownership через parent product
    3. HARD_BLOCKED → 403
    4. reserved_quantity > 0 → 409
    5. Удаление + side-эффекты
    """
    # 1. Загружаем SKU вместе с продуктом и всеми активными SKU продукта
    result = await db.execute(
        select(SKU)
        .where(SKU.id == sku_id, SKU.is_active == True)  # noqa: E712
        .options(
            selectinload(SKU.product).selectinload(Product.skus)
        )
    )
    sku = result.scalar_one_or_none()

    if sku is None:
        raise LookupError("SKU not found")

    product = sku.product

    # 2. IDOR — ownership через parent product
    if product.seller_id != seller_id:
        raise PermissionError("NOT_OWNER")

    # 3. HARD_BLOCKED → 403
    if product.status == ProductStatus.HARD_BLOCKED:
        raise BlockedError("Cannot delete SKU of hard-blocked product")

    # 4. Активные резервы → 409
    if sku.reserved_quantity > 0:
        raise ConflictError("Cannot delete SKU with active reserves")

    # 5. Удаление (мягкое — is_active=False)
    active_qty_before = sku.active_quantity  # quantity - reserved_quantity
    sku.is_active = False
    await db.flush()

    # Считаем оставшиеся активные SKU после удаления текущего
    remaining_skus = [s for s in product.skus if s.id != sku_id and s.is_active]

    # Side-эффект А: последний SKU при ON_MODERATION → CREATED + DELETED в Moderation
    if len(remaining_skus) == 0 and product.status == ProductStatus.ON_MODERATION:
        product.status = ProductStatus.CREATED
        await db.flush()
        await _send_moderation_deleted_event(product)

    # Side-эффект Б: active_quantity > 0 и товар MODERATED → SKU_OUT_OF_STOCK в B2C
    if active_qty_before > 0 and product.status == ProductStatus.MODERATED:
        await _send_b2c_sku_out_of_stock(product, sku_id)


class BlockedError(Exception):
    """Товар HARD_BLOCKED — операция запрещена."""


class ConflictError(Exception):
    """SKU имеет активные резервы — нельзя удалить."""