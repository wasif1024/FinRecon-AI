"""Evaluation harness, dataset and evaluators, with scripted agents (no Claude calls, no LangSmith traffic)."""

import json
import uuid
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from langgraph.checkpoint.memory import InMemorySaver
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
from tests.fakes import REPORT, AgentScripts, FakeClient, final, reply, tool_use
from tests.test_agent_runtime import run as run_investigation
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
                      "checked_engine_evidence": 1, "read_playbook": 0, "no_fraud_determination": 1}, scores


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
    verdict = json.dumps({"reasoning": "Names the fee.", "matches_root_cause": True})
    client = FakeClient(reply("end_turn", verdict), reply("refusal"))
    judge = ev.make_root_cause_judge(client(None))
    run, example = _pair({"completed": True, "summary": "Settlement fee higher.", "findings": []},
                         {"root_cause": "Higher fee at settlement."})[0]

    assert judge(run, example) == {"score": 1, "comment": "Names the fee."}
    assert "<root_cause>Higher fee at settlement.</root_cause>" in client.calls[0]["messages"][0].content
    assert ev.JUDGE_SYSTEM in client.calls[0]["system"]
    assert judge(run, example)["score"] is None, "a declined grade is missing, not a failure"
    not_run = _pair({"completed": False}, {"root_cause": "x"})[0]
    assert judge(*not_run)["score"] is None and len(client.calls) == 2, "no judge call for incomplete runs"


def test_judge_model_requests_structured_grade():
    model = ev.judge_chat_model()
    assert model.output_config["format"]["schema"] == ev.JUDGE_SCHEMA and model.output_config["effort"] == "low"


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
    assert run_investigation(session, ctx, FakeClient(final(REPORT))).execution.langsmith_run_id is None

    monkeypatch.setattr(runtime, "tracing_enabled", lambda: True)
    assert run_investigation(session, ctx, FakeClient(final(REPORT))).execution.langsmith_run_id


# --- 6b.3: Agent Server runs, repetitions, dataset file, metadata, pairwise judge ---


def test_dataset_file_matches_the_scenarios():
    """evals/dataset.jsonl is the reviewed source of truth; regenerate it when scenarios change."""
    from finrecon_ai.evals import run as eval_run

    on_disk = eval_run.load_examples()
    assert on_disk == json.loads(json.dumps(eval_run.examples())), "run `finrecon eval dataset --regenerate`"
    assert {e["outputs"]["expected_playbook"] for e in on_disk} <= set(load_prompt_files())


def load_prompt_files():
    from finrecon_ai.agents.prompt_registry import SKILLS_DIR

    return {p.name for p in SKILLS_DIR.iterdir() if (p / "SKILL.md").is_file()}


def test_dataset_sync_matches_by_case_and_never_deletes(tmp_path):
    from finrecon_ai.evals.run import load_examples, plan_sync

    local = [{"metadata": {"case": c}, "inputs": {"scenario": c}, "outputs": {"v": 2}} for c in ("a", "b", "c")]
    remote = [SimpleNamespace(id="1", metadata={"case": "a"}, inputs={"scenario": "a"}, outputs={"v": 2}),
              SimpleNamespace(id="2", metadata={"scenario": "b"}, inputs={"scenario": "b"}, outputs={"v": 1}),
              SimpleNamespace(id="9", metadata={"case": "old"}, inputs={"scenario": "old"}, outputs={})]
    create, update, remote_only = plan_sync(local, remote)
    assert [e["metadata"]["case"] for e in create] == ["c"]
    assert [example_id for example_id, _ in update] == ["2"], "updated in place: past experiments keep pointing at it"
    assert remote_only == ["old"]

    bad = tmp_path / "d.jsonl"
    bad.write_text('{"metadata": {"case": "a"}, "inputs": {"scenario": "a"}}\n' * 2)
    with pytest.raises(ValueError, match="duplicate case"):
        load_examples(bad)


def test_experiment_metadata_identifies_the_agent(tmp_path):
    from finrecon_ai.evals.run import agent_fingerprint, estimated_cost, experiment_metadata

    meta = experiment_metadata()
    assert set(meta) >= {"git_sha", "git_dirty", "agent_fingerprint", "prompts", "models", "workspace"}
    assert meta["prompts"]["investigation"].startswith("investigation@v3#")
    assert {"claude-opus-5-5", "claude-haiku-5-5"} <= set(meta["models"])
    (tmp_path / "src/finrecon_ai/agents").mkdir(parents=True)
    (tmp_path / "src/finrecon_ai/agents/a.py").write_text("x = 1")
    before = agent_fingerprint(tmp_path)
    (tmp_path / "src/finrecon_ai/agents/a.py").write_text("x = 2")
    assert agent_fingerprint(tmp_path) != before, "uncommitted edits change the fingerprint"
    assert estimated_cost(13, 3) == 35.1


def test_case_pool_gives_each_repetition_a_fresh_case():
    from finrecon_ai.evals.harness import CasePool, EvalTenant

    tenants = [EvalTenant(uuid.uuid4(), uuid.uuid4(), {"fee": uuid.uuid4()}, {}, f"eval-{i}") for i in range(2)]
    pool = CasePool(tenants)
    taken = [pool.take("fee") for _ in range(2)]
    assert [t.slug for t, _ in taken] == ["eval-0", "eval-1"] and taken[0][1] != taken[1][1]
    with pytest.raises(RuntimeError, match="no fresh case"):
        pool.take("fee")


def test_run_through_the_agent_server_feeds_the_evaluators(eval_tenant):
    """The target starts `case-investigation` on the server; results and trajectory come from the records."""
    from finrecon_ai.evals.harness import investigate_on_server

    unit_of_work, store, tenant = eval_tenant
    llm = AgentScripts(investigation=[reply("tool_use", tool_use(
        "read_file", {"file_path": "/skills/fee-discrepancy/SKILL.md"})), *investigation_steps()],
        resolution=resolution_steps())

    class InProcessServer:  # stands in for the Agent Server: runs the same graph, here
        def __init__(self):
            self.inputs, self.saver = [], InMemorySaver()
            server = self

            class Threads:
                def create(self, metadata=None):
                    return {"thread_id": "t1"}

                def get_state(self, thread_id):
                    snap = server.graph.get_state({"configurable": {"thread_id": thread_id}})
                    return {"values": snap.values, "next": list(snap.next), "tasks": []}

            class Runs:
                def wait(self, thread_id, graph_id, input, raise_error=False):
                    server.inputs.append((graph_id, input))
                    from finrecon_ai.authz import load_actor
                    from finrecon_ai.workflows.case_investigation import WorkflowDeps, build_case_graph, initial_state

                    with unit_of_work() as session:
                        actor = load_actor(session, tenant.tenant_id, tenant.manager_id)
                    server.graph = build_case_graph(WorkflowDeps(unit_of_work, llm, store), server.saver)
                    return server.graph.invoke(initial_state(actor, tenant.cases["fee_discrepancy"]),
                                               {"configurable": {"thread_id": thread_id}})

            self.threads, self.runs = Threads(), Runs()

    server = InProcessServer()
    out = investigate_on_server(server, unit_of_work, tenant, tenant.cases["fee_discrepancy"], "fee_discrepancy")
    assert server.inputs == [("case-investigation", {"case_number": tenant.case_numbers["fee_discrepancy"],
                                                     "tenant_slug": tenant.slug})]
    reference = next(e for e in examples() if e["inputs"]["scenario"] == "fee_discrepancy")["outputs"]
    run_obj, example = SimpleNamespace(outputs=out), SimpleNamespace(outputs=reference)
    assert out["completed"] and ev.read_playbook(run_obj, example)["score"] == 1
    assert [t["tool"] for t in out["trajectory"]][:2] == ["read_file", "get_case_transactions"]


def test_read_playbook_needs_the_category_playbook():
    run_obj, example = _pair({"completed": True, "trajectory": [
        {"agent": "resolution", "tool": "read_file", "path": "/skills/duplicates/SKILL.md", "ok": True}]},
        {"expected_playbook": "duplicates"})[0]
    assert ev.read_playbook(run_obj, example)["score"] == 0, "only the investigation's own reading counts"


def test_pairwise_judge_scores_both_runs():
    verdict = json.dumps({"reasoning": "B cites the policy.", "preferred": "B"})
    client = FakeClient(reply("end_turn", verdict))
    judge = ev.make_pairwise_judge(client(None))
    runs = [SimpleNamespace(id="ra", outputs={"completed": True, "resolution_type": "escalate"}),
            SimpleNamespace(id="rb", outputs={"completed": True, "resolution_type": "request_documentation"})]
    result = judge(runs, SimpleNamespace(outputs={"root_cause": "fee"}))
    assert result == {"key": "recommendation_preference", "scores": {"ra": 0, "rb": 1}}
    assert "<A>" in client.calls[0]["messages"][0].content and ev.PAIRWISE_SYSTEM in client.calls[0]["system"]
