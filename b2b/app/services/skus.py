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
    """Fire-and-forget POST в Moderation. Ошибка не прерывает создание SKU."""
    payload = {
        "idempotency_key": str(idempotency_key),
        "product_id": str(product.id),
        "seller_id": str(product.seller_id),
        "event": event_type,
        "date": datetime.now(UTC).isoformat(),
    }
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                f"{settings.moderation_internal_url}/api/v1/events/product",
                json=payload,
                headers={"X-Service-Key": settings.service_key},
            )
    except Exception:
        # Moderation недоступна — не роняем запрос продавца.
        # В production здесь был бы outbox, пока логируем молча.
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
        event_type = "CREATED" if product.status == ProductStatus.CREATED else "EDITED"
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