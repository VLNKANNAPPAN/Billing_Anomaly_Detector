"""Transaction ingestion routes that invoke the established billing services."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.cache.redis_client import invalidate_customer_reads
from app.database import get_db
from app.models import CalculationLineage, Customer, FeeRule, Transaction
from app.schemas.lineage import CalculationLineageRead
from app.schemas.transaction import TransactionBatchSummary, TransactionCreate, TransactionRead
from app.services.anomaly_detector import build_anomaly_flag, classify_discrepancy
from app.services.fee_calculator import (
    calculate_expected_fee,
    calculation_hash,
    update_monthly_volume,
)
from app.services.reconciliation import mark_reconciliation_if_late


router = APIRouter(prefix="/transactions", tags=["transactions"])


def _same_payload(transaction: Transaction, payload: TransactionCreate) -> bool:
    """Ensure an idempotency-key replay cannot silently mask changed input."""
    return (
        transaction.customer_id == payload.customer_id
        and transaction.amount == payload.amount
        and transaction.transaction_type == payload.transaction_type
        and transaction.timestamp == payload.timestamp
        and transaction.fee_charged == payload.fee_charged
    )


def process_transaction(session: Session, payload: TransactionCreate) -> tuple[Transaction, bool]:
    """Build one transaction, its optional flag, and its new accumulator total.

    This helper deliberately does not commit. That lets batch ingestion handle
    all its sorted rows as one transaction while the single endpoint commits one.
    """
    existing_transaction = session.scalar(
        select(Transaction)
        .where(Transaction.external_reference == payload.external_reference)
        .options(selectinload(Transaction.anomaly_flag))
    )
    if existing_transaction is not None:
        if _same_payload(existing_transaction, payload):
            return existing_transaction, False
        raise HTTPException(
            status_code=409,
            detail="external_reference was already used with different transaction data.",
        )

    if session.get(Customer, payload.customer_id) is None:
        raise HTTPException(status_code=404, detail="Customer not found.")

    try:
        calculation = calculate_expected_fee(
            session,
            payload.customer_id,
            payload.transaction_type,
            payload.amount,
            payload.timestamp,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    transaction = Transaction(
        external_reference=payload.external_reference,
        customer_id=payload.customer_id,
        amount=payload.amount,
        transaction_type=payload.transaction_type,
        timestamp=payload.timestamp,
        fee_charged=payload.fee_charged,
        fee_expected=calculation.fee_expected_recomputed,
    )
    session.add(transaction)

    # The enriched FeeCalculation now carries all the context needed for
    # anomaly classification — no need for separate queries here.
    rule = session.get(FeeRule, calculation.fee_rule_id)
    decision = classify_discrepancy(
        transaction,
        rule,
        calculation.recomputed_volume_before,
        expired_waiver_present=calculation.expired_waiver_present,
        observed_volume_before=calculation.stored_volume_before,
        fee_expected_from_stored=calculation.fee_expected_from_stored,
        standalone_volume=calculation.standalone_volume,
        rule_is_override=calculation.rule_is_override,
    )
    if decision is not None:
        session.add(build_anomaly_flag(transaction, decision))

    # Accept the event and preserve its lineage, but do not pretend later
    # transactions in the month were priced with this newly arrived history.
    # That period is queued for an explicit, auditable reconciliation run.
    mark_reconciliation_if_late(
        session, payload.customer_id, calculation.fee_type, payload.timestamp
    )

    session.add(
        CalculationLineage(
            transaction=transaction,
            rule_version_id=rule.rule_version_id,
            stored_cumulative_volume=calculation.stored_volume_before,
            recomputed_cumulative_volume=calculation.recomputed_volume_before,
            fee_expected_from_stored=calculation.fee_expected_from_stored,
            fee_expected_recomputed=calculation.fee_expected_recomputed,
            # FX source is intentionally explicit even though seeded FX rules
            # are contractual rates, not a market-data feed, in this phase.
            fx_source="contractual_fee_rule" if calculation.fee_type == "fx_fee" else None,
            fx_rate_timestamp=payload.timestamp if calculation.fee_type == "fx_fee" else None,
            calculation_hash=calculation_hash(
                customer_id=payload.customer_id,
                fee_type=calculation.fee_type,
                amount=payload.amount,
                occurred_at=payload.timestamp,
                rule=rule,
                stored_volume_before=calculation.stored_volume_before,
                recomputed_volume_before=calculation.recomputed_volume_before,
                fee_expected_from_stored=calculation.fee_expected_from_stored,
                fee_expected_recomputed=calculation.fee_expected_recomputed,
            ),
            calculated_at=datetime.now(),
        )
    )

    update_monthly_volume(
        session, payload.customer_id, calculation.fee_type, payload.timestamp, payload.amount
    )
    # SessionLocal deliberately disables autoflush.  Persist this sorted row
    # before processing the next batch item so both the stored accumulator and
    # the independent ledger recomputation see the same prior transactions.
    session.flush()
    return transaction, True


@router.post("", response_model=TransactionRead, status_code=status.HTTP_201_CREATED)
def create_transaction(
    payload: TransactionCreate,
    response: Response,
    session: Session = Depends(get_db),
) -> Transaction:
    """Ingest one billable event and calculate its expected fee server-side."""
    transaction, created = process_transaction(session, payload)
    if not created:
        response.status_code = status.HTTP_200_OK
        return transaction
    session.commit()
    invalidate_customer_reads(payload.customer_id)
    session.refresh(transaction)
    return transaction


@router.post("/batch", response_model=TransactionBatchSummary, status_code=status.HTTP_201_CREATED)
def create_transaction_batch(
    payloads: list[TransactionCreate], session: Session = Depends(get_db)
) -> TransactionBatchSummary:
    """Ingest a chronological batch so running totals are applied correctly."""
    ordered_payloads = sorted(payloads, key=lambda item: (item.customer_id, item.timestamp))
    flagged_count = 0
    duplicate_count = 0
    try:
        for payload in ordered_payloads:
            transaction, created = process_transaction(session, payload)
            if not created:
                duplicate_count += 1
            elif transaction.anomaly_flag is not None:
                flagged_count += 1
        session.commit()
    except Exception:
        session.rollback()
        raise
    for customer_id in {payload.customer_id for payload in ordered_payloads}:
        invalidate_customer_reads(customer_id)
    return TransactionBatchSummary(
        count_created=len(ordered_payloads) - duplicate_count,
        count_flagged=flagged_count,
        count_duplicates=duplicate_count,
    )


@router.get("/{transaction_id}/lineage", response_model=CalculationLineageRead)
def get_transaction_lineage(
    transaction_id: int, session: Session = Depends(get_db)
) -> CalculationLineage:
    """Expose the immutable evidence behind a transaction's expected fee."""
    lineage = session.scalar(
        select(CalculationLineage).where(CalculationLineage.transaction_id == transaction_id)
    )
    if lineage is None:
        raise HTTPException(status_code=404, detail="Calculation lineage not found.")
    return lineage
