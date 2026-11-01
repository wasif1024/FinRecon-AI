# FinRecon AI

<p align="center">
  <img src="docs/assets/finrecon-flow.svg" alt="FinRecon AI pipeline: three financial sources are ingested, matched deterministically and turned into exceptions, which AI agents investigate and humans approve; every step is written to a hash-chained audit log." width="100%">
</p>

Agentic AI platform for financial reconciliation, exception investigation and human-approved resolution. See [docs/SRS.md](docs/SRS.md).

## Setup

Requires Python 3.13, [uv](https://docs.astral.sh/uv/) and PostgreSQL 13+.

```bash
cp .env.example .env        # fill in values
uv sync
uv run alembic upgrade head # create the database schema
```

## Synthetic data and ingestion

```bash
uv run finrecon generate-data   # writes data/synthetic/ (3 source files + manifest.json)
uv run finrecon ingest all      # creates the demo tenant + sources, imports all three files
uv run finrecon reconcile       # match the latest batches and record exceptions
```

Matching runs in two legs (gateway -> settlement, settlement -> bank): exact reference first, then
amount + currency + date window for what is left. Rules live in `src/finrecon_ai/reconciliation/config.py`;
every run stores a snapshot of them and every match stores which rules passed or failed.

`manifest.json` lists every planted problem (fee discrepancies, duplicates, missing records, malformed rows, ...).
It is the ground truth for matching tests and agent evaluations. Re-importing the same file is skipped.

Source column mappings live in `financial_sources.config` (defaults in `src/finrecon_ai/ingestion/mappings.py`).

## Tests

```bash
psql -d postgres -c "CREATE DATABASE finrecon_test OWNER finrecon"   # once
uv run pytest
```

Integration tests run against `<dbname>_test` and roll back after each test.

## Database migrations

Models live in `src/finrecon_ai/db/models/`, migrations in `migrations/versions/`.

```bash
uv run alembic revision --autogenerate -m "describe change"   # after editing models
uv run alembic upgrade head
uv run alembic check                                          # models and DB in sync?
```

Notes:
- Money is `NUMERIC(19,4)`, always stored with an ISO-4217 currency code.
- Enums are stored as `VARCHAR` + `CHECK` constraints, so adding a value is a normal migration.
- `audit_events` and `approval_decisions` are append-only: a trigger rejects `UPDATE`, `DELETE` and `TRUNCATE`.
- LangGraph checkpoint tables are created by `PostgresSaver.setup()`, not by Alembic.
