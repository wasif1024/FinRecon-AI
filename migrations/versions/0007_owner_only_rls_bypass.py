"""owner-only RLS bypass, and narrow cross-tenant lookups for the application

The API now connects as its own role, which owns nothing (see `finrecon db grant-app-role`). That stops it from
altering tables or switching row-level security off, but the bypass was only a setting anyone can write
(`set_config('app.rls_bypass', 'on')`). From here the bypass counts only for the role that owns the tables
(migrations, test setup); for any other login it is ignored.

The application's three uses of the bypass become two SECURITY DEFINER functions owned by the owner that answer
one narrow question each, so no connection the API holds can read across tenants. They run as the owner and turn
the bypass on for their one query; the application role cannot become the owner (db/roles.py refuses a role
with the owner's rights), so these functions are the only way it reaches past its tenant.
- finrecon_case_tenant(case_id): which tenant a case belongs to (resuming an approved workflow, the Studio actor)
- finrecon_interrupted_cases(): cases whose API-started workflow stopped mid-way (the startup recovery sweep)

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-02 23:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

revision: str = '0007'
down_revision: Union[str, Sequence[str], None] = '0006'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # The owner is whoever runs the migrations; its name is fixed into the function, so no other login, and no
    # setting it writes, can borrow it.
    op.execute("""
        DO $$ BEGIN
            EXECUTE format($f$
                CREATE OR REPLACE FUNCTION finrecon_rls_bypass() RETURNS boolean LANGUAGE sql STABLE AS
                $body$ SELECT current_user = %L AND coalesce(current_setting('app.rls_bypass', true), '') = 'on' $body$
            $f$, current_user);
        END $$
    """)
    # Each lookup runs as the owner (SECURITY DEFINER), turns the bypass on for its one query and restores it.
    op.execute(_lookup("finrecon_case_tenant(case_id uuid) RETURNS uuid", "uuid",
                       "SELECT tenant_id INTO result FROM investigation_cases WHERE id = case_id"))
    op.execute(_lookup("finrecon_interrupted_cases() RETURNS TABLE (case_id uuid, tenant_id uuid)", None,
                       "RETURN QUERY SELECT c.id, c.tenant_id FROM investigation_cases c "
                       "WHERE c.status = 'investigating' AND c.workflow_thread_id LIKE 'case:%'"))
    # Only roles granted it (finrecon db grant-app-role) may call them.
    for function in ("finrecon_case_tenant(uuid)", "finrecon_interrupted_cases()"):
        op.execute(f"REVOKE ALL ON FUNCTION {function} FROM PUBLIC")


def _lookup(signature: str, result_type: str | None, query: str) -> str:
    declare = f"result {result_type};" if result_type else ""
    finish = "RETURN result;" if result_type else "RETURN;"
    return f"""
        CREATE FUNCTION {signature}
        LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = public, pg_temp AS $$
        DECLARE previous text := coalesce(current_setting('app.rls_bypass', true), ''); {declare}
        BEGIN
            PERFORM set_config('app.rls_bypass', 'on', true);
            {query};
            PERFORM set_config('app.rls_bypass', previous, true);
            {finish}
        END $$
    """


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS finrecon_interrupted_cases()")
    op.execute("DROP FUNCTION IF EXISTS finrecon_case_tenant(uuid)")
    op.execute("""
        CREATE OR REPLACE FUNCTION finrecon_rls_bypass() RETURNS boolean LANGUAGE sql STABLE AS
        $$ SELECT coalesce(current_setting('app.rls_bypass', true), '') = 'on' $$
    """)
