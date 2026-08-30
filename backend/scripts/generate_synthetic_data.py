r"""Create reproducible demo data for the billing anomaly service.

Run from ``backend`` with:
    .\.venv\Scripts\python.exe -m scripts.generate_synthetic_data --reset
"""

from __future__ import annotations

import argparse
import random
from datetime import date, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import AnomalyFlag, Customer, CustomerMonthlyVolume, FeeRule, Transaction
from app.services.fee_calculator import calculate_marginal_fee, fee_type_for_transaction


CUSTOMER_COUNT = 100
TRANSACTION_COUNT = 24_000
LEAK_RATIO = 0.30
RANDOM_SEED = 42
LEAK_TYPES = (
    "expired_waiver",
    "wrong_tier",
    "stale_fx_rate",
    "bundling_error",
    "subaccount_discount_error",
    "stale_running_total",
)


def reset_data(session: Session) -> None:
    """Remove dependent records first so foreign-key relationships remain valid."""
    session.execute(delete(AnomalyFlag))
    session.execute(delete(Transaction))
    session.execute(delete(CustomerMonthlyVolume))
    session.execute(delete(FeeRule))
    session.execute(delete(Customer))
    session.commit()


def create_customers_and_rules(session: Session, rng: random.Random) -> list[Customer]:
    """Create varied contracts plus active and expired fee-rule versions."""
    customers: list[Customer] = []
    industries = ("retail", "healthcare", "manufacturing", "logistics", "technology")
    today = date.today()

    for number in range(1, CUSTOMER_COUNT + 1):
        tier = "premium" if number % 3 == 0 else "standard"
        customer = Customer(
            name=f"Customer {number:03d}",
            industry=rng.choice(industries),
            contract_start_date=today - timedelta(days=rng.randint(365, 1_800)),
            account_tier=tier,
        )
        session.add(customer)
        customers.append(customer)

    session.flush()

    for customer in customers:
        transaction_rate = 0.012 if customer.account_tier == "standard" else 0.009
        transaction_tier_rate = transaction_rate - 0.003
        active_from = today - timedelta(days=365)

        session.add_all(
            [
                FeeRule(
                    customer_id=customer.id,
                    fee_type="transaction_fee",
                    rate=transaction_rate,
                    tier_threshold=5_000.0,
                    tier_rate=transaction_tier_rate,
                    effective_from=active_from,
                    effective_to=None,
                ),
                FeeRule(
                    customer_id=customer.id,
                    fee_type="fx_fee",
                    rate=0.018,
                    tier_threshold=None,
                    tier_rate=None,
                    effective_from=active_from,
                    effective_to=None,
                ),
                # This old zero-fee waiver is deliberately retained. Some seeded
                # transactions incorrectly use it, modelling an expired waiver.
                FeeRule(
                    customer_id=customer.id,
                    fee_type="transaction_fee",
                    rate=0.0,
                    tier_threshold=None,
                    tier_rate=None,
                    effective_from=active_from - timedelta(days=365),
                    effective_to=today - timedelta(days=181),
                ),
            ]
        )

    session.commit()
    return customers


def create_transactions(session: Session, customers: list[Customer], rng: random.Random) -> None:
    """Create 70% correct fees and an even spread of the five leak categories."""
    today = datetime.now().replace(microsecond=0)
    start = today - timedelta(days=183)
    leaked_count = int(TRANSACTION_COUNT * LEAK_RATIO)
    leak_assignments = [LEAK_TYPES[index % len(LEAK_TYPES)] for index in range(leaked_count)]
    assignments: list[str | None] = leak_assignments + [None] * (TRANSACTION_COUNT - leaked_count)
    rng.shuffle(assignments)

    transaction_specs: list[tuple[Customer, str | None, str, float, datetime]] = []
    for index, leak_type in enumerate(assignments):
        transaction_type = "fx" if leak_type == "stale_fx_rate" else "card_payment"
        transaction_specs.append(
            (
                customers[index % len(customers)],
                leak_type,
                transaction_type,
                round(rng.uniform(100.0, 12_000.0), 2),
                start + timedelta(seconds=rng.randint(0, int((today - start).total_seconds()))),
            )
        )

    # Monthly totals affect fees, so this matches the chronological order that
    # the future batch endpoint must use when it updates the real accumulator.
    transaction_specs.sort(key=lambda item: (item[0].id, item[4]))
    running_volumes: dict[tuple[int, str, date], float] = {}
    transaction_rows: list[Transaction] = []
    anomaly_rows: list[AnomalyFlag] = []

    for transaction_number, (customer, leak_type, transaction_type, amount, occurred_at) in enumerate(
        transaction_specs, start=1
    ):
        fee_type = fee_type_for_transaction(transaction_type)
        month = date(occurred_at.year, occurred_at.month, 1)
        volume_key = (customer.id, fee_type, month)
        volume_before = running_volumes.get(volume_key, 0.0)
        base_rate = 0.018 if transaction_type == "fx" else (
            0.012 if customer.account_tier == "standard" else 0.009
        )
        tier_threshold = None if transaction_type == "fx" else 5_000.0
        tier_rate = None if transaction_type == "fx" else base_rate - 0.003
        expected = calculate_marginal_fee(
            amount, base_rate, tier_threshold, tier_rate, volume_before
        )

        charged = expected
        if leak_type == "expired_waiver":
            charged = 0.0
        elif leak_type == "wrong_tier":
            # This invoice ignores the correctly maintained running total.
            charged = round(amount * base_rate, 2)
        elif leak_type == "stale_fx_rate":
            charged = round(amount * 0.013, 2)
        elif leak_type == "bundling_error":
            charged = 0.0
        elif leak_type == "subaccount_discount_error":
            charged = round(expected * 0.7, 2)
        elif leak_type == "stale_running_total":
            # This invoice uses a corrupted total: zero after a real threshold
            # crossing, or an overstated threshold total before one.
            stale_volume_before = 0.0 if volume_before >= 5_000.0 else 5_000.0
            charged = calculate_marginal_fee(
                amount, base_rate, tier_threshold, tier_rate, volume_before=stale_volume_before
            )

        transaction_rows.append(
            Transaction(
                external_reference=f"synthetic-{transaction_number:06d}",
                customer_id=customer.id,
                amount=amount,
                transaction_type=transaction_type,
                timestamp=occurred_at,
                fee_charged=charged,
                fee_expected=expected,
            )
        )

        if leak_type is not None:
            # The schema stores positive revenue leakage as expected minus charged.
            anomaly_rows.append(
                AnomalyFlag(
                    transaction=transaction_rows[-1],
                    leak_type=leak_type,
                    leak_amount=round(expected - charged, 2),
                    detected_at=today,
                )
            )

        running_volumes[volume_key] = volume_before + amount

    session.add_all(
        CustomerMonthlyVolume(
            customer_id=customer_id,
            fee_type=fee_type,
            month=month,
            cumulative_amount=amount,
        )
        for (customer_id, fee_type, month), amount in running_volumes.items()
    )
    session.add_all(transaction_rows)
    session.add_all(anomaly_rows)
    session.commit()


def print_summary(session: Session) -> None:
    """Print compact, screenshot-friendly evidence that the target split loaded."""
    total_transactions = session.scalar(select(func.count(Transaction.id)))
    total_anomalies = session.scalar(select(func.count(AnomalyFlag.id)))
    print(f"Customers: {session.scalar(select(func.count(Customer.id)))}")
    print(f"Transactions: {total_transactions}")
    print(f"Clean transactions: {total_transactions - total_anomalies}")
    print(f"Leaked transactions: {total_anomalies}")
    print(f"Clean/leak split: {(total_transactions - total_anomalies) / total_transactions:.0%}/"
          f"{total_anomalies / total_transactions:.0%}")
    for leak_type, count in session.execute(
        select(AnomalyFlag.leak_type, func.count(AnomalyFlag.id))
        .group_by(AnomalyFlag.leak_type)
        .order_by(AnomalyFlag.leak_type)
    ):
        print(f"  {leak_type}: {count}")


def main() -> None:
    """Parse options, create tables, and seed the local development database."""
    parser = argparse.ArgumentParser(description="Generate billing anomaly demo data.")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete existing database rows before generating the new dataset.",
    )
    args = parser.parse_args()

    rng = random.Random(RANDOM_SEED)
    with SessionLocal() as session:
        existing_transactions = session.scalar(select(func.count(Transaction.id)))
        if existing_transactions and not args.reset:
            raise SystemExit("Database already has data. Re-run with --reset to replace it.")
        if args.reset:
            reset_data(session)
        customers = create_customers_and_rules(session, rng)
        create_transactions(session, customers, rng)
        print_summary(session)


if __name__ == "__main__":
    main()
