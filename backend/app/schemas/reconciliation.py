"""Read schema for outstanding reconciliation work."""

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict


class PendingReconciliationRead(BaseModel):
    """A period whose fees may need review after a late arrival."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    customer_id: int
    fee_type: str
    month: date
    earliest_affected_at: datetime
    status: str
    created_at: datetime
