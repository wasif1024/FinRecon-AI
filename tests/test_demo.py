"""Demo snapshot round trip: export a tenant with a paused investigation, wipe the database, load it, approve.

Export and load work on committed data in their own connections, so this test uses its own database,
<db>_demo_test (CI creates it; locally: createdb -O finrecon finrecon_demo_test). Skipped where it is missing.
"""

import uuid
from contextlib import contextmanager

import pytest
from alembic import command
from alembic.config import Config
from qdrant_client import QdrantClient
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from finrecon_ai.authz import load_actor
from finrecon_ai.cases.service import create_cases_for_run
from finrecon_ai.cli import DEMO_APPROVER, DEMO_SOURCES, DEMO_USERS, seed_demo
from finrecon_ai.config import get_settings
from finrecon_ai.db.enums import ApprovalDecisionType, CaseStatus, ExceptionCategory, RoleName
from finrecon_ai.db.models import FinancialSource, InvestigationCase, PolicyDocument, User
from finrecon_ai.db.tenancy import bypass_rls
from finrecon_ai.demo import SnapshotError, export_tenant, load_snapshot
from finrecon_ai.ingestion.service import ingest_file
from finrecon_ai.policies.embedding import HashingEmbedder
from finrecon_ai.policies.service import upload_policy
from finrecon_ai.policies.store import PolicyStore
from finrecon_ai.reconciliation.service import run_reconciliation
from finrecon_ai.workflows.case_investigation import (
    WorkflowDeps,
    decide_and_resume,
    ensure_checkpoint_tables,
    investigate_case,
    postgres_checkpointer,
)
from tests.conftest import ROOT
from tests.fakes import AgentScripts
from tests.test_approvals import proposal_steps
from tests.test_workflows import SAMPLES, case_of, investigation_steps


def migrate_fresh(url: str) -> None:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    ensure_checkpoint_tables(url)
    engine = create_engine(url)  # LangGraph's tables are not Alembic's: empty them too
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE checkpoints, checkpoint_blobs, checkpoint_writes"))
    engine.dispose()


@pytest.fixture
def demo_db():
    url = make_url(get_settings().owner_database_url)
    url = url.set(database=f"{url.database}_demo_test").render_as_string(hide_password=False)
    engine = create_engine(url)
    try:
        engine.connect().close()
    except OperationalError as exc:
        pytest.skip(f"no demo test database: {exc.orig}")
    migrate_fresh(url)
    yield url, engine
    engine.dispose()


def deps_for(engine, llm, store):
    @contextmanager
    def unit_of_work():
        with Session(engine, expire_on_commit=False) as session:
            yield session
            session.commit()

    return WorkflowDeps(unit_of_work=unit_of_work, llm=llm, policy_store=store)


def actor(engine, email):
    with Session(engine) as session:
        bypass_rls(session)
        user = session.scalar(select(User).where(User.email == email))
        return load_actor(session, user.tenant_id, user.id)


def test_snapshot_round_trip_resumes_the_paused_investigation(demo_db, small_dataset_dir, tmp_path):
    url, engine = demo_db
    store = PolicyStore(QdrantClient(":memory:"), HashingEmbedder())
    with Session(engine) as session:
        bypass_rls(session)
        tenant = seed_demo(session)
        batches = []
        for name, *_, file_name in DEMO_SOURCES.values():
            source = session.scalar(select(FinancialSource).where(FinancialSource.name == name))
            batches.append(ingest_file(session, tenant_id=tenant.id, source_id=source.id, file_name=file_name,
                                       content=(small_dataset_dir / file_name).read_bytes()).batch.id)
        run = run_reconciliation(session, tenant_id=tenant.id, import_batch_ids=batches).run
        from finrecon_ai.authz import Actor

        create_cases_for_run(session, Actor.system(tenant.id), run.id)
        admin = session.scalar(select(User).where(User.email == DEMO_USERS[RoleName.ADMIN][0]))
        upload_policy(session, load_actor(session, tenant.id, admin.id), store, file_name="fees.md",
                      content=(SAMPLES / "payment_processing_fee_policy.md").read_bytes(),
                      storage_dir=tmp_path / "before")
        admin.password_hash = "$argon2id$not-exported"
        session.commit()
        tenant_id, fee = tenant.id, case_of(session, tenant.id, ExceptionCategory.FEE_DISCREPANCY).id
        cited = sorted(p.id for p in store.client.scroll(store.collection, limit=100)[0])

    llm = AgentScripts(investigation=investigation_steps(), resolution=proposal_steps("request_documentation"))
    with postgres_checkpointer(url) as saver:
        paused = investigate_case(deps_for(engine, llm, store), saver, actor(engine, DEMO_USERS[RoleName.MANAGER][0]),
                                  fee)
    assert paused.paused_for_approval

    snapshot = tmp_path / "snapshot.tar.gz"
    rows = export_tenant(url, "demo", snapshot)
    assert rows["checkpoints"] > 0 and rows["audit_events"] > 0 and rows["policy_documents"] == 1

    migrate_fresh(url)  # an empty database
    fresh = PolicyStore(QdrantClient(":memory:"), HashingEmbedder())
    result = load_snapshot(url, snapshot, storage_dir=tmp_path / "after", store=fresh)
    assert result.chain_intact and result.policies_indexed == 1 and result.tenant_slug == "demo"
    assert sorted(p.id for p in fresh.client.scroll(fresh.collection, limit=100)[0]) == cited, \
        "the cited passages keep their ids"
    with Session(engine) as session:
        bypass_rls(session)
        assert session.scalar(select(func.count()).select_from(User).where(User.password_hash.is_not(None))) == 0
        document = session.scalar(select(PolicyDocument))
        assert document.storage_uri.startswith(str(tmp_path / "after"))
        assert session.get(InvestigationCase, fee).status is CaseStatus.AWAITING_APPROVAL
    with pytest.raises(SnapshotError, match="already exists"):
        load_snapshot(url, snapshot, storage_dir=tmp_path / "again", store=fresh)

    # The loaded checkpoint resumes: a second manager approves and fixed code carries it out.
    with postgres_checkpointer(url) as saver:
        resumed = decide_and_resume(deps_for(engine, AgentScripts(), fresh), saver, actor(engine, DEMO_APPROVER[0]),
                                    uuid.UUID(paused.state["approval_request_id"]), ApprovalDecisionType.APPROVE)
    assert not resumed.paused_for_approval
    with Session(engine) as session:
        bypass_rls(session)
        assert session.get(InvestigationCase, fee).status is CaseStatus.AWAITING_EVIDENCE
        assert session.scalar(text("SELECT count(*) FROM audit_events WHERE tenant_id = :t"), {"t": tenant_id}) > 0


def test_a_snapshot_from_another_schema_revision_is_refused(demo_db, tmp_path):
    import io
    import json
    import tarfile

    url, _ = demo_db
    snapshot = tmp_path / "old.tar.gz"
    manifest = json.dumps({"format": 1, "revision": "0001", "tenant_id": str(uuid.uuid4()), "slug": "x",
                           "tables": {}, "policy_files": {}}).encode()
    with tarfile.open(snapshot, "w:gz") as archive:
        info = tarfile.TarInfo("manifest.json")
        info.size = len(manifest)
        archive.addfile(info, io.BytesIO(manifest))
    with pytest.raises(SnapshotError, match="schema revision 0001"):
        load_snapshot(url, snapshot, storage_dir=tmp_path, store=None)
