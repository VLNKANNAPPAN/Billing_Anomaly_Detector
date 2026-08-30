"""Schemas for transaction creation and read responses."""

from datetime import UTC, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from app.schemas.anomaly import AnomalyRead


class TransactionCreate(BaseModel):
    """Input received from a billing source; expected fee is server-calculated."""

    customer_id: int = Field(gt=0)
    external_reference: str = Field(min_length=1, max_length=100)
    amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2)
    transaction_type: str = Field(min_length=1, max_length=50)
    timestamp: datetime
    fee_charged: Decimal = Field(ge=0, max_digits=18, decimal_places=2)

    @field_validator("timestamp")
    @classmethod
    def normalize_timestamp_to_utc(cls, value: datetime) -> datetime:
        """Require an explicit offset, then persist the canonical UTC instant.

        SQLite does not preserve timezone offsets reliably.  Storing a naive
        UTC value gives it unambiguous semantics while response serialization
        restores the explicit ``+00:00`` marker to API consumers.
        """
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include a timezone offset, for example +00:00.")
        return value.astimezone(UTC).replace(tzinfo=None)


class TransactionRead(BaseModel):
    """Transaction details plus an optional anomaly flag."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    external_reference: str
    customer_id: int
    amount: Decimal
    transaction_type: str
    timestamp: datetime
    fee_charged: Decimal
    fee_expected: Decimal
    anomaly_flag: AnomalyRead | None = None

    @field_serializer("timestamp", when_used="json")
    def serialize_utc_timestamp(self, value: datetime) -> str:
        """Make the UTC contract visible even though SQLite stores it naive."""
        return value.replace(tzinfo=UTC).isoformat()

    @field_serializer("amount", "fee_charged", "fee_expected", when_used="json")
    def serialize_money(self, value: Decimal) -> str:
        """Return lossless fixed-point values instead of JSON binary floats."""
        return f"{value:.2f}"


class TransactionBatchSummary(BaseModel):
    """Compact result for a batch upload."""

    count_created: int
    count_flagged: int
    count_duplicates: int


class TransactionPage(BaseModel):
    """One offset-based page of customer transactions."""

    total: int
    skip: int
    limit: int
    items: list[TransactionRead]
