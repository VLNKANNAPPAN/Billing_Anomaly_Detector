"""Tests for immutable rule history and dual-volume calculation evidence."""

from datetime import date, datetime, timedelta
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Customer, FeeRule, Transaction
from app.services.fee_calculator import calculate_expected_fee
from app.services.rule_versions import FeeRuleVersionInput, create_fee_rule_version


class AuditFoundationTests(unittest.TestCase):
    """Use an isolated in-memory ledger so tests never modify demo data."""

    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.customer = Customer(
            name="Audit Customer",
            industry="technology",
            contract_start_date=date(2025, 1, 1),
            account_tier="standard",
        )
        self.session.add(self.customer)
        self.session.flush()
        self.rule = FeeRule(
            customer_id=self.customer.id,
            fee_type="transaction_fee",
            rate=0.01,
            tier_threshold=5_000.0,
            tier_rate=0.005,
            effective_from=date(2025, 1, 1),
        )
        self.session.add(self.rule)
        self.session.commit()

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def test_recomputed_volume_uses_ledger_not_stored_accumulator(self) -> None:
        timestamp = datetime(2026, 8, 15, 9, 0)
        self.session.add(
            Transaction(
                external_reference="audit-ledger-001",
                customer_id=self.customer.id,
                amount=4_800.0,
                transaction_type="card_payment",
                timestamp=timestamp,
                fee_charged=48.0,
                fee_expected=48.0,
            )
        )
        self.session.commit()

        calculation = calculate_expected_fee(
            self.session,
            self.customer.id,
            "card_payment",
            1_000.0,
            timestamp + timedelta(minutes=1),
        )

        self.assertEqual(calculation.recomputed_volume_before, 4_800.0)
        self.assertEqual(calculation.stored_volume_before, 0.0)
        self.assertEqual(calculation.fee_expected_recomputed, 6.0)
        self.assertEqual(calculation.fee_expected_from_stored, 10.0)

    def test_successor_rule_is_inserted_without_editing_predecessor(self) -> None:
        successor = create_fee_rule_version(
            self.session,
            FeeRuleVersionInput(
                customer_id=self.customer.id,
                account_tier=None,
                fee_type="transaction_fee",
                rate=0.008,
                tier_threshold=5_000.0,
                tier_rate=0.004,
                effective_from=date(2026, 1, 1),
                supersedes_rule_version_id=self.rule.rule_version_id,
            ),
        )
        self.session.commit()
        self.session.refresh(self.rule)

        self.assertNotEqual(successor.rule_version_id, self.rule.rule_version_id)
        self.assertEqual(successor.supersedes_rule_version_id, self.rule.rule_version_id)
        self.assertIsNone(self.rule.effective_to)


if __name__ == "__main__":
    unittest.main()
