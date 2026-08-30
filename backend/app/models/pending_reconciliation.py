"""Durable work markers for periods affected by late-arriving transactions."""

from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class PendingReconciliation(Base):
    """A customer/fee-type/month that needs an explicit reconciliation run.

    This is mutable operational state, unlike fee rules and calculation lineage.
    It records that history may need review; it never silently rewrites the
    original billed result or its immutable evidence.
    """

    __tablename__ = "pending_reconciliations"
    __table_args__ = (
        UniqueConstraint("customer_id", "fee_type", "month", name="uq_reconciliation_scope"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), nullable=False)
    fee_type: Mapped[str] = mapped_column(String(50), nullable=False)
    month: Mapped[date] = mapped_column(Date, nullable=False)
    earliest_affected_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    customer: Mapped["Customer"] = relationship()
