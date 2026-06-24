"""initial b2c tables

Revision ID: 001
Revises:
Create Date: 2026-06-24 18:30:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Buyers
    op.create_table(
        "buyers",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=sa.text("gen_random_uuid()")),
        sa.Column("email", sa.String(255), unique=True, nullable=False),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("first_name", sa.String(100), nullable=True),
        sa.Column("last_name", sa.String(100), nullable=True),
        sa.Column("phone", sa.String(20), nullable=True),
        sa.Column("is_active", sa.Boolean, default=True, nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # Refresh tokens
    op.create_table(
        "refresh_tokens",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("buyer_id", UUID(as_uuid=True), sa.ForeignKey("buyers.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("token_hash", sa.String(255), unique=True, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # Addresses
    op.create_table(
        "addresses",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("buyer_id", UUID(as_uuid=True), sa.ForeignKey("buyers.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("label", sa.String(100), nullable=False),
        sa.Column("city", sa.String(100), nullable=False),
        sa.Column("street", sa.String(255), nullable=False),
        sa.Column("building", sa.String(50), nullable=False),
        sa.Column("apartment", sa.String(50), nullable=True),
        sa.Column("zip_code", sa.String(20), nullable=False),
        sa.Column("is_default", sa.Boolean, default=False, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # Payment methods
    op.create_table(
        "payment_methods",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("buyer_id", UUID(as_uuid=True), sa.ForeignKey("buyers.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("label", sa.String(100), nullable=False),
        sa.Column("card_last4", sa.String(4), nullable=True),
        sa.Column("card_brand", sa.String(50), nullable=True),
        sa.Column("is_default", sa.Boolean, default=False, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # Carts
    op.create_table(
        "carts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("buyer_id", UUID(as_uuid=True), sa.ForeignKey("buyers.id", ondelete="CASCADE"), unique=True, nullable=True),
        sa.Column("session_id", sa.String(255), unique=True, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("(buyer_id IS NOT NULL) OR (session_id IS NOT NULL)", name="ck_carts_owner_present"),
    )

    # Cart items
    op.create_table(
        "cart_items",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("cart_id", UUID(as_uuid=True), sa.ForeignKey("carts.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("sku_id", UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", UUID(as_uuid=True), nullable=False),
        sa.Column("quantity", sa.Integer, nullable=False),
        sa.Column("unavailable_reason", sa.String(50), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("cart_id", "sku_id", name="uq_cart_items_cart_sku"),
        sa.CheckConstraint("quantity > 0", name="ck_cart_items_quantity_positive"),
    )

    # Favorites
    op.create_table(
        "favorites",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("buyer_id", UUID(as_uuid=True), sa.ForeignKey("buyers.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("product_id", UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("buyer_id", "product_id", name="uq_favorites_buyer_product"),
    )

    # Product subscriptions
    op.create_table(
        "product_subscriptions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("buyer_id", UUID(as_uuid=True), sa.ForeignKey("buyers.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("sku_id", UUID(as_uuid=True), nullable=False),
        sa.Column("type", sa.Enum("BACK_IN_STOCK", "PRICE_DROP", name="subscriptiontype"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("buyer_id", "sku_id", "type", name="uq_product_subscriptions_buyer_sku_type"),
    )

    # Orders
    op.create_table(
        "orders",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("number", sa.String(50), unique=True, nullable=False),
        sa.Column("buyer_id", UUID(as_uuid=True), sa.ForeignKey("buyers.id", ondelete="RESTRICT"), nullable=False, index=True),
        sa.Column("idempotency_key", UUID(as_uuid=True), unique=True, nullable=False),
        sa.Column("status", sa.Enum("CREATED", "PAID", "ASSEMBLING", "DELIVERING", "DELIVERED", "CANCEL_PENDING", "CANCELLED", name="orderstatus"), default="CREATED", nullable=False, index=True),
        sa.Column("address_snapshot", JSONB, nullable=False),
        sa.Column("payment_method_id", UUID(as_uuid=True), nullable=True),
        sa.Column("subtotal", sa.Integer, nullable=False),
        sa.Column("delivery_cost", sa.Integer, default=0, nullable=False),
        sa.Column("total", sa.Integer, nullable=False),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_reason", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # Order items
    op.create_table(
        "order_items",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("order_id", UUID(as_uuid=True), sa.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("sku_id", UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("sku_attributes", JSONB, nullable=False, default=dict),
        sa.Column("unit_price", sa.Integer, nullable=False),
        sa.Column("quantity", sa.Integer, nullable=False),
        sa.Column("line_total", sa.Integer, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("quantity > 0", name="ck_order_items_quantity_positive"),
        sa.CheckConstraint("unit_price >= 0", name="ck_order_items_price_non_negative"),
    )

    # Order status history
    op.create_table(
        "order_status_history",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("order_id", UUID(as_uuid=True), sa.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("status", sa.Enum("CREATED", "PAID", "ASSEMBLING", "DELIVERING", "DELIVERED", "CANCEL_PENDING", "CANCELLED", name="orderstatusenum"), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("comment", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # Notifications
    op.create_table(
        "notifications",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("buyer_id", UUID(as_uuid=True), sa.ForeignKey("buyers.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("type", sa.Enum("ORDER_STATUS_CHANGED", "BACK_IN_STOCK", "PRICE_DROP", "PROMO", "SYSTEM", name="notificationtype"), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("body", sa.Text, nullable=True),
        sa.Column("payload", JSONB, nullable=False, default=dict),
        sa.Column("is_read", sa.Boolean, default=False, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # B2B event inbox
    op.create_table(
        "b2b_event_inbox",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("idempotency_key", UUID(as_uuid=True), unique=True, nullable=False),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("raw_payload", JSONB, nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # Banners
    op.create_table(
        "banners",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("image_url", sa.Text, nullable=False),
        sa.Column("link_url", sa.Text, nullable=True),
        sa.Column("priority", sa.Integer, default=0, nullable=False),
        sa.Column("is_active", sa.Boolean, default=True, nullable=False),
        sa.Column("active_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("active_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # Banner events
    op.create_table(
        "banner_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("banner_id", UUID(as_uuid=True), sa.ForeignKey("banners.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # Collections
    op.create_table(
        "collections",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(255), unique=True, nullable=False),
        sa.Column("is_active", sa.Boolean, default=False, nullable=False),
        sa.Column("product_ids", JSONB, nullable=False, default=list),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("collections")
    op.drop_table("banner_events")
    op.drop_table("banners")
    op.drop_table("b2b_event_inbox")
    op.drop_table("notifications")
    op.drop_table("order_status_history")
    op.drop_table("order_items")
    op.drop_table("orders")
    op.drop_table("product_subscriptions")
    op.drop_table("favorites")
    op.drop_table("cart_items")
    op.drop_table("carts")
    op.drop_table("payment_methods")
    op.drop_table("addresses")
    op.drop_table("refresh_tokens")
    op.drop_table("buyers")
    op.execute("DROP TYPE IF EXISTS orderstatus")
    op.execute("DROP TYPE IF EXISTS orderstatusenum")
    op.execute("DROP TYPE IF EXISTS subscriptiontype")
    op.execute("DROP TYPE IF EXISTS notificationtype")
