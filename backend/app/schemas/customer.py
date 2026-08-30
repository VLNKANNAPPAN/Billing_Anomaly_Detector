"""Schemas for customer records and aggregate customer metrics."""

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class CustomerRead(BaseModel):
    """Customer attributes exposed by the read endpoint."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    industry: str
    contract_start_date: date
    account_tier: str
    parent_id: int | None = None
    minimum_monthly_floor: Decimal | None = None


class CustomerMetrics(BaseModel):
    """Volume and discrepancy totals for one customer."""

    customer_id: int
    total_volume: Decimal
    total_leaked_amount: Decimal
    leak_breakdown: dict[str, Decimal]
