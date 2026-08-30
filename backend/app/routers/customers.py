"""Customer detail, history, anomaly, and aggregate endpoints."""

from datetime import date, datetime, time
from decimal import Decimal

from time import perf_counter

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.cache.redis_client import get_cached_json, set_cached_json
from app.database import get_db
from app.models import AnomalyFlag, Customer, Transaction
from app.schemas.anomaly import AnomalyRead
from app.schemas.customer import CustomerMetrics, CustomerRead
from app.schemas.transaction import TransactionPage, TransactionRead
from app.services.money import money


router = APIRouter(prefix="/customers", tags=["customers"])


def get_customer_or_404(session: Session, customer_id: int) -> Customer:
    """Avoid repeating the same not-found rule across customer routes."""
    customer = session.get(Customer, customer_id)
    if customer is None:
        raise HTTPException(status_code=404, detail="Customer not found.")
    return customer


@router.get("/{customer_id}", response_model=CustomerRead)
def get_customer(customer_id: int, session: Session = Depends(get_db)) -> Customer:
    """Return the contract-level customer record."""
    return get_customer_or_404(session, customer_id)


@router.get("/{customer_id}/transactions", response_model=TransactionPage)
def list_transactions(
    customer_id: int,
    start_date: date | None = None,
    end_date: date | None = None,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    session: Session = Depends(get_db),
) -> TransactionPage:
    """List a customer's transactions with optional inclusive date filtering."""
    get_customer_or_404(session, customer_id)
    statement = select(Transaction).where(Transaction.customer_id == customer_id)
    count_statement = select(func.count(Transaction.id)).where(Transaction.customer_id == customer_id)
    if start_date is not None:
        start_timestamp = datetime.combine(start_date, time.min)
        statement = statement.where(Transaction.timestamp >= start_timestamp)
        count_statement = count_statement.where(Transaction.timestamp >= start_timestamp)
    if end_date is not None:
        end_timestamp = datetime.combine(end_date, time.max)
        statement = statement.where(Transaction.timestamp <= end_timestamp)
        count_statement = count_statement.where(Transaction.timestamp <= end_timestamp)
    transactions = session.scalars(
        statement.options(selectinload(Transaction.anomaly_flag))
        .order_by(Transaction.timestamp.desc())
        .offset(skip)
        .limit(limit)
    ).all()
    return TransactionPage(
        total=session.scalar(count_statement) or 0, skip=skip, limit=limit, items=transactions
    )


@router.get("/{customer_id}/anomalies", response_model=list[AnomalyRead])
def list_anomalies(
    customer_id: int,
    response: Response,
    use_cache: bool = True,
    session: Session = Depends(get_db),
) -> list[AnomalyFlag] | list[dict[str, object]]:
    """Return every flagged transaction for a customer (caching comes next phase)."""
    start_time = perf_counter()
    cache_key = f"customer:{customer_id}:anomalies"
    cached = get_cached_json(cache_key) if use_cache else None
    cache_hit = cached is not None
    if cached is not None:
        result = cached
    else:
        get_customer_or_404(session, customer_id)
        anomalies = session.scalars(
            select(AnomalyFlag)
            .join(Transaction)
            .where(Transaction.customer_id == customer_id)
            .order_by(AnomalyFlag.detected_at.desc())
        ).all()
        result = [AnomalyRead.model_validate(item).model_dump(mode="json") for item in anomalies]
        if use_cache:
            set_cached_json(cache_key, result, ttl_seconds=600)
    response.headers["X-Cache-Hit"] = str(cache_hit).lower()
    response.headers["X-Query-Time-Ms"] = f"{(perf_counter() - start_time) * 1_000:.3f}"
    return result


@router.get("/{customer_id}/metrics", response_model=CustomerMetrics)
def get_metrics(
    customer_id: int,
    response: Response,
    use_cache: bool = True,
    session: Session = Depends(get_db),
) -> CustomerMetrics | dict[str, object]:
    """Aggregate current volume and anomaly amounts directly from SQLite."""
    start_time = perf_counter()
    cache_key = f"customer:{customer_id}:metrics"
    cached = get_cached_json(cache_key) if use_cache else None
    cache_hit = cached is not None
    if cached is not None:
        result = cached
    else:
        get_customer_or_404(session, customer_id)
        total_volume = session.scalar(
            select(func.coalesce(func.sum(Transaction.amount), Decimal("0.00"))).where(
                Transaction.customer_id == customer_id
            )
        )
        total_leaked_amount = session.scalar(
            select(func.coalesce(func.sum(AnomalyFlag.leak_amount), Decimal("0.00")))
            .join(Transaction)
            .where(Transaction.customer_id == customer_id)
        )
        breakdown = dict(
            session.execute(
                select(AnomalyFlag.leak_type, func.sum(AnomalyFlag.leak_amount))
                .join(Transaction)
                .where(Transaction.customer_id == customer_id)
                .group_by(AnomalyFlag.leak_type)
            ).all()
        )
        result = CustomerMetrics(
            customer_id=customer_id,
            total_volume=money(total_volume or Decimal("0.00")),
            total_leaked_amount=money(total_leaked_amount or Decimal("0.00")),
            leak_breakdown={key: money(value) for key, value in breakdown.items()},
        ).model_dump(mode="json")
        if use_cache:
            set_cached_json(cache_key, result, ttl_seconds=1_800)
    response.headers["X-Cache-Hit"] = str(cache_hit).lower()
    response.headers["X-Query-Time-Ms"] = f"{(perf_counter() - start_time) * 1_000:.3f}"
    return result
