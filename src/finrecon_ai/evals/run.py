"""LangSmith dataset and experiments for the agent workflow.

    uv run finrecon eval dataset            # create/update the dataset in LangSmith (free)
    uv run finrecon eval run                # one experiment over every scenario (calls Claude, ~$5)
"""

import tempfile
from collections.abc import Iterable
from pathlib import Path

from finrecon_ai.agents.prompt_registry import load_prompt
from finrecon_ai.db.enums import AgentName
from finrecon_ai.evals.evaluators import CODE_EVALUATORS, make_root_cause_judge
from finrecon_ai.evals.harness import investigate, prepare_tenant, scenario_details
from finrecon_ai.evals.scenarios import EXPECTATIONS
from finrecon_ai.log import get_logger
from finrecon_ai.synthetic.generator import generate

log = get_logger(__name__)

DATASET = "finrecon-agent-scenarios"


def examples() -> list[dict]:
    """One example per planted scenario; the reference carries the manifest's actual figures."""
    manifest = generate(seed=3, payments=250).manifest
    result = []
    for scenario, expectation in EXPECTATIONS.items():
        details = scenario_details(manifest, scenario.value)
        facts = f" Planted details: {details}." if details else ""
        result.append({
            "inputs": {"scenario": scenario.value},
            "outputs": {
                "expected_category": expectation.category.value,
                "acceptable_resolutions": [r.value for r in expectation.acceptable],
                "expected_policy": expectation.policy,
                "root_cause": expectation.root_cause + facts,
            },
            "metadata": {"scenario": scenario.value},
        })
    return result


def sync_dataset(client) -> str:
    """Create the dataset, or replace its examples so they always match the current expectations."""
    if client.has_dataset(dataset_name=DATASET):
        dataset = client.read_dataset(dataset_name=DATASET)
        for example in client.list_examples(dataset_id=dataset.id):
            client.delete_example(example.id)
    else:
        dataset = client.create_dataset(DATASET, description="One case per planted reconciliation problem, with "
                                        "acceptable resolutions, governing policy and root cause.")
    client.create_examples(dataset_id=dataset.id, examples=examples())
    return str(dataset.id)


def run_experiment(scenarios: Iterable[str] | None = None,
                   prefix: str = "baseline") -> tuple[str, dict[str, tuple[float, int]]]:
    """Returns the experiment name and, per evaluator, the mean score and how many runs it graded."""
    from langsmith import Client, evaluate

    from finrecon_ai.agents.llm import default_client
    from finrecon_ai.policies.store import default_store
    from finrecon_ai.workflows.case_investigation import session_unit_of_work

    client = Client()
    sync_dataset(client)
    unit_of_work, store, llm = session_unit_of_work(), default_store(), default_client()
    with tempfile.TemporaryDirectory() as workdir:
        tenant = prepare_tenant(unit_of_work, store, Path(workdir))
        log.info("eval.tenant_ready", tenant_id=tenant.tenant_id, scenarios=len(tenant.cases))
        wanted = set(scenarios or tenant.cases)
        data = [e for e in client.list_examples(dataset_name=DATASET) if e.inputs["scenario"] in wanted
                and e.inputs["scenario"] in tenant.cases]

        def target(inputs: dict) -> dict:
            return investigate(unit_of_work, llm, store, tenant, inputs["scenario"])

        prompts = {agent.value: load_prompt(agent).ref for agent in
                   (AgentName.INVESTIGATION, AgentName.RISK_ANALYSIS, AgentName.RESOLUTION)}
        results = evaluate(
            target, data=data, evaluators=[*CODE_EVALUATORS, make_root_cause_judge(llm)],
            experiment_prefix=f"{prefix}-{prompts['investigation'].split('#')[0]}", max_concurrency=3,
            metadata={"prompts": prompts, "eval_tenant": str(tenant.tenant_id)},
        )
        return results.experiment_name, summarize(results)


def summarize(results: Iterable[dict]) -> dict[str, tuple[float, int]]:
    scores: dict[str, list[float]] = {}
    for row in results:
        for result in row["evaluation_results"]["results"]:
            if result.score is not None:
                scores.setdefault(result.key, []).append(float(result.score))
    return {key: (sum(values) / len(values), len(values)) for key, values in scores.items()}
