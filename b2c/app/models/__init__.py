import uuid
from datetime import datetime
from enum import Enum

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


# ─────────────────────────────────────────────
# Enums
# ─────────────────────────────────────────────

class OrderStatus(str, Enum):
    CREATED = "CREATED"
    PAID = "PAID"
    ASSEMBLING = "ASSEMBLING"
    DELIVERING = "DELIVERING"
    DELIVERED = "DELIVERED"
    CANCEL_PENDING = "CANCEL_PENDING"
    CANCELLED = "CANCELLED"


class NotificationType(str, Enum):
    ORDER_STATUS_CHANGED = "ORDER_STATUS_CHANGED"
    BACK_IN_STOCK = "BACK_IN_STOCK"
    PRICE_DROP = "PRICE_DROP"
    PROMO = "PROMO"
    SYSTEM = "SYSTEM"


class SubscriptionType(str, Enum):
    BACK_IN_STOCK = "BACK_IN_STOCK"
    PRICE_DROP = "PRICE_DROP"


class OutboxStatus(str, Enum):
    PENDING = "PENDING"
    SENT = "SENT"
    FAILED = "FAILED"


# ─────────────────────────────────────────────
# Buyer
# ─────────────────────────────────────────────

class Buyer(Base):
    __tablename__ = "buyers"

    email: Mapped[str] = mapped_column(sa.String(255), unique=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    first_name: Mapped[str | None] = mapped_column(sa.String(100))
    last_name: Mapped[str | None] = mapped_column(sa.String(100))
    phone: Mapped[str | None] = mapped_column(sa.String(20))
    is_active: Mapped[bool] = mapped_column(sa.Boolean, default=True, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    refresh_tokens: Mapped[list["RefreshToken"]] = relationship(
        back_populates="buyer", cascade="all, delete-orphan"
    )
    addresses: Mapped[list["Address"]] = relationship(
        back_populates="buyer", cascade="all, delete-orphan"
    )
    payment_methods: Mapped[list["PaymentMethod"]] = relationship(
        back_populates="buyer", cascade="all, delete-orphan"
    )
    cart: Mapped["Cart | None"] = relationship(back_populates="buyer", uselist=False)
    orders: Mapped[list["Order"]] = relationship(back_populates="buyer")
    notifications: Mapped[list["Notification"]] = relationship(back_populates="buyer")
    favorites: Mapped[list["Favorite"]] = relationship(
        back_populates="buyer", cascade="all, delete-orphan"
    )
    subscriptions: Mapped[list["ProductSubscription"]] = relationship(
        back_populates="buyer", cascade="all, delete-orphan"
    )


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    buyer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("buyers.id", ondelete="CASCADE"),
        nullable=False,
    )
    token_hash: Mapped[str] = mapped_column(sa.String(255), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    buyer: Mapped["Buyer"] = relationship(back_populates="refresh_tokens")

    @property
    def is_valid(self) -> bool:
        from datetime import UTC
        return self.revoked_at is None and self.expires_at > datetime.now(UTC)


class Address(Base):
    __tablename__ = "addresses"

    buyer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("buyers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    label: Mapped[str] = mapped_column(sa.String(100), nullable=False)
    city: Mapped[str] = mapped_column(sa.String(100), nullable=False)
    street: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    building: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    apartment: Mapped[str | None] = mapped_column(sa.String(50))
    zip_code: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    is_default: Mapped[bool] = mapped_column(sa.Boolean, default=False, nullable=False)

    buyer: Mapped["Buyer"] = relationship(back_populates="addresses")


class PaymentMethod(Base):
    __tablename__ = "payment_methods"

    buyer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("buyers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    label: Mapped[str] = mapped_column(sa.String(100), nullable=False)
    card_last4: Mapped[str | None] = mapped_column(sa.String(4))
    card_brand: Mapped[str | None] = mapped_column(sa.String(50))
    is_default: Mapped[bool] = mapped_column(sa.Boolean, default=False, nullable=False)

    buyer: Mapped["Buyer"] = relationship(back_populates="payment_methods")


# ─────────────────────────────────────────────
# Cart
# ─────────────────────────────────────────────

class Cart(Base):
    __tablename__ = "carts"

    buyer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("buyers.id", ondelete="CASCADE"),
        unique=True,
    )
    session_id: Mapped[str | None] = mapped_column(sa.String(255), unique=True)

    __table_args__ = (
        sa.CheckConstraint(
            "(buyer_id IS NOT NULL) OR (session_id IS NOT NULL)",
            name="ck_carts_owner_present",
        ),
    )

    buyer: Mapped["Buyer | None"] = relationship(back_populates="cart")
    items: Mapped[list["CartItem"]] = relationship(
        back_populates="cart", cascade="all, delete-orphan"
    )


class CartItem(Base):
    __tablename__ = "cart_items"

    cart_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("carts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sku_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    quantity: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    # Вычисляется при обогащении из B2B, не хранится в БД постоянно
    unavailable_reason: Mapped[str | None] = mapped_column(sa.String(50))

    __table_args__ = (
        sa.UniqueConstraint("cart_id", "sku_id", name="uq_cart_items_cart_sku"),
        sa.CheckConstraint("quantity > 0", name="ck_cart_items_quantity_positive"),
    )

    cart: Mapped["Cart"] = relationship(back_populates="items")


# ─────────────────────────────────────────────
# Favorite / Subscription
# ─────────────────────────────────────────────

class Favorite(Base):
    __tablename__ = "favorites"

    buyer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("buyers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    __table_args__ = (
        sa.UniqueConstraint("buyer_id", "product_id", name="uq_favorites_buyer_product"),
    )

    buyer: Mapped["Buyer"] = relationship(back_populates="favorites")


class ProductSubscription(Base):
    __tablename__ = "product_subscriptions"

    buyer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("buyers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sku_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    type: Mapped[SubscriptionType] = mapped_column(
        sa.Enum(SubscriptionType, name="subscriptiontype"),
        nullable=False,
    )

    __table_args__ = (
        sa.UniqueConstraint(
            "buyer_id", "sku_id", "type",
            name="uq_product_subscriptions_buyer_sku_type"
        ),
    )

    buyer: Mapped["Buyer"] = relationship(back_populates="subscriptions")


# ─────────────────────────────────────────────
# Order
# ─────────────────────────────────────────────

class Order(Base):
    __tablename__ = "orders"

    number: Mapped[str] = mapped_column(sa.String(50), unique=True, nullable=False)
    buyer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("buyers.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    idempotency_key: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), unique=True, nullable=False
    )
    status: Mapped[OrderStatus] = mapped_column(
        sa.Enum(OrderStatus, name="orderstatus"),
        default=OrderStatus.CREATED,
        nullable=False,
        index=True,
    )
    address_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    payment_method_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    subtotal: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    delivery_cost: Mapped[int] = mapped_column(sa.Integer, default=0, nullable=False)
    total: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    cancelled_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    cancel_reason: Mapped[str | None] = mapped_column(sa.Text)

    buyer: Mapped["Buyer"] = relationship(back_populates="orders")
    items: Mapped[list["OrderItem"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )
    status_history: Mapped[list["OrderStatusHistory"]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
        order_by="OrderStatusHistory.changed_at",
    )


class OrderItem(Base):
    __tablename__ = "order_items"

    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sku_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(sa.String(500), nullable=False)
    sku_attributes: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    unit_price: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    quantity: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    line_total: Mapped[int] = mapped_column(sa.Integer, nullable=False)

    __table_args__ = (
        sa.CheckConstraint("quantity > 0", name="ck_order_items_quantity_positive"),
        sa.CheckConstraint("unit_price >= 0", name="ck_order_items_price_non_negative"),
    )

    order: Mapped["Order"] = relationship(back_populates="items")


class OrderStatusHistory(Base):
    __tablename__ = "order_status_history"

    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[OrderStatus] = mapped_column(
        sa.Enum(OrderStatus, name="orderstatusenum"),
        nullable=False,
    )
    changed_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        server_default=sa.func.now(),
        nullable=False,
    )
    comment: Mapped[str | None] = mapped_column(sa.Text)

    order: Mapped["Order"] = relationship(back_populates="status_history")


# ─────────────────────────────────────────────
# Notification / B2BEventInbox / Banner / Collection
# ─────────────────────────────────────────────

class Notification(Base):
    __tablename__ = "notifications"

    buyer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("buyers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    type: Mapped[NotificationType] = mapped_column(
        sa.Enum(NotificationType, name="notificationtype"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    body: Mapped[str | None] = mapped_column(sa.Text)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    is_read: Mapped[bool] = mapped_column(sa.Boolean, default=False, nullable=False)

    buyer: Mapped["Buyer"] = relationship(back_populates="notifications")


class B2BEventInbox(Base):
    """Входящие события от B2B (изменения товаров, цен, остатков)."""
    __tablename__ = "b2b_event_inbox"

    idempotency_key: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), unique=True, nullable=False
    )
    event_type: Mapped[str] = mapped_column(sa.String(100), nullable=False)
    raw_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


class Banner(Base):
    """Баннеры на главной странице (US-CART-04)."""
    __tablename__ = "banners"

    title: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    image_url: Mapped[str] = mapped_column(sa.Text, nullable=False)
    link_url: Mapped[str | None] = mapped_column(sa.Text)
    priority: Mapped[int] = mapped_column(sa.Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, default=True, nullable=False)
    active_from: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    active_to: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


class BannerEvent(Base):
    """CTR-события по баннерам (US-CART-04)."""
    __tablename__ = "banner_events"

    banner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("banners.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(sa.String(50), nullable=False)  # "click", "view"


class Collection(Base):
    """Тематические подборки на главной (US-CART-05)."""
    __tablename__ = "collections"

    title: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    slug: Mapped[str] = mapped_column(sa.String(255), unique=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, default=True, nullable=False)
    product_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
