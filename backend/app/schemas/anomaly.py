"""Schemas representing detected billing discrepancies."""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class AnomalyRead(BaseModel):
    """A persisted anomaly returned by customer and transaction endpoints."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    transaction_id: int
    leak_type: str
    leak_amount: Decimal
    detected_at: datetime
    implied_volume: Decimal | None = None
    diagnostic_details: str | None = None
