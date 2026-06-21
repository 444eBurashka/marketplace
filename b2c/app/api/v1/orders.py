import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.dependencies import CurrentBuyer
from app.db.session import get_db
from app.models import Address, Buyer, Cart, CartItem, Order, OrderItem, OrderStatus, OrderStatusHistory
from app.schemas.orders import CancelRequest, CheckoutRequest
from app.services import b2b_client
from shared.errors.http import NotFoundError
from shared.service_auth import verify_service_key
from app.core.config import settings

router = APIRouter()
DB = Annotated[AsyncSession, Depends(get_db)]


# ─── US-ORD-01: Checkout ─────────────────────────────────────────────────────

@router.post("/orders", status_code=201)
async def checkout(body: CheckoutRequest, buyer: CurrentBuyer, db: DB) -> dict:
    # Идемпотентность по idempotency_key
    existing = await db.scalar(
        select(Order).where(Order.idempotency_key == body.idempotency_key)
    )
    if existing:
        return {"id": str(existing.id), "number": existing.number, "status": existing.status.value}

    # Получаем адрес
    address = await db.get(Address, body.address_id)
    if not address or address.buyer_id != buyer.id:
        raise HTTPException(status_code=400, detail={"code": "INVALID_ADDRESS", "message": "Address not found"})

    # Собираем items из запроса + обогащаем из B2B
    items_to_reserve = []
    order_items_data = []
    subtotal = 0

    for item in body.items:
        product_data = await b2b_client.get_product(str(item.sku_id))  # упрощение: ищем через sku
        # В реальности нужен эндпоинт B2B /api/v1/skus/{sku_id}
        # Для MVP получаем через products, ищем SKU
        # Здесь предполагаем, что b2b_client.get_product возвращает нужный объект
        # Для теста — мокаем этот вызов

        # Добавляем в список резервирования
        items_to_reserve.append({"sku_id": str(item.sku_id), "quantity": item.quantity})

    # Резервируем в B2B — all-or-nothing
    reserve_result = await b2b_client.reserve(
        order_id=str(uuid.uuid4()),  # временный, заменим после создания Order
        idempotency_key=str(body.idempotency_key),
        items=items_to_reserve,
    )

    if reserve_result["status_code"] == 409:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "RESERVE_FAILED",
                "message": "One or more items could not be reserved",
                "failed_items": reserve_result["body"].get("failed_items", []),
            },
        )
    if reserve_result["status_code"] >= 500:
        raise HTTPException(status_code=503, detail={"code": "B2B_UNAVAILABLE", "message": "B2B service unavailable"})

    # Создаём заказ
    order_number = f"ORD-{datetime.now(UTC).strftime('%Y%m%d')}-{str(uuid.uuid4())[:8].upper()}"
    address_snapshot = {
        "city": address.city,
        "street": address.street,
        "building": address.building,
        "apartment": address.apartment,
        "zip_code": address.zip_code,
    }

    order = Order(
        number=order_number,
        buyer_id=buyer.id,
        idempotency_key=body.idempotency_key,
        status=OrderStatus.PAID,
        address_snapshot=address_snapshot,
        payment_method_id=body.payment_method_id,
        subtotal=subtotal,
        delivery_cost=0,
        total=subtotal,
    )
    db.add(order)
    await db.flush()

    # Записываем историю статусов
    db.add(OrderStatusHistory(order_id=order.id, status=OrderStatus.PAID))

    return {"id": str(order.id), "number": order.number, "status": order.status.value}


# ─── US-ORD-02: Просмотр заказов ─────────────────────────────────────────────

@router.get("/orders")
async def list_orders(
    buyer: CurrentBuyer,
    db: DB,
    status: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> dict:
    query = select(Order).where(Order.buyer_id == buyer.id)
    if status:
        try:
            query = query.where(Order.status == OrderStatus(status))
        except ValueError:
            raise HTTPException(status_code=400, detail={"code": "INVALID_STATUS", "message": f"Unknown status: {status}"})

    query = query.order_by(Order.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    orders = result.scalars().all()

    return {
        "items": [
            {"id": str(o.id), "number": o.number, "status": o.status.value, "total": o.total}
            for o in orders
        ],
        "page": page,
        "page_size": page_size,
    }


@router.get("/orders/{order_id}")
async def get_order(order_id: uuid.UUID, buyer: CurrentBuyer, db: DB) -> dict:
    # IDOR защита: всегда 404, никогда 403
    result = await db.execute(
        select(Order).where(
            Order.id == order_id,
            Order.buyer_id == buyer.id,  # фильтруем сразу по buyer_id
        ).options(selectinload(Order.items), selectinload(Order.status_history))
    )
    order = result.scalar_one_or_none()
    if not order:
        raise NotFoundError(detail="Order not found")

    return {
        "id": str(order.id),
        "number": order.number,
        "status": order.status.value,
        "subtotal": order.subtotal,
        "delivery_cost": order.delivery_cost,
        "total": order.total,
        "items": [
            {
                "sku_id": str(i.sku_id),
                "name": i.name,
                "unit_price": i.unit_price,
                "quantity": i.quantity,
                "line_total": i.line_total,
            }
            for i in order.items
        ],
        "created_at": order.created_at.isoformat(),
    }


# ─── US-ORD-03: Отмена заказа ────────────────────────────────────────────────

CANCELLABLE_STATUSES = {OrderStatus.CREATED, OrderStatus.PAID}


@router.post("/orders/{order_id}/cancel")
async def cancel_order(
    order_id: uuid.UUID,
    body: CancelRequest,
    buyer: CurrentBuyer,
    db: DB,
) -> dict:
    result = await db.execute(
        select(Order).where(Order.id == order_id, Order.buyer_id == buyer.id)
    )
    order = result.scalar_one_or_none()
    if not order:
        raise NotFoundError(detail="Order not found")

    if order.status not in CANCELLABLE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "CANCEL_NOT_ALLOWED",
                "message": f"Cannot cancel order in status {order.status.value}",
                "current_status": order.status.value,
            },
        )

    # Пытаемся снять резерв в B2B
    unreserve_status = await b2b_client.unreserve(str(order.id))

    if unreserve_status in (200, 204):
        order.status = OrderStatus.CANCELLED
        order.cancelled_at = datetime.now(UTC)
        order.cancel_reason = body.reason
        db.add(OrderStatusHistory(order_id=order.id, status=OrderStatus.CANCELLED, comment=body.reason))
    else:
        # B2B недоступен — ставим CANCEL_PENDING, ретраим позже
        order.status = OrderStatus.CANCEL_PENDING
        db.add(OrderStatusHistory(order_id=order.id, status=OrderStatus.CANCEL_PENDING))

    return {"id": str(order.id), "status": order.status.value}


# ─── US-ORD-04: События от B2B ───────────────────────────────────────────────

from app.models import B2BEventInbox, CartItem
from sqlalchemy import delete as sa_delete

events_router = APIRouter(
    dependencies=[Depends(verify_service_key(settings.service_key))]
)


@events_router.post("/events/product", status_code=200)
async def handle_product_event(body: dict, db: DB) -> dict:
    idempotency_key = body.get("idempotency_key")
    event_type = body.get("event_type")
    sku_ids = body.get("sku_ids", [])

    # Идемпотентность
    if idempotency_key:
        existing = await db.scalar(
            select(B2BEventInbox).where(
                B2BEventInbox.idempotency_key == uuid.UUID(idempotency_key)
            )
        )
        if existing:
            return {"status": "already_processed"}

        inbox = B2BEventInbox(
            idempotency_key=uuid.UUID(idempotency_key),
            event_type=event_type,
            raw_payload=body,
            processed_at=datetime.now(UTC),
        )
        db.add(inbox)

    # Обновляем unavailable_reason в cart_items для всех затронутых sku_ids
    if sku_ids and event_type in ("PRODUCT_BLOCKED", "PRODUCT_DELETED", "OUT_OF_STOCK"):
        sku_uuids = [uuid.UUID(s) for s in sku_ids]
        await db.execute(
            update(CartItem)
            .where(CartItem.sku_id.in_(sku_uuids))
            .values(unavailable_reason=event_type)
        )

    # ЗАКАЗЫ НЕ ТРОГАЕМ — цены зафиксированы

    return {"status": "processed"}


# ─── US-ORD-05: Fulfill при доставке ─────────────────────────────────────────

@router.post("/orders/{order_id}/deliver")
async def mark_delivered(order_id: uuid.UUID, buyer: CurrentBuyer, db: DB) -> dict:
    """Пример: переводим заказ в DELIVERED и вызываем fulfill в B2B."""
    result = await db.execute(
        select(Order).where(Order.id == order_id, Order.buyer_id == buyer.id)
    )
    order = result.scalar_one_or_none()
    if not order:
        raise NotFoundError()

    if order.status != OrderStatus.DELIVERING:
        raise HTTPException(status_code=409, detail={"code": "INVALID_STATUS"})

    order.status = OrderStatus.DELIVERED
    db.add(OrderStatusHistory(order_id=order.id, status=OrderStatus.DELIVERED))
    await db.flush()

    # Fulfill — fire-and-forget (scaffold)
    fulfill_status = await b2b_client.fulfill(str(order.id))
    if fulfill_status not in (200, 204):
        # Логируем ошибку, не откатываем — товар уже у покупателя
        import logging
        logging.error(f"Fulfill failed for order {order.id}, status {fulfill_status}. Will retry later.")

    return {"id": str(order.id), "status": order.status.value}
