import uuid
from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.models import Reservation, ReservationItem, ReservationStatus, SKU
from app.schemas.inventory import ReserveRequest, UnreserveRequest


# ─── Исходящие события ───────────────────────────────────────────────────────

async def _send_b2c_sku_out_of_stock(sku_id: uuid.UUID, product_id: uuid.UUID) -> None:
    """Fire-and-forget: SKU_OUT_OF_STOCK в B2C когда active_quantity стал 0."""
    payload = {
        "idempotency_key": str(uuid.uuid4()),
        "event_type": "SKU_OUT_OF_STOCK",
        "occurred_at": datetime.now(UTC).isoformat(),
        "payload": {
            "product_id": str(product_id),
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


# ─── Reserve ─────────────────────────────────────────────────────────────────

class InsufficientStockError(Exception):
    """Недостаточно остатков для резервирования."""
    def __init__(self, failed_items: list[dict]):
        self.failed_items = failed_items
        super().__init__("Insufficient stock")


async def reserve(body: ReserveRequest, db: AsyncSession) -> Reservation:
    """
    All-or-nothing резервирование SKU.
    Идемпотентность по idempotency_key — повторный запрос возвращает
    существующую резервацию без изменений.
    SELECT FOR UPDATE по всем задействованным SKU.
    """
    # ── Идемпотентность ──────────────────────────────────────────────────────
    existing = await db.scalar(
        select(Reservation)
        .where(Reservation.idempotency_key == body.idempotency_key)
        .options(selectinload(Reservation.items))
    )
    if existing is not None:
        return existing

    # ── Загружаем SKU с блокировкой FOR UPDATE (all-or-nothing) ──────────────
    sku_ids = [item.sku_id for item in body.items]
    result = await db.execute(
        select(SKU)
        .where(SKU.id.in_(sku_ids), SKU.is_active == True)  # noqa: E712
        .with_for_update()
    )
    skus_by_id: dict[uuid.UUID, SKU] = {sku.id: sku for sku in result.scalars().all()}

    # ── Проверяем наличие и достаточность остатков ───────────────────────────
    failed_items = []
    quantity_map: dict[uuid.UUID, int] = {item.sku_id: item.quantity for item in body.items}

    for item in body.items:
        sku = skus_by_id.get(item.sku_id)
        if sku is None:
            raise LookupError(f"SKU {item.sku_id} not found")

        available = sku.active_quantity  # quantity - reserved_quantity
        if available < item.quantity:
            failed_items.append({
                "sku_id": str(item.sku_id),
                "requested": item.quantity,
                "available": available,
                "reason": "OUT_OF_STOCK" if available == 0 else "INSUFFICIENT_STOCK",
            })

    if failed_items:
        raise InsufficientStockError(failed_items)

    # ── Всё ок — обновляем остатки и создаём резервацию ─────────────────────
    out_of_stock_skus: list[SKU] = []

    for item in body.items:
        sku = skus_by_id[item.sku_id]
        sku.reserved_quantity += item.quantity
        # active_quantity — computed property (quantity - reserved_quantity)
        # после увеличения reserved_quantity active_quantity уменьшился
        if sku.active_quantity == 0:
            out_of_stock_skus.append(sku)

    reservation = Reservation(
        idempotency_key=body.idempotency_key,
        order_id=body.order_id,
        status=ReservationStatus.RESERVED,
    )
    db.add(reservation)
    await db.flush()  # получаем reservation.id

    for item in body.items:
        db.add(ReservationItem(
            reservation_id=reservation.id,
            sku_id=item.sku_id,
            quantity=item.quantity,
        ))

    await db.flush()

    # ── Fire-and-forget: SKU_OUT_OF_STOCK в B2C ──────────────────────────────
    for sku in out_of_stock_skus:
        await _send_b2c_sku_out_of_stock(sku.id, sku.product_id)

    return reservation


# ─── Unreserve ───────────────────────────────────────────────────────────────

async def unreserve(body: UnreserveRequest, db: AsyncSession) -> Reservation:
    """
    Компенсирующая операция при отмене заказа.
    Идемпотентность по order_id — повторный вызов возвращает существующую
    запись без двойного восстановления остатков.
    """
    # ── Идемпотентность по order_id ──────────────────────────────────────────
    existing = await db.scalar(
        select(Reservation)
        .where(
            Reservation.order_id == body.order_id,
            Reservation.status == ReservationStatus.UNRESERVED,
        )
        .options(selectinload(Reservation.items))
    )
    if existing is not None:
        return existing

    # ── Загружаем SKU с блокировкой FOR UPDATE ───────────────────────────────
    sku_ids = [item.sku_id for item in body.items]
    result = await db.execute(
        select(SKU)
        .where(SKU.id.in_(sku_ids))
        .with_for_update()
    )
    skus_by_id: dict[uuid.UUID, SKU] = {sku.id: sku for sku in result.scalars().all()}

    for item in body.items:
        sku = skus_by_id.get(item.sku_id)
        if sku is None:
            raise LookupError(f"SKU {item.sku_id} not found")
        # Восстанавливаем: снимаем резерв, остаток возвращается в active
        sku.reserved_quantity = max(0, sku.reserved_quantity - item.quantity)

    # Помечаем резервацию как снятую
    reservation = await db.scalar(
        select(Reservation)
        .where(
            Reservation.order_id == body.order_id,
            Reservation.status == ReservationStatus.RESERVED,
        )
        .options(selectinload(Reservation.items))
    )
    if reservation is not None:
        reservation.status = ReservationStatus.UNRESERVED
    else:
        # Резервация не найдена — создаём запись об отмене для идемпотентности
        reservation = Reservation(
            idempotency_key=uuid.uuid4(),
            order_id=body.order_id,
            status=ReservationStatus.UNRESERVED,
        )
        db.add(reservation)

    await db.flush()
    return reservation


# ─── Fulfill ─────────────────────────────────────────────────────────────────

async def fulfill(body: "FulfillRequest", db: AsyncSession) -> "Reservation":
    """
    Финальное списание резерва при доставке.
    active_quantity НЕ меняется — товар уже у покупателя.
    Идемпотентность по order_id: повторный вызов → 200 без изменений.
    SELECT FOR UPDATE по задействованным SKU.
    """
    from app.schemas.inventory import FulfillRequest
    from app.models import ReservationStatus

    # ── Идемпотентность: уже выполнено для этого order_id ───────────────────
    existing = await db.scalar(
        select(Reservation)
        .where(
            Reservation.order_id == body.order_id,
            Reservation.status == ReservationStatus.FULFILLED,
        )
    )
    if existing is not None:
        return existing

    # ── SELECT FOR UPDATE ────────────────────────────────────────────────────
    sku_ids = [item.sku_id for item in body.items]
    result = await db.execute(
        select(SKU)
        .where(SKU.id.in_(sku_ids))
        .with_for_update()
    )
    skus_by_id: dict[uuid.UUID, SKU] = {sku.id: sku for sku in result.scalars().all()}

    for item in body.items:
        sku = skus_by_id.get(item.sku_id)
        if sku is None:
            raise LookupError(f"SKU {item.sku_id} not found")
        # Списываем только из резерва; active_quantity не трогаем
        sku.reserved_quantity = max(0, sku.reserved_quantity - item.quantity)

    # ── Помечаем резервацию как FULFILLED ────────────────────────────────────
    reservation = await db.scalar(
        select(Reservation)
        .where(
            Reservation.order_id == body.order_id,
            Reservation.status == ReservationStatus.RESERVED,
        )
    )
    if reservation is not None:
        reservation.status = ReservationStatus.FULFILLED
    else:
        # Резервация не найдена (возможно уже UNRESERVED) — создаём запись
        # для идемпотентности будущих вызовов
        reservation = Reservation(
            idempotency_key=uuid.uuid4(),
            order_id=body.order_id,
            status=ReservationStatus.FULFILLED,
        )
        db.add(reservation)

    await db.flush()
    return reservation