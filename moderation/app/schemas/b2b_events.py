import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class EventProductCreated(BaseModel):
    product_id: uuid.UUID
    seller_id: uuid.UUID
    category_id: uuid.UUID | None = None
    queue_priority: int = Field(default=3, ge=1, le=4)
    json_after: dict


class EventProductEdited(BaseModel):
    product_id: uuid.UUID
    seller_id: uuid.UUID
    category_id: uuid.UUID | None = None
    queue_priority: int = Field(default=3, ge=1, le=4)
    json_before: dict
    json_after: dict


class EventProductDeleted(BaseModel):
    product_id: uuid.UUID


class IncomingB2BEvent(BaseModel):
    event_type: str = Field(pattern=r"^(PRODUCT_CREATED|PRODUCT_EDITED|PRODUCT_DELETED)$")
    idempotency_key: uuid.UUID
    occurred_at: datetime
    payload: dict