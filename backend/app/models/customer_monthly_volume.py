"""Persisted monthly usage totals used by marginal fee calculations."""

from datetime import date
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class CustomerMonthlyVolume(Base):
    """Running volume for one customer, fee type, and calendar month."""

    __tablename__ = "customer_monthly_volume"

    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), primary_key=True)
    fee_type: Mapped[str] = mapped_column(String(50), primary_key=True)
    month: Mapped[date] = mapped_column(Date, primary_key=True)
    # This is the operational value updated on ingestion.  It is never treated
    # as the audit source of truth: each calculation also recomputes volume
    # from the transaction ledger and records both values in its lineage row.
    cumulative_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2), nullable=False, default=Decimal("0.00")
    )

    customer: Mapped["Customer"] = relationship(back_populates="monthly_volumes")
