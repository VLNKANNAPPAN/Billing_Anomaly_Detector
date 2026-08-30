"""Small, explicit fixed-precision helpers for financial calculations."""

from decimal import Decimal, ROUND_HALF_UP


MONEY_QUANTUM = Decimal("0.01")
RATE_QUANTUM = Decimal("0.000001")


def decimal_value(value: Decimal | int | float | str) -> Decimal:
    """Convert through text so binary float representation never leaks in."""
    return value if isinstance(value, Decimal) else Decimal(str(value))


def money(value: Decimal | int | float | str) -> Decimal:
    """Round monetary results to cents using the conventional half-up rule."""
    return decimal_value(value).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def rate(value: Decimal | int | float | str) -> Decimal:
    """Normalize fee rates to six fractional places for stable audit evidence."""
    return decimal_value(value).quantize(RATE_QUANTUM, rounding=ROUND_HALF_UP)
