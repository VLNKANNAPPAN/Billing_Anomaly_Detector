"""Add durable markers for late-arrival reconciliation.

Revision ID: 20260830_02
Revises: 20260830_01
Create Date: 2026-08-30
"""

from alembic import op
import sqlalchemy as sa


revision = "20260830_02"
down_revision = "20260830_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the work queue without touching transaction history."""
    op.create_table(
        "pending_reconciliations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("fee_type", sa.String(length=50), nullable=False),
        sa.Column("month", sa.Date(), nullable=False),
        sa.Column("earliest_affected_at", sa.DateTime(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("customer_id", "fee_type", "month", name="uq_reconciliation_scope"),
    )


def downgrade() -> None:
    """Remove only operational queue state; financial history remains intact."""
    op.drop_table("pending_reconciliations")
