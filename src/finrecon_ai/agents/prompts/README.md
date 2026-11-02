# Agent prompts

Prompts are versioned files, kept out of Python code so they can be reviewed, diffed and evaluated on their own.

```
prompts/
  registry.yaml              # which version of each agent is active
  prompts.lock.json          # content hashes of released versions (immutable)
  _shared/                   # fragments included by several agents
  _memory/AGENTS.md          # operating manual, loaded as Deep Agents memory (versions list it under `memory`)
  _skills/<name>/SKILL.md    # playbook per exception category (versions list them under `skills`)
  coordinators/              # ops-assistant and batch-investigator prompts (Agent Server only)
  <agent>/<version>/
    manifest.yaml            # model, effort, limits, allowed tools, subagents, output schema, changelog
    system.md                # system prompt; may include shared fragments
    <subagent>.md            # one prompt per subagent declared in the manifest
```

## Rules

- **Released versions never change.** `status: released` versions are hashed in `prompts.lock.json`;
  the test suite fails if their content (including included fragments) changes. To change a released
  prompt, copy it to a new version folder, edit, and point `registry.yaml` at it.
- **Drafts may change freely.** Release a draft by setting `status: released` and running
  `uv run finrecon prompts lock`.
- **System prompts are static.** No timestamps, IDs or case data in `system.md`: that keeps the prompt
  cacheable and keeps untrusted data out of instructions. Case data goes in the user message.
- **Includes:** `{{> _shared/name.md }}` inlines a fragment. Unresolved `{{` fails loading.
- **Tools are allow-listed** per version in the manifest, and per subagent. Besides them the model sees only
  the Deep Agents harness tools: `write_todos`, scratch files held in the run's state, and `task` for the
  declared subagents (no shell, no general-purpose subagent).
- **Subagents** are declared in the manifest (`name`, `description` the parent reads, `system` prompt file,
  `tools`, `max_turns`). Their prompts are part of the version hash.
- **Every agent run records** `<agent>@<version>#<hash>` in `agent_execution_records.workflow_version`,
  so any output can be traced to the exact prompt text that produced it.
