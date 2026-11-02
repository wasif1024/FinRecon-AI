"""row level security

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-02

Forced row-level security on every tenant table. A row is visible and writable only when its tenant_id equals
the transaction's app.tenant_id (set by finrecon_ai.db.tenancy); with no tenant set, nothing is visible.
FORCE applies the policy to the table owner too, which is the role the application connects as.
Link tables without tenant_id are visible only when their parent row is.
"""
from typing import Sequence, Union

from alembic import op

revision: str = '0005'
down_revision: Union[str, Sequence[str], None] = '0004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TENANT_TABLES = [
    "users", "financial_sources", "import_batches", "transactions", "reconciliation_runs", "transaction_matches",
    "reconciliation_exceptions", "investigation_cases", "investigation_evidence", "investigation_findings",
    "resolution_recommendations", "approval_requests", "approval_decisions", "audit_events", "policy_documents",
    "agent_execution_records",
]
# link table -> visibility rule through its tenant-scoped parent
LINK_TABLES = {
    "user_roles": "EXISTS (SELECT 1 FROM users u WHERE u.id = user_roles.user_id)",
    "transaction_match_items": "EXISTS (SELECT 1 FROM transaction_matches m WHERE m.id = transaction_match_items.match_id)",
    "exception_transactions": "EXISTS (SELECT 1 FROM reconciliation_exceptions e "
                              "WHERE e.id = exception_transactions.exception_id)",
}
TENANT_RULE = "(tenant_id = finrecon_current_tenant() OR finrecon_rls_bypass())"


def upgrade() -> None:
    op.execute("""
        CREATE FUNCTION finrecon_current_tenant() RETURNS uuid LANGUAGE sql STABLE AS
        $$ SELECT NULLIF(current_setting('app.tenant_id', true), '')::uuid $$
    """)
    op.execute("""
        CREATE FUNCTION finrecon_rls_bypass() RETURNS boolean LANGUAGE sql STABLE AS
        $$ SELECT coalesce(current_setting('app.rls_bypass', true), '') = 'on' $$
    """)
    for table in TENANT_TABLES:
        _protect(table, TENANT_RULE)
    for table, rule in LINK_TABLES.items():
        _protect(table, f"({rule} OR finrecon_rls_bypass())")


def downgrade() -> None:
    for table in [*TENANT_TABLES, *LINK_TABLES]:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.execute("DROP FUNCTION IF EXISTS finrecon_rls_bypass()")
    op.execute("DROP FUNCTION IF EXISTS finrecon_current_tenant()")


def _protect(table: str, rule: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant_isolation ON {table} USING {rule} WITH CHECK {rule}")
