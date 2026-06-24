import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
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


@router.post("/cart/items", status_code=201)
async def add_to_cart(
    body: CartItemIn,
    buyer: OptionalBuyer,
    db: DB,
    x_session_id: str | None = Header(default=None),
) -> dict:
    cart = await _get_or_create_cart(db, buyer, x_session_id)

    # Если SKU уже в корзине — увеличиваем quantity
    existing = await db.scalar(
        select(CartItem).where(
            CartItem.cart_id == cart.id,
            CartItem.sku_id == body.sku_id,
        )
    )
    if existing:
        existing.quantity += body.quantity
        return {"id": str(existing.id), "quantity": existing.quantity}

    item = CartItem(
        cart_id=cart.id,
        sku_id=body.sku_id,
        product_id=body.product_id,
        quantity=body.quantity,
    )
    db.add(item)
    await db.flush()
    return {"id": str(item.id), "sku_id": str(body.sku_id), "quantity": item.quantity}


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
        return CartOut(items=[], total_amount=0)

    if not cart:
        return CartOut(items=[], total_amount=0)

    result = await db.execute(select(CartItem).where(CartItem.cart_id == cart.id))
    items_db = result.scalars().all()

    enriched: list[CartItemOut] = []
    total_amount = 0

    for item in items_db:
        product = await b2b_client.get_product(str(item.product_id))

        if not product or product.get("status") != "MODERATED" or product.get("deleted"):
            enriched.append(CartItemOut(
                id=item.id,
                sku_id=item.sku_id,
                product_id=item.product_id,
                quantity=item.quantity,
                available=False,
                unavailable_reason="PRODUCT_BLOCKED",
            ))
            continue

        sku = next((s for s in product.get("skus", []) if s["id"] == str(item.sku_id)), None)
        if not sku:
            enriched.append(CartItemOut(
                id=item.id,
                sku_id=item.sku_id,
                product_id=item.product_id,
                quantity=item.quantity,
                available=False,
                unavailable_reason="SKU_NOT_FOUND",
            ))
            continue

        price = sku.get("price", 0)
        qty_available = sku.get("quantity", 0) - sku.get("reserved_quantity", 0)
        title = product.get("title") or product.get("name", "")

        if qty_available <= 0:
            enriched.append(CartItemOut(
                id=item.id,
                sku_id=item.sku_id,
                product_id=item.product_id,
                quantity=item.quantity,
                available=False,
                unavailable_reason="OUT_OF_STOCK",
                title=title,
                price=price,
            ))
        else:
            total_amount += price * item.quantity
            enriched.append(CartItemOut(
                id=item.id,
                sku_id=item.sku_id,
                product_id=item.product_id,
                quantity=item.quantity,
                available=True,
                title=title,
                price=price,
            ))

    return CartOut(items=enriched, total_amount=total_amount)

@router.delete("/cart/items/{sku_id}", status_code=204)
async def remove_cart_item(
    sku_id: uuid.UUID,
    buyer: OptionalBuyer,
    db: DB,
    x_session_id: str | None = Header(default=None),
) -> None:
    # Сначала найдём корзину, потом удалим по sku_id
    if buyer:
        cart = await db.scalar(select(Cart).where(Cart.buyer_id == buyer.id))
    elif x_session_id:
        cart = await db.scalar(select(Cart).where(Cart.session_id == x_session_id))
    else:
        return
    if cart:
        await db.execute(
            delete(CartItem).where(
                CartItem.cart_id == cart.id,
                CartItem.sku_id == sku_id,
            )
        )

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


@router.post("/cart/merge")
async def merge_cart(
    buyer: CurrentBuyer,
    db: DB,
    x_session_id: str | None = Header(default=None),
) -> dict:
    """Объединяет гостевую корзину с авторизованной при логине."""
    if not x_session_id:
        return {"merged": 0}

    guest_cart = await db.scalar(select(Cart).where(Cart.session_id == x_session_id))
    if not guest_cart:
        return {"merged": 0}

    auth_cart = await db.scalar(select(Cart).where(Cart.buyer_id == buyer.id))
    if not auth_cart:
        # Просто привязываем гостевую корзину к покупателю
        guest_cart.buyer_id = buyer.id
        guest_cart.session_id = None
        return {"merged": 0}

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

    # Удаляем гостевую корзину
    await db.execute(delete(CartItem).where(CartItem.cart_id == guest_cart.id))
    await db.delete(guest_cart)

    return {"merged": len(guest_items)}


@router.patch("/cart/items/{sku_id}", status_code=200)
async def update_cart_item(
    sku_id: uuid.UUID,
    body: CartItemUpdate,
    buyer: OptionalBuyer,
    db: DB,
    x_session_id: str | None = Header(default=None),
) -> dict:
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

    if body.quantity <= 0:
        await db.execute(delete(CartItem).where(CartItem.id == item.id))
        return {"sku_id": str(sku_id), "quantity": 0}

    item.quantity = body.quantity
    return {"sku_id": str(sku_id), "quantity": item.quantity}