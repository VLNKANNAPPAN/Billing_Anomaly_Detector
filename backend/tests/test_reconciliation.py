"""Tests for late-arrival detection without mutating historical calculations."""

from datetime import date, datetime
from decimal import Decimal
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Customer, FeeRule, PendingReconciliation, Transaction
from app.services.reconciliation import mark_reconciliation_if_late, run_reconciliation


class LateArrivalTests(unittest.TestCase):
    """Exercise the queue using an isolated in-memory transaction ledger."""

    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.customer = Customer(
            name="Late Arrival Customer",
            industry="technology",
            contract_start_date=date(2025, 1, 1),
            account_tier="standard",
        )
        self.session.add(self.customer)
        self.session.flush()
        self.session.add(
            Transaction(
                external_reference="already-ingested-later-event",
                customer_id=self.customer.id,
                amount=Decimal("100.00"),
                transaction_type="card_payment",
                timestamp=datetime(2026, 8, 20, 10, 0),
                fee_charged=Decimal("1.00"),
                fee_expected=Decimal("1.00"),
            )
        )
        self.session.add(
            FeeRule(
                customer_id=self.customer.id,
                fee_type="transaction_fee",
                rate=Decimal("0.010000"),
                tier_threshold=Decimal("500.00"),
                tier_rate=Decimal("0.005000"),
                effective_from=date(2025, 1, 1),
                effective_to=None,
                rule_version_id="test-late-arrival-tier-rule",
            )
        )
        self.session.commit()

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def test_late_arrival_creates_one_pending_month_marker(self) -> None:
        marker = mark_reconciliation_if_late(
            self.session,
            self.customer.id,
            "transaction_fee",
            datetime(2026, 8, 10, 10, 0),
        )
        self.session.commit()

        self.assertIsNotNone(marker)
        self.assertEqual(marker.status, "pending")
        self.assertEqual(marker.earliest_affected_at, datetime(2026, 8, 10, 10, 0))

        later_marker = mark_reconciliation_if_late(
            self.session,
            self.customer.id,
            "transaction_fee",
            datetime(2026, 8, 15, 10, 0),
        )
        self.session.commit()
        self.assertEqual(marker.id, later_marker.id)
        self.assertEqual(
            self.session.query(PendingReconciliation).count(), 1
        )

    def test_run_creates_adjustment_without_mutating_original_transaction(self) -> None:
        self.session.add(
            Transaction(
                external_reference="late-tier-crossing-event",
                customer_id=self.customer.id,
                amount=Decimal("500.00"),
                transaction_type="card_payment",
                timestamp=datetime(2026, 8, 10, 10, 0),
                fee_charged=Decimal("5.00"),
                fee_expected=Decimal("5.00"),
            )
        )
        self.session.flush()
        marker = mark_reconciliation_if_late(
            self.session,
            self.customer.id,
            "transaction_fee",
            datetime(2026, 8, 10, 10, 0),
        )
        self.assertIsNotNone(marker)

        reconciliation_run = run_reconciliation(self.session, marker)
        self.session.commit()

        self.assertEqual(reconciliation_run.status, "completed")
        self.assertEqual(reconciliation_run.transactions_recalculated, 2)
        self.assertEqual(marker.status, "reconciled")
        adjustment = reconciliation_run.adjustment_candidates[0]
        self.assertEqual(adjustment.original_fee_expected, Decimal("1.00"))
        self.assertEqual(adjustment.recalculated_fee_expected, Decimal("0.50"))
        self.assertEqual(adjustment.adjustment_amount, Decimal("-0.50"))
        original_later_transaction = self.session.get(Transaction, 1)
        self.assertEqual(original_later_transaction.fee_expected, Decimal("1.00"))


if __name__ == "__main__":
    unittest.main()
