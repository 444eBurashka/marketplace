import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Header
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.dependencies import CurrentBuyer
from app.db.session import get_db
from app.models import Address, Buyer, Cart, CartItem, Order, OrderItem, OrderStatus, OrderStatusHistory
from app.schemas.orders import CancelRequest, CheckoutRequest, CheckoutItemIn
from app.services import b2b_client
from shared.errors.http import NotFoundError
from shared.service_auth import verify_service_key
from app.core.config import settings

router = APIRouter()
DB = Annotated[AsyncSession, Depends(get_db)]


def _address_to_response(address: Address) -> dict:
    """Маппинг модели Address → AddressResponse по контракту B2C.

    Обязательные поля: country, city, street, building, id, created_at.
    Поле почтового индекса в контракте — postal_code (в модели — zip_code).
    country отсутствует в модели Address — подставляем пустую строку,
    пока поле не добавлено в схему БД.
    """
    return {
        "id": str(address.id),
        "created_at": address.created_at.isoformat(),
        "country": getattr(address, "country", ""),   # обязательное; добавить в модель
        "city": address.city,
        "street": address.street,
        "building": address.building,
        "apartment": address.apartment,
        "postal_code": address.zip_code,              # zip_code → postal_code по контракту
        "is_default": getattr(address, "is_default", False),
        **({"region": address.region} if getattr(address, "region", None) else {}),
        **({"recipient_name": address.recipient_name} if getattr(address, "recipient_name", None) else {}),
        **({"recipient_phone": address.recipient_phone} if getattr(address, "recipient_phone", None) else {}),
        **({"comment": address.comment} if getattr(address, "comment", None) else {}),
    }


def _build_order_response(order: Order, items_data: list[dict], address_response: dict) -> dict:
    """Собирает полный OrderResponse по контракту B2C.

    Обязательные поля: id, buyer_id, status, items, subtotal, total, address, created_at.
    """
    return {
        "id": str(order.id),
        "number": order.number,
        "buyer_id": str(order.buyer_id),
        "status": order.status.value,
        "items": items_data,
        "subtotal": order.subtotal,
        "delivery_cost": order.delivery_cost,
        "total": order.total,
        "address": address_response,
        "created_at": order.created_at.isoformat(),
        # опциональные поля
        **({"cancel_reason": order.cancel_reason} if order.cancel_reason else {}),
    }


def _order_items_to_response(items: list) -> list[dict]:
    """Маппинг OrderItem → OrderItem в ответе по контракту."""
    result = []
    for i in items:
        item: dict = {
            "sku_id": str(i["sku_id"]) if isinstance(i, dict) else str(i.sku_id),
            "product_id": str(i["product_id"]) if isinstance(i, dict) else str(i.product_id),
            "name": i["name"] if isinstance(i, dict) else i.name,
            "quantity": i["quantity"] if isinstance(i, dict) else i.quantity,
            "unit_price": i["unit_price"] if isinstance(i, dict) else i.unit_price,
            "line_total": i["line_total"] if isinstance(i, dict) else i.line_total,
        }
        result.append(item)
    return result


# ─── US-ORD-01: Checkout ─────────────────────────────────────────────────────

@router.post("/orders", status_code=201)
async def checkout(
    body: CheckoutRequest,
    buyer: CurrentBuyer,
    db: DB,
    idempotency_key: uuid.UUID = Header(..., alias="Idempotency-Key"),
) -> dict:
    # Идемпотентность: при повторе возвращаем полный OrderResponse со статусом 201
    existing = await db.scalar(
        select(Order).where(Order.idempotency_key == idempotency_key)
        .options(selectinload(Order.items))
    )
    if existing:
        # Восстанавливаем адрес — он хранится в address_snapshot (уже в нужной форме)
        return _build_order_response(
            order=existing,
            items_data=_order_items_to_response(existing.items),
            address_response=existing.address_snapshot,  # снимок уже сохранён в формате AddressResponse
        )

    # Получаем адрес
    address = await db.get(Address, body.address_id)
    if not address or address.buyer_id != buyer.id:
        raise HTTPException(status_code=400, detail={"code": "INVALID_ADDRESS", "message": "Address not found"})

    if body.items:
        cart_items_raw = body.items
    else:
        cart = await db.scalar(select(Cart).where(Cart.buyer_id == buyer.id))
        if not cart:
            raise HTTPException(status_code=400, detail={"code": "EMPTY_CART", "message": "Cart is empty"})
        result = await db.execute(select(CartItem).where(CartItem.cart_id == cart.id))
        db_items = result.scalars().all()
        cart_items_raw = [CheckoutItemIn(sku_id=i.sku_id, quantity=i.quantity) for i in db_items]

    items_to_reserve = []
    order_items_data = []
    subtotal = 0

    for item in cart_items_raw:
        product = await b2b_client.get_product_by_sku(str(item.sku_id))
        if not product:
            raise HTTPException(status_code=400, detail={"code": "PRODUCT_NOT_FOUND"})

        sku = next((s for s in product.get("skus", []) if s["id"] == str(item.sku_id)), None)
        if not sku:
            raise HTTPException(status_code=400, detail={"code": "SKU_NOT_FOUND"})

        price = sku.get("price", 0)
        line_total = price * item.quantity
        subtotal += line_total

        items_to_reserve.append({"sku_id": str(item.sku_id), "quantity": item.quantity})
        order_items_data.append({
            "sku_id": item.sku_id,
            "product_id": uuid.UUID(product["id"]),
            "name": product.get("title", ""),
            "sku_attributes": sku.get("attributes", []),
            "unit_price": price,
            "quantity": item.quantity,
            "line_total": line_total,
        })

    # Резервируем в B2B — all-or-nothing
    reserve_result = await b2b_client.reserve(
        order_id=str(uuid.uuid4()),
        idempotency_key=str(idempotency_key),  # передаём str, не UUID
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

    # Формируем снимок адреса в формате AddressResponse — сохраняем именно его,
    # чтобы при идемпотентном повторе вернуть корректную форму без обращения к БД.
    address_response = _address_to_response(address)

    order_number = f"ORD-{datetime.now(UTC).strftime('%Y%m%d')}-{str(uuid.uuid4())[:8].upper()}"

    order = Order(
        number=order_number,
        buyer_id=buyer.id,
        idempotency_key=idempotency_key,
        status=OrderStatus.PAID,
        address_snapshot=address_response,  # сохраняем уже в форме AddressResponse
        payment_method_id=body.payment_method_id,
        subtotal=subtotal,
        delivery_cost=0,
        total=subtotal,
    )
    db.add(order)
    await db.flush()

    for oid in order_items_data:
        db.add(OrderItem(order_id=order.id, **oid))
    await db.flush()

    db.add(OrderStatusHistory(order_id=order.id, status=OrderStatus.PAID))

    return _build_order_response(
        order=order,
        items_data=_order_items_to_response(order_items_data),
        address_response=address_response,
    )


# ─── US-ORD-02: Просмотр заказов ─────────────────────────────────────────────

@router.get("/orders")
async def list_orders(
    buyer: CurrentBuyer,
    db: DB,
    status: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict:
    query = select(Order).where(Order.buyer_id == buyer.id)
    count_q = select(func.count()).select_from(Order).where(Order.buyer_id == buyer.id)

    if status:
        try:
            query = query.where(Order.status == OrderStatus(status))
            count_q = count_q.where(Order.status == OrderStatus(status))
        except ValueError:
            raise HTTPException(status_code=400, detail={"code": "INVALID_STATUS", "message": f"Unknown status: {status}"})

    total_count = await db.scalar(count_q)
    query = query.order_by(Order.created_at.desc()).offset(offset).limit(limit)
    result = await db.execute(query)
    orders = result.scalars().all()

    return {
        "items": [
            {"id": str(o.id), "number": o.number, "status": o.status.value, "total": o.total}
            for o in orders
        ],
        "total_count": total_count,
        "limit": limit,
        "offset": offset,
    }


@router.get("/orders/{order_id}")
async def get_order(order_id: uuid.UUID, buyer: CurrentBuyer, db: DB) -> dict:
    result = await db.execute(
        select(Order).where(
            Order.id == order_id,
            Order.buyer_id == buyer.id,
        ).options(selectinload(Order.items), selectinload(Order.status_history))
    )
    order = result.scalar_one_or_none()
    if not order:
        raise NotFoundError(detail="Order not found")

    return _build_order_response(
        order=order,
        items_data=_order_items_to_response(order.items),
        address_response=order.address_snapshot,  # уже в форме AddressResponse
    )


# ─── US-ORD-03: Отмена заказа ────────────────────────────────────────────────

CANCELLABLE_STATUSES = {
    OrderStatus.CREATED,
    OrderStatus.PAID,
}

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

    result_items = await db.execute(select(OrderItem).where(OrderItem.order_id == order.id))
    items_for_unreserve = [
        {"sku_id": str(i.sku_id), "quantity": i.quantity}
        for i in result_items.scalars().all()
    ]
    unreserve_status = await b2b_client.unreserve(str(order.id), items_for_unreserve)

    if unreserve_status in (200, 204):
        order.status = OrderStatus.CANCELLED
        order.cancelled_at = datetime.now(UTC)
        order.cancel_reason = body.reason
        db.add(OrderStatusHistory(order_id=order.id, status=OrderStatus.CANCELLED, comment=body.reason))
    else:
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

    if sku_ids and event_type in ("PRODUCT_BLOCKED", "PRODUCT_DELETED", "OUT_OF_STOCK"):
        sku_uuids = [uuid.UUID(s) for s in sku_ids]
        await db.execute(
            update(CartItem)
            .where(CartItem.sku_id.in_(sku_uuids))
            .values(unavailable_reason=event_type)
        )

    return {"status": "processed"}


# ─── US-ORD-05: Fulfill при доставке ─────────────────────────────────────────

@router.post("/orders/{order_id}/deliver")
async def mark_delivered(order_id: uuid.UUID, buyer: CurrentBuyer, db: DB) -> dict:
    result = await db.execute(
        select(Order).where(Order.id == order_id, Order.buyer_id == buyer.id)
        .options(selectinload(Order.items))
    )
    order = result.scalar_one_or_none()
    if not order:
        raise NotFoundError()

    if order.status != OrderStatus.DELIVERING:
        raise HTTPException(status_code=409, detail={"code": "INVALID_STATUS"})

    order.status = OrderStatus.DELIVERED
    db.add(OrderStatusHistory(order_id=order.id, status=OrderStatus.DELIVERED))
    await db.flush()

    # Передаём items в fulfill — исправлен баг с одним аргументом
    items_for_fulfill = [
        {"sku_id": str(i.sku_id), "quantity": i.quantity}
        for i in order.items
    ]
    fulfill_status = await b2b_client.fulfill(str(order.id), items_for_fulfill)
    if fulfill_status not in (200, 204):
        import logging
        logging.error(f"Fulfill failed for order {order.id}, status {fulfill_status}. Will retry later.")

    return {"id": str(order.id), "status": order.status.value}