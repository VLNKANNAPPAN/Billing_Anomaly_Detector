"""Late-arrival detection and reconciliation work-queue management."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    AdjustmentCandidate,
    PendingReconciliation,
    ReconciliationRun,
    Transaction,
)
from app.services.fee_calculator import (
    _get_hierarchy_customer_ids,
    calendar_month,
    calculate_marginal_fee,
    get_active_fee_rule,
)
from app.services.money import money


def _transaction_type_condition(fee_type: str):
    """Keep late-arrival scope consistent with calculator fee-type mapping."""
    return Transaction.transaction_type == "fx" if fee_type == "fx_fee" else Transaction.transaction_type != "fx"


def mark_reconciliation_if_late(
    session: Session, customer_id: int, fee_type: str, occurred_at: datetime
) -> PendingReconciliation | None:
    """Queue a period if the new event predates an already-ingested ledger row.

    The current transaction is not in the session yet when this runs. Thus a
    later timestamp in the same monthly billing scope proves it arrived late.
    """
    hierarchy_ids = _get_hierarchy_customer_ids(session, customer_id)
    latest_existing_timestamp = session.scalar(
        select(func.max(Transaction.timestamp)).where(
            Transaction.customer_id.in_(hierarchy_ids),
            _transaction_type_condition(fee_type),
            Transaction.timestamp >= datetime(occurred_at.year, occurred_at.month, 1),
        )
    )
    if latest_existing_timestamp is None or occurred_at >= latest_existing_timestamp:
        return None

    scope_customer_id = hierarchy_ids[0]
    month = calendar_month(occurred_at)
    marker = session.scalar(
        select(PendingReconciliation).where(
            PendingReconciliation.customer_id == scope_customer_id,
            PendingReconciliation.fee_type == fee_type,
            PendingReconciliation.month == month,
        )
    )
    if marker is None:
        marker = PendingReconciliation(
            customer_id=scope_customer_id,
            fee_type=fee_type,
            month=month,
            earliest_affected_at=occurred_at,
            status="pending",
            created_at=datetime.now(),
        )
        session.add(marker)
    elif occurred_at < marker.earliest_affected_at:
        # The marker tracks the earliest affected point; it does not alter
        # transaction or rule history, so this operational update is safe.
        marker.earliest_affected_at = occurred_at
    return marker


def run_reconciliation(session: Session, marker: PendingReconciliation) -> ReconciliationRun:
    """Recalculate a queued scope and persist proposed adjustments.

    This deliberately does *not* update ``Transaction.fee_expected`` or
    calculation lineage. It produces a separate review artifact, preserving
    the original billing decision and showing exactly what later history would
    have changed.
    """
    if marker.status != "pending":
        raise ValueError("Only pending reconciliation markers can be run.")

    scope_customer_ids = _get_hierarchy_customer_ids(session, marker.customer_id)
    month_start = datetime(marker.month.year, marker.month.month, 1)
    if marker.month.month == 12:
        next_month = datetime(marker.month.year + 1, 1, 1)
    else:
        next_month = datetime(marker.month.year, marker.month.month + 1, 1)

    transactions = session.scalars(
        select(Transaction)
        .where(
            Transaction.customer_id.in_(scope_customer_ids),
            _transaction_type_condition(marker.fee_type),
            Transaction.timestamp >= month_start,
            Transaction.timestamp < next_month,
        )
        .order_by(Transaction.timestamp, Transaction.id)
    ).all()

    started_at = datetime.now()
    run = ReconciliationRun(
        pending_reconciliation=marker,
        status="completed",
        transactions_recalculated=len(transactions),
        started_at=started_at,
        completed_at=started_at,
    )
    session.add(run)

    running_volume = Decimal("0.00")
    for transaction in transactions:
        rule = get_active_fee_rule(
            session,
            transaction.customer_id,
            marker.fee_type,
            transaction.timestamp,
        )
        recalculated_fee = calculate_marginal_fee(
            transaction.amount,
            rule.rate,
            rule.tier_threshold,
            rule.tier_rate,
            running_volume,
        )
        adjustment = money(recalculated_fee - transaction.fee_expected)
        if abs(adjustment) > Decimal("0.01"):
            session.add(
                AdjustmentCandidate(
                    reconciliation_run=run,
                    transaction=transaction,
                    original_fee_expected=transaction.fee_expected,
                    recalculated_fee_expected=recalculated_fee,
                    adjustment_amount=adjustment,
                    reason="late_arriving_transaction",
                )
            )
        running_volume += money(transaction.amount)

    marker.status = "reconciled"
    return run
