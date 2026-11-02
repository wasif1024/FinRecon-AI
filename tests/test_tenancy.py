"""PostgreSQL row-level security (migration 0005, db/tenancy.py): isolation holds even when application code
forgets its tenant filter."""

import uuid

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import DBAPIError

from finrecon_ai.db.base import Base
from finrecon_ai.db.enums import IngestionMethod, SourceType
from finrecon_ai.db.models import FinancialSource, InvestigationCase, Role, Tenant, Transaction, User
from finrecon_ai.db.tenancy import TenantScopeError, bind_tenant, bypass_rls, unbind

TENANT_TABLES = sorted(t.name for t in Base.metadata.sorted_tables if "tenant_id" in t.c)
LINK_TABLES = ["user_roles", "transaction_match_items", "exception_transactions"]


@pytest.fixture
def two_tenants(session, world, case_ids):
    """The demo tenant (with data) and a second tenant with one source and one user."""
    other = Tenant(name="Other", slug=f"other-{uuid.uuid4().hex[:6]}")
    session.add(other)
    session.flush()
    session.add_all([
        FinancialSource(tenant_id=other.id, name="Other Bank", source_type=SourceType.BANK_STATEMENT,
                        ingestion_method=IngestionMethod.CSV),
        User(tenant_id=other.id, email="u@other.dev", full_name="U",
             roles=[session.scalar(select(Role).where(Role.name == "analyst"))]),
    ])
    session.flush()
    unbind(session)  # from here on the session behaves like the application's
    yield world[0], other
    bypass_rls(session)


def count(session, table: str) -> int:
    return session.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()


def test_every_tenant_table_is_protected(session):
    protected = set(session.execute(text(
        "SELECT relname FROM pg_class WHERE relrowsecurity AND relforcerowsecurity")).scalars())
    assert set(TENANT_TABLES) | set(LINK_TABLES) <= protected, "a tenant table is missing row-level security"


@pytest.mark.parametrize("table", TENANT_TABLES + LINK_TABLES)
def test_unscoped_session_sees_nothing(session, two_tenants, table):
    assert count(session, table) == 0


def test_bound_session_sees_only_its_tenant(session, two_tenants):
    demo, other = two_tenants
    bind_tenant(session, other.id)
    assert session.scalar(select(func.count()).select_from(Transaction)) == 0
    assert [s.name for s in session.scalars(select(FinancialSource))] == ["Other Bank"]
    assert session.scalar(select(func.count()).select_from(InvestigationCase)) == 0
    assert count(session, "exception_transactions") == 0, "link rows follow their parent's visibility"


def test_query_without_tenant_filter_cannot_leak(session, two_tenants):
    demo, other = two_tenants
    bind_tenant(session, demo.id)
    # Deliberately no tenant_id filter: the database applies it.
    sources = session.scalars(select(FinancialSource)).all()
    assert sources and all(s.tenant_id == demo.id for s in sources)
    users = session.scalars(select(User)).all()
    assert all(u.tenant_id == demo.id for u in users)


def test_cannot_write_into_another_tenant(session, two_tenants):
    demo, other = two_tenants
    bind_tenant(session, demo.id)
    with pytest.raises(DBAPIError, match="row-level security"), session.begin_nested():
        session.add(FinancialSource(tenant_id=other.id, name="Smuggled", source_type=SourceType.BANK_STATEMENT,
                                    ingestion_method=IngestionMethod.CSV))
        session.flush()


def test_cannot_update_or_move_another_tenants_rows(session, two_tenants):
    demo, other = two_tenants
    bind_tenant(session, demo.id)
    hidden = session.execute(update(FinancialSource).where(FinancialSource.name == "Other Bank")
                             .values(is_active=False))
    assert hidden.rowcount == 0, "rows of another tenant are invisible to UPDATE as well"
    with pytest.raises(DBAPIError, match="row-level security"), session.begin_nested():
        session.execute(update(FinancialSource).where(FinancialSource.tenant_id == demo.id)
                        .values(tenant_id=other.id))


def test_session_cannot_switch_tenant_silently(session, two_tenants):
    demo, other = two_tenants
    bind_tenant(session, demo.id)
    with pytest.raises(TenantScopeError):
        bind_tenant(session, other.id)
    unbind(session)
    bind_tenant(session, other.id)  # an explicit unbind is required


def test_scope_survives_commit(session, two_tenants):
    demo, _ = two_tenants
    bind_tenant(session, demo.id)
    before = count(session, "transactions")
    session.commit()  # a savepoint in tests; a new transaction in the application
    assert before > 0 and count(session, "transactions") == before


def test_global_tables_stay_readable(session, two_tenants):
    assert count(session, "tenants") >= 2  # login looks tenants up before any tenant is bound
    assert count(session, "roles") == 4


def test_setting_cannot_be_spoofed_by_a_bound_session_value(session, two_tenants):
    """The scope lives in a transaction-local setting the application writes; an unbound session has none."""
    assert session.execute(text("SELECT finrecon_current_tenant()")).scalar() is None
    assert session.execute(text("SELECT finrecon_rls_bypass()")).scalar() is False
