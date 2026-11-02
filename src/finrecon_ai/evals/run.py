"""LangSmith dataset and experiments for the agent workflow, run through the LangGraph Agent Server.

    uv run finrecon eval dataset              # sync evals/dataset.jsonl to LangSmith (free)
    uv run finrecon eval dataset --regenerate # rewrite the JSONL from evals/scenarios.py and the generator
    scripts/studio.sh                         # in another terminal: the Agent Server the runs go through
    uv run finrecon eval run                  # 13 scenarios x 3 repetitions (calls Claude; asks first)
    uv run finrecon eval compare <exp-a> <exp-b>   # pairwise judge, order randomised

Each repetition gets its own eval-* tenant, so every run investigates a fresh case. Experiments carry the git SHA,
whether the tree was dirty, a fingerprint of the agents' code and prompts (two experiments with the same
fingerprint ran the same agent), the prompt versions and the models.
"""

import hashlib
import json
import subprocess
import tempfile
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from finrecon_ai.agents.prompt_registry import load_prompt
from finrecon_ai.db.enums import AgentName
from finrecon_ai.evals.evaluators import CODE_EVALUATORS, judge_chat_model, make_root_cause_judge
from finrecon_ai.evals.harness import CasePool, investigate_on_server, prepare_tenant, scenario_details
from finrecon_ai.evals.scenarios import CATEGORY_PLAYBOOK, EXPECTATIONS
from finrecon_ai.log import get_logger
from finrecon_ai.synthetic.generator import generate

log = get_logger(__name__)

DATASET = "finrecon-agent-scenarios"
ROOT = Path(__file__).resolve().parents[3]
DATASET_FILE = ROOT / "evals" / "dataset.jsonl"
AGENTS = (AgentName.INVESTIGATION, AgentName.RISK_ANALYSIS, AgentName.RESOLUTION)
# What decides how the agents behave.
AGENT_FILES = ("src/finrecon_ai/agents/**/*.py", "src/finrecon_ai/agents/prompts/**/*", "src/finrecon_ai/agent_server.py",
               "src/finrecon_ai/workflows/case_investigation.py")
EST_COST_PER_CASE_USD = 0.9  # measured on the v3 agents (CASE-000022); the judge adds about a cent


# --- dataset (evals/dataset.jsonl is the source of truth; LangSmith is synced to it) ---


def examples() -> list[dict]:
    """One example per planted scenario; the reference carries the manifest's actual figures."""
    manifest = generate(seed=3, payments=250).manifest
    result = []
    for scenario, expectation in EXPECTATIONS.items():
        details = scenario_details(manifest, scenario.value)
        facts = f" Planted details: {json.dumps(details, sort_keys=True)}." if details else ""
        result.append({
            "metadata": {"case": scenario.value},
            "inputs": {"scenario": scenario.value},
            "outputs": {
                "expected_category": expectation.category.value,
                "acceptable_resolutions": [r.value for r in expectation.acceptable],
                "expected_policy": expectation.policy,
                "expected_playbook": CATEGORY_PLAYBOOK[expectation.category],
                "root_cause": expectation.root_cause + facts,
            },
        })
    return result


def write_dataset_file(path: Path = DATASET_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(e, sort_keys=True) + "\n" for e in examples()))


def load_examples(path: Path = DATASET_FILE) -> list[dict]:
    """Read and check the JSONL file. Raises ValueError on a malformed line."""
    found, seen = [], set()
    for number, line in enumerate(path.read_text().splitlines(), start=1):
        if not line.strip():
            continue
        example = json.loads(line)
        case = (example.get("metadata") or {}).get("case")
        if not isinstance(case, str) or not case:
            raise ValueError(f"line {number}: metadata.case is required")
        if case in seen:
            raise ValueError(f"line {number}: duplicate case {case!r}")
        if not (example.get("inputs") or {}).get("scenario"):
            raise ValueError(f"line {number}: inputs.scenario is required")
        seen.add(case)
        found.append(example)
    return found


def plan_sync(local: list[Mapping], remote: Iterable[Any]) -> tuple[list, list, list[str]]:
    """(to_create, to_update as (example_id, example), remote-only cases). Examples are matched by case, so past
    experiments keep pointing at the same examples; nothing is deleted."""
    def case_of(r):
        return (r.metadata or {}).get("case") or (r.inputs or {}).get("scenario")

    by_case = {case_of(r): r for r in remote if case_of(r)}
    create, update = [], []
    for example in local:
        existing = by_case.pop(example["metadata"]["case"], None)
        if existing is None:
            create.append(example)
        elif ((existing.inputs, existing.outputs or {}) != (example["inputs"], example["outputs"])
              or (existing.metadata or {}).get("case") != example["metadata"]["case"]):
            update.append((existing.id, example))
    return create, update, sorted(by_case)


def sync_dataset(client, path: Path = DATASET_FILE) -> dict[str, Any]:
    local = load_examples(path)
    if client.has_dataset(dataset_name=DATASET):
        dataset = client.read_dataset(dataset_name=DATASET)
    else:
        dataset = client.create_dataset(DATASET, description="One case per planted reconciliation problem, with "
                                        "acceptable resolutions, governing policy, playbook and root cause.")
    create, update, remote_only = plan_sync(local, client.list_examples(dataset_id=dataset.id))
    if create:
        client.create_examples(dataset_id=dataset.id, examples=create)
    for example_id, example in update:
        client.update_example(example_id, inputs=example["inputs"], outputs=example["outputs"],
                              metadata=example["metadata"])
    return {"created": len(create), "updated": len(update), "remote_only": remote_only}


# --- experiment metadata ---


def agent_fingerprint(root: Path = ROOT) -> str:
    """A short hash of the agents' code and prompts. Unlike the git SHA it also changes with uncommitted edits."""
    digest = hashlib.sha256()
    paths = sorted({p for pattern in AGENT_FILES for p in root.glob(pattern)
                    if p.is_file() and "__pycache__" not in p.parts})
    for path in paths:
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


def _git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def experiment_metadata() -> dict[str, Any]:
    from finrecon_ai.config import get_settings

    specs = {agent.value: load_prompt(agent) for agent in AGENTS}
    return {
        "git_sha": _git("rev-parse", "--short", "HEAD"),
        "git_dirty": bool(_git("status", "--porcelain")),
        "agent_fingerprint": agent_fingerprint(),
        "prompts": {name: spec.ref for name, spec in specs.items()},
        "models": sorted({spec.manifest.model for spec in specs.values()}
                         | {s.model or spec.manifest.model for spec in specs.values() for s in spec.manifest.subagents}),
        "workspace": get_settings().agent_workspace,
    }


def estimated_cost(scenarios: int, repetitions: int) -> float:
    return round(scenarios * repetitions * EST_COST_PER_CASE_USD, 2)


# --- running ---


def run_experiment(scenarios: Iterable[str] | None = None, prefix: str = "agents", repetitions: int = 3,
                   max_concurrency: int = 3, judge: bool = True, url: str | None = None) -> tuple[str, dict]:
    """Returns the experiment name and, per evaluator, the mean score and how many runs it graded."""
    from langgraph_sdk import get_sync_client
    from langsmith import Client, evaluate

    from finrecon_ai.config import get_settings
    from finrecon_ai.policies.store import default_store
    from finrecon_ai.workflows.case_investigation import session_unit_of_work

    client = Client()
    sync_dataset(client)
    agent_server = get_sync_client(url=url or get_settings().agent_server_url, timeout=900)
    agent_server.assistants.search(graph_id="case-investigation", limit=1)  # fail fast if the server is not up
    unit_of_work, store = session_unit_of_work(), default_store()
    wanted = set(scenarios or (s.value for s in EXPECTATIONS))
    with tempfile.TemporaryDirectory() as workdir:
        tenants = [prepare_tenant(unit_of_work, store, Path(workdir) / str(i)) for i in range(repetitions)]
        log.info("eval.tenants_ready", tenants=[t.slug for t in tenants])
        pool = CasePool(tenants)
        data = [e for e in client.list_examples(dataset_name=DATASET) if e.inputs["scenario"] in wanted
                and all(e.inputs["scenario"] in t.cases for t in tenants)]

        def target(inputs: dict) -> dict:
            tenant, case_id = pool.take(inputs["scenario"])
            return investigate_on_server(agent_server, unit_of_work, tenant, case_id, inputs["scenario"])

        evaluators = [*CODE_EVALUATORS, *([make_root_cause_judge(judge_chat_model())] if judge else [])]
        metadata = experiment_metadata() | {"eval_tenants": [t.slug for t in tenants]}
        results = evaluate(target, data=data, evaluators=evaluators, num_repetitions=repetitions,
                           max_concurrency=max_concurrency, metadata=metadata,
                           experiment_prefix=f"{prefix}-{metadata['agent_fingerprint']}",
                           description="Planted-scenario regression: completion, resolution, grounding, policy, "
                                       "evidence, playbook, fraud language, root cause.")
        return results.experiment_name, summarize(results)


def summarize(results: Iterable[dict]) -> dict[str, tuple[float, int]]:
    scores: dict[str, list[float]] = {}
    for row in results:
        for result in row["evaluation_results"]["results"]:
            if result.score is not None:
                scores.setdefault(result.key, []).append(float(result.score))
    return {key: (sum(values) / len(values), len(values)) for key, values in scores.items()}


def compare_experiments(experiment_a: str, experiment_b: str, max_concurrency: int = 2) -> None:
    from langsmith import evaluate

    from finrecon_ai.evals.evaluators import make_pairwise_judge, pairwise_judge_model

    evaluate((experiment_a, experiment_b), evaluators=[make_pairwise_judge(pairwise_judge_model())],
             randomize_order=True, max_concurrency=max_concurrency)
