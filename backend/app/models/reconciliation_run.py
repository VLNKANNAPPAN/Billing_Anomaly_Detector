"""Immutable outputs of an explicit reconciliation execution."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class ReconciliationRun(Base):
    """A completed recalculation of one late-arrival reconciliation marker."""

    __tablename__ = "reconciliation_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pending_reconciliation_id: Mapped[int] = mapped_column(
        ForeignKey("pending_reconciliations.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    transactions_recalculated: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    completed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    pending_reconciliation: Mapped["PendingReconciliation"] = relationship()
    adjustment_candidates: Mapped[list["AdjustmentCandidate"]] = relationship(
        back_populates="reconciliation_run"
    )
