# Billing Anomaly Agent

Learning project for detecting billing revenue leakage in bank transactions.

## What this service protects

The API calculates expected transaction and FX fees from effective-dated,
versioned rules and compares them with the fee that was actually charged. It
is designed to make a suspected revenue leak explainable: every new
calculation records the rule, input volumes, rates, and calculation hash used
to reach its result.

The current implementation covers the core control loop:

1. ingest an idempotent, timestamped ledger event;
2. calculate a marginal fee from that calendar month's cumulative volume;
3. persist anomaly evidence and immutable calculation lineage; and
4. queue late-arriving events for a separate, reviewable reconciliation run.

## Current architecture

| Concern | Current implementation |
| --- | --- |
| API | FastAPI with OpenAPI documentation at `/docs` |
| Persistence | SQLAlchemy with SQLite for local development; Alembic migrations for schema evolution |
| Money | Fixed-point `NUMERIC(18,2)` amounts and six-decimal rates; money is returned as JSON strings |
| Rule control | Append-only, effective-dated fee-rule versions |
| Tier pricing | Per customer, fee type, and calendar month; marginal brackets rather than cliff pricing |
| Auditability | Calculation lineage, rule version, source/stored volume comparison, and SHA-256 calculation hash |
| Late data | One queued reconciliation scope per customer, fee type, and month; proposed adjustments are immutable artifacts |
| Performance | Optional Redis cache for read-heavy customer endpoints |

## Phase 1: run the API

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload
```

Visit `http://127.0.0.1:8000/health`; it returns `{"status":"ok"}`.
FastAPI documentation is available at `http://127.0.0.1:8000/docs`.

## Phase 3: generate demo data

```powershell
cd backend
.\.venv\Scripts\alembic.exe upgrade head
.\.venv\Scripts\python.exe -m scripts.generate_synthetic_data --reset
```

`--reset` is required to deliberately replace an existing local dataset.

## Phase 4: billing rules

Tiered transaction fees use cumulative customer volume per fee type and calendar
month. The stored `customer_monthly_volume` accumulator resets on the first of
each month. Pricing is marginal: a transaction that crosses the threshold is
split between the base and tier rates.

```powershell
cd backend
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

Batch ingestion will process each customer's transactions chronologically so it
updates this accumulator correctly.

## Phase 5: core API

Run the server with `uvicorn app.main:app --reload` from `backend`, then use
the interactive API documentation at `http://127.0.0.1:8000/docs`.

- `POST /transactions` — calculate and store one transaction and its flag.
- `POST /transactions/batch` — sort and process a JSON array of transactions.
- `GET /customers/{id}` — customer contract details.
- `GET /customers/{id}/transactions` — paginated transaction history, with
  optional `start_date`, `end_date`, `skip`, and `limit` query parameters.
- `GET /customers/{id}/anomalies` and `GET /customers/{id}/metrics` — direct
  database reads for now.
- `GET /fee-rules/{customer_id}` — rules active today.

## Phase 6: Redis cache

`GET /customers/{id}/metrics`, `GET /customers/{id}/anomalies`, and
`GET /fee-rules/{customer_id}` accept `use_cache=true|false` (default `true`).
Their responses always include `X-Cache-Hit` and `X-Query-Time-Ms` headers.

- Metrics: 30-minute TTL
- Anomalies: 10-minute TTL
- Fee rules: one-hour TTL

Transaction ingestion invalidates that customer's cached metrics and anomalies.
For testing, `DELETE /cache/{key}` removes one explicit key, such as
`customer:1:metrics`.

To run Redis locally with Docker:

```powershell
docker run --name billing-anomaly-redis -d -p 6379:6379 redis:7-alpine
```

Set `REDIS_URL` in `.env` if your Redis server is elsewhere.

## Phase 7: benchmark cache performance

With both the API and Redis running, benchmark 20 customer metrics/anomaly
paths ten times each in both modes:

```powershell
cd backend
.\.venv\Scripts\python.exe -m scripts.benchmark_cache
```

The script prints average client and server query times, cache-hit rate, and
the cached speedup factor in a screenshot-friendly table.

Latest local run (100 customers, SQLite, Redis on localhost):

```text
Mode       Requests    Avg client ms  Avg DB/query ms  Cache hit rate
uncached        200            30.65           11.20           0.0%
cached          200            26.33            5.95          90.0%

Speedup (uncached / cached): 1.16x
```

## Audit foundation: versioned rules and calculation lineage

Fee-rule changes are append-only. Every new `fee_rules` row receives a
`rule_version_id`; a replacement points to the version it supersedes instead
of changing the historical row. Rule resolution selects the latest rule that
was effective on the transaction date.

For each newly ingested transaction, `calculation_lineages` records:

- the immutable rule version used;
- the operational stored monthly volume and the independently recomputed
  ledger volume;
- the fee each volume would produce;
- FX source/timestamp when applicable; and
- a deterministic SHA-256 calculation hash.

The recomputed ledger volume is the source of truth for `fee_expected`.
When the stored-derived fee differs from it, the detector can prove a
`stale_running_total`; when the two calculated fees agree but the customer was
charged incorrectly, it can classify a pricing issue such as `wrong_tier`.

Existing historical transactions remain unchanged by the additive SQLite
upgrade. Their lineage can be backfilled as a later, explicit reconciliation
step; all new transaction API inserts create lineage immediately.

## Ingestion hardening

Every `POST /transactions` and `POST /transactions/batch` item now requires:

- `external_reference`: the billing source's stable transaction ID; and
- a timezone-aware `timestamp`, for example `2026-08-30T11:00:00+00:00`.

The service normalizes timestamps to UTC. Replaying the exact same external
reference returns the existing transaction with HTTP 200 and does not update
monthly volume a second time. Reusing it with changed transaction data returns
HTTP 409. Batch results include `count_duplicates`.

Use `GET /transactions/{transaction_id}/lineage` to inspect the stored audit
evidence for a newly ingested transaction.

## Schema migrations and exact money

Schema changes are applied with Alembic, never on API startup:

```powershell
cd backend
.\.venv\Scripts\alembic.exe upgrade head
```

Financial amounts and monthly volumes use fixed two-decimal `NUMERIC` values;
rates use six decimals. The transaction API returns money as strings such as
`"20.00"`, preserving value exactly across the JSON boundary.

## Late-arrival reconciliation queue

Transactions are accepted provisionally even when their timestamp is earlier
than an event already ingested for the same customer, fee type, and calendar
month. The original transaction and its lineage remain immutable. Instead, the
service creates or updates one pending reconciliation marker for that period.

Use `GET /reconciliations/pending` to view affected periods. This makes the
control visible without silently changing historical fees.

Run a queued period with `POST /reconciliations/{marker_id}/run`. The service
replays that month's relevant transactions in timestamp and ID order using the
rule effective at each transaction. It writes an immutable
`reconciliation_runs` record and one `adjustment_candidates` record for each
material difference; it never overwrites the original transaction fee or
lineage. Retrieve the saved result with
`GET /reconciliations/runs/{run_id}`.

## Delivery roadmap

The project is being delivered in small, independently testable increments.
The first three foundation increments are complete; the remaining work is
sequenced so that financial correctness and auditability precede advanced
analytics and user-facing automation.

| Status | Increment | Scope |
| --- | --- | --- |
| Complete | Billing calculation core | Cumulative calendar-month volume, marginal tier pricing, anomaly flags, batch ordering, and synthetic data |
| Complete | Audit and ingestion foundation | Immutable rule versions, calculation lineage, exact money, UTC validation, idempotency, and schema migrations |
| Complete | Late-arrival reconciliation | Pending-scope queue, chronological replay, immutable reconciliation runs, and adjustment candidates |
| Next | Reversals and refunds | Model reversing transactions explicitly; prevent reversed volume from being double-counted; evaluate refund/chargeback fee policy through versioned rules |
| Planned | Concurrency and ordering safeguards | Database-level uniqueness/locking strategy, deterministic same-timestamp ordering, retry-safe accumulator updates, and stale-running-total repair controls |
| Planned | FX provenance | Versioned FX rates with provider, observation time, currency pair, fallback policy, and missing/stale-rate anomalies |
| Planned | Broader anomaly coverage | Duplicate-charge indicators, invalid waivers, rule-window mismatches, rate discrepancies, volume anomalies, and robust statistical outliers |
| Planned | Measurement and operations | Precision/recall/F1 evaluation, confusion matrix, recovered-dollar reporting, review states, and approved-adjustment workflow |
| Planned | Investigation experience | Dashboard and assistant-style explanations that link each finding to its rule, lineage, evidence, and recommended action |

### Next increment: reversals and refunds

The next feature set will add a first-class relationship between an original
transaction and a reversal/refund event. It will preserve the source ledger,
make the volume effect explicit, and generate auditable fee adjustments rather
than silently rewriting already-calculated transaction records.
