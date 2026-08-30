"""Transaction database model."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Transaction(Base):
    """One billable event with both charged and calculated fees retained."""

    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # The billing source's stable ID makes retrying an ingestion safe.
    external_reference: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    transaction_type: Mapped[str] = mapped_column(String(50), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    fee_charged: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    fee_expected: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)

    customer: Mapped["Customer"] = relationship(back_populates="transactions")
    anomaly_flag: Mapped["AnomalyFlag | None"] = relationship(
        back_populates="transaction", uselist=False
    )
    calculation_lineage: Mapped["CalculationLineage | None"] = relationship(
        back_populates="transaction", uselist=False
    )
