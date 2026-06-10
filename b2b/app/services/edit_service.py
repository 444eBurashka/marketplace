import uuid
from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.models import (
    Image, ImageEntityType, Product, ProductCharacteristic,
    ProductStatus, SKU, SKUAttribute,
)
from app.schemas.patch_schemas import ProductPatchRequest, SKUPatchRequest
from app.schemas.products import ProductResponse
from app.schemas.skus import SKUResponse

# Статусы, при которых правка отправляет товар на повторную модерацию
_MODERATION_TRIGGER_STATUSES = {ProductStatus.MODERATED, ProductStatus.BLOCKED}


async def _send_moderation_event(
    product: Product,
    idempotency_key: uuid.UUID,
) -> None:
    """Fire-and-forget POST в Moderation."""
    payload = {
        "event_type": "EDITED",
        "occurred_at": datetime.now(UTC).isoformat(),
        "payload": {
            "idempotency_key": str(idempotency_key),
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


async def _load_product(product_id: uuid.UUID, db: AsyncSession) -> Product | None:
    result = await db.execute(
        select(Product)
        .where(Product.id == product_id, Product.deleted == False)  # noqa: E712
        .options(
            selectinload(Product.images),
            selectinload(Product.characteristics),
            selectinload(Product.skus).selectinload(SKU.images),
            selectinload(Product.skus).selectinload(SKU.attributes),
        )
    )
    return result.scalar_one_or_none()


async def patch_product(
    product_id: uuid.UUID,
    body: ProductPatchRequest,
    seller_id: uuid.UUID,
    db: AsyncSession,
) -> ProductResponse:
    product = await _load_product(product_id, db)

    if product is None:
        raise LookupError("Product not found")
    if product.seller_id != seller_id:
        raise PermissionError("NOT_OWNER")
    if product.status == ProductStatus.HARD_BLOCKED:
        raise PermissionError("HARD_BLOCKED")

    # Применяем изменения полей
    if body.title is not None:
        product.title = body.title
    if body.description is not None:
        product.description = body.description
    if body.category_id is not None:
        product.category_id = body.category_id

    if body.images is not None:
        # Удаляем старые изображения товара и добавляем новые
        old_images = await db.execute(
            select(Image).where(
                Image.entity_type == ImageEntityType.PRODUCT,
                Image.entity_id == product.id,
            )
        )
        for img in old_images.scalars():
            await db.delete(img)
        for img in body.images:
            db.add(Image(
                entity_type=ImageEntityType.PRODUCT,
                entity_id=product.id,
                url=img.url,
                ordering=img.ordering,
            ))

    if body.characteristics is not None:
        for ch in product.characteristics:
            await db.delete(ch)
        for ch in body.characteristics:
            db.add(ProductCharacteristic(
                product_id=product.id,
                name=ch.name,
                value=ch.value,
            ))

    # Переход статуса
    if product.status in _MODERATION_TRIGGER_STATUSES:
        product.status = ProductStatus.ON_MODERATION
        await db.flush()
        await _send_moderation_event(product, uuid.uuid4())
    else:
        await db.flush()

    # Перезагружаем для актуального ответа
    product = await _load_product(product_id, db)
    return ProductResponse.model_validate(product)


async def patch_sku(
    sku_id: uuid.UUID,
    body: SKUPatchRequest,
    seller_id: uuid.UUID,
    db: AsyncSession,
) -> SKUResponse:
    result = await db.execute(
        select(SKU)
        .where(SKU.id == sku_id)
        .options(
            selectinload(SKU.product),
            selectinload(SKU.images),
            selectinload(SKU.attributes),
        )
    )
    sku = result.scalar_one_or_none()

    if sku is None:
        raise LookupError("SKU not found")

    product = sku.product

    if product.seller_id != seller_id:
        raise PermissionError("NOT_OWNER")
    if product.status == ProductStatus.HARD_BLOCKED:
        raise PermissionError("HARD_BLOCKED")

    # Применяем изменения (reserved_quantity не трогаем — это ключевое требование)
    if body.name is not None:
        sku.name = body.name
    if body.price is not None:
        sku.price = body.price
    if body.discount is not None:
        sku.discount = body.discount
    if body.cost_price is not None:
        sku.cost_price = body.cost_price
    if body.article is not None:
        sku.article = body.article

    if body.characteristics is not None:
        for attr in sku.attributes:
            await db.delete(attr)
        for ch in body.characteristics:
            db.add(SKUAttribute(sku_id=sku.id, name=ch.name, value=ch.value))

    # Переход статуса родительского товара
    if product.status in _MODERATION_TRIGGER_STATUSES:
        product.status = ProductStatus.ON_MODERATION
        await db.flush()
        await _send_moderation_event(product, uuid.uuid4())
    else:
        await db.flush()

    await db.refresh(sku)
    # Перезагружаем с relationships
    result2 = await db.execute(
        select(SKU)
        .where(SKU.id == sku.id)
        .options(selectinload(SKU.images), selectinload(SKU.attributes))
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