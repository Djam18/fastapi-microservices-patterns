from pydantic import BaseModel
from typing import Literal
from datetime import datetime
import uuid as _uuid

# Event-driven messaging — Django signals are process-local.
# For cross-service communication we need serialisable event schemas.
# Pydantic makes them self-documenting and validates on deserialisation.


class OrderCreatedEvent(BaseModel):
    event_type: Literal["order.created"] = "order.created"
    event_id:   str = None  # type: ignore[assignment]
    occurred_at: datetime = None  # type: ignore[assignment]

    order_id:   str
    user_id:    int
    total_cents: int
    items:      list[dict]

    def __init__(self, **data):
        if "event_id" not in data:
            data["event_id"] = str(_uuid.uuid4())
        if "occurred_at" not in data:
            data["occurred_at"] = datetime.utcnow()
        super().__init__(**data)


class OrderPaidEvent(BaseModel):
    event_type: Literal["order.paid"] = "order.paid"
    event_id:   str = None  # type: ignore[assignment]
    occurred_at: datetime = None  # type: ignore[assignment]

    order_id:   str
    user_id:    int
    amount_cents: int

    def __init__(self, **data):
        if "event_id" not in data:
            data["event_id"] = str(_uuid.uuid4())
        if "occurred_at" not in data:
            data["occurred_at"] = datetime.utcnow()
        super().__init__(**data)


class OrderCancelledEvent(BaseModel):
    event_type: Literal["order.cancelled"] = "order.cancelled"
    event_id:   str = None  # type: ignore[assignment]
    occurred_at: datetime = None  # type: ignore[assignment]

    order_id: str
    user_id:  int
    reason:   str = ""

    def __init__(self, **data):
        if "event_id" not in data:
            data["event_id"] = str(_uuid.uuid4())
        if "occurred_at" not in data:
            data["occurred_at"] = datetime.utcnow()
        super().__init__(**data)
