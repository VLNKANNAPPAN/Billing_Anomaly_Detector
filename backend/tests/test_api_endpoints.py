"""Integration tests for FastAPI endpoints, caching behavior, and transaction processing."""

from datetime import datetime, timezone
import unittest

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import Base, SessionLocal, engine
from app.main import app
from app.models import AnomalyFlag, CalculationLineage, Customer, CustomerMonthlyVolume, FeeRule, Transaction


class ApiEndpointTests(unittest.TestCase):
    """Verify all REST API endpoints with an isolated SQLite session."""

    @classmethod
    def setUpClass(cls) -> None:
        # Unit tests use an isolated test fixture schema. Production databases
        # are created and upgraded exclusively by Alembic migrations.
        Base.metadata.create_all(bind=engine)
        cls.client = TestClient(app)

    def setUp(self) -> None:
        with SessionLocal() as session:
            # This suite uses a shared local SQLite file, so reset only its
            # dedicated fixture rows.  A clean ledger is essential now that
            # tests verify stored and recomputed monthly volume independently.
            transaction_ids = select(Transaction.id).where(Transaction.customer_id == 999)
            session.query(CalculationLineage).filter(
                CalculationLineage.transaction_id.in_(transaction_ids)
            ).delete(synchronize_session=False)
            session.query(AnomalyFlag).filter(
                AnomalyFlag.transaction_id.in_(transaction_ids)
            ).delete(synchronize_session=False)
            session.query(Transaction).filter_by(customer_id=999).delete()
            session.query(CustomerMonthlyVolume).filter_by(customer_id=999).delete()
            session.query(FeeRule).filter_by(customer_id=999).delete()
            session.query(Customer).filter_by(id=999).delete()
            session.commit()

            cust = Customer(
                id=999,
                name="Test Customer 999",
                industry="technology",
                contract_start_date=datetime.now(timezone.utc).date(),
                account_tier="standard",
            )
            session.add(cust)
            session.add(
                FeeRule(
                    id=999,
                    customer_id=999,
                    fee_type="transaction_fee",
                    rate=0.01,
                    tier_threshold=5000.0,
                    tier_rate=0.005,
                    effective_from=datetime.now(timezone.utc).date(),
                    effective_to=None,
                )
            )
            session.commit()

    def test_health_check(self) -> None:
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_customer_detail(self) -> None:
        response = self.client.get("/customers/999")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["id"], 999)
        self.assertEqual(data["account_tier"], "standard")

    def test_customer_not_found(self) -> None:
        response = self.client.get("/customers/999999")
        self.assertEqual(response.status_code, 404)

    def test_customer_metrics_headers(self) -> None:
        response = self.client.get("/customers/999/metrics?use_cache=false")
        self.assertEqual(response.status_code, 200)
        self.assertIn("x-cache-hit", response.headers)
        self.assertIn("x-query-time-ms", response.headers)
        self.assertEqual(response.headers["x-cache-hit"], "false")

    def test_customer_anomalies_headers(self) -> None:
        response = self.client.get("/customers/999/anomalies?use_cache=false")
        self.assertEqual(response.status_code, 200)
        self.assertIn("x-cache-hit", response.headers)
        self.assertIn("x-query-time-ms", response.headers)

    def test_fee_rules_endpoint(self) -> None:
        response = self.client.get("/fee-rules/999?use_cache=false")
        self.assertEqual(response.status_code, 200)
        rules = response.json()
        self.assertGreaterEqual(len(rules), 1)
        self.assertEqual(rules[0]["fee_type"], "transaction_fee")

    def test_create_single_transaction_and_detect_anomaly(self) -> None:
        payload = {
            "customer_id": 999,
            "external_reference": "single-transaction-001",
            "amount": 2000.0,
            "transaction_type": "card_payment",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "fee_charged": 0.0,  # Leaked: charged $0 with no waiver -> bundling_error
        }
        response = self.client.post("/transactions", json=payload)
        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(data["customer_id"], 999)
        self.assertEqual(data["fee_expected"], "20.00")
        self.assertIsNotNone(data["anomaly_flag"])
        self.assertEqual(data["anomaly_flag"]["leak_type"], "bundling_error")
        with SessionLocal() as session:
            lineage = session.scalar(
                select(CalculationLineage).where(
                    CalculationLineage.transaction_id == data["id"]
                )
            )
            self.assertIsNotNone(lineage)
            self.assertEqual(lineage.fee_expected_recomputed, 20.0)
            self.assertEqual(lineage.fee_expected_from_stored, 20.0)
            self.assertEqual(len(lineage.calculation_hash), 64)

        lineage_response = self.client.get(f"/transactions/{data['id']}/lineage")
        self.assertEqual(lineage_response.status_code, 200)
        self.assertEqual(lineage_response.json()["transaction_id"], data["id"])

    def test_retry_returns_existing_transaction_without_double_counting(self) -> None:
        payload = {
            "customer_id": 999,
            "external_reference": "retry-safe-001",
            "amount": 1000.0,
            "transaction_type": "card_payment",
            "timestamp": "2026-08-30T11:00:00+00:00",
            "fee_charged": 10.0,
        }
        first = self.client.post("/transactions", json=payload)
        retry = self.client.post("/transactions", json=payload)
        self.assertEqual(first.status_code, 201)
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(first.json()["id"], retry.json()["id"])

        conflicting_retry = self.client.post(
            "/transactions", json={**payload, "amount": 1001.0}
        )
        self.assertEqual(conflicting_retry.status_code, 409)

    def test_rejects_timestamp_without_timezone(self) -> None:
        response = self.client.post(
            "/transactions",
            json={
                "customer_id": 999,
                "external_reference": "naive-timestamp-001",
                "amount": 1000.0,
                "transaction_type": "card_payment",
                "timestamp": "2026-08-30T11:00:00",
                "fee_charged": 10.0,
            },
        )
        self.assertEqual(response.status_code, 422)

    def test_create_transaction_batch(self) -> None:
        payload = [
            {
                "customer_id": 999,
                "external_reference": "batch-transaction-001",
                "amount": 1000.0,
                "transaction_type": "card_payment",
                "timestamp": "2026-08-30T11:00:00+00:00",
                "fee_charged": 10.0,
            },
            {
                "customer_id": 999,
                "external_reference": "batch-transaction-002",
                "amount": 1500.0,
                "transaction_type": "card_payment",
                "timestamp": "2026-08-30T11:05:00+00:00",
                "fee_charged": 0.0,
            },
        ]
        response = self.client.post("/transactions/batch", json=payload)
        self.assertEqual(response.status_code, 201)
        summary = response.json()
        self.assertEqual(summary["count_created"], 2)
        self.assertEqual(summary["count_flagged"], 1)
        self.assertEqual(summary["count_duplicates"], 0)

    def test_delete_cache_key(self) -> None:
        response = self.client.delete("/cache/customer:999:metrics")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["key"], "customer:999:metrics")


if __name__ == "__main__":
    unittest.main()
