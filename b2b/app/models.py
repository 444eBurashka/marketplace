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

class ProductStatus(str, Enum):
    CREATED = "CREATED"
    ON_MODERATION = "ON_MODERATION"
    MODERATED = "MODERATED"
    BLOCKED = "BLOCKED"
    HARD_BLOCKED = "HARD_BLOCKED"


class InvoiceStatus(str, Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class ReservationStatus(str, Enum):
    RESERVED = "RESERVED"
    UNRESERVED = "UNRESERVED"
    FULFILLED = "FULFILLED"


class OutboxStatus(str, Enum):
    PENDING = "PENDING"
    SENT = "SENT"
    FAILED = "FAILED"


class OutboxDestination(str, Enum):
    MODERATION = "moderation"
    B2C = "b2c"


class ImageEntityType(str, Enum):
    PRODUCT = "product"
    SKU = "sku"


# ─────────────────────────────────────────────
# Seller
# ─────────────────────────────────────────────

class Seller(Base):
    __tablename__ = "sellers"

    email: Mapped[str] = mapped_column(sa.String(255), unique=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    company_name: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    inn: Mapped[str] = mapped_column(sa.String(12), nullable=False)
    phone: Mapped[str | None] = mapped_column(sa.String(20))
    description: Mapped[str | None] = mapped_column(sa.Text)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, default=True, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    # relationships
    refresh_tokens: Mapped[list["RefreshToken"]] = relationship(
        back_populates="seller", cascade="all, delete-orphan"
    )
    products: Mapped[list["Product"]] = relationship(back_populates="seller")
    invoices: Mapped[list["Invoice"]] = relationship(back_populates="seller")


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    seller_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("sellers.id", ondelete="CASCADE"),
        nullable=False,
    )
    token_hash: Mapped[str] = mapped_column(sa.String(255), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    # relationships
    seller: Mapped["Seller"] = relationship(back_populates="refresh_tokens")

    @property
    def is_valid(self) -> bool:
        from datetime import UTC
        return self.revoked_at is None and self.expires_at > datetime.now(UTC)


# ─────────────────────────────────────────────
# Category
# ─────────────────────────────────────────────

class Category(Base):
    __tablename__ = "categories"

    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("categories.id", ondelete="SET NULL"),
    )
    name: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    slug: Mapped[str] = mapped_column(sa.String(255), unique=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, default=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(sa.Integer, default=0, nullable=False)

    # relationships
    parent: Mapped["Category | None"] = relationship(
        back_populates="children", remote_side="Category.id"
    )
    children: Mapped[list["Category"]] = relationship(back_populates="parent")
    products: Mapped[list["Product"]] = relationship(back_populates="category")


# ─────────────────────────────────────────────
# Product / SKU / Image
# ─────────────────────────────────────────────

class Product(Base):
    __tablename__ = "products"

    seller_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("sellers.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("categories.id", ondelete="SET NULL"),
    )
    title: Mapped[str] = mapped_column(sa.String(500), nullable=False)
    slug: Mapped[str] = mapped_column(sa.String(500), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(sa.Text, nullable=False)
    status: Mapped[ProductStatus] = mapped_column(
        sa.Enum(ProductStatus, name="productstatus"),
        default=ProductStatus.CREATED,
        nullable=False,
        index=True,
    )
    deleted: Mapped[bool] = mapped_column(sa.Boolean, default=False, nullable=False)
    blocked: Mapped[bool] = mapped_column(sa.Boolean, default=False, nullable=False)
    blocking_reason_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("blocking_reasons.id", ondelete="SET NULL"),
    )

    # relationships
    seller: Mapped["Seller"] = relationship(back_populates="products")
    category: Mapped["Category | None"] = relationship(back_populates="products")
    skus: Mapped[list["SKU"]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )
    characteristics: Mapped[list["ProductCharacteristic"]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )
    images: Mapped[list["Image"]] = relationship(
        primaryjoin="and_(Image.entity_type == 'product', "
                    "foreign(Image.entity_id) == Product.id)",
        viewonly=True,
    )
    blocking_reason: Mapped["BlockingReason | None"] = relationship()


class ProductCharacteristic(Base):
    __tablename__ = "product_characteristics"

    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    value: Mapped[str] = mapped_column(sa.String(500), nullable=False)
    sort_order: Mapped[int] = mapped_column(sa.Integer, default=0, nullable=False)

    # relationships
    product: Mapped["Product"] = relationship(back_populates="characteristics")


class SKU(Base):
    __tablename__ = "skus"

    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    code: Mapped[str] = mapped_column(sa.String(100), nullable=False)
    # Цена в копейках: 10000 = 100.00 руб.
    price: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    cost_price: Mapped[int] = mapped_column(sa.Integer, nullable=False)  # скрыт от B2C
    quantity: Mapped[int] = mapped_column(sa.Integer, default=0, nullable=False)
    reserved_quantity: Mapped[int] = mapped_column(sa.Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, default=True, nullable=False)

    __table_args__ = (
        sa.CheckConstraint("price >= 0", name="ck_skus_price_non_negative"),
        sa.CheckConstraint("cost_price >= 0", name="ck_skus_cost_price_non_negative"),
        sa.CheckConstraint("quantity >= 0", name="ck_skus_quantity_non_negative"),
        sa.CheckConstraint(
            "reserved_quantity >= 0", name="ck_skus_reserved_quantity_non_negative"
        ),
    )

    # relationships
    product: Mapped["Product"] = relationship(back_populates="skus")
    attributes: Mapped[list["SKUAttribute"]] = relationship(
        back_populates="sku", cascade="all, delete-orphan"
    )
    images: Mapped[list["Image"]] = relationship(
        primaryjoin="and_(Image.entity_type == 'sku', "
                    "foreign(Image.entity_id) == SKU.id)",
        viewonly=True,
    )
    reservation_items: Mapped[list["ReservationItem"]] = relationship(
        back_populates="sku"
    )

    @property
    def available_quantity(self) -> int:
        """Доступное к резервированию количество."""
        return self.quantity - self.reserved_quantity


class SKUAttribute(Base):
    __tablename__ = "sku_attributes"

    sku_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("skus.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(sa.String(100), nullable=False)
    value: Mapped[str] = mapped_column(sa.String(255), nullable=False)

    # relationships
    sku: Mapped["SKU"] = relationship(back_populates="attributes")


class Image(Base):
    __tablename__ = "images"

    entity_type: Mapped[ImageEntityType] = mapped_column(
        sa.Enum(ImageEntityType, name="imageentitytype"),
        nullable=False,
    )
    # NULL — изображение загружено, но ещё не прикреплено к сущности
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    url: Mapped[str] = mapped_column(sa.Text, nullable=False)
    ordering: Mapped[int] = mapped_column(sa.Integer, default=0, nullable=False)


# ─────────────────────────────────────────────
# Invoice (накладная на пополнение остатков)
# ─────────────────────────────────────────────

class Invoice(Base):
    __tablename__ = "invoices"

    seller_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("sellers.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    status: Mapped[InvoiceStatus] = mapped_column(
        sa.Enum(InvoiceStatus, name="invoicestatus"),
        default=InvoiceStatus.PENDING,
        nullable=False,
    )
    processed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    # relationships
    seller: Mapped["Seller"] = relationship(back_populates="invoices")
    items: Mapped[list["InvoiceItem"]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan"
    )


class InvoiceItem(Base):
    __tablename__ = "invoice_items"

    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("invoices.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sku_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("skus.id", ondelete="RESTRICT"),
        nullable=False,
    )
    quantity: Mapped[int] = mapped_column(sa.Integer, nullable=False)

    __table_args__ = (
        sa.CheckConstraint("quantity > 0", name="ck_invoice_items_quantity_positive"),
    )

    # relationships
    invoice: Mapped["Invoice"] = relationship(back_populates="items")
    sku: Mapped["SKU"] = relationship()


# ─────────────────────────────────────────────
# Reservation (резерв от B2C)
# ─────────────────────────────────────────────

class Reservation(Base):
    __tablename__ = "reservations"

    idempotency_key: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), unique=True, nullable=False
    )
    order_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    status: Mapped[ReservationStatus] = mapped_column(
        sa.Enum(ReservationStatus, name="reservationstatus"),
        default=ReservationStatus.RESERVED,
        nullable=False,
    )

    # relationships
    items: Mapped[list["ReservationItem"]] = relationship(
        back_populates="reservation", cascade="all, delete-orphan"
    )


class ReservationItem(Base):
    __tablename__ = "reservation_items"

    reservation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("reservations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sku_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("skus.id", ondelete="RESTRICT"),
        nullable=False,
    )
    quantity: Mapped[int] = mapped_column(sa.Integer, nullable=False)

    __table_args__ = (
        sa.CheckConstraint("quantity > 0", name="ck_reservation_items_quantity_positive"),
    )

    # relationships
    reservation: Mapped["Reservation"] = relationship(back_populates="items")
    sku: Mapped["SKU"] = relationship(back_populates="reservation_items")


# ─────────────────────────────────────────────
# Outbox / Inbox
# ─────────────────────────────────────────────

class OutboxEvent(Base):
    __tablename__ = "outbox_events"

    idempotency_key: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), unique=True, default=uuid.uuid4, nullable=False
    )
    destination: Mapped[OutboxDestination] = mapped_column(
        sa.Enum(OutboxDestination, name="outboxdestination"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(sa.String(100), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[OutboxStatus] = mapped_column(
        sa.Enum(OutboxStatus, name="outboxstatus"),
        default=OutboxStatus.PENDING,
        nullable=False,
        index=True,
    )
    attempts: Mapped[int] = mapped_column(sa.Integer, default=0, nullable=False)
    next_retry_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    __table_args__ = (
        sa.Index("ix_outbox_events_status_retry", "status", "next_retry_at"),
    )


class ModerationEventInbox(Base):
    """Входящие события от Moderation (решения по товарам)."""
    __tablename__ = "moderation_event_inbox"

    idempotency_key: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), unique=True, nullable=False
    )
    event_type: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    raw_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


# ─────────────────────────────────────────────
# BlockingReason (справочник, используется в Product)
# ─────────────────────────────────────────────

class BlockingReason(Base):
    __tablename__ = "blocking_reasons"

    title: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    comment: Mapped[str | None] = mapped_column(sa.Text)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, default=True, nullable=False)
