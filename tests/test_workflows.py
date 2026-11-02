"""Case investigation workflow and reconciliation pipeline, end to end with scripted agents."""

import uuid
from contextlib import contextmanager

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from qdrant_client import QdrantClient
from sqlalchemy import func, select

from finrecon_ai.authz import PermissionDenied
from finrecon_ai.cases.service import assign_case
from finrecon_ai.db.enums import (
    AgentExecutionStatus,
    AgentName,
    ApprovalRequestStatus,
    CaseStatus,
    EvidenceType,
    ExceptionCategory,
    FindingType,
    RecommendationStatus,
    ResolutionType,
    RoleName,
)
from finrecon_ai.db.models import (
    AgentExecutionRecord,
    ApprovalRequest,
    AuditEvent,
    InvestigationCase,
    InvestigationEvidence,
    InvestigationFinding,
    ReconciliationException,
    ResolutionRecommendation,
)
from finrecon_ai.policies.embedding import HashingEmbedder
from finrecon_ai.policies.service import upload_policy
from finrecon_ai.policies.store import PolicyStore
from finrecon_ai.workflows.case_investigation import (
    WorkflowDeps,
    WorkflowError,
    investigate_case,
    postgres_checkpointer,
    resume_case,
    workflow_state,
)
from tests.fakes import AgentScripts, final, last_tool_results, reply, tool_use
from tests.test_policies import SAMPLES


# --- scripted agents ---


def investigation_steps():
    def report(params):
        txns = last_tool_results(params)[0]["transactions"]
        return final({
            "summary": "Fee differs.",
            "evidence": [
                {"label": "E1", "kind": "transaction", "id": txns[0]["source_transaction_id"], "why_relevant": "a"},
                {"label": "E2", "kind": "transaction", "id": txns[-1]["id"], "why_relevant": "b"},
                {"label": "E9", "kind": "transaction", "id": "pg_invented", "why_relevant": "made up"},
            ],
            "findings": [
                {"type": "verified_fact", "statement": "Fees differ.", "confidence": None,
                 "evidence_labels": ["E1", "E2"]},
                {"type": "verified_fact", "statement": "Invented fact.", "confidence": None,
                 "evidence_labels": ["E9"]},
                {"type": "hypothesis", "statement": "Surcharge.", "confidence": "medium", "evidence_labels": ["E1"]},
                {"type": "outstanding_requirement", "statement": "Fee breakdown.", "confidence": None,
                 "evidence_labels": []},
            ],
            "possible_explanation": "Surcharge.", "confidence": "medium", "recommended_next_action": "Ask PayGate.",
        })

    return [reply("tool_use", tool_use("get_case_transactions")), report]


def risk_steps():
    return [reply("tool_use", tool_use("get_case_findings")), final({
        "summary": "Indicators look routine.", "evidence": [], "indicators": [
            {"type": "hypothesis", "statement": "Corporate order.", "confidence": "low", "evidence_labels": []}],
        "referral_recommended": False, "referral_reason": None, "confidence": "low"})]


def resolution_steps():
    def proposal(params):
        chunk = last_tool_results(params)[0]["passages"][0]["chunk_id"]
        return final({
            "resolution_type": "request_documentation", "summary": "Get the fee breakdown.",
            "rationale": "Policy requires it.", "action_steps": ["Email PayGate", "Attach breakdown"],
            "contact": "paygate",
            "evidence": [{"label": "P1", "kind": "policy_passage", "id": chunk, "why_relevant": "threshold"}],
            "policy_labels": ["P1"], "confidence": "high"})

    query = {"query": "fee discrepancy thresholds", "limit": 3}
    return [reply("tool_use", tool_use("search_policies", query)), proposal]


# --- fixtures ---


@pytest.fixture
def store(session, world, tmp_path):
    store = PolicyStore(QdrantClient(":memory:"), HashingEmbedder())
    upload_policy(session, world[2][RoleName.ADMIN], store, file_name="fees.md",
                  content=(SAMPLES / "payment_processing_fee_policy.md").read_bytes(), storage_dir=tmp_path)
    return store


@pytest.fixture
def make_deps(session, store):
    @contextmanager
    def unit_of_work():
        yield session  # the test transaction; nodes' commits become savepoints

    return lambda llm: WorkflowDeps(unit_of_work=unit_of_work, llm=llm, policy_store=store)


def case_of(session, tenant_id, category):
    return session.scalars(
        select(InvestigationCase).join(ReconciliationException,
                                       ReconciliationException.id == InvestigationCase.exception_id)
        .where(InvestigationCase.tenant_id == tenant_id, ReconciliationException.category == category)
    ).first()


@pytest.fixture
def fee_case(session, world, case_ids):
    return case_of(session, world[0].id, ExceptionCategory.FEE_DISCREPANCY)


# --- case workflow ---


def test_fee_case_runs_to_approval_and_pauses(session, world, fee_case, make_deps):
    manager = world[2][RoleName.MANAGER]
    llm = AgentScripts(investigation=investigation_steps(), resolution=resolution_steps())
    result = investigate_case(make_deps(llm), InMemorySaver(), manager, fee_case.id)

    assert result.paused_for_approval
    assert llm.calls == ["investigation", "investigation", "resolution", "resolution"], "no risk step for fees"
    session.refresh(fee_case)
    assert fee_case.status is CaseStatus.AWAITING_APPROVAL
    assert fee_case.workflow_thread_id == f"case:{fee_case.id}"

    # Only verifiable evidence is stored; the invented ID is rejected and its "fact" downgraded.
    evidence = session.scalars(select(InvestigationEvidence).where(InvestigationEvidence.case_id == fee_case.id)).all()
    assert sorted(e.evidence_type for e in evidence) == [EvidenceType.POLICY_PASSAGE, EvidenceType.TRANSACTION,
                                                         EvidenceType.TRANSACTION]
    findings = {f.statement: f for f in session.scalars(
        select(InvestigationFinding).where(InvestigationFinding.case_id == fee_case.id))}
    assert findings["Fees differ."].finding_type is FindingType.VERIFIED_FACT
    assert len(findings["Fees differ."].evidence_ids) == 2
    downgraded = next(f for s, f in findings.items() if s.endswith("Invented fact."))
    assert downgraded.finding_type is FindingType.HYPOTHESIS and downgraded.statement.startswith("[Unverified")
    assert any("pg_invented" in note for note in result.state["rejected_output"])

    recommendation = session.get(ResolutionRecommendation, uuid.UUID(result.state["recommendation_id"]))
    assert recommendation.status is RecommendationStatus.PENDING_APPROVAL
    assert recommendation.resolution_type is ResolutionType.REQUEST_DOCUMENTATION
    action = recommendation.proposed_action
    assert action["amount"] == str(fee_case.discrepancy_amount) and action["currency"] == fee_case.currency
    assert len(action["policy_evidence_ids"]) == 1 and action["warnings"] == []

    request = session.get(ApprovalRequest, uuid.UUID(result.state["approval_request_id"]))
    assert request.status is ApprovalRequestStatus.PENDING and request.required_role is RoleName.MANAGER
    assert request.workflow_thread_id == result.thread_id


def test_workflow_is_fully_audited(session, world, fee_case, make_deps):
    llm = AgentScripts(investigation=investigation_steps(), resolution=resolution_steps())
    investigate_case(make_deps(llm), InMemorySaver(), world[2][RoleName.MANAGER], fee_case.id)
    events = session.scalars(select(AuditEvent.event_type).where(AuditEvent.entity_id == str(fee_case.id))
                             .order_by(AuditEvent.id)).all()
    for expected in ["workflow.started", "case.status_changed", "agent.findings_recorded",
                     "agent.recommendation_recorded", "approval.requested"]:
        assert expected in events


def test_anomaly_case_goes_through_risk_analysis(session, world, case_ids, make_deps):
    case = case_of(session, world[0].id, ExceptionCategory.ANOMALY)

    def investigation(params):
        return final({"summary": "s", "evidence": [], "findings": [], "possible_explanation": "p",
                      "confidence": "low", "recommended_next_action": "n"})

    llm = AgentScripts(investigation=[investigation], risk_analysis=risk_steps(), resolution=resolution_steps())
    result = investigate_case(make_deps(llm), InMemorySaver(), world[2][RoleName.MANAGER], case.id)
    assert result.paused_for_approval
    assert llm.calls == ["investigation", "risk_analysis", "risk_analysis", "resolution", "resolution"]
    risk = session.get(AgentExecutionRecord, uuid.UUID(result.state["risk_execution_id"]))
    assert risk.agent_name is AgentName.RISK_ANALYSIS and risk.status is AgentExecutionStatus.COMPLETED


def test_agent_failure_ends_workflow_without_approval(session, world, fee_case, make_deps):
    bad = final({"summary": "missing everything"})
    result = investigate_case(make_deps(AgentScripts(investigation=[bad])), InMemorySaver(),
                              world[2][RoleName.MANAGER], fee_case.id)
    assert not result.paused_for_approval
    assert "investigation" in result.state["error"]
    session.refresh(fee_case)
    assert fee_case.status is CaseStatus.INVESTIGATING
    assert session.scalar(select(func.count()).select_from(ApprovalRequest)
                          .where(ApprovalRequest.case_id == fee_case.id)) == 0
    assert "workflow.failed" in session.scalars(select(AuditEvent.event_type)
                                                .where(AuditEvent.entity_id == str(fee_case.id))).all()


def test_permissions_and_state_checked_before_starting(session, world, fee_case, make_deps):
    users = world[2]
    deps = make_deps(AgentScripts())
    with pytest.raises(PermissionDenied):
        investigate_case(deps, InMemorySaver(), users[RoleName.AUDITOR], fee_case.id)
    with pytest.raises(PermissionDenied, match="not assigned"):
        investigate_case(deps, InMemorySaver(), users[RoleName.ANALYST], fee_case.id)

    fee_case.status = CaseStatus.AWAITING_APPROVAL
    session.flush()
    with pytest.raises(WorkflowError, match="awaiting_approval"):
        investigate_case(deps, InMemorySaver(), users[RoleName.MANAGER], fee_case.id)


def test_assigned_analyst_can_start(session, world, fee_case, make_deps):
    analyst = world[2][RoleName.ANALYST]
    assign_case(session, world[2][RoleName.MANAGER], fee_case.id, analyst.user_id)
    llm = AgentScripts(investigation=investigation_steps(), resolution=resolution_steps())
    assert investigate_case(make_deps(llm), InMemorySaver(), analyst, fee_case.id).paused_for_approval


def test_cannot_start_twice_while_waiting(session, world, fee_case, make_deps):
    saver = InMemorySaver()
    llm = AgentScripts(investigation=investigation_steps(), resolution=resolution_steps())
    investigate_case(make_deps(llm), saver, world[2][RoleName.MANAGER], fee_case.id)
    fee_case.status = CaseStatus.INVESTIGATING  # even if someone moved the case, the paused thread blocks a rerun
    session.flush()
    with pytest.raises(WorkflowError, match="already running or waiting"):
        investigate_case(make_deps(AgentScripts()), saver, world[2][RoleName.MANAGER], fee_case.id)


def test_returned_case_is_reinvestigated_and_recommendation_superseded(session, world, fee_case, make_deps):
    saver = InMemorySaver()
    llm = AgentScripts(investigation=investigation_steps() + investigation_steps(),
                       resolution=resolution_steps() + resolution_steps())
    deps = make_deps(llm)
    first = investigate_case(deps, saver, world[2][RoleName.MANAGER], fee_case.id)

    fee_case.status = CaseStatus.INVESTIGATING  # the Phase 4 approval service makes this move
    session.flush()
    second = resume_case(deps, saver, fee_case.id, {"decision": "return_for_investigation",
                                                    "comment": "Check the merchant's other fees."})
    assert second.paused_for_approval and second.state["round"] == 2
    old = session.get(ResolutionRecommendation, uuid.UUID(first.state["recommendation_id"]))
    new = session.get(ResolutionRecommendation, uuid.UUID(second.state["recommendation_id"]))
    assert (old.status, new.status) == (RecommendationStatus.SUPERSEDED, RecommendationStatus.PENDING_APPROVAL)
    assert new.version == 2

    # now() is fixed within the test transaction, so check the set of runs rather than their order.
    briefs = session.scalars(select(AgentExecutionRecord.input).where(
        AgentExecutionRecord.case_id == fee_case.id, AgentExecutionRecord.agent_name == AgentName.INVESTIGATION)).all()
    assert len(briefs) == 2
    assert sum("Check the merchant's other fees." in b["user_content"] for b in briefs) == 1


def test_pause_survives_restart_with_postgres_checkpointer(session, world, fee_case, make_deps, engine):
    url = engine.url.render_as_string(hide_password=False)
    deps = make_deps(AgentScripts(investigation=investigation_steps(), resolution=resolution_steps()))
    with postgres_checkpointer(url) as saver:  # tables were created by the engine fixture
        investigate_case(deps, saver, world[2][RoleName.MANAGER], fee_case.id)

    with postgres_checkpointer(url) as fresh:  # a new process would start here
        pending, values = workflow_state(deps, fresh, fee_case.id)
    assert pending == ("await_decision",)
    assert values["case_id"] == str(fee_case.id) and "recommendation_id" in values


# --- reconciliation pipeline ---


def test_pipeline_records_deterministic_agents(session, small_dataset_dir):
    from finrecon_ai.cli import DEMO_SOURCES, seed_demo
    from finrecon_ai.db.models import FinancialSource
    from finrecon_ai.workflows.reconciliation_pipeline import run_pipeline

    tenant = seed_demo(session)
    sources = []
    for name, *_, file_name in DEMO_SOURCES.values():
        source = session.scalar(select(FinancialSource).where(FinancialSource.tenant_id == tenant.id,
                                                              FinancialSource.name == name))
        sources.append((source.id, small_dataset_dir / file_name))

    @contextmanager
    def unit_of_work():
        yield session

    state = run_pipeline(unit_of_work, tenant.id, sources)
    assert state["cases_opened"] > 0 and len(state["batch_ids"]) == 3

    records = session.scalars(select(AgentExecutionRecord).where(AgentExecutionRecord.tenant_id == tenant.id)
                              .order_by(AgentExecutionRecord.started_at)).all()
    assert [(r.agent_name, r.status, r.model) for r in records] == [
        (AgentName.INGESTION, AgentExecutionStatus.COMPLETED, None),
        (AgentName.RECONCILIATION, AgentExecutionStatus.COMPLETED, None)]
    assert str(records[1].run_id) == state["run_id"]


def test_policy_passage_seen_only_through_earlier_findings_can_be_cited(session, world, fee_case, make_deps):
    """Regression: the resolution agent may cite a passage it only saw via get_case_findings."""
    def investigation(params):
        chunk = last_tool_results(params)[0]["passages"][0]["chunk_id"]
        return final({"summary": "s", "possible_explanation": "p", "confidence": "low",
                      "recommended_next_action": "n",
                      "evidence": [{"label": "P1", "kind": "policy_passage", "id": chunk, "why_relevant": "r"}],
                      "findings": [{"type": "verified_fact", "statement": "Policy says so.", "confidence": None,
                                    "evidence_labels": ["P1"]}]})

    def resolution(params):
        evidence = last_tool_results(params)[0]["evidence"]
        chunk = evidence[0]["source_reference"]["chunk_id"]
        return final({"resolution_type": "request_documentation", "summary": "s", "rationale": "r",
                      "action_steps": ["a"], "contact": "paygate", "policy_labels": ["P1"], "confidence": "medium",
                      "evidence": [{"label": "P1", "kind": "policy_passage", "id": chunk, "why_relevant": "r"}]})

    query = {"query": "fee discrepancy thresholds", "limit": 1}
    llm = AgentScripts(investigation=[reply("tool_use", tool_use("search_policies", query)), investigation],
                       resolution=[reply("tool_use", tool_use("get_case_findings")), resolution])
    result = investigate_case(make_deps(llm), InMemorySaver(), world[2][RoleName.MANAGER], fee_case.id)

    assert result.state["rejected_output"] == []
    recommendation = session.get(ResolutionRecommendation, uuid.UUID(result.state["recommendation_id"]))
    assert len(recommendation.proposed_action["policy_evidence_ids"]) == 1
