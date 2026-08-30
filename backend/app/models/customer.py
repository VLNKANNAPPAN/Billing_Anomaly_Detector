"""Customer database model."""

from datetime import date
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Customer(Base):
    """A bank customer whose contract determines applicable fee rules."""

    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    industry: Mapped[str] = mapped_column(String(100), nullable=False)
    contract_start_date: Mapped[date] = mapped_column(Date, nullable=False)
    account_tier: Mapped[str] = mapped_column(String(20), nullable=False)
    # Self-referencing FK: sub-accounts point to their corporate parent.
    # NULL means this customer is a top-level (standalone or parent) entity.
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("customers.id"), nullable=True
    )
    # Guaranteed monthly revenue floor from the customer's contract.
    # When total monthly fees fall below this, a contract_floor_violation is raised.
    minimum_monthly_floor: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)

    fee_rules: Mapped[list["FeeRule"]] = relationship(back_populates="customer")
    transactions: Mapped[list["Transaction"]] = relationship(back_populates="customer")
    monthly_volumes: Mapped[list["CustomerMonthlyVolume"]] = relationship(
        back_populates="customer"
    )
    # Corporate hierarchy: navigate from a sub-account up to its parent entity.
    parent: Mapped["Customer | None"] = relationship(
        back_populates="sub_accounts", remote_side=[id]
    )
    # Corporate hierarchy: list all direct sub-accounts under this parent.
    sub_accounts: Mapped[list["Customer"]] = relationship(back_populates="parent")
