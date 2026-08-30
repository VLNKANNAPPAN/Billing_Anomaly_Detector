"""Read schemas for reconciliation results and proposed adjustments."""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class AdjustmentCandidateRead(BaseModel):
    """A reviewable difference between original and reconciled expected fees."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    transaction_id: int
    original_fee_expected: Decimal
    recalculated_fee_expected: Decimal
    adjustment_amount: Decimal
    reason: str


class ReconciliationRunRead(BaseModel):
    """Summary of an immutable reconciliation execution."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    pending_reconciliation_id: int
    status: str
    transactions_recalculated: int
    started_at: datetime
    completed_at: datetime
    adjustment_candidates: list[AdjustmentCandidateRead] = Field(default_factory=list)
