"""Expected-fee calculation using dated rules and monthly volume accumulators.

Rule resolution follows a two-level priority:
  1. Customer-specific override  (FeeRule where customer_id == target customer)
  2. Global tier template        (FeeRule where customer_id IS NULL and
                                  account_tier == customer.account_tier)

Volume accumulation supports corporate hierarchies: when a customer has a
parent_id, the monthly volume is pooled across the parent entity and all
its direct sub-accounts so that tier thresholds apply to the aggregate.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import Customer, CustomerMonthlyVolume, FeeRule, Transaction
from app.services.money import money, rate as normalized_rate


def fee_type_for_transaction(transaction_type: str) -> str:
    """Map the current transaction categories to their applicable fee-rule type."""
    return "fx_fee" if transaction_type == "fx" else "transaction_fee"


def calendar_month(value: date | datetime) -> date:
    """Normalize a timestamp to its calendar-month key (always the first day)."""
    return date(value.year, value.month, 1)


def calculate_marginal_fee(
    amount: Decimal | int | float | str,
    rate: Decimal | int | float | str,
    tier_threshold: Decimal | int | float | str | None,
    tier_rate: Decimal | int | float | str | None,
    volume_before: Decimal | int | float | str,
) -> Decimal:
    """Bill each portion of a transaction at the rate for its volume bracket.

    The volume before this transaction is supplied by the accumulator. This
    keeps the calculation deterministic and lets a transaction crossing the
    threshold be split between base and discounted tier rates.
    """
    amount = money(amount)
    volume_before = money(volume_before)
    rate_value = normalized_rate(rate)
    if amount < 0 or volume_before < 0:
        raise ValueError("Transaction amount and accumulated volume cannot be negative.")
    if tier_threshold is None or tier_rate is None:
        return money(amount * rate_value)

    tier_threshold = money(tier_threshold)
    tier_rate_value = normalized_rate(tier_rate)
    remaining_base_volume = max(tier_threshold - volume_before, Decimal("0.00"))
    base_portion = min(amount, remaining_base_volume)
    tier_portion = amount - base_portion
    return money((base_portion * rate_value) + (tier_portion * tier_rate_value))


# ---------------------------------------------------------------------------
# Hybrid rule resolution: customer override → global tier template fallback
# ---------------------------------------------------------------------------

def _find_customer_override(
    session: Session, customer_id: int, fee_type: str, tx_date: date,
) -> FeeRule | None:
    """Search for a rule tied directly to this customer (highest priority)."""
    return session.scalar(
        select(FeeRule)
        .where(
            FeeRule.customer_id == customer_id,
            FeeRule.fee_type == fee_type,
            FeeRule.effective_from <= tx_date,
            or_(FeeRule.effective_to.is_(None), FeeRule.effective_to >= tx_date),
        )
        .order_by(FeeRule.effective_from.desc())
    )


def _find_tier_template(
    session: Session, account_tier: str, fee_type: str, tx_date: date,
) -> FeeRule | None:
    """Fallback: find the global shared rule matching the customer's tier."""
    return session.scalar(
        select(FeeRule)
        .where(
            # Global templates have no customer_id and carry an account_tier tag.
            FeeRule.customer_id.is_(None),
            FeeRule.account_tier == account_tier,
            FeeRule.fee_type == fee_type,
            FeeRule.effective_from <= tx_date,
            or_(FeeRule.effective_to.is_(None), FeeRule.effective_to >= tx_date),
        )
        .order_by(FeeRule.effective_from.desc())
    )


def get_active_fee_rule(
    session: Session, customer_id: int, fee_type: str, occurred_at: datetime,
) -> FeeRule:
    """Resolve the fee rule for a transaction using the priority chain.

    1. Customer-specific contract override (most specific, wins if present).
    2. Global tier template matching the customer's account_tier (fallback).
    """
    tx_date = occurred_at.date()

    # Priority 1: direct customer override / contract rate.
    rule = _find_customer_override(session, customer_id, fee_type, tx_date)
    if rule is not None:
        return rule

    # Priority 2: global tier template matched by the customer's account_tier.
    customer = session.get(Customer, customer_id)
    if customer is not None:
        rule = _find_tier_template(session, customer.account_tier, fee_type, tx_date)
    if rule is not None:
        return rule

    raise ValueError(
        f"No active {fee_type} rule for customer {customer_id} on {tx_date}."
    )


# ---------------------------------------------------------------------------
# Volume accumulation with corporate hierarchy pooling
# ---------------------------------------------------------------------------

def _get_hierarchy_customer_ids(session: Session, customer_id: int) -> list[int]:
    """Return the IDs whose volumes should be pooled for tier calculations.

    Standalone customers (no parent_id) return only their own ID.
    Sub-accounts pool with the parent and every sister sub-account.
    Parent entities pool with all their direct children.
    """
    customer = session.get(Customer, customer_id)
    if customer is None:
        return [customer_id]

    # If the customer IS a sub-account, pool under its parent.
    if customer.parent_id is not None:
        root_id = customer.parent_id
    else:
        # Check whether the customer IS a parent (has children).
        has_children = session.scalar(
            select(func.count(Customer.id)).where(Customer.parent_id == customer_id)
        )
        if has_children:
            root_id = customer_id
        else:
            # Standalone customer — no hierarchy to pool.
            return [customer_id]

    # Collect root + all direct children.
    child_ids = list(
        session.scalars(
            select(Customer.id).where(Customer.parent_id == root_id)
        ).all()
    )
    return [root_id] + child_ids


def get_monthly_volume(
    session: Session, customer_id: int, fee_type: str, occurred_at: datetime,
) -> Decimal:
    """Read the stored running total, pooling across corporate hierarchy.

    When a customer belongs to a parent-child group, the volume is summed
    across the parent and all its sub-accounts.  This ensures that tier
    thresholds reflect the aggregate corporate relationship, not each
    subsidiary in isolation.
    """
    month = calendar_month(occurred_at)
    hierarchy_ids = _get_hierarchy_customer_ids(session, customer_id)

    if len(hierarchy_ids) == 1:
        # Fast path: standalone customer — single primary-key lookup.
        accumulator = session.get(
            CustomerMonthlyVolume,
            {"customer_id": customer_id, "fee_type": fee_type, "month": month},
        )
        return accumulator.cumulative_amount if accumulator is not None else Decimal("0.00")

    # Pooled path: SUM across all entities in the hierarchy.
    total = session.scalar(
        select(func.coalesce(func.sum(CustomerMonthlyVolume.cumulative_amount), Decimal("0.00")))
        .where(
            CustomerMonthlyVolume.customer_id.in_(hierarchy_ids),
            CustomerMonthlyVolume.fee_type == fee_type,
            CustomerMonthlyVolume.month == month,
        )
    )
    return money(total or Decimal("0.00"))


def get_standalone_monthly_volume(
    session: Session, customer_id: int, fee_type: str, occurred_at: datetime,
) -> Decimal:
    """Read only this customer's own volume, ignoring hierarchy.

    Used by the anomaly detector to compare the standalone volume against
    the pooled volume — a mismatch signals a hierarchy_aggregation_error.
    """
    accumulator = session.get(
        CustomerMonthlyVolume,
        {"customer_id": customer_id, "fee_type": fee_type, "month": calendar_month(occurred_at)},
    )
    return accumulator.cumulative_amount if accumulator is not None else Decimal("0.00")


def get_recomputed_monthly_volume(
    session: Session, customer_id: int, fee_type: str, occurred_at: datetime,
) -> Decimal:
    """Derive the pre-transaction volume directly from the immutable ledger.

    This is intentionally separate from ``get_monthly_volume``.  The stored
    accumulator is fast operational state and can be wrong; the ledger sum is
    the audit reference used to calculate the authoritative expected fee.
    Transactions with the same timestamp are processed in the batch's stable
    input order, so a caller must process batches chronologically.
    """
    month_start = datetime(occurred_at.year, occurred_at.month, 1)
    hierarchy_ids = _get_hierarchy_customer_ids(session, customer_id)
    transaction_type_condition = (
        Transaction.transaction_type == "fx"
        if fee_type == "fx_fee"
        else Transaction.transaction_type != "fx"
    )
    total = session.scalar(
        select(func.coalesce(func.sum(Transaction.amount), Decimal("0.00"))).where(
            Transaction.customer_id.in_(hierarchy_ids),
            transaction_type_condition,
            Transaction.timestamp >= month_start,
            Transaction.timestamp < occurred_at,
        )
    )
    return money(total or Decimal("0.00"))


# ---------------------------------------------------------------------------
# Expired-waiver detection helper
# ---------------------------------------------------------------------------

def has_expired_waiver(
    session: Session, customer_id: int, fee_type: str, tx_date: date,
) -> bool:
    """Return True if the customer once had a zero-rate rule that has since expired.

    This feeds the expired_waiver classification: fee_charged == 0 AND an old
    promotional waiver existed but is no longer active on the transaction date.
    """
    from sqlalchemy import exists as sql_exists  # avoid shadowing outer import
    return bool(
        session.scalar(
            select(
                sql_exists().where(
                    FeeRule.customer_id == customer_id,
                    FeeRule.fee_type == fee_type,
                    FeeRule.rate == 0,
                    FeeRule.effective_to.is_not(None),
                    FeeRule.effective_to < tx_date,
                )
            )
        )
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FeeCalculation:
    """Inputs and result retained to make later anomaly explanations transparent."""

    fee_type: str
    fee_rule_id: int
    # Ledger-derived source of truth, used for the persisted expected fee.
    recomputed_volume_before: Decimal
    fee_expected_recomputed: Decimal
    # Fast operational state and its counterfactual result are retained to
    # isolate stale accumulator defects from a pricing-rule defect.
    stored_volume_before: Decimal
    fee_expected_from_stored: Decimal
    # Whether the rule came from a customer override or a global tier template.
    rule_is_override: bool = True
    # Volume for this customer alone, without hierarchy pooling.
    # Non-None only when the customer belongs to a hierarchy.
    standalone_volume: Decimal | None = None
    # Whether an expired zero-rate waiver exists for this customer/fee_type.
    expired_waiver_present: bool = False

    @property
    def volume_before(self) -> Decimal:
        """Compatibility alias for classifiers expecting authoritative volume."""
        return self.recomputed_volume_before

    @property
    def fee_expected(self) -> Decimal:
        """Compatibility alias for the authoritative ledger-derived fee."""
        return self.fee_expected_recomputed


def calculation_hash(
    *,
    customer_id: int,
    fee_type: str,
    amount: Decimal | int | float | str,
    occurred_at: datetime,
    rule: FeeRule,
    stored_volume_before: Decimal,
    recomputed_volume_before: Decimal,
    fee_expected_from_stored: Decimal,
    fee_expected_recomputed: Decimal,
) -> str:
    """Create a reproducible fingerprint of the calculation's material inputs."""
    payload = {
        "amount": amount,
        "customer_id": customer_id,
        "fee_expected_from_stored": fee_expected_from_stored,
        "fee_expected_recomputed": fee_expected_recomputed,
        "fee_type": fee_type,
        "occurred_at": occurred_at.isoformat(),
        "recomputed_volume_before": recomputed_volume_before,
        "rule_version_id": rule.rule_version_id,
        "stored_volume_before": stored_volume_before,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def calculate_expected_fee(
    session: Session, customer_id: int, transaction_type: str,
    amount: Decimal | int | float | str, occurred_at: datetime,
) -> FeeCalculation:
    """Calculate a fee from the active rule and the persisted pre-transaction volume.

    Returns an enriched FeeCalculation with diagnostic context that the
    anomaly detector needs to classify discrepancies precisely.
    """
    fee_type = fee_type_for_transaction(transaction_type)
    rule = get_active_fee_rule(session, customer_id, fee_type, occurred_at)

    # Determine whether the resolved rule is a customer override or a tier template.
    rule_is_override = rule.customer_id is not None

    # Both totals use the same hierarchy scope.  Their difference proves that
    # stored operational state and the raw billing ledger have diverged.
    stored_volume_before = get_monthly_volume(session, customer_id, fee_type, occurred_at)
    recomputed_volume_before = get_recomputed_monthly_volume(
        session, customer_id, fee_type, occurred_at
    )

    # Standalone volume (only if customer belongs to a hierarchy — used for
    # hierarchy_aggregation_error detection downstream).
    customer = session.get(Customer, customer_id)
    hierarchy_ids = _get_hierarchy_customer_ids(session, customer_id)
    standalone_volume: Decimal | None = None
    if len(hierarchy_ids) > 1:
        standalone_volume = get_standalone_monthly_volume(
            session, customer_id, fee_type, occurred_at
        )

    # Expired-waiver check.
    waiver_present = has_expired_waiver(
        session, customer_id, fee_type, occurred_at.date()
    )

    return FeeCalculation(
        fee_type=fee_type,
        fee_rule_id=rule.id,
        recomputed_volume_before=recomputed_volume_before,
        fee_expected_recomputed=calculate_marginal_fee(
            amount, rule.rate, rule.tier_threshold, rule.tier_rate, recomputed_volume_before,
        ),
        stored_volume_before=stored_volume_before,
        fee_expected_from_stored=calculate_marginal_fee(
            amount, rule.rate, rule.tier_threshold, rule.tier_rate, stored_volume_before,
        ),
        rule_is_override=rule_is_override,
        standalone_volume=standalone_volume,
        expired_waiver_present=waiver_present,
    )


def update_monthly_volume(
    session: Session, customer_id: int, fee_type: str, occurred_at: datetime,
    amount: Decimal | int | float | str,
) -> None:
    """Advance the stored total after a transaction has been accepted.

    Updating once on insertion is deliberately cheaper than summing every past
    transaction for each calculation. Batch callers must process chronologically.

    Volume is always recorded against the individual customer_id, not the
    parent.  Pooling is handled at read-time by get_monthly_volume, which
    sums across the hierarchy.
    """
    amount = money(amount)
    if amount < 0:
        raise ValueError("Transaction amount cannot be negative.")
    key = {"customer_id": customer_id, "fee_type": fee_type, "month": calendar_month(occurred_at)}
    accumulator = session.get(CustomerMonthlyVolume, key)
    if accumulator is None:
        accumulator = CustomerMonthlyVolume(**key, cumulative_amount=Decimal("0.00"))
        session.add(accumulator)
    accumulator.cumulative_amount += amount
