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
uv run finrecon cases create    # open one investigation case per qualifying exception
uv run finrecon cases list --status new
```

Matching runs in two legs (gateway -> settlement, settlement -> bank): exact reference first, then
amount + currency + date window for what is left. Rules live in `src/finrecon_ai/reconciliation/config.py`;
every run stores a snapshot of them and every match stores which rules passed or failed.
The same run flags anomalies (amounts far outside a merchant's range, payment bursts) as indicators
for review, never as fraud determinations.

## Policy documents

Policies and procedures (PDF, DOCX, TXT, Markdown) are split by section, embedded locally with fastembed
(`BAAI/bge-small-en-v1.5`, no extra API key) and indexed in Qdrant per tenant. Agents search them with the
`search_policies` tool and cite the passage's `chunk_id`. Uploading a document with the same title creates a
new version and removes the old one from search. Only admins may upload.

```bash
docker compose up -d                                 # Qdrant
uv run finrecon policies upload                      # indexes data/policies/ (sample policies)
uv run finrecon policies search "late bank credit"
uv run finrecon policies list
```

## Docker

`docker compose up -d` starts Qdrant. The app runs on demand in a container against the host's PostgreSQL:

```bash
docker compose run --rm app policies search "duplicate bank credit"
```

The container writes logs to the same `./logs` folder (mounted at `/app/logs`) and keeps uploaded originals in
`./storage`. Set `DOCKER_DATABASE_URL` in `.env` if your database credentials differ from `.env.example`.

## Cases and permissions

Cases follow the SRS lifecycle (`src/finrecon_ai/cases/lifecycle.py`); every change is permission-checked
and audited. Analysts work cases assigned to them, managers assign and work any case, admins and auditors
read only. Decisions out of `awaiting_approval` are reserved for the approval workflow (Phase 4).
Permissions are fail-closed (`src/finrecon_ai/authz.py`).

`manifest.json` lists every planted problem (fee discrepancies, duplicates, missing records, malformed rows, ...).
It is the ground truth for matching tests and agent evaluations. Re-importing the same file is skipped.

Source column mappings live in `financial_sources.config` (defaults in `src/finrecon_ai/ingestion/mappings.py`).

## Logs

Three separate records, each with its own job:

| Record | Answers | Where |
|---|---|---|
| Application logs | How did the system behave? Timings, errors, retries, Claude calls | stderr + `logs/finrecon.log` |
| Audit log | What happened to business data, and who did it? | `audit_events` (append-only, hash-chained) |
| Agent executions | What did each agent do? Prompt version, tool calls, tokens, output | `agent_execution_records` |

Log files are JSON lines (`jq` friendly), rotate at 10 MB and keep 5 backups. Every line carries a
`correlation_id` that is also stored as `audit_events.request_id`, so a log line leads to its audit events.
Logs hold IDs and counts only, never transaction contents or prompt text; secret-looking values are masked.
Configure with `LOG_LEVEL`, `LOG_FORMAT` (`console` or `json` for the terminal) and `LOG_FILE`.
In Docker, `./logs` is mounted at `/app/logs`, so container logs land in the same folder.

```bash
jq 'select(.event == "claude.call")' logs/finrecon.log                     # every Claude call
jq 'select(.correlation_id == "a90f1fb721fb4a59")' logs/finrecon.log      # one command, end to end
```

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
