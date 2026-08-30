"""Adopt versioned migrations and exact numeric financial columns.

Revision ID: 20260830_01
Revises:
Create Date: 2026-08-30
"""

from alembic import op
import sqlalchemy as sa

from app.database import Base
import app.models  # noqa: F401  # Register all model tables for fresh databases.


revision = "20260830_01"
down_revision = None
branch_labels = None
depends_on = None


def _alter_money_columns(table_name: str, columns: dict[str, sa.types.TypeEngine]) -> None:
    """Rebuild a SQLite table so existing float-affinity values become NUMERIC."""
    with op.batch_alter_table(table_name, recreate="always") as batch:
        for column_name, target_type in columns.items():
            batch.alter_column(column_name, existing_type=sa.Float(), type_=target_type)


def upgrade() -> None:
    """Create the schema on fresh installs or preserve-and-upgrade legacy SQLite."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    already_has_transactions = inspector.has_table("transactions")

    if not already_has_transactions:
        # Pin this revision to the tables that existed when it was authored.
        # A historical migration must never start creating tables added by a
        # future model change merely because it imports current metadata.
        initial_table_names = (
            "customers",
            "fee_rules",
            "transactions",
            "customer_monthly_volume",
            "anomaly_flags",
            "calculation_lineages",
        )
        Base.metadata.create_all(
            bind=bind,
            tables=[Base.metadata.tables[name] for name in initial_table_names],
        )
        return

    # Legacy databases already gained audit/idempotency columns in earlier
    # learning phases. This migration converts their financial storage exactly
    # without deleting data or inventing a second source of truth.
    _alter_money_columns(
        "fee_rules",
        {
            "rate": sa.Numeric(12, 6),
            "tier_threshold": sa.Numeric(18, 2),
            "tier_rate": sa.Numeric(12, 6),
        },
    )
    _alter_money_columns(
        "transactions",
        {
            "amount": sa.Numeric(18, 2),
            "fee_charged": sa.Numeric(18, 2),
            "fee_expected": sa.Numeric(18, 2),
        },
    )
    _alter_money_columns(
        "customer_monthly_volume", {"cumulative_amount": sa.Numeric(18, 2)}
    )
    _alter_money_columns(
        "anomaly_flags",
        {"leak_amount": sa.Numeric(18, 2), "implied_volume": sa.Numeric(18, 2)},
    )
    if inspector.has_table("calculation_lineages"):
        _alter_money_columns(
            "calculation_lineages",
            {
                "stored_cumulative_volume": sa.Numeric(18, 2),
                "recomputed_cumulative_volume": sa.Numeric(18, 2),
                "fee_expected_from_stored": sa.Numeric(18, 2),
                "fee_expected_recomputed": sa.Numeric(18, 2),
            },
        )
    if inspector.has_table("customers"):
        _alter_money_columns("customers", {"minimum_monthly_floor": sa.Numeric(18, 2)})


def downgrade() -> None:
    """Restore legacy float-affinity columns for local rollback only."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for table_name, columns in (
        ("customers", ("minimum_monthly_floor",)),
        ("calculation_lineages", (
            "stored_cumulative_volume", "recomputed_cumulative_volume",
            "fee_expected_from_stored", "fee_expected_recomputed",
        )),
        ("anomaly_flags", ("leak_amount", "implied_volume")),
        ("customer_monthly_volume", ("cumulative_amount",)),
        ("transactions", ("amount", "fee_charged", "fee_expected")),
        ("fee_rules", ("rate", "tier_threshold", "tier_rate")),
    ):
        if inspector.has_table(table_name):
            _alter_money_columns(table_name, {column: sa.Float() for column in columns})
