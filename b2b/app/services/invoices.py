import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Invoice, InvoiceItem, InvoiceStatus, ProductStatus, SKU
from app.schemas.invoices import InvoiceAcceptRequest, InvoiceCreateRequest


def _load_invoice_query(invoice_id: uuid.UUID):
    return (
        select(Invoice)
        .where(Invoice.id == invoice_id)
        .options(selectinload(Invoice.items).selectinload(InvoiceItem.sku))
    )


async def create_invoice(
    body: InvoiceCreateRequest,
    seller_id: uuid.UUID,
    db: AsyncSession,
) -> Invoice:
    # Загружаем все SKU за один запрос
    sku_ids = [item.sku_id for item in body.items]
    result = await db.execute(
        select(SKU)
        .where(SKU.id.in_(sku_ids))
        .options(selectinload(SKU.product))
    )
    skus_by_id = {sku.id: sku for sku in result.scalars()}

    # Валидация каждой позиции
    for item in body.items:
        sku = skus_by_id.get(item.sku_id)
        if sku is None:
            raise LookupError(f"SKU {item.sku_id} not found")
        if sku.product.seller_id != seller_id:
            raise PermissionError("NOT_OWNER")
        if sku.product.status != ProductStatus.MODERATED:
            raise ValueError("Invoice can only be created for MODERATED products")

    # Создаём накладную
    invoice = Invoice(
        seller_id=seller_id,
        status=InvoiceStatus.PENDING,
    )
    db.add(invoice)
    await db.flush()

    for item in body.items:
        db.add(InvoiceItem(
            invoice_id=invoice.id,
            sku_id=item.sku_id,
            quantity=item.quantity,
            accepted_quantity=None,
        ))

    await db.flush()

    result = await db.execute(_load_invoice_query(invoice.id))
    return result.scalar_one()


async def accept_invoice(
    invoice_id: uuid.UUID,
    body: InvoiceAcceptRequest,
    db: AsyncSession,
) -> Invoice:
    result = await db.execute(_load_invoice_query(invoice_id))
    invoice = result.scalar_one_or_none()

    if invoice is None:
        raise LookupError("Invoice not found")

    if invoice.status != InvoiceStatus.PENDING:
        raise ValueError("Invoice already processed")

    # Индекс позиций по id
    items_by_id = {item.id: item for item in invoice.items}

    # Применяем приёмку и обновляем active_quantity (quantity на SKU) атомарно
    for accept in body.accepted_items:
        item = items_by_id.get(accept.invoice_item_id)
        if item is None:
            raise LookupError(f"InvoiceItem {accept.invoice_item_id} not found")
        if accept.accepted_quantity > item.quantity:
            raise ValueError(
                f"accepted_quantity {accept.accepted_quantity} exceeds quantity {item.quantity}"
            )
        item.accepted_quantity = accept.accepted_quantity
        # Атомарно увеличиваем остаток SKU
        item.sku.quantity += accept.accepted_quantity

    # Определяем итоговый статус накладной
    accepted = [i.accepted_quantity for i in invoice.items if i.accepted_quantity is not None]
    quantities = [i.quantity for i in invoice.items]

    if len(accepted) == len(invoice.items):
        if all(a == q for a, q in zip(accepted, quantities)):
            invoice.status = InvoiceStatus.ACCEPTED
        elif all(a == 0 for a in accepted):
            invoice.status = InvoiceStatus.REJECTED
        else:
            invoice.status = InvoiceStatus.PARTIALLY_ACCEPTED
    else:
        invoice.status = InvoiceStatus.PARTIALLY_ACCEPTED

    invoice.accepted_at = datetime.now(UTC)

    await db.flush()

    result = await db.execute(_load_invoice_query(invoice.id))
    return result.scalar_one()