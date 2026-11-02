"""Evaluation harness, dataset and evaluators, with scripted agents (no Claude calls, no LangSmith traffic)."""

import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from qdrant_client import QdrantClient

from finrecon_ai.agents import runtime
from finrecon_ai.agents.tools import ToolContext
from finrecon_ai.db.enums import ResolutionType
from finrecon_ai.db.models import InvestigationCase, ReconciliationException
from finrecon_ai.evals import evaluators as ev
from finrecon_ai.evals.harness import investigate, prepare_tenant
from finrecon_ai.evals.run import examples
from finrecon_ai.evals.scenarios import EXPECTATIONS
from finrecon_ai.policies.embedding import HashingEmbedder
from finrecon_ai.policies.store import PolicyStore
from tests.fakes import REPORT, AgentScripts, FakeClient, final, reply
from tests.test_agent_runtime import run
from tests.test_workflows import investigation_steps, resolution_steps


@pytest.fixture
def eval_tenant(session, tmp_path):
    @contextmanager
    def unit_of_work():
        yield session

    store = PolicyStore(QdrantClient(":memory:"), HashingEmbedder())
    return unit_of_work, store, prepare_tenant(unit_of_work, store, tmp_path)


def test_every_scenario_gets_its_own_case(eval_tenant):
    *_, tenant = eval_tenant
    assert set(tenant.cases) == {s.value for s in EXPECTATIONS}
    assert len(set(tenant.cases.values())) == len(EXPECTATIONS)


def test_fee_scenario_output_feeds_the_evaluators(eval_tenant):
    unit_of_work, store, tenant = eval_tenant
    llm = AgentScripts(investigation=investigation_steps(), resolution=resolution_steps())
    out = investigate(unit_of_work, llm, store, tenant, "fee_discrepancy")
    reference = next(e for e in examples() if e["inputs"]["scenario"] == "fee_discrepancy")
    run, example = SimpleNamespace(outputs=out), SimpleNamespace(outputs=reference["outputs"])
    scores = {f.__name__: f(run, example)["score"] for f in ev.CODE_EVALUATORS}
    assert scores == {"completed": 1, "resolution_acceptable": 1, "grounded": 0, "cites_expected_policy": 1,
                      "checked_engine_evidence": 1, "no_fraud_determination": 1}, scores


# --- evaluators ---


def _pair(outputs, reference):
    """LangSmith passes objects locally and dicts when evaluators run server-side; cover both."""
    return [(SimpleNamespace(outputs=outputs), SimpleNamespace(outputs=reference)),
            ({"outputs": outputs}, {"outputs": reference})]


def test_resolution_and_policy_evaluators_use_the_reference():
    reference = {"acceptable_resolutions": ["manual_match"], "expected_policy": "Duplicates"}
    for run, example in _pair({"completed": True, "resolution_type": "escalate", "policies_cited": ["Fees"]},
                              reference):
        assert ev.resolution_acceptable(run, example)["score"] == 0
        assert ev.cites_expected_policy(run, example)["score"] == 0
    for run, example in _pair({"completed": True, "resolution_type": "manual_match",
                               "policies_cited": ["Duplicates"]}, reference):
        assert ev.resolution_acceptable(run, example)["score"] == 1
        assert ev.cites_expected_policy(run, example)["score"] == 1
    run, example = _pair({"completed": True, "policies_cited": []}, {"expected_policy": None})[0]
    assert ev.cites_expected_policy(run, example)["score"] == 1, "no governing policy to cite"


@pytest.mark.parametrize("outputs", [{"completed": False, "error": "agent failed"}, None])
def test_failed_run_counts_against_completion_only(outputs):
    """A crashed run (no outputs at all) must not lift the quality averages by scoring 1 on 'nothing wrong'."""
    run, example = _pair(outputs, {"acceptable_resolutions": ["escalate"], "expected_policy": None})[1]
    assert ev.completed(run, example)["score"] == 0
    for evaluator in ev.CODE_EVALUATORS[1:]:
        assert evaluator(run, example)["score"] is None, evaluator.__name__


@pytest.mark.parametrize(("text", "score"), [
    ("The payment is fraudulent.", 0),
    ("This was fraud by the customer.", 0),
    ("There is no evidence of fraud; the burst is an indicator only.", 1),
    ("Routine fee difference.", 1),
])
def test_no_fraud_determination(text, score):
    run, example = _pair({"completed": True, "findings": [{"type": "hypothesis", "statement": text}]}, {})[0]
    assert ev.no_fraud_determination(run, example)["score"] == score


def test_root_cause_judge():
    verdict = SimpleNamespace(type="text", text=json.dumps({"reasoning": "Names the fee.", "matches_root_cause": True}))
    llm = FakeClient(reply("end_turn", verdict), reply("refusal"))
    judge = ev.make_root_cause_judge(llm)
    run, example = _pair({"completed": True, "summary": "Settlement fee higher.", "findings": []},
                         {"root_cause": "Higher fee at settlement."})[0]

    assert judge(run, example) == {"score": 1, "comment": "Names the fee."}
    request = llm.calls[0]
    assert request["output_config"]["format"]["schema"] == ev.JUDGE_SCHEMA
    assert "<root_cause>Higher fee at settlement.</root_cause>" in request["messages"][0]["content"]
    assert judge(run, example)["score"] is None, "a declined grade is missing, not a failure"
    not_run = _pair({"completed": False}, {"root_cause": "x"})[0]
    assert judge(*not_run)["score"] is None and len(llm.calls) == 2, "no judge call for incomplete runs"


def test_dataset_examples_cover_every_scenario_with_planted_details():
    data = {e["inputs"]["scenario"]: e["outputs"] for e in examples()}
    assert set(data) == {s.value for s in EXPECTATIONS}
    fee = data["fee_discrepancy"]
    assert fee["expected_policy"] == "Payment Processing Fee Policy"
    assert ResolutionType.CONFIRM_FEE_DISCREPANCY.value in fee["acceptable_resolutions"]
    assert "Planted details" in fee["root_cause"]


# --- tracing link ---


def test_execution_links_to_its_trace_only_when_tracing(session, world, case_ids, monkeypatch):
    case = session.get(InvestigationCase, case_ids[0])
    ctx = ToolContext(session=session, tenant_id=world[0].id, case=case,
                      exception=session.get(ReconciliationException, case.exception_id))
    assert run(session, ctx, FakeClient(final(REPORT))).execution.langsmith_run_id is None

    monkeypatch.setattr(runtime, "tracing_enabled", lambda: True)
    assert run(session, ctx, FakeClient(final(REPORT))).execution.langsmith_run_id
