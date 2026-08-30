"""Unit tests for pure fee and anomaly business rules.

Tests cover:
  - Marginal fee calculation (threshold straddling, post-threshold volume)
  - All anomaly classification scenarios:
    1. stale_running_total  (concurrency race condition)
    2. stale_fx_rate        (FX rate desync)
    3. expired_waiver       (zero fee + expired promo)
    4. bundling_error       (zero fee, no waiver)
    5. hierarchy_aggregation_error (standalone vs pooled volume mismatch)
    6. wrong_tier           (threshold crossed, base rate still charged)
    7. subaccount_discount_error (catch-all)
    8. No anomaly when difference is within tolerance
"""

import json
import unittest

from app.models import FeeRule, Transaction
from app.services.anomaly_detector import classify_discrepancy
from app.services.fee_calculator import calculate_marginal_fee


class MarginalFeeTests(unittest.TestCase):
    """Confirm volume thresholds are calculated per bracket, not as a cliff."""

    def test_transaction_straddling_threshold_is_split_between_rates(self) -> None:
        fee = calculate_marginal_fee(
            amount=3_000.0,
            rate=0.01,
            tier_threshold=5_000.0,
            tier_rate=0.005,
            volume_before=4_000.0,
        )
        # $1,000 at 1% plus $2,000 at 0.5%.
        self.assertEqual(fee, 20.0)

    def test_all_volume_after_threshold_uses_tier_rate(self) -> None:
        fee = calculate_marginal_fee(2_000.0, 0.01, 5_000.0, 0.005, 6_000.0)
        self.assertEqual(fee, 10.0)

    def test_flat_rate_when_no_tier(self) -> None:
        fee = calculate_marginal_fee(1_000.0, 0.018, None, None, 0.0)
        self.assertEqual(fee, 18.0)

    def test_entirely_below_threshold(self) -> None:
        fee = calculate_marginal_fee(1_000.0, 0.01, 5_000.0, 0.005, 0.0)
        # Entire amount is below threshold — base rate only.
        self.assertEqual(fee, 10.0)


class AnomalyClassificationTests(unittest.TestCase):
    """Ensure accumulator defects remain distinct from ordinary tier mistakes."""

    def setUp(self) -> None:
        self.rule = FeeRule(rate=0.01, tier_threshold=5_000.0, tier_rate=0.005)
        self.transaction = Transaction(
            amount=1_000.0,
            transaction_type="card_payment",
            fee_expected=5.0,
            fee_charged=10.0,
        )

    def test_tier_mistake_uses_wrong_tier_when_volume_is_correct(self) -> None:
        decision = classify_discrepancy(self.transaction, self.rule, volume_before=5_500.0)
        self.assertEqual(decision.leak_type, "wrong_tier")

    def test_mismatched_accumulator_uses_stale_running_total(self) -> None:
        decision = classify_discrepancy(
            self.transaction,
            self.rule,
            volume_before=5_500.0,
            observed_volume_before=0.0,
        )
        self.assertEqual(decision.leak_type, "stale_running_total")


class StaleRunningTotalTests(unittest.TestCase):
    """Scenario 1: Concurrency race condition — dirty volume read."""

    def test_detects_race_condition_with_diagnostic_details(self) -> None:
        tx = Transaction(
            amount=4_000.0,
            transaction_type="card_payment",
            fee_expected=36.0,  # Expected at pooled volume of $6,000
            fee_charged=45.0,   # Charged at stale volume of $2,000
        )
        rule = FeeRule(rate=0.012, tier_threshold=5_000.0, tier_rate=0.009)

        decision = classify_discrepancy(
            tx, rule, volume_before=6_000.0, observed_volume_before=2_000.0,
        )
        self.assertIsNotNone(decision)
        self.assertEqual(decision.leak_type, "stale_running_total")
        self.assertEqual(decision.implied_volume, 2_000.0)

        # Verify diagnostic JSON is parseable and contains expected fields.
        details = json.loads(decision.diagnostic_details)
        self.assertEqual(details["correct_chronological_volume"], 6_000.0)
        self.assertEqual(details["implied_charged_volume"], 2_000.0)
        self.assertTrue(details["race_condition"])


class StaleFxRateTests(unittest.TestCase):
    """Scenario 2: FX transaction with outdated exchange rate."""

    def test_detects_stale_fx_rate(self) -> None:
        tx = Transaction(
            amount=10_000.0,
            transaction_type="fx",
            fee_expected=180.0,   # Expected at 1.8%
            fee_charged=130.0,    # Charged at stale 1.3%
        )
        rule = FeeRule(rate=0.018, tier_threshold=None, tier_rate=None)

        decision = classify_discrepancy(tx, rule, volume_before=0.0)
        self.assertIsNotNone(decision)
        self.assertEqual(decision.leak_type, "stale_fx_rate")
        self.assertEqual(decision.leak_amount, 50.0)

        details = json.loads(decision.diagnostic_details)
        self.assertEqual(details["official_spot_fx_rate"], 0.018)
        self.assertEqual(details["implied_charged_fx_rate"], 0.013)


class ExpiredWaiverTests(unittest.TestCase):
    """Scenario 3: Zero fee charged with an expired promotional waiver."""

    def test_detects_expired_waiver(self) -> None:
        tx = Transaction(
            amount=5_000.0,
            transaction_type="card_payment",
            fee_expected=60.0,   # Expected at active rate
            fee_charged=0.0,     # Charged nothing (expired promo)
        )
        rule = FeeRule(rate=0.012, tier_threshold=5_000.0, tier_rate=0.009)

        decision = classify_discrepancy(
            tx, rule, volume_before=0.0, expired_waiver_present=True,
        )
        self.assertIsNotNone(decision)
        self.assertEqual(decision.leak_type, "expired_waiver")
        self.assertEqual(decision.leak_amount, 60.0)


class BundlingErrorTests(unittest.TestCase):
    """Scenario 4: Zero fee with no waiver — product unbundling error."""

    def test_detects_bundling_error(self) -> None:
        tx = Transaction(
            amount=2_000.0,
            transaction_type="card_payment",
            fee_expected=24.0,
            fee_charged=0.0,
        )
        rule = FeeRule(rate=0.012, tier_threshold=None, tier_rate=None)

        decision = classify_discrepancy(
            tx, rule, volume_before=0.0, expired_waiver_present=False,
        )
        self.assertIsNotNone(decision)
        self.assertEqual(decision.leak_type, "bundling_error")
        self.assertEqual(decision.leak_amount, 24.0)


class HierarchyAggregationTests(unittest.TestCase):
    """Scenario 5: Sub-account billed standalone, missed pooled tier."""

    def test_detects_hierarchy_error_when_standalone_fee_matches_charged(self) -> None:
        # Sub-account has $800 standalone volume, but pooled is $12,000.
        # Tier threshold is $5,000 at base 1.2%, tier 0.9%.
        # Transaction amount = $1,000.
        # Standalone fee: $800 + $1,000 = $1,800 total, all below $5k => $1,000 * 0.012 = $12.00
        # Pooled fee: $12,000 pre-volume, all above $5k => $1,000 * 0.009 = $9.00
        tx = Transaction(
            amount=1_000.0,
            transaction_type="card_payment",
            fee_expected=9.0,    # Pooled calculation
            fee_charged=12.0,    # Standalone calculation (what billing system did)
        )
        rule = FeeRule(rate=0.012, tier_threshold=5_000.0, tier_rate=0.009)

        decision = classify_discrepancy(
            tx, rule, volume_before=12_000.0, standalone_volume=800.0,
        )
        self.assertIsNotNone(decision)
        self.assertEqual(decision.leak_type, "hierarchy_aggregation_error")
        self.assertEqual(decision.implied_volume, 800.0)
        self.assertEqual(decision.leak_amount, -3.0)

        details = json.loads(decision.diagnostic_details)
        self.assertEqual(details["standalone_subaccount_volume"], 800.0)
        self.assertEqual(details["pooled_corporate_volume"], 12_000.0)


class WrongTierTests(unittest.TestCase):
    """Scenario 6: Threshold crossed but base rate charged."""

    def test_detects_wrong_tier_with_diagnostic(self) -> None:
        # Volume $4,500, transaction $1,000 => crosses $5,000 threshold.
        # Expected: $500 at 1.2% + $500 at 0.9% = $10.50
        # Charged: $1,000 at 1.2% = $12.00
        tx = Transaction(
            amount=1_000.0,
            transaction_type="card_payment",
            fee_expected=10.50,
            fee_charged=12.0,
        )
        rule = FeeRule(rate=0.012, tier_threshold=5_000.0, tier_rate=0.009)

        decision = classify_discrepancy(tx, rule, volume_before=4_500.0)
        self.assertIsNotNone(decision)
        self.assertEqual(decision.leak_type, "wrong_tier")

        details = json.loads(decision.diagnostic_details)
        self.assertEqual(details["tier_threshold"], 5_000.0)
        self.assertEqual(details["actual_volume"], 5_500.0)


class CatchAllTests(unittest.TestCase):
    """Scenario 7: Subaccount discount error — catch-all classification."""

    def test_catch_all_when_no_specific_scenario_matches(self) -> None:
        # Non-FX, non-zero charged, below threshold, no hierarchy.
        tx = Transaction(
            amount=1_000.0,
            transaction_type="card_payment",
            fee_expected=12.0,
            fee_charged=8.40,   # 70% of expected — generic mismatch
        )
        rule = FeeRule(rate=0.012, tier_threshold=5_000.0, tier_rate=0.009)

        decision = classify_discrepancy(tx, rule, volume_before=0.0)
        self.assertIsNotNone(decision)
        self.assertEqual(decision.leak_type, "subaccount_discount_error")


class NoAnomalyTests(unittest.TestCase):
    """Verify that small rounding differences are correctly ignored."""

    def test_within_tolerance_returns_none(self) -> None:
        tx = Transaction(
            amount=1_000.0,
            transaction_type="card_payment",
            fee_expected=12.00,
            fee_charged=12.005,  # Within $0.01 tolerance
        )
        rule = FeeRule(rate=0.012, tier_threshold=None, tier_rate=None)

        decision = classify_discrepancy(tx, rule, volume_before=0.0)
        self.assertIsNone(decision)

    def test_exact_match_returns_none(self) -> None:
        tx = Transaction(
            amount=1_000.0,
            transaction_type="card_payment",
            fee_expected=12.0,
            fee_charged=12.0,
        )
        rule = FeeRule(rate=0.012, tier_threshold=None, tier_rate=None)

        decision = classify_discrepancy(tx, rule, volume_before=0.0)
        self.assertIsNone(decision)


if __name__ == "__main__":
    unittest.main()
