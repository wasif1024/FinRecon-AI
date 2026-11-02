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
from finrecon_ai.ingestion.service import ingest_file
from finrecon_ai.reconciliation.service import run_reconciliation
from finrecon_ai.synthetic.generator import generate, write_dataset

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    """Migrated <dbname>_test database. Integration tests skip if it is unreachable."""
    url = make_url(get_settings().database_url)
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


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    """Each test runs in a transaction that is rolled back, so tests never see each other's data."""
    with engine.connect() as connection:
        transaction = connection.begin()
        session = Session(bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False)
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
