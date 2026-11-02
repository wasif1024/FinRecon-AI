"""The database ER diagram (docs/er-diagram.md), generated from the SQLAlchemy models so it cannot drift:

    uv run finrecon docs er        # rewrite docs/er-diagram.md; tests/test_er.py fails if it is stale

Mermaid `erDiagram`: every table with its columns (type, PK, FK, unique), and one relationship per foreign key
except `tenant_id` (every tenant table has one; drawn, it would hide the rest).
LangGraph's checkpoint tables are not models (PostgresSaver owns them) and are described in a note instead.
"""

from pathlib import Path

import sqlalchemy as sa

import finrecon_ai.db.models  # noqa: F401  (registers every table)
from finrecon_ai.db.base import Base

DOC = Path(__file__).resolve().parents[3] / "docs" / "er-diagram.md"

GROUPS = {  # how the doc lists the tables; every table must appear once (tests/test_er.py)
    "Tenancy and identity": ["tenants", "users", "roles", "user_roles"],
    "Financial data": ["financial_sources", "import_batches", "transactions"],
    "Reconciliation": ["reconciliation_runs", "transaction_matches", "transaction_match_items",
                       "reconciliation_exceptions", "exception_transactions"],
    "Investigation": ["investigation_cases", "agent_execution_records", "investigation_evidence",
                      "investigation_findings", "policy_documents"],
    "Governance": ["resolution_recommendations", "approval_requests", "approval_decisions", "audit_events"],
}


def _type(column: sa.Column) -> str:
    kind = column.type
    if isinstance(kind, sa.Enum):
        return "enum"
    if isinstance(kind, sa.Numeric) and not isinstance(kind, sa.Float):
        return f"numeric_{kind.precision}_{kind.scale}" if kind.precision else "numeric"
    if isinstance(kind, sa.String) and kind.length:
        return f"varchar_{kind.length}"
    name = type(kind).__name__.lower()
    return {"uuid": "uuid", "jsonb": "jsonb", "json": "json", "datetime": "timestamptz", "date": "date",
            "boolean": "bool", "integer": "int", "biginteger": "bigint", "text": "text", "array": "array"}.get(
        name, name)


def _markers(table: sa.Table, column: sa.Column) -> str:
    keys = []
    if column.primary_key:
        keys.append("PK")
    if column.foreign_keys:
        keys.append("FK")
    unique = column.unique or any(isinstance(c, sa.UniqueConstraint) and list(c.columns) == [column]
                                  for c in table.constraints)
    if unique and not column.primary_key:
        keys.append("UK")
    return ",".join(keys)


def mermaid() -> str:
    tables = Base.metadata.tables
    lines = ["erDiagram"]
    for name in sorted(tables):
        table = tables[name]
        lines.append(f"  {name} {{")
        for column in table.columns:
            markers = _markers(table, column)
            nullable = '"nullable"' if column.nullable and not column.primary_key else ""
            lines.append(f"    {_type(column)} {column.name} {markers} {nullable}".rstrip())
        lines.append("  }")
    for name in sorted(tables):
        for column in tables[name].columns:
            for fk in sorted(column.foreign_keys, key=lambda f: f.target_fullname):
                parent = fk.column.table.name
                if column.name == "tenant_id" and parent == "tenants":
                    continue
                many = "o{" if not (column.primary_key and len(tables[name].primary_key.columns) == 1) else "o|"
                optional = "o|" if column.nullable else "||"
                lines.append(f'  {parent} {optional}--{many} {name} : "{column.name}"')
    return "\n".join(lines) + "\n"


def document() -> str:
    groups = "\n".join(f"| {group} | {', '.join(f'`{t}`' for t in names)} |" for group, names in GROUPS.items())
    return f"""# Database ER diagram

Generated from the SQLAlchemy models by `uv run finrecon docs er` (`src/finrecon_ai/db/er.py`); the test suite
fails if it falls behind them. Migrations are in `migrations/versions/`.

| Area | Tables |
|---|---|
{groups}

Every table except `tenants`, `roles` and the link tables carries `tenant_id` (a foreign key to `tenants`, not
drawn below to keep the diagram readable) and is protected by forced row-level security
(migration 0005; link tables follow their parent row). `audit_events` and `approval_decisions` are append-only
(trigger and revoked privileges) and the audit events form a SHA-256 hash chain per tenant. Money is
`numeric(19,4)` with an ISO-4217 currency; enums are stored as `varchar` with a `CHECK` constraint.

LangGraph's checkpoint tables (`checkpoints`, `checkpoint_blobs`, `checkpoint_writes`, `checkpoint_migrations`)
live in the same database, keyed by thread id (`case:<case_id>` for the case workflow); they are created by
LangGraph's `PostgresSaver`, not by these models.

```mermaid
{mermaid()}```
"""


def write(path: Path = DOC) -> Path:
    path.write_text(document())
    return path
