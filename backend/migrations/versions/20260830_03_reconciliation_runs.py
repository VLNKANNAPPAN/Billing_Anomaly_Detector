"""Add immutable reconciliation runs and adjustment candidates.

Revision ID: 20260830_03
Revises: 20260830_02
Create Date: 2026-08-30
"""

from alembic import op
import sqlalchemy as sa


revision = "20260830_03"
down_revision = "20260830_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reconciliation_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("pending_reconciliation_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("transactions_recalculated", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["pending_reconciliation_id"], ["pending_reconciliations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "adjustment_candidates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("reconciliation_run_id", sa.Integer(), nullable=False),
        sa.Column("transaction_id", sa.Integer(), nullable=False),
        sa.Column("original_fee_expected", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("recalculated_fee_expected", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("adjustment_amount", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("reason", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(["reconciliation_run_id"], ["reconciliation_runs.id"]),
        sa.ForeignKeyConstraint(["transaction_id"], ["transactions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("reconciliation_run_id", "transaction_id", name="uq_adjustment_run_transaction"),
    )


def downgrade() -> None:
    op.drop_table("adjustment_candidates")
    op.drop_table("reconciliation_runs")
