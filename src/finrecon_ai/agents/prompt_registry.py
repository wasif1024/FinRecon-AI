"""Load versioned agent prompts from agents/prompts/ (see the README there)."""

import hashlib
import json
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict

from finrecon_ai.db.enums import AgentName

PROMPTS_DIR = Path(__file__).parent / "prompts"
LOCK_FILE = PROMPTS_DIR / "prompts.lock.json"
INCLUDE = re.compile(r"\{\{>\s*([\w./-]+)\s*\}\}")
MAX_INCLUDE_DEPTH = 5


class PromptError(Exception):
    pass


class Manifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: AgentName
    version: str
    status: Literal["draft", "released"]
    description: str
    model: str
    effort: Literal["low", "medium", "high", "xhigh", "max"]
    max_tokens: int
    max_turns: int
    system: str
    output_schema: str
    tools: tuple[str, ...]
    changelog: tuple[dict[str, str], ...] = ()


@dataclass(frozen=True)
class PromptSpec:
    manifest: Manifest
    system_text: str
    sha256: str  # over manifest + fully resolved system text

    @property
    def ref(self) -> str:
        """Recorded on every agent execution: which prompt produced the output."""
        return f"{self.manifest.id}@{self.manifest.version}#{self.sha256[:12]}"


def active_version(agent: AgentName) -> str:
    registry = yaml.safe_load((PROMPTS_DIR / "registry.yaml").read_text())
    try:
        return registry["agents"][agent.value]
    except KeyError:
        raise PromptError(f"no active prompt version for agent {agent}") from None


@cache
def load_prompt(agent: AgentName, version: str | None = None) -> PromptSpec:
    version = version or active_version(agent)
    folder = PROMPTS_DIR / agent.value / version
    manifest_path = folder / "manifest.yaml"
    if not manifest_path.is_file():
        raise PromptError(f"prompt {agent}@{version} not found")
    raw_manifest = manifest_path.read_text()
    manifest = Manifest.model_validate(yaml.safe_load(raw_manifest))
    if manifest.id != agent or manifest.version != version:
        raise PromptError(f"{manifest_path} declares {manifest.id}@{manifest.version}")

    system_text = _resolve_includes((folder / manifest.system).read_text(), depth=0).strip() + "\n"
    if "{{" in system_text:
        raise PromptError(f"{agent}@{version}: unresolved template marker in system prompt")
    digest = hashlib.sha256((raw_manifest + "\0" + system_text).encode()).hexdigest()
    return PromptSpec(manifest=manifest, system_text=system_text, sha256=digest)


def all_versions() -> list[tuple[AgentName, str]]:
    found = []
    for agent in AgentName:
        agent_dir = PROMPTS_DIR / agent.value
        if agent_dir.is_dir():
            found += [(agent, d.name) for d in sorted(agent_dir.iterdir()) if (d / "manifest.yaml").is_file()]
    return found


def read_lock() -> dict[str, str]:
    return json.loads(LOCK_FILE.read_text()) if LOCK_FILE.is_file() else {}


def lock_released() -> list[str]:
    """Adds hashes for newly released versions. Never rewrites an existing entry."""
    lock = read_lock()
    added = []
    for agent, version in all_versions():
        spec = load_prompt(agent, version)
        key = f"{agent.value}@{version}"
        if spec.manifest.status != "released":
            continue
        if key in lock and lock[key] != spec.sha256:
            raise PromptError(f"{key} is released and its content changed; create a new version instead")
        if key not in lock:
            lock[key] = spec.sha256
            added.append(key)
    LOCK_FILE.write_text(json.dumps(dict(sorted(lock.items())), indent=2) + "\n")
    return added


def _resolve_includes(text: str, depth: int) -> str:
    if depth > MAX_INCLUDE_DEPTH:
        raise PromptError("prompt includes nested too deeply (cycle?)")

    def replace(match: re.Match[str]) -> str:
        path = (PROMPTS_DIR / match.group(1)).resolve()
        if PROMPTS_DIR.resolve() not in path.parents or not path.is_file():
            raise PromptError(f"include not found: {match.group(1)}")
        return _resolve_includes(path.read_text(), depth + 1).strip()

    return INCLUDE.sub(replace, text)
