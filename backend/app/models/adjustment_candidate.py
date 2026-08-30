"""Proposed, reviewable financial adjustments from reconciliation."""

from decimal import Decimal

from sqlalchemy import ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class AdjustmentCandidate(Base):
    """A difference discovered without mutating the original transaction."""

    __tablename__ = "adjustment_candidates"
    __table_args__ = (
        UniqueConstraint("reconciliation_run_id", "transaction_id", name="uq_adjustment_run_transaction"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    reconciliation_run_id: Mapped[int] = mapped_column(
        ForeignKey("reconciliation_runs.id"), nullable=False
    )
    transaction_id: Mapped[int] = mapped_column(ForeignKey("transactions.id"), nullable=False)
    original_fee_expected: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    recalculated_fee_expected: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    adjustment_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    reason: Mapped[str] = mapped_column(String(50), nullable=False)

    reconciliation_run: Mapped["ReconciliationRun"] = relationship(
        back_populates="adjustment_candidates"
    )
    transaction: Mapped["Transaction"] = relationship()
