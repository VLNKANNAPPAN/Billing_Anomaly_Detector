"""Fee-rule database model."""

from datetime import date
from uuid import uuid4

from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class FeeRule(Base):
    """A dated fee schedule: either a global tier template or a customer override.

    When customer_id is NULL and account_tier is set, this row is a shared
    template that applies to every customer in that tier.  When customer_id
    is set, this row is a contract-specific override (highest priority).
    """

    __tablename__ = "fee_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # A stable public ID identifies this exact immutable pricing version even
    # when integer IDs differ between environments or database restores.
    rule_version_id: Mapped[str] = mapped_column(
        String(36), unique=True, nullable=False, default=lambda: str(uuid4())
    )
    # A successor points backward instead of editing its predecessor.  This
    # makes version history append-only while still showing replacement intent.
    supersedes_rule_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # NULL customer_id marks this rule as a global tier template.
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"), nullable=True)
    fee_type: Mapped[str] = mapped_column(String(50), nullable=False)
    rate: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False)
    tier_threshold: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    tier_rate: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Tier affinity for global templates (e.g. "standard", "premium").
    # NULL when this rule is a customer-specific override.
    account_tier: Mapped[str | None] = mapped_column(String(20), nullable=True)

    customer: Mapped["Customer | None"] = relationship(back_populates="fee_rules")
