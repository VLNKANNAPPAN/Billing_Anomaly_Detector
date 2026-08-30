"""Immutable audit evidence for each expected-fee calculation."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class CalculationLineage(Base):
    """Inputs, outputs, and identity of one calculation at ingestion time.

    The row is deliberately one-to-one with a transaction.  It freezes the
    evidence needed to reproduce or challenge the result after rules and
    operational accumulators have moved on.
    """

    __tablename__ = "calculation_lineages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    transaction_id: Mapped[int] = mapped_column(
        ForeignKey("transactions.id"), unique=True, nullable=False
    )
    rule_version_id: Mapped[str] = mapped_column(String(36), nullable=False)
    stored_cumulative_volume: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    recomputed_cumulative_volume: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    fee_expected_from_stored: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    fee_expected_recomputed: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    fx_source: Mapped[str | None] = mapped_column(String(100), nullable=True)
    fx_rate_timestamp: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    calculation_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    calculated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    transaction: Mapped["Transaction"] = relationship(back_populates="calculation_lineage")
