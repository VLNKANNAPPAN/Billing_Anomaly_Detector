"""Read-only schema for calculation audit evidence."""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class CalculationLineageRead(BaseModel):
    """Evidence captured when the expected fee was calculated."""

    model_config = ConfigDict(from_attributes=True)

    transaction_id: int
    rule_version_id: str
    stored_cumulative_volume: Decimal
    recomputed_cumulative_volume: Decimal
    fee_expected_from_stored: Decimal
    fee_expected_recomputed: Decimal
    fx_source: str | None
    fx_rate_timestamp: datetime | None
    calculation_hash: str
    calculated_at: datetime
