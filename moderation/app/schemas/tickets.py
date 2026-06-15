import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class FieldReportIn(BaseModel):
    field_path: str = Field(max_length=500)
    message: str = Field(max_length=1000)
    severity: str = Field(default="ERROR", pattern=r"^(INFO|WARNING|ERROR)$")


class FieldReportOut(BaseModel):
    model_config = {"from_attributes": True}
    field_path: str
    message: str
    severity: str


class BlockDecisionRequest(BaseModel):
    blocking_reason_ids: list[uuid.UUID] = Field(min_length=1)
    comment: str | None = Field(default=None, max_length=2000)
    field_reports: list[FieldReportIn] = Field(default_factory=list)


class TicketHistoryEntry(BaseModel):
    model_config = {"from_attributes": True}
    at: datetime
    action: str
    moderator_id: uuid.UUID | None = None
    comment: str | None = None


class TicketResponse(BaseModel):
    model_config = {"from_attributes": True}
    id: uuid.UUID
    product_id: uuid.UUID
    seller_id: uuid.UUID
    category_id: uuid.UUID | None = None
    kind: str
    status: str
    queue_priority: int
    assigned_moderator_id: uuid.UUID | None = None
    claimed_at: datetime | None = None
    claim_expires_at: datetime | None = None
    decision_at: datetime | None = None
    created_at: datetime
    updated_at: datetime | None = None


class BlockingReasonOut(BaseModel):
    model_config = {"from_attributes": True}
    id: uuid.UUID
    code: str
    title: str
    hard_block: bool


class TicketDetailResponse(TicketResponse):
    json_before: dict | None = None
    json_after: dict | None = None
    diff: list[dict] | None = None
    field_reports: list[FieldReportOut] = Field(default_factory=list)
    blocking_reasons: list[BlockingReasonOut] = Field(default_factory=list)
    decision_comment: str | None = None
    history: list[TicketHistoryEntry] = Field(default_factory=list)


class PaginatedTickets(BaseModel):
    items: list[TicketResponse]
    total_count: int
    limit: int
    offset: int


class PaginatedTicketsDetail(BaseModel):
    items: list[TicketDetailResponse]
    total_count: int
    limit: int
    offset: int