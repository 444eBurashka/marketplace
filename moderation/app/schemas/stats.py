import uuid
from pydantic import BaseModel, Field


class StatsOverview(BaseModel):
    pending_count: int
    in_review_count: int
    approved_count: int
    blocked_count: int
    hard_blocked_count: int
    avg_review_time_seconds: int | None = None
    pending_by_priority: dict[str, int] = Field(default_factory=dict)


class ModeratorStats(BaseModel):
    moderator_id: uuid.UUID
    moderator_name: str | None = None
    decisions_count: int
    approved_count: int
    blocked_count: int
    hard_blocked_count: int
    avg_review_time_seconds: int | None = None
    released_count: int = 0