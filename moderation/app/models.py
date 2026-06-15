import uuid
from datetime import UTC, datetime
from enum import Enum

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

class ModeratorRole(str, Enum):
    MODERATOR = "MODERATOR"
    ADMIN = "ADMIN"

class TicketKind(str, Enum):
    CREATE = "CREATE"
    EDIT = "EDIT"

class TicketStatus(str, Enum):
    PENDING = "PENDING"
    IN_REVIEW = "IN_REVIEW"
    APPROVED = "APPROVED"
    BLOCKED = "BLOCKED"
    HARD_BLOCKED = "HARD_BLOCKED"

class TicketHistoryAction(str, Enum):
    CREATED = "CREATED"
    CLAIMED = "CLAIMED"
    RELEASED = "RELEASED"
    APPROVED = "APPROVED"
    BLOCKED = "BLOCKED"
    HARD_BLOCKED = "HARD_BLOCKED"
    AUTO_RETURNED = "AUTO_RETURNED"

class FieldReportSeverity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"

class B2BEventType(str, Enum):
    PRODUCT_CREATED = "PRODUCT_CREATED"
    PRODUCT_EDITED = "PRODUCT_EDITED"
    PRODUCT_DELETED = "PRODUCT_DELETED"

class Moderator(Base):
    __tablename__ = "moderators"
    email: Mapped[str] = mapped_column(sa.String(255), unique=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    first_name: Mapped[str] = mapped_column(sa.String(100), nullable=False)
    last_name: Mapped[str | None] = mapped_column(sa.String(100))
    role: Mapped[ModeratorRole] = mapped_column(sa.Enum(ModeratorRole, name="moderatorrole"), default=ModeratorRole.MODERATOR, nullable=False)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, default=True, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    refresh_tokens: Mapped[list["RefreshToken"]] = relationship(back_populates="moderator", cascade="all, delete-orphan")
    assigned_tickets: Mapped[list["Ticket"]] = relationship(back_populates="assigned_moderator", foreign_keys="Ticket.assigned_moderator_id")

class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    moderator_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), sa.ForeignKey("moderators.id", ondelete="CASCADE"), nullable=False)
    token_hash: Mapped[str] = mapped_column(sa.String(255), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    moderator: Mapped["Moderator"] = relationship(back_populates="refresh_tokens")
    @property
    def is_valid(self) -> bool:
        return self.revoked_at is None and self.expires_at > datetime.now(UTC)

class BlockingReason(Base):
    __tablename__ = "blocking_reasons"
    code: Mapped[str] = mapped_column(sa.String(64), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(sa.String(2000))
    hard_block: Mapped[bool] = mapped_column(sa.Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, default=True, nullable=False)

class Ticket(Base):
    __tablename__ = "tickets"
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    seller_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    category_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    kind: Mapped[TicketKind] = mapped_column(sa.Enum(TicketKind, name="ticketkind"), nullable=False)
    status: Mapped[TicketStatus] = mapped_column(sa.Enum(TicketStatus, name="ticketstatus"), default=TicketStatus.PENDING, nullable=False, index=True)
    queue_priority: Mapped[int] = mapped_column(sa.Integer, default=3, nullable=False)
    assigned_moderator_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), sa.ForeignKey("moderators.id", ondelete="SET NULL"), nullable=True, index=True)
    claimed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    claim_expires_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    decision_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    decision_comment: Mapped[str | None] = mapped_column(sa.String(2000))
    json_before: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    json_after: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    assigned_moderator: Mapped["Moderator | None"] = relationship(back_populates="assigned_tickets", foreign_keys=[assigned_moderator_id])
    blocking_reasons: Mapped[list["BlockingReason"]] = relationship(secondary="ticket_blocking_reasons", backref="tickets")
    history: Mapped[list["TicketHistory"]] = relationship(back_populates="ticket", order_by="TicketHistory.at", cascade="all, delete-orphan")
    field_reports: Mapped[list["TicketFieldReport"]] = relationship(back_populates="ticket", cascade="all, delete-orphan")
    __table_args__ = (
        sa.CheckConstraint("queue_priority >= 1 AND queue_priority <= 4", name="ck_tickets_queue_priority_range"),
        sa.Index("ix_tickets_queue", "status", "queue_priority", "created_at"),
    )

class TicketBlockingReason(Base):
    __tablename__ = "ticket_blocking_reasons"
    ticket_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), sa.ForeignKey("tickets.id", ondelete="CASCADE"), primary_key=True)
    blocking_reason_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), sa.ForeignKey("blocking_reasons.id", ondelete="RESTRICT"), primary_key=True)

class TicketHistory(Base):
    __tablename__ = "ticket_history"
    ticket_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), sa.ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False, index=True)
    at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)
    action: Mapped[TicketHistoryAction] = mapped_column(sa.Enum(TicketHistoryAction, name="tickethistoryaction"), nullable=False)
    moderator_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), sa.ForeignKey("moderators.id", ondelete="SET NULL"), nullable=True)
    comment: Mapped[str | None] = mapped_column(sa.String(2000))
    ticket: Mapped["Ticket"] = relationship(back_populates="history")
    moderator: Mapped["Moderator | None"] = relationship()

class TicketFieldReport(Base):
    __tablename__ = "ticket_field_reports"
    ticket_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), sa.ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False, index=True)
    field_path: Mapped[str] = mapped_column(sa.String(500), nullable=False)
    message: Mapped[str] = mapped_column(sa.String(1000), nullable=False)
    severity: Mapped[FieldReportSeverity] = mapped_column(sa.Enum(FieldReportSeverity, name="fieldreportseverity"), default=FieldReportSeverity.ERROR, nullable=False)
    ticket: Mapped["Ticket"] = relationship(back_populates="field_reports")

class B2BEventInbox(Base):
    __tablename__ = "b2b_event_inbox"
    idempotency_key: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), unique=True, nullable=False)
    event_type: Mapped[B2BEventType] = mapped_column(sa.Enum(B2BEventType, name="b2beventtype"), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    raw_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)