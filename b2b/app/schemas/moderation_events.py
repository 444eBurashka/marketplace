import uuid
from datetime import datetime

from pydantic import BaseModel, Field


# ─── Вложенные схемы ─────────────────────────────────────────────────────────

class FieldReportIn(BaseModel):
    field_name: str
    sku_id: uuid.UUID | None = None
    comment: str


class BlockingReasonIn(BaseModel):
    id: uuid.UUID
    title: str
    comment: str | None = None


# ─── Request ─────────────────────────────────────────────────────────────────

class ModerationEventRequest(BaseModel):
    idempotency_key: uuid.UUID
    product_id: uuid.UUID
    event_type: str                          # MODERATED или BLOCKED
    moderator_id: uuid.UUID | None = None
    moderator_comment: str | None = None
    blocking_reason_id: uuid.UUID | None = None
    hard_block: bool = False
    field_reports: list[FieldReportIn] = Field(default_factory=list)
    occurred_at: datetime