"""Init Moderation: create all enums and 8 tables.

Revision ID: 001
Revises:
Create Date: 2026-06-15 12:00:00.000000
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
    # --- Enums ---
    op.execute("CREATE TYPE moderatorrole AS ENUM ('MODERATOR', 'ADMIN')")
    op.execute("CREATE TYPE ticketkind AS ENUM ('CREATE', 'EDIT')")
    op.execute("CREATE TYPE ticketstatus AS ENUM ('PENDING', 'IN_REVIEW', 'APPROVED', 'BLOCKED', 'HARD_BLOCKED')")
    op.execute("CREATE TYPE tickethistoryaction AS ENUM ('CREATED', 'CLAIMED', 'RELEASED', 'APPROVED', 'BLOCKED', 'HARD_BLOCKED', 'AUTO_RETURNED')")
    op.execute("CREATE TYPE fieldreportseverity AS ENUM ('ERROR', 'WARNING', 'INFO')")
    op.execute("CREATE TYPE b2beventtype AS ENUM ('PRODUCT_CREATED', 'PRODUCT_EDITED', 'PRODUCT_DELETED')")


    # --- Moderators ---
    op.create_table(
        "moderators",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("first_name", sa.String(100), nullable=False),
        sa.Column("last_name", sa.String(100), nullable=True),
        sa.Column("role", sa.Enum("MODERATOR", "ADMIN", name="moderatorrole"), nullable=False, server_default="MODERATOR"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_unique_constraint("uq_moderators_email", "moderators", ["email"])

    # --- Refresh Tokens ---
    op.create_table(
        "refresh_tokens",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("moderator_id", UUID(as_uuid=True), sa.ForeignKey("moderators.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(255), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_refresh_tokens_moderator_id", "refresh_tokens", ["moderator_id"])
    op.create_unique_constraint("uq_refresh_tokens_token_hash", "refresh_tokens", ["token_hash"])

    # --- Blocking Reasons ---
    op.create_table(
        "blocking_reasons",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.String(2000), nullable=True),
        sa.Column("hard_block", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_unique_constraint("uq_blocking_reasons_code", "blocking_reasons", ["code"])


    # --- Tickets ---
    op.create_table(
        "tickets",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("product_id", UUID(as_uuid=True), nullable=False),
        sa.Column("seller_id", UUID(as_uuid=True), nullable=False),
        sa.Column("category_id", UUID(as_uuid=True), nullable=True),
        sa.Column("kind", sa.Enum("CREATE", "EDIT", name="ticketkind"), nullable=False),
        sa.Column("status", sa.Enum("PENDING", "IN_REVIEW", "APPROVED", "BLOCKED", "HARD_BLOCKED", name="ticketstatus"), nullable=False, server_default="PENDING"),
        sa.Column("queue_priority", sa.Integer(), nullable=False, server_default=sa.text("3")),
        sa.Column("assigned_moderator_id", UUID(as_uuid=True), sa.ForeignKey("moderators.id", ondelete="SET NULL"), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_comment", sa.String(2000), nullable=True),
        sa.Column("json_before", JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("json_after", JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_tickets_product_id", "tickets", ["product_id"])
    op.create_index("ix_tickets_seller_id", "tickets", ["seller_id"])
    op.create_index("ix_tickets_status", "tickets", ["status"])
    op.create_index("ix_tickets_assigned_moderator_id", "tickets", ["assigned_moderator_id"])
    op.create_index("ix_tickets_queue", "tickets", ["status", "queue_priority", "created_at"])
    op.create_check_constraint("ck_tickets_queue_priority_range", "tickets", "queue_priority >= 1 AND queue_priority <= 4")


    # --- Ticket Blocking Reasons (M2M) ---
    op.create_table(
        "ticket_blocking_reasons",
        sa.Column("ticket_id", UUID(as_uuid=True), sa.ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("blocking_reason_id", UUID(as_uuid=True), sa.ForeignKey("blocking_reasons.id", ondelete="RESTRICT"), nullable=False),
        sa.PrimaryKeyConstraint("ticket_id", "blocking_reason_id", name="pk_ticket_blocking_reasons"),
    )

    # --- Ticket History ---
    op.create_table(
        "ticket_history",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("ticket_id", UUID(as_uuid=True), sa.ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("action", sa.Enum("CREATED", "CLAIMED", "RELEASED", "APPROVED", "BLOCKED", "HARD_BLOCKED", "AUTO_RETURNED", name="tickethistoryaction"), nullable=False),
        sa.Column("moderator_id", UUID(as_uuid=True), sa.ForeignKey("moderators.id", ondelete="SET NULL"), nullable=True),
        sa.Column("comment", sa.String(2000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_ticket_history_ticket_id", "ticket_history", ["ticket_id"])

    # --- Ticket Field Reports ---
    op.create_table(
        "ticket_field_reports",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("ticket_id", UUID(as_uuid=True), sa.ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("field_path", sa.String(500), nullable=False),
        sa.Column("message", sa.String(1000), nullable=False),
        sa.Column("severity", sa.Enum("ERROR", "WARNING", "INFO", name="fieldreportseverity"), nullable=False, server_default="ERROR"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_ticket_field_reports_ticket_id", "ticket_field_reports", ["ticket_id"])

    # --- B2B Event Inbox (idempotency) ---
    op.create_table(
        "b2b_event_inbox",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("idempotency_key", UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.Enum("PRODUCT_CREATED", "PRODUCT_EDITED", "PRODUCT_DELETED", name="b2beventtype"), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw_payload", JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_unique_constraint("uq_b2b_event_inbox_idempotency_key", "b2b_event_inbox", ["idempotency_key"])


def downgrade() -> None:
    op.drop_index("ix_ticket_field_reports_ticket_id", table_name="ticket_field_reports")
    op.drop_table("ticket_field_reports")
    op.drop_index("ix_ticket_history_ticket_id", table_name="ticket_history")
    op.drop_table("ticket_history")
    op.drop_table("ticket_blocking_reasons")
    op.drop_index("ix_tickets_queue", table_name="tickets")
    op.drop_index("ix_tickets_assigned_moderator_id", table_name="tickets")
    op.drop_index("ix_tickets_status", table_name="tickets")
    op.drop_index("ix_tickets_seller_id", table_name="tickets")
    op.drop_index("ix_tickets_product_id", table_name="tickets")
    op.drop_table("tickets")
    op.drop_table("blocking_reasons")
    op.drop_index("ix_refresh_tokens_moderator_id", table_name="refresh_tokens")
    op.drop_table("refresh_tokens")
    op.drop_table("moderators")
    op.execute("DROP TYPE IF EXISTS b2beventtype")
    op.execute("DROP TYPE IF EXISTS fieldreportseverity")
    op.execute("DROP TYPE IF EXISTS tickethistoryaction")
    op.execute("DROP TYPE IF EXISTS ticketstatus")
    op.execute("DROP TYPE IF EXISTS ticketkind")
    op.execute("DROP TYPE IF EXISTS moderatorrole")
