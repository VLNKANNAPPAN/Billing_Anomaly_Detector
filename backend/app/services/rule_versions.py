"""Append-only fee-rule version creation and validation."""

from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import FeeRule


@dataclass(frozen=True)
class FeeRuleVersionInput:
    """The immutable values required to create one rule version."""

    customer_id: int | None
    account_tier: str | None
    fee_type: str
    rate: float
    tier_threshold: float | None
    tier_rate: float | None
    effective_from: date
    effective_to: date | None = None
    supersedes_rule_version_id: str | None = None


def create_fee_rule_version(session: Session, values: FeeRuleVersionInput) -> FeeRule:
    """Insert a rule version after rejecting overlapping periods in its scope.

    Existing rows are never edited.  A new successor can begin on a later date;
    resolution chooses the newest effective row, so it logically closes an open
    predecessor without mutating historical data.
    """
    if (values.customer_id is None) == (values.account_tier is None):
        raise ValueError("A rule must target exactly one customer or one account tier.")
    if values.effective_to is not None and values.effective_to < values.effective_from:
        raise ValueError("effective_to cannot be earlier than effective_from.")

    scope_filter = (
        FeeRule.customer_id == values.customer_id
        if values.customer_id is not None
        else FeeRule.account_tier == values.account_tier
    )
    existing_rules = session.scalars(
        select(FeeRule).where(scope_filter, FeeRule.fee_type == values.fee_type)
    ).all()
    for existing in existing_rules:
        existing_end = existing.effective_to or date.max
        new_end = values.effective_to or date.max
        if existing.effective_from <= new_end and values.effective_from <= existing_end:
            # A successor is permitted to start after an open predecessor. The
            # resolver's latest-effective rule wins from that instant onward.
            if not (
                existing.effective_to is None
                and values.supersedes_rule_version_id == existing.rule_version_id
                and values.effective_from > existing.effective_from
            ):
                raise ValueError("Rule version overlaps an existing rule in the same scope.")

    rule = FeeRule(
        customer_id=values.customer_id,
        account_tier=values.account_tier,
        fee_type=values.fee_type,
        rate=values.rate,
        tier_threshold=values.tier_threshold,
        tier_rate=values.tier_rate,
        effective_from=values.effective_from,
        effective_to=values.effective_to,
        supersedes_rule_version_id=values.supersedes_rule_version_id,
    )
    session.add(rule)
    return rule
