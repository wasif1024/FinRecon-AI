"""Developer CLI: finrecon generate-data | seed-demo | ingest | reconcile | cases | prompts."""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from finrecon_ai.authz import Actor
from finrecon_ai.cases.service import create_cases_for_run, list_cases
from finrecon_ai.db.enums import CaseStatus, IngestionMethod, RoleName, SourceType
from finrecon_ai.db.models import FinancialSource, ImportBatch, ReconciliationRun, Role, Tenant, User
from finrecon_ai.db.session import new_session
from finrecon_ai.ingestion.mappings import DEFAULT_MAPPINGS
from finrecon_ai.ingestion.service import IngestionError, ingest_file
from finrecon_ai.reconciliation.service import ReconciliationError, run_reconciliation
from finrecon_ai.synthetic.generator import BANK_FILE, GATEWAY_FILE, SETTLEMENT_FILE, generate, write_dataset

DEMO_TENANT_SLUG = "demo"
DEMO_SOURCES = {
    "bank": ("Demo Bank Statement", SourceType.BANK_STATEMENT, IngestionMethod.CSV, BANK_FILE),
    "gateway": ("PayGate Transactions", SourceType.PAYMENT_GATEWAY, IngestionMethod.JSON, GATEWAY_FILE),
    "settlement": ("PayGate Merchant Settlements", SourceType.MERCHANT_SETTLEMENT, IngestionMethod.CSV,
                   SETTLEMENT_FILE),
}
# One user per role. No passwords: authentication arrives in Phase 4.
DEMO_USERS = {
    RoleName.ANALYST: ("analyst@demo.finrecon.dev", "Demo Analyst"),
    RoleName.MANAGER: ("manager@demo.finrecon.dev", "Demo Manager"),
    RoleName.ADMIN: ("admin@demo.finrecon.dev", "Demo Admin"),
    RoleName.AUDITOR: ("auditor@demo.finrecon.dev", "Demo Auditor"),
}


def seed_demo(session: Session) -> Tenant:
    """Idempotent: creates the demo tenant, its three sources and one user per role if missing."""
    tenant = session.scalar(select(Tenant).where(Tenant.slug == DEMO_TENANT_SLUG))
    if tenant is None:
        tenant = Tenant(name="Demo Fintech Ltd", slug=DEMO_TENANT_SLUG)
        session.add(tenant)
        session.flush()
    for name, source_type, method, _ in DEMO_SOURCES.values():
        exists = session.scalar(
            select(FinancialSource).where(FinancialSource.tenant_id == tenant.id, FinancialSource.name == name)
        )
        if exists is None:
            session.add(FinancialSource(
                tenant_id=tenant.id,
                name=name,
                source_type=source_type,
                ingestion_method=method,
                config={"mapping": DEFAULT_MAPPINGS[source_type].model_dump(mode="json")},
            ))
    roles = {r.name: r for r in session.scalars(select(Role))}
    for role, (email, full_name) in DEMO_USERS.items():
        if session.scalar(select(User).where(User.tenant_id == tenant.id, User.email == email)) is None:
            session.add(User(tenant_id=tenant.id, email=email, full_name=full_name, roles=[roles[role]]))
    session.flush()
    return tenant


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="finrecon")
    commands = parser.add_subparsers(dest="command", required=True)

    gen = commands.add_parser("generate-data", help="write synthetic source files and a ground-truth manifest")
    gen.add_argument("--out", type=Path, default=Path("data/synthetic"))
    gen.add_argument("--seed", type=int, default=42)
    gen.add_argument("--payments", type=int, default=1000)

    commands.add_parser("seed-demo", help="create the demo tenant and its three financial sources")

    ing = commands.add_parser("ingest", help="import a file into a demo source")
    ing.add_argument("source", choices=[*DEMO_SOURCES, "all"])
    ing.add_argument("--dir", type=Path, default=Path("data/synthetic"), help="folder holding the synthetic files")
    ing.add_argument("--file", type=Path, help="explicit file (single source only)")

    commands.add_parser("reconcile", help="reconcile the latest import batch of each demo source")

    cases = commands.add_parser("cases", help="investigation cases")
    case_commands = cases.add_subparsers(dest="case_command", required=True)
    case_commands.add_parser("create", help="open a case for each qualifying exception of the latest run")
    case_list = case_commands.add_parser("list", help="list cases")
    case_list.add_argument("--status", choices=[s.value for s in CaseStatus])
    case_list.add_argument("--limit", type=int, default=20)

    prompts = commands.add_parser("prompts", help="versioned agent prompts")
    prompt_commands = prompts.add_subparsers(dest="prompt_command", required=True)
    prompt_commands.add_parser("list", help="show every prompt version, its status and hash")
    prompt_commands.add_parser("lock", help="record hashes of newly released prompt versions")

    args = parser.parse_args(argv)

    if args.command == "prompts":
        return _prompts(args)

    if args.command == "generate-data":
        dataset = generate(seed=args.seed, payments=args.payments)
        write_dataset(dataset, args.out)
        print(json.dumps(dataset.manifest["files"], indent=2))
        return 0

    with new_session() as session:
        tenant = seed_demo(session)
        if args.command == "seed-demo":
            session.commit()
            print(f"demo tenant ready: {tenant.id}")
            return 0

        if args.command == "reconcile":
            return _reconcile(session, tenant)

        if args.command == "cases":
            return _cases(session, tenant, args)

        keys = list(DEMO_SOURCES) if args.source == "all" else [args.source]
        if args.file and len(keys) > 1:
            parser.error("--file needs a single source")
        for key in keys:
            name, _, _, default_file = DEMO_SOURCES[key]
            path = args.file or args.dir / default_file
            source = session.scalar(
                select(FinancialSource).where(FinancialSource.tenant_id == tenant.id, FinancialSource.name == name)
            )
            assert source is not None
            try:
                result = ingest_file(session, tenant_id=tenant.id, source_id=source.id, file_name=path.name,
                                     content=path.read_bytes(), storage_uri=str(path.resolve()))
            except (IngestionError, OSError) as exc:
                session.rollback()
                print(f"{key}: {exc}", file=sys.stderr)
                return 1
            session.commit()
            b = result.batch
            note = "" if result.created else " (already imported, skipped)"
            print(f"{key}: {b.status} - {b.accepted_records} accepted, {b.rejected_records} rejected "
                  f"of {b.total_records}{note}")
    return 0


def _reconcile(session: Session, tenant: Tenant) -> int:
    batch_ids = []
    for name, *_ in DEMO_SOURCES.values():
        batch = session.scalar(
            select(ImportBatch)
            .join(FinancialSource, FinancialSource.id == ImportBatch.source_id)
            .where(FinancialSource.tenant_id == tenant.id, FinancialSource.name == name)
            .order_by(ImportBatch.created_at.desc())
            .limit(1)
        )
        if batch is None:
            print(f"no import batch for {name!r}; run `finrecon ingest all` first", file=sys.stderr)
            return 1
        batch_ids.append(batch.id)
    try:
        result = run_reconciliation(session, tenant_id=tenant.id, import_batch_ids=batch_ids)
    except ReconciliationError as exc:
        session.rollback()
        print(exc, file=sys.stderr)
        return 1
    session.commit()
    note = "" if result.created else "identical run already exists, showing it\n"
    print(note + json.dumps(result.run.summary, indent=2))
    return 0


def _cases(session: Session, tenant: Tenant, args: argparse.Namespace) -> int:
    actor = Actor.system(tenant.id)
    if args.case_command == "create":
        run = session.scalar(select(ReconciliationRun).where(ReconciliationRun.tenant_id == tenant.id)
                             .order_by(ReconciliationRun.created_at.desc()).limit(1))
        if run is None:
            print("no reconciliation run; run `finrecon reconcile` first", file=sys.stderr)
            return 1
        result = create_cases_for_run(session, actor, run.id)
        session.commit()
        by_priority = Counter(c.priority.value for c in result.created)
        print(f"{len(result.created)} cases opened ({dict(sorted(by_priority.items()))}), "
              f"{result.not_qualifying} exceptions below policy")
        return 0

    found = list_cases(session, actor, status=CaseStatus(args.status) if args.status else None)
    print(f"{len(found)} cases" + (f", showing {args.limit}" if len(found) > args.limit else ""))
    for case in found[:args.limit]:
        print(f"{case.case_number}  {case.status:<17} {case.priority:<8} {case.title}")
    return 0


def _prompts(args: argparse.Namespace) -> int:
    from finrecon_ai.agents.prompt_registry import PromptError, active_version, all_versions, load_prompt, \
        lock_released, read_lock

    if args.prompt_command == "lock":
        try:
            added = lock_released()
        except PromptError as exc:
            print(exc, file=sys.stderr)
            return 1
        print(f"locked: {', '.join(added)}" if added else "nothing new to lock")
        return 0

    lock = read_lock()
    for agent, version in all_versions():
        spec = load_prompt(agent, version)
        active = "*" if active_version(agent) == version else " "
        locked = "locked" if lock.get(f"{agent.value}@{version}") == spec.sha256 else ""
        print(f"{active} {spec.ref:<40} {spec.manifest.status:<9} {locked}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
