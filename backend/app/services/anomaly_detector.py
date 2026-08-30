"""Explainable, rule-based classification of fee discrepancies.

Each classification carries a structured diagnostic payload so that
financial operations teams can trace the root cause without re-querying
the original transaction data.

Supported leak types (in priority order):
  1. stale_running_total     — race condition / dirty volume read
  2. stale_fx_rate           — FX transaction with outdated exchange rate
  3. expired_waiver          — zero fee charged against an expired promo
  4. bundling_error          — zero fee with no waiver (product unbundling)
  5. hierarchy_aggregation_error — sub-account billed standalone, missed
                                   pooled corporate tier discount
  6. wrong_tier              — volume crossed threshold but base rate charged
  7. contract_floor_violation — monthly billing below guaranteed minimum
                                (detected at aggregation time, not per-tx)
  8. subaccount_discount_error — catch-all for remaining discrepancies
"""

import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from app.models import AnomalyFlag, FeeRule, Transaction
from app.services.fee_calculator import calculate_marginal_fee
from app.services.money import money, rate


TOLERANCE = Decimal("0.01")


@dataclass(frozen=True)
class AnomalyDecision:
    """A classification, its revenue impact, and structured audit evidence."""

    leak_type: str
    leak_amount: Decimal
    implied_volume: Decimal | None = None
    diagnostic_details: str | None = None


def _make_diagnostics(**kwargs: object) -> str:
    """Serialize diagnostic evidence as a compact JSON string.

    Using a helper ensures consistent formatting and lets callers pass
    only the fields relevant to their scenario.
    """
    return json.dumps(
        {k: v for k, v in kwargs.items() if v is not None},
        # Diagnostics are presentation evidence.  The authoritative exact
        # values live in NUMERIC lineage columns, while JSON keeps numbers
        # convenient for API and dashboard consumers.
        default=float,
    )


def classify_discrepancy(
    transaction: Transaction,
    rule: FeeRule,
    volume_before: Decimal | int | float,
    expired_waiver_present: bool = False,
    observed_volume_before: Decimal | int | float | None = None,
    fee_expected_from_stored: Decimal | int | float | None = None,
    standalone_volume: Decimal | int | float | None = None,
    rule_is_override: bool = True,
) -> AnomalyDecision | None:
    """Classify a material fee difference using inspectable business heuristics.

    Parameters
    ----------
    transaction:
        The ORM row with fee_expected and fee_charged already set.
    rule:
        The fee rule that was used to compute fee_expected.
    volume_before:
        The correct chronological volume (pooled if hierarchy exists).
    expired_waiver_present:
        True if the customer once had a zero-rate rule that has since expired.
    observed_volume_before:
        When provided, represents a potentially stale volume state that the
        billing system appeared to use.  A mismatch with volume_before
        signals a race condition / dirty-read.
    standalone_volume:
        The customer's own volume, ignoring hierarchy pooling.  Non-None only
        when the customer belongs to a parent-child group.  A difference from
        volume_before signals a hierarchy_aggregation_error.
    rule_is_override:
        True if the resolved rule was a customer-specific override, False if
        it was a global tier template fallback.
    """
    expected_fee = money(transaction.fee_expected)
    charged_fee = money(transaction.fee_charged)
    amount = money(transaction.amount)
    volume_before = money(volume_before)
    observed_volume_before = (
        money(observed_volume_before) if observed_volume_before is not None else None
    )
    standalone_volume = money(standalone_volume) if standalone_volume is not None else None
    difference = money(expected_fee - charged_fee)
    stored_fee_difference = (
        money(expected_fee - money(fee_expected_from_stored))
        if fee_expected_from_stored is not None
        else Decimal("0.00")
    )
    if abs(difference) <= TOLERANCE:
        # A diverged accumulator that produces the same fee is still captured
        # by lineage, but it is not a revenue-impacting anomaly flag.
        return None

    # ------------------------------------------------------------------
    # Scenario 1: Concurrency Race Condition (stale_running_total)
    # The billing system used a volume state that doesn't match the true
    # chronological running total — classic dirty-read under concurrency.
    # ------------------------------------------------------------------
    if (
        observed_volume_before is not None
        and abs(observed_volume_before - volume_before) > TOLERANCE
        # Older direct callers provide only volumes.  The ingestion workflow
        # always supplies the stored-derived fee and therefore uses the
        # stronger, fee-impacting proof.
        and (
            fee_expected_from_stored is None
            or abs(stored_fee_difference) > TOLERANCE
        )
    ):
        return AnomalyDecision(
            leak_type="stale_running_total",
            leak_amount=difference,
            implied_volume=observed_volume_before,
            diagnostic_details=_make_diagnostics(
                correct_chronological_volume=volume_before,
                implied_charged_volume=observed_volume_before,
                fee_expected_recomputed=expected_fee,
                fee_expected_from_stored=fee_expected_from_stored,
                race_condition=True,
                explanation=(
                    "Transaction billed using dirty-read volume state; "
                    f"correct total was ${volume_before:,.2f} but billing "
                    f"system used ${observed_volume_before:,.2f}."
                ),
            ),
        )

    # ------------------------------------------------------------------
    # Scenario 2: Stale FX Rate
    # An FX transaction whose charged fee doesn't match the expected rate.
    # ------------------------------------------------------------------
    if transaction.transaction_type == "fx":
        implied_fx_rate = rate(charged_fee / amount) if amount else Decimal("0")
        expected_fx_rate = rate(rule.rate)
        return AnomalyDecision(
            leak_type="stale_fx_rate",
            leak_amount=difference,
            diagnostic_details=_make_diagnostics(
                official_spot_fx_rate=expected_fx_rate,
                implied_charged_fx_rate=implied_fx_rate,
                explanation=(
                    f"FX fee derived from rate {implied_fx_rate} instead of "
                    f"the contractual rate {expected_fx_rate}."
                ),
            ),
        )

    # ------------------------------------------------------------------
    # Scenario 3: Expired Promotional Fee Waiver
    # Customer charged $0, and there's evidence of an old zero-rate waiver
    # that has passed its effective_to date.
    # ------------------------------------------------------------------
    if charged_fee == 0 and expired_waiver_present:
        return AnomalyDecision(
            leak_type="expired_waiver",
            leak_amount=difference,
            diagnostic_details=_make_diagnostics(
                fee_expected=expected_fee,
                active_rule_rate=rule.rate,
                explanation=(
                    "Zero-fee waiver expired; customer should be billed "
                    f"at the active rule rate of {rule.rate:.4f}."
                ),
            ),
        )

    # ------------------------------------------------------------------
    # Scenario 4: Bundling Error
    # Customer charged $0, but no expired waiver exists — the billing
    # system erroneously applied a bundle discount that was removed.
    # ------------------------------------------------------------------
    if charged_fee == 0:
        return AnomalyDecision(
            leak_type="bundling_error",
            leak_amount=difference,
            diagnostic_details=_make_diagnostics(
                fee_expected=expected_fee,
                explanation=(
                    "Fee charged is $0.00 with no active or expired waiver; "
                    "likely an unbundled product still receiving bundle pricing."
                ),
            ),
        )

    # ------------------------------------------------------------------
    # Scenario 5: Hierarchy Aggregation Error
    # Sub-account billed using its standalone volume instead of the
    # pooled corporate parent volume, missing a tier discount.
    # ------------------------------------------------------------------
    if standalone_volume is not None and abs(standalone_volume - volume_before) > TOLERANCE:
        # The fee that would result from standalone-only volume.
        standalone_fee = calculate_marginal_fee(
            amount, rule.rate, rule.tier_threshold, rule.tier_rate,
            standalone_volume,
        )
        # If the charged fee is close to the standalone fee (not the pooled
        # expected fee), the billing system ignored the hierarchy.
        if abs(charged_fee - standalone_fee) <= TOLERANCE:
            return AnomalyDecision(
                leak_type="hierarchy_aggregation_error",
                leak_amount=difference,
                implied_volume=standalone_volume,
                diagnostic_details=_make_diagnostics(
                    standalone_subaccount_volume=standalone_volume,
                    pooled_corporate_volume=volume_before,
                    standalone_fee=standalone_fee,
                    expected_pooled_fee=expected_fee,
                    explanation=(
                        f"Sub-account billed at standalone volume "
                        f"(${standalone_volume:,.2f}) instead of pooled "
                        f"corporate volume (${volume_before:,.2f}); "
                        f"missed tier discount."
                    ),
                ),
            )

    # ------------------------------------------------------------------
    # Scenario 6: Wrong Tier
    # Volume crossed the threshold, but the billing system charged the
    # full base rate instead of applying the discounted tier rate.
    # ------------------------------------------------------------------
    if (
        rule.tier_threshold is not None
        and volume_before + amount >= money(rule.tier_threshold)
    ):
        return AnomalyDecision(
            leak_type="wrong_tier",
            leak_amount=difference,
            implied_volume=volume_before,
            diagnostic_details=_make_diagnostics(
                tier_threshold=rule.tier_threshold,
                actual_volume=volume_before + amount,
                charged_rate=rate(charged_fee / amount) if amount else Decimal("0"),
                expected_tier_rate=rule.tier_rate,
                explanation=(
                    f"Volume crossed ${rule.tier_threshold:,.2f} threshold "
                    f"but base rate was charged instead of tier rate "
                    f"{rule.tier_rate}."
                ),
            ),
        )

    # ------------------------------------------------------------------
    # Catch-all: Subaccount Discount Error
    # The fee differs from expected but none of the specific scenarios
    # above matched — classified as a generic discount miscalculation.
    # ------------------------------------------------------------------
    return AnomalyDecision(
        leak_type="subaccount_discount_error",
        leak_amount=difference,
        diagnostic_details=_make_diagnostics(
            fee_expected=expected_fee,
            fee_charged=charged_fee,
            volume_before=volume_before,
            rule_id=rule.id,
            rule_is_override=rule_is_override,
            explanation=(
                f"Fee mismatch of ${abs(difference):,.2f} does not match "
                "any specific leakage pattern; review rule configuration."
            ),
        ),
    )


def build_anomaly_flag(
    transaction: Transaction, decision: AnomalyDecision,
) -> AnomalyFlag:
    """Create an ORM record only after the detector has made a decision.

    The diagnostic payload is persisted alongside the flag so that every
    anomaly is self-documenting — no need to re-derive the root cause later.
    """
    return AnomalyFlag(
        transaction=transaction,
        leak_type=decision.leak_type,
        leak_amount=decision.leak_amount,
        detected_at=datetime.now(),
        implied_volume=decision.implied_volume,
        diagnostic_details=decision.diagnostic_details,
    )
