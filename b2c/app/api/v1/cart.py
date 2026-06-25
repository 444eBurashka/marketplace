import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import CurrentBuyer, OptionalBuyer
from app.db.session import get_db
from app.models import Buyer, Cart, CartItem
from app.schemas.cart import CartItemIn, CartItemOut, CartOut, CartItemUpdate
from app.services import b2b_client

router = APIRouter()
DB = Annotated[AsyncSession, Depends(get_db)]


async def _get_or_create_cart(
    db: AsyncSession,
    buyer: Buyer | None,
    session_id: str | None,
) -> Cart:
    if buyer:
        cart = await db.scalar(select(Cart).where(Cart.buyer_id == buyer.id))
        if not cart:
            cart = Cart(buyer_id=buyer.id)
            db.add(cart)
            await db.flush()
    elif session_id:
        cart = await db.scalar(select(Cart).where(Cart.session_id == session_id))
        if not cart:
            cart = Cart(session_id=session_id)
            db.add(cart)
            await db.flush()
    else:
        raise HTTPException(status_code=400, detail="Provide Authorization header or X-Session-Id")
    return cart


async def _enrich_cart(cart: Cart, db: AsyncSession) -> CartOut:
    """Build a CartOut with B2B-enriched items."""
    result = await db.execute(select(CartItem).where(CartItem.cart_id == cart.id))
    items_db = result.scalars().all()

    enriched: list[CartItemOut] = []
    subtotal = 0
    items_count = 0

    for item in items_db:
        items_count += item.quantity
        product = await b2b_client.get_product(str(item.product_id))

        if not product or product.get("status") != "MODERATED" or product.get("deleted"):
            # Карточки продукта может не быть вовсе (true 404) — тогда нет
            # никаких данных о названии, иначе берём то, что есть в product.
            fallback_name = (
                (product.get("title") or product.get("name")) if product else None
            ) or "Товар недоступен"
            enriched.append(CartItemOut(
                id=item.id,
                sku_id=item.sku_id,
                product_id=item.product_id,
                quantity=item.quantity,
                name=fallback_name,
                available=False,
                unavailable_reason="PRODUCT_BLOCKED",
            ))
            continue

        product_name = product.get("title") or product.get("name", "")
        sku = next((s for s in product.get("skus", []) if s["id"] == str(item.sku_id)), None)
        if not sku:
            enriched.append(CartItemOut(
                id=item.id,
                sku_id=item.sku_id,
                product_id=item.product_id,
                quantity=item.quantity,
                name=product_name or "Товар недоступен",
                available=False,
                unavailable_reason="SKU_NOT_FOUND",
            ))
            continue

        price = sku.get("price", 0)
        # Витринный SKU не содержит reserved_quantity — доступный остаток
        # для покупателя это active_quantity (см. SKUPublicResponse).
        qty_available = sku.get("active_quantity", 0)
        name = sku.get("name") or product_name

        if qty_available <= 0:
            enriched.append(CartItemOut(
                id=item.id,
                sku_id=item.sku_id,
                product_id=item.product_id,
                quantity=item.quantity,
                name=name,
                unit_price=price,
                line_total=0,
                available_quantity=0,
                is_available=False,
                available=False,
                unavailable_reason="OUT_OF_STOCK",
            ))
        else:
            line_total = price * item.quantity
            subtotal += line_total
            enriched.append(CartItemOut(
                id=item.id,
                sku_id=item.sku_id,
                product_id=item.product_id,
                quantity=item.quantity,
                name=name,
                unit_price=price,
                line_total=line_total,
                available_quantity=qty_available,
                is_available=True,
                available=True,
            ))

    return CartOut(
        items=enriched,
        items_count=items_count,
        subtotal=subtotal,
        is_valid=all(i.available for i in enriched),
    )


@router.post("/cart/items", status_code=200, response_model=CartOut)
async def add_to_cart(
    body: CartItemIn,
    buyer: OptionalBuyer,
    db: DB,
    x_session_id: str | None = Header(default=None),
) -> CartOut:
    cart = await _get_or_create_cart(db, buyer, x_session_id)

    existing = await db.scalar(
        select(CartItem).where(
            CartItem.cart_id == cart.id,
            CartItem.sku_id == body.sku_id,
        )
    )
    if existing:
        existing.quantity += body.quantity
    else:
        # Контракт не передаёт product_id — резолвим его сами через B2B.
        sku = await b2b_client.get_sku_public(str(body.sku_id))
        if not sku:
            raise HTTPException(
                status_code=404,
                detail={"code": "NOT_FOUND", "message": "SKU not found"},
            )
        item = CartItem(
            cart_id=cart.id,
            sku_id=body.sku_id,
            product_id=sku["product_id"],
            quantity=body.quantity,
        )
        db.add(item)

    await db.flush()
    return await _enrich_cart(cart, db)


@router.get("/cart", response_model=CartOut)
async def get_cart(
    buyer: OptionalBuyer,
    db: DB,
    x_session_id: str | None = Header(default=None),
) -> CartOut:
    if buyer:
        cart = await db.scalar(select(Cart).where(Cart.buyer_id == buyer.id))
    elif x_session_id:
        cart = await db.scalar(select(Cart).where(Cart.session_id == x_session_id))
    else:
        return CartOut(items=[], items_count=0, subtotal=0, is_valid=True)

    if not cart:
        return CartOut(items=[], items_count=0, subtotal=0, is_valid=True)

    return await _enrich_cart(cart, db)


@router.delete("/cart/items/{sku_id}", status_code=200, response_model=CartOut)
async def remove_cart_item(
    sku_id: uuid.UUID,
    buyer: OptionalBuyer,
    db: DB,
    x_session_id: str | None = Header(default=None),
) -> CartOut:
    cart: Cart | None = None
    if buyer:
        cart = await db.scalar(select(Cart).where(Cart.buyer_id == buyer.id))
    elif x_session_id:
        cart = await db.scalar(select(Cart).where(Cart.session_id == x_session_id))

    if cart:
        await db.execute(
            delete(CartItem).where(
                CartItem.cart_id == cart.id,
                CartItem.sku_id == sku_id,
            )
        )
        await db.flush()
        return await _enrich_cart(cart, db)

    return CartOut(items=[], items_count=0, subtotal=0, is_valid=True)


@router.delete("/cart", status_code=204)
async def clear_cart(
    buyer: OptionalBuyer,
    db: DB,
    x_session_id: str | None = Header(default=None),
) -> None:
    if buyer:
        cart = await db.scalar(select(Cart).where(Cart.buyer_id == buyer.id))
    elif x_session_id:
        cart = await db.scalar(select(Cart).where(Cart.session_id == x_session_id))
    else:
        return

    if cart:
        await db.execute(delete(CartItem).where(CartItem.cart_id == cart.id))


@router.post("/cart/merge", response_model=CartOut)
async def merge_cart(
    buyer: CurrentBuyer,
    db: DB,
    x_session_id: str | None = Header(default=None),
) -> CartOut:
    """Объединяет гостевую корзину с авторизованной при логине."""
    if not x_session_id:
        return await _get_or_create_cart_response(db, buyer)

    guest_cart = await db.scalar(select(Cart).where(Cart.session_id == x_session_id))
    if not guest_cart:
        return await _get_or_create_cart_response(db, buyer)

    auth_cart = await db.scalar(select(Cart).where(Cart.buyer_id == buyer.id))
    if not auth_cart:
        guest_cart.buyer_id = buyer.id
        guest_cart.session_id = None
        await db.flush()
        return await _enrich_cart(guest_cart, db)

    # Мёрж: берём MAX(guest, auth) для каждого SKU
    guest_items_res = await db.execute(select(CartItem).where(CartItem.cart_id == guest_cart.id))
    guest_items = guest_items_res.scalars().all()

    for g_item in guest_items:
        auth_item = await db.scalar(
            select(CartItem).where(
                CartItem.cart_id == auth_cart.id,
                CartItem.sku_id == g_item.sku_id,
            )
        )
        if auth_item:
            auth_item.quantity = max(g_item.quantity, auth_item.quantity)
        else:
            new_item = CartItem(
                cart_id=auth_cart.id,
                sku_id=g_item.sku_id,
                product_id=g_item.product_id,
                quantity=g_item.quantity,
            )
            db.add(new_item)

    await db.execute(delete(CartItem).where(CartItem.cart_id == guest_cart.id))
    await db.delete(guest_cart)
    await db.flush()

    return await _enrich_cart(auth_cart, db)


async def _get_or_create_cart_response(db: AsyncSession, buyer: Buyer) -> CartOut:
    auth_cart = await db.scalar(select(Cart).where(Cart.buyer_id == buyer.id))
    if not auth_cart:
        return CartOut(items=[], items_count=0, subtotal=0, is_valid=True)
    return await _enrich_cart(auth_cart, db)


@router.patch("/cart/items/{sku_id}", status_code=200, response_model=CartOut)
async def update_cart_item(
    sku_id: uuid.UUID,
    body: CartItemUpdate,
    buyer: OptionalBuyer,
    db: DB,
    x_session_id: str | None = Header(default=None),
) -> CartOut:
    if buyer:
        cart = await db.scalar(select(Cart).where(Cart.buyer_id == buyer.id))
    elif x_session_id:
        cart = await db.scalar(select(Cart).where(Cart.session_id == x_session_id))
    else:
        raise HTTPException(status_code=400, detail="No cart found")

    if not cart:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Cart not found"})

    item = await db.scalar(
        select(CartItem).where(
            CartItem.cart_id == cart.id,
            CartItem.sku_id == sku_id,
        )
    )
    if not item:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Item not in cart"})

    # Контракт: quantity >= 1; удаление делается через DELETE, а не quantity<=0.
    item.quantity = body.quantity
    await db.flush()
    return await _enrich_cart(cart, db)
