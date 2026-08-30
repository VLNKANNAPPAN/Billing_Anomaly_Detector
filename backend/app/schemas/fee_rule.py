"""Schema for read-only fee-rule responses."""

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class FeeRuleRead(BaseModel):
    """A versioned fee rule effective for a customer."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    customer_id: int | None = None
    fee_type: str
    rate: Decimal
    tier_threshold: Decimal | None
    tier_rate: Decimal | None
    effective_from: date
    effective_to: date | None
    account_tier: str | None = None
