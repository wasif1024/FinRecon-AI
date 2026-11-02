"""Developer CLI: finrecon generate-data | seed-demo | ingest | reconcile | cases | policies | prompts | agents |
investigate | workflow | approvals | pipeline."""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from finrecon_ai.authz import Actor, PermissionDenied
from finrecon_ai.cases.service import create_cases_for_run, list_cases
from finrecon_ai.db.enums import ApprovalDecisionType, CaseStatus, IngestionMethod, RoleName, SourceType
from finrecon_ai.db.models import (
    FinancialSource,
    ImportBatch,
    InvestigationCase,
    ReconciliationRun,
    Role,
    Tenant,
    User,
)
from finrecon_ai.db.session import new_session
from finrecon_ai.ingestion.mappings import DEFAULT_MAPPINGS
from finrecon_ai.log import configure_logging, get_logger, log_context, new_correlation_id
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
# A second manager: whoever starts an investigation may not approve it (separation of duties).
DEMO_APPROVER = ("approver@demo.finrecon.dev", "Demo Approver")


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
    for role, (email, full_name) in [*DEMO_USERS.items(), (RoleName.MANAGER, DEMO_APPROVER)]:
        if session.scalar(select(User).where(User.tenant_id == tenant.id, User.email == email)) is None:
            session.add(User(tenant_id=tenant.id, email=email, full_name=full_name, roles=[roles[role]]))
    session.flush()
    return tenant


log = get_logger(__name__)


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

    policies = commands.add_parser("policies", help="policy documents for agent retrieval")
    policy_commands = policies.add_subparsers(dest="policy_command", required=True)
    policy_upload = policy_commands.add_parser("upload", help="upload policy files (as the demo admin)")
    policy_upload.add_argument("paths", nargs="*", type=Path, default=[Path("data/policies")],
                               help="files or folders (default: data/policies)")
    policy_commands.add_parser("list", help="list policy documents")
    policy_search = policy_commands.add_parser("search", help="semantic search over current policies")
    policy_search.add_argument("query")
    policy_search.add_argument("--limit", type=int, default=5)

    agents = commands.add_parser("agents", help="run agents")
    agent_commands = agents.add_subparsers(dest="agent_command", required=True)
    agent_try = agent_commands.add_parser("try", help="run the investigation agent on one case (calls Claude) "
                                          "and print its report; findings are not saved to the case")
    agent_try.add_argument("case_number")

    investigate = commands.add_parser("investigate", help="run the agent workflow on a case until it pauses for "
                                      "approval (calls Claude)")
    investigate.add_argument("case_number")
    investigate.add_argument("--as", dest="role", choices=["manager", "analyst"], default="manager",
                             help="demo user to act as (an analyst must be assigned to the case)")

    workflow = commands.add_parser("workflow", help="inspect case workflows")
    workflow_commands = workflow.add_subparsers(dest="workflow_command", required=True)
    workflow_status = workflow_commands.add_parser("status", help="where a case's workflow stands")
    workflow_status.add_argument("case_number")

    approvals = commands.add_parser("approvals", help="decide recommended resolutions")
    approval_commands = approvals.add_subparsers(dest="approval_command", required=True)
    approval_commands.add_parser("list", help="pending approval requests")
    approval_decide = approval_commands.add_parser("decide", help="decide a case's pending recommendation")
    approval_decide.add_argument("case_number")
    approval_decide.add_argument("--decision", required=True, choices=[d.value for d in ApprovalDecisionType])
    approval_decide.add_argument("--comment")
    approval_decide.add_argument("--as", dest="role", choices=["approver", "manager"], default="approver",
                                 help="demo user deciding (the manager who started the investigation may not)")
    approval_commands.add_parser("resume", help="resume workflows whose decision was recorded but not applied")

    pipeline = commands.add_parser("pipeline", help="ingest -> reconcile -> open cases as one workflow")
    pipeline.add_argument("--dir", type=Path, default=Path("data/synthetic"))

    args = parser.parse_args(argv)
    _setup_logging()
    with log_context(correlation_id=new_correlation_id(), command=args.command):
        log.info("cli.started", argv=sys.argv[1:] if argv is None else argv)
        try:
            code = _run(parser, args)
        except Exception:
            log.exception("cli.failed")
            raise
        log.info("cli.finished", exit_code=code)
        return code


def _setup_logging() -> None:
    from finrecon_ai.config import get_settings

    try:
        settings = get_settings()
        configure_logging(settings.log_level, settings.log_format, settings.log_file or None)
    except Exception:  # e.g. generate-data without a .env: still log to the terminal
        configure_logging()


def _run(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    if args.command == "prompts":
        return _prompts(args)

    if args.command == "generate-data":
        dataset = generate(seed=args.seed, payments=args.payments)
        write_dataset(dataset, args.out)
        print(json.dumps(dataset.manifest["files"], indent=2))
        return 0

    if args.command in ("investigate", "workflow", "approvals"):
        from finrecon_ai.workflows.case_investigation import ensure_checkpoint_tables

        ensure_checkpoint_tables()  # before any other transaction opens (see its docstring)

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

        if args.command == "agents":
            return _agents_try(session, tenant, args.case_number)

        if args.command == "policies":
            return _policies(session, tenant, args)

        if args.command in ("investigate", "workflow"):
            case = session.scalar(select(InvestigationCase).where(InvestigationCase.tenant_id == tenant.id,
                                                                  InvestigationCase.case_number == args.case_number))
            if case is None:
                print(f"case {args.case_number} not found", file=sys.stderr)
                return 1
            role = RoleName(args.role) if args.command == "investigate" else RoleName.AUDITOR
            actor = _demo_actor(session, tenant, role)
            session.commit()  # release locks: the workflow's nodes use their own transactions
            return _investigate(actor, case.id, case.case_number) if args.command == "investigate" \
                else _workflow_status(case.id)

        if args.command == "approvals":
            return _approvals(session, tenant, args)

        if args.command == "pipeline":
            from finrecon_ai.workflows.case_investigation import session_unit_of_work
            from finrecon_ai.workflows.reconciliation_pipeline import run_pipeline

            sources = []
            for name, *_, file_name in DEMO_SOURCES.values():
                source = session.scalar(select(FinancialSource).where(FinancialSource.tenant_id == tenant.id,
                                                                      FinancialSource.name == name))
                sources.append((source.id, args.dir / file_name))
            session.commit()
            state = run_pipeline(session_unit_of_work(), tenant.id, sources)
            print(json.dumps({k: state.get(k) for k in ("batch_ids", "run_id", "cases_opened", "error")}, indent=2))
            return 1 if state.get("error") else 0

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


def _investigate(actor: Actor, case_id, case_number: str) -> int:
    from finrecon_ai.agents.llm import default_client
    from finrecon_ai.policies.store import default_store
    from finrecon_ai.workflows.case_investigation import (
        WorkflowDeps,
        WorkflowError,
        investigate_case,
        postgres_checkpointer,
        session_unit_of_work,
    )

    deps = WorkflowDeps(unit_of_work=session_unit_of_work(), llm=default_client(), policy_store=default_store())
    with postgres_checkpointer() as checkpointer:
        try:
            result = investigate_case(deps, checkpointer, actor, case_id)
        except (WorkflowError, PermissionDenied) as exc:
            print(f"{case_number}: {exc}", file=sys.stderr)
            return 1
    _print_case_summary(case_id, result.state, result.paused_for_approval)
    return 0 if result.paused_for_approval else 1


def _workflow_status(case_id) -> int:
    from finrecon_ai.workflows.case_investigation import (
        WorkflowDeps,
        postgres_checkpointer,
        session_unit_of_work,
        workflow_state,
    )

    with postgres_checkpointer() as checkpointer:
        pending, values = workflow_state(WorkflowDeps(session_unit_of_work(), None, None), checkpointer, case_id)
    if not values:
        print("no workflow has run for this case")
        return 0
    _print_case_summary(case_id, values, pending == ("await_decision",))
    return 0


def _print_case_summary(case_id, state: dict, paused: bool) -> None:
    from finrecon_ai.db.models import InvestigationFinding, ResolutionRecommendation

    with new_session() as session:
        case = session.get(InvestigationCase, case_id)
        findings = session.scalars(select(InvestigationFinding).where(InvestigationFinding.case_id == case_id)
                                   .order_by(InvestigationFinding.created_at)).all()
        rec = session.get(ResolutionRecommendation, state["recommendation_id"]) \
            if state.get("recommendation_id") else None
        print(f"{case.case_number}  status={case.status}  round={state.get('round')}  "
              f"{'PAUSED for approval ' + str(state.get('approval_request_id')) if paused else ''}"
              + (f"  error={state['error']}" if state.get("error") else ""))
        print(f"\nfindings ({len(findings)}):")
        for f in findings:
            print(f"  [{f.finding_type}] {f.statement}  (evidence: {len(f.evidence_ids)})")
        if state.get("rejected_output"):
            print("\nnot accepted from agent output:")
            for note in state["rejected_output"]:
                print(f"  - {note}")
        if rec:
            print(f"\nrecommendation v{rec.version} [{rec.status}] {rec.resolution_type}: {rec.summary}")
            for i, step in enumerate(rec.proposed_action["action_steps"], 1):
                print(f"  {i}. {step}")
            action = rec.proposed_action
            print(f"  amount {action['amount']} {action['currency']} | contact {action['contact']} | "
                  f"policy passages {len(action['policy_evidence_ids'])}"
                  + (f" | warnings: {action['warnings']}" if action["warnings"] else ""))


def _approvals(session: Session, tenant: Tenant, args: argparse.Namespace) -> int:
    from finrecon_ai.agents.llm import default_client
    from finrecon_ai.approvals.executor import ExecutionError
    from finrecon_ai.approvals.service import ApprovalError, list_pending
    from finrecon_ai.db.models import ApprovalRequest, ResolutionRecommendation
    from finrecon_ai.policies.store import default_store
    from finrecon_ai.workflows.case_investigation import (
        WorkflowDeps,
        decide_and_resume,
        postgres_checkpointer,
        resume_decided,
        session_unit_of_work,
    )

    approver = _demo_approver(session, tenant)
    if args.approval_command == "list":
        for request in list_pending(session, approver):
            case = session.get(InvestigationCase, request.case_id)
            rec = session.get(ResolutionRecommendation, request.recommendation_id)
            print(f"{case.case_number}  {rec.resolution_type:<24} v{rec.version}  {rec.summary[:90]}")
        return 0

    actor = approver if getattr(args, "role", "approver") == "approver" else \
        _demo_actor(session, tenant, RoleName.MANAGER)
    session.commit()
    # Agents run again only on "return_for_investigation"; the client is created lazily by the SDK.
    deps = WorkflowDeps(unit_of_work=session_unit_of_work(), llm=default_client(), policy_store=default_store())
    with postgres_checkpointer() as checkpointer:
        if args.approval_command == "resume":
            resumed = resume_decided(deps, checkpointer, tenant.id)
            print(f"resumed {len(resumed)} workflow(s)")
            return 0
        case = session.scalar(select(InvestigationCase).where(InvestigationCase.tenant_id == tenant.id,
                                                              InvestigationCase.case_number == args.case_number))
        request = session.scalar(select(ApprovalRequest).where(
            ApprovalRequest.case_id == case.id, ApprovalRequest.status == "pending")) if case else None
        if request is None:
            print(f"{args.case_number}: no pending approval request", file=sys.stderr)
            return 1
        session.commit()
        try:
            result = decide_and_resume(deps, checkpointer, actor, request.id, ApprovalDecisionType(args.decision),
                                       comment=args.comment)
        except (ApprovalError, ExecutionError, PermissionDenied) as exc:
            print(f"{args.case_number}: {exc}", file=sys.stderr)
            return 1
    _print_case_summary(case.id, result.state, result.paused_for_approval)
    if result.state.get("execution_result"):
        print(f"\nexecuted: {json.dumps(result.state['execution_result'], indent=2)}")
    return 0


def _demo_approver(session: Session, tenant: Tenant) -> Actor:
    from finrecon_ai.authz import load_actor

    user = session.scalar(select(User).where(User.tenant_id == tenant.id, User.email == DEMO_APPROVER[0]))
    return load_actor(session, tenant.id, user.id)


def _demo_actor(session: Session, tenant: Tenant, role: RoleName) -> Actor:
    from finrecon_ai.authz import load_actor

    user = session.scalar(select(User).where(User.tenant_id == tenant.id, User.email == DEMO_USERS[role][0]))
    assert user is not None
    return load_actor(session, tenant.id, user.id)


def _policies(session: Session, tenant: Tenant, args: argparse.Namespace) -> int:
    from finrecon_ai.config import get_settings
    from finrecon_ai.policies.service import list_policies, search_policies, upload_policy
    from finrecon_ai.policies.store import default_store

    if args.policy_command == "list":
        for doc in list_policies(session, _demo_actor(session, tenant, RoleName.AUDITOR)):
            print(f"{doc.status:<9} v{doc.version}  {doc.chunk_count:>3} chunks  {doc.title}  ({doc.file_name})"
                  + (f"  error: {doc.error}" if doc.error else ""))
        return 0

    store = default_store()
    if args.policy_command == "search":
        for hit in search_policies(_demo_actor(session, tenant, RoleName.ANALYST), store, args.query, args.limit):
            print(f"[{hit.score:.3f}] {hit.title} v{hit.version} › {hit.section}"
                  + (f" (p.{hit.page})" if hit.page else "") + f"\n        {hit.text[:160].replace(chr(10), ' ')}…")
        return 0

    admin = _demo_actor(session, tenant, RoleName.ADMIN)
    files = [f for p in args.paths for f in (sorted(p.iterdir()) if p.is_dir() else [p]) if f.is_file()]
    for path in files:
        result = upload_policy(session, admin, store, file_name=path.name, content=path.read_bytes(),
                               storage_dir=Path(get_settings().document_storage_dir))
        session.commit()
        doc = result.document
        state = "already uploaded" if not result.created else doc.status
        print(f"{path.name}: {state} - {doc.title} v{doc.version}, {doc.chunk_count} chunks"
              + (f" ({doc.error})" if doc.error else ""))
    return 0


def _agents_try(session: Session, tenant: Tenant, case_number: str) -> int:
    from finrecon_ai.agents.llm import default_client
    from finrecon_ai.agents.prompt_registry import load_prompt
    from finrecon_ai.agents.runtime import AgentFailed, run_agent
    from finrecon_ai.agents.tools import ToolContext
    from finrecon_ai.db.enums import AgentName
    from finrecon_ai.db.models import InvestigationCase, ReconciliationException

    case = session.scalar(select(InvestigationCase).where(InvestigationCase.tenant_id == tenant.id,
                                                          InvestigationCase.case_number == case_number))
    if case is None:
        print(f"case {case_number} not found", file=sys.stderr)
        return 1
    from finrecon_ai.policies.store import default_store

    ctx = ToolContext(session=session, tenant_id=tenant.id, case=case,
                      exception=session.get(ReconciliationException, case.exception_id), policy_store=default_store())
    spec = load_prompt(AgentName.INVESTIGATION)
    try:
        result = run_agent(session, spec=spec, ctx=ctx, client=default_client(),
                           user_content=f"Investigate case {case.case_number}: {case.title}")
    except AgentFailed as exc:
        session.commit()
        print(f"agent failed: {exc}", file=sys.stderr)
        return 1
    session.commit()
    e = result.execution
    print(json.dumps(result.output.model_dump(mode="json"), indent=2))
    print(f"\n{spec.ref} | {len(e.tool_calls)} tool calls: {[c['tool'] for c in e.tool_calls]} | "
          f"tokens in {e.input_tokens}, out {e.output_tokens} | execution {e.id}", file=sys.stderr)
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
