"""Anomaly-flag database model."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class AnomalyFlag(Base):
    """Explainable indication that a transaction fee differs from expectation."""

    __tablename__ = "anomaly_flags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    transaction_id: Mapped[int] = mapped_column(
        ForeignKey("transactions.id"), nullable=False, unique=True
    )
    leak_type: Mapped[str] = mapped_column(String(50), nullable=False)
    leak_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    detected_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    # The volume state that the billing system appeared to use when computing
    # fee_charged.  Comparing this against the correct chronological total
    # distinguishes race-condition errors from simple rate mistakes.
    implied_volume: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    # JSON blob carrying the full root-cause breakdown (rates used, thresholds
    # crossed, expired rule IDs, etc.) so audit teams can trace every anomaly
    # without re-querying the original data.
    diagnostic_details: Mapped[str | None] = mapped_column(Text, nullable=True)

    transaction: Mapped["Transaction"] = relationship(back_populates="anomaly_flag")
