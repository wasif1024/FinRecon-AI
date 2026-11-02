"""The application's database role: rows only, its own tenant only, never the schema (db/roles.py, migration 0007).

Each test switches the fixture's transaction to the application role with SET ROLE, as if the API had connected
as it; the test database's data is the uncommitted `world`. Skipped where the role does not exist (see conftest).
"""

import uuid
from contextlib import contextmanager

import pytest
from sqlalchemy import column, func, select, text
from sqlalchemy.exc import DBAPIError, ProgrammingError

from finrecon_ai.db.enums import CaseStatus
from finrecon_ai.db.models import AuditEvent, InvestigationCase, Tenant
from finrecon_ai.db.roles import RoleError, grant_app_role, report
from finrecon_ai.db.tenancy import bind_tenant, unbind


@pytest.fixture
def as_app(session, app_role):
    if app_role is None:
        pytest.skip("no application role in the test database (TEST_APP_DB_ROLE)")

    @contextmanager
    def switch():
        unbind(session)
        session.execute(text(f'SET ROLE "{app_role}"'))
        try:
            yield session
        finally:
            session.rollback()  # a refused statement leaves the savepoint failed
            session.execute(text("RESET ROLE"))

    return switch


def refused(session, sql: str) -> bool:
    try:
        with session.begin_nested():
            session.execute(text(sql))
    except (ProgrammingError, DBAPIError) as exc:
        return "permission denied" in str(exc.orig) or "must be owner" in str(exc.orig)
    return False


def test_the_role_owns_nothing_and_bypasses_nothing(as_app):
    with as_app() as session:
        login = report(session.connection())
    assert login.safe_for_application and not login.owned_tables


def test_setting_the_bypass_does_not_reveal_other_tenants(session, world, case_ids, as_app):
    """The bypass was a setting anyone could write; it now counts only for the table owner."""
    session.execute(text("SELECT set_config('app.rls_bypass', 'on', true)"))
    assert session.scalar(select(func.count()).select_from(InvestigationCase)) == len(case_ids), "owner: bypass"
    with as_app() as app:
        app.execute(text("SELECT set_config('app.rls_bypass', 'on', true)"))
        assert app.scalar(select(func.count()).select_from(InvestigationCase)) == 0
        bind_tenant(app, world[0].id)
        assert app.scalar(select(func.count()).select_from(InvestigationCase)) == len(case_ids)


def test_schema_and_security_cannot_be_changed(as_app):
    with as_app() as session:
        for sql in ("ALTER TABLE investigation_cases DISABLE ROW LEVEL SECURITY",
                    "ALTER TABLE investigation_cases NO FORCE ROW LEVEL SECURITY",
                    "DROP POLICY tenant_isolation ON investigation_cases",
                    "DROP TRIGGER audit_events_append_only ON audit_events",
                    "CREATE TABLE stolen (x int)",
                    "DROP TABLE investigation_cases",
                    "CREATE OR REPLACE FUNCTION finrecon_rls_bypass() RETURNS boolean LANGUAGE sql AS $$ SELECT true $$",
                    "UPDATE alembic_version SET version_num = '0001'"):
            assert refused(session, sql), sql


def test_the_audit_trail_cannot_be_rewritten(session, world, case_ids, as_app):
    with as_app() as app:
        bind_tenant(app, world[0].id)
        assert app.scalar(select(func.count()).select_from(AuditEvent)) > 0, "it can read its tenant's trail"
        assert refused(app, "UPDATE audit_events SET payload = '{}'")
        assert refused(app, "DELETE FROM audit_events")
        assert refused(app, "DELETE FROM approval_decisions")


def test_the_cross_tenant_lookups_answer_only_their_question(session, world, case_ids, as_app):
    case = session.get(InvestigationCase, case_ids[0])
    case.status, case.workflow_thread_id = CaseStatus.INVESTIGATING, f"case:{case.id}"
    session.flush()
    with as_app() as app:
        assert app.scalar(select(func.finrecon_case_tenant(case.id))) == world[0].id
        assert app.scalar(select(func.finrecon_case_tenant(uuid.uuid4()))) is None
        interrupted = app.scalars(select(column("case_id")).select_from(func.finrecon_interrupted_cases())).all()
        assert interrupted == [case.id]
        assert app.scalar(select(func.count()).select_from(InvestigationCase)) == 0, "still nothing else"


def test_grant_refuses_logins_that_would_defeat_it(session, app_role):
    if app_role is None:
        pytest.skip("no application role in the test database")
    owner = session.scalar(text("SELECT current_user"))
    with pytest.raises(RoleError, match="table owner"):
        grant_app_role(session.connection(), owner)
    with pytest.raises(RoleError, match="does not exist"):
        grant_app_role(session.connection(), "no_such_role_" + uuid.uuid4().hex[:8])


def test_tenants_stay_readable_for_sign_in(as_app, world):
    """Login finds the tenant by slug before any tenant is bound; tenants hold no tenant data."""
    with as_app() as app:
        assert app.scalar(select(Tenant.slug).where(Tenant.id == world[0].id)) == world[0].slug


def test_api_requests_in_the_tests_run_as_the_application_role(session, app_role):
    """So the API and RPC suites prove the application works with only these rights."""
    from tests.test_api import strict_unit_of_work

    if app_role is None:
        pytest.skip("no application role in the test database")
    with strict_unit_of_work(session, app_role)() as request_session:
        assert request_session.scalar(text("SELECT current_user")) == app_role
    assert session.scalar(text("SELECT current_user")) != app_role
