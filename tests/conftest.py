import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from finrecon_ai.authz import Actor, load_actor
from finrecon_ai.cases.service import create_cases_for_run
from finrecon_ai.cli import DEMO_SOURCES, DEMO_USERS, seed_demo
from finrecon_ai.config import get_settings
from finrecon_ai.db.models import FinancialSource, User
from finrecon_ai.db.tenancy import bypass_rls
from finrecon_ai.ingestion.service import ingest_file
from finrecon_ai.reconciliation.service import run_reconciliation
from finrecon_ai.synthetic.generator import generate, write_dataset

ROOT = Path(__file__).resolve().parent.parent

os.environ["LANGSMITH_TRACING"] = "false"  # tests never send traces to LangSmith


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    """Migrated <dbname>_test database, as the table owner. Integration tests skip if it is unreachable."""
    url = make_url(get_settings().owner_database_url)
    url = url.set(database=f"{url.database}_test")
    engine = create_engine(url)
    try:
        engine.connect().close()
    except OperationalError as exc:
        pytest.skip(f"test database unavailable: {exc.orig}")

    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", url.render_as_string(hide_password=False).replace("%", "%%"))
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    from finrecon_ai.workflows.case_investigation import ensure_checkpoint_tables

    ensure_checkpoint_tables(url.render_as_string(hide_password=False))  # before any test transaction opens
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def app_role(engine: Engine) -> str | None:
    """The application's database role (TEST_APP_DB_ROLE, default finrecon_app), granted its rights on the test
    database, if it exists and the owner may SET ROLE to it; otherwise None. API tests then run every request as
    it, with the privileges the API really has. CI creates it; locally see README (Database logins)."""
    from sqlalchemy import text

    from finrecon_ai.db.roles import grant_app_role

    role = os.environ.get("TEST_APP_DB_ROLE", "finrecon_app")
    with engine.begin() as connection:
        usable = connection.execute(text("SELECT pg_has_role(current_user, oid, 'MEMBER') FROM pg_roles "
                                         "WHERE rolname = :role"), {"role": role}).scalar()
        if not usable:
            return None
        grant_app_role(connection, role)
    return role


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    """Each test runs in a transaction that is rolled back, so tests never see each other's data."""
    with engine.connect() as connection:
        transaction = connection.begin()
        session = Session(bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False)
        # Test setup reads and writes across tenants. Row-level security itself is tested in test_tenancy.py and
        # through the API tests, whose requests run strictly scoped.
        bypass_rls(session)
        try:
            yield session
        finally:
            session.close()
            transaction.rollback()


# --- a demo tenant with a reconciled small dataset (250 payments, every scenario planted) ---


@pytest.fixture(scope="session")
def small_dataset_dir(tmp_path_factory):
    out = tmp_path_factory.mktemp("small")
    write_dataset(generate(seed=3, payments=250), out)
    return out


@pytest.fixture
def world(session: Session, small_dataset_dir):
    tenant = seed_demo(session)
    batch_ids = []
    for name, *_, file_name in DEMO_SOURCES.values():
        source = session.scalar(select(FinancialSource).where(FinancialSource.tenant_id == tenant.id,
                                                              FinancialSource.name == name))
        batch_ids.append(ingest_file(session, tenant_id=tenant.id, source_id=source.id, file_name=file_name,
                                     content=(small_dataset_dir / file_name).read_bytes()).batch.id)
    run = run_reconciliation(session, tenant_id=tenant.id, import_batch_ids=batch_ids).run
    users = {}
    for role, (email, _) in DEMO_USERS.items():
        user = session.scalar(select(User).where(User.tenant_id == tenant.id, User.email == email))
        users[role] = load_actor(session, tenant.id, user.id)
    return tenant, run, users


@pytest.fixture
def case_ids(session, world):
    tenant, run, _ = world
    created = create_cases_for_run(session, Actor.system(tenant.id), run.id).created
    return [c.id for c in created]


@pytest.fixture
def approver(session, world):
    """A second manager: the first one starts investigations, so they may not approve them."""
    import uuid

    from finrecon_ai.db.enums import RoleName
    from finrecon_ai.db.models import Role

    tenant = world[0]
    user = User(tenant_id=tenant.id, email=f"approver-{uuid.uuid4().hex[:6]}@demo.dev", full_name="Approver",
                roles=[session.scalar(select(Role).where(Role.name == RoleName.MANAGER))])
    session.add(user)
    session.flush()
    return load_actor(session, tenant.id, user.id)

