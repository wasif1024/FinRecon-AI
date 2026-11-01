# Agent prompts

Prompts are versioned files, kept out of Python code so they can be reviewed, diffed and evaluated on their own.

```
prompts/
  registry.yaml              # which version of each agent is active
  prompts.lock.json          # content hashes of released versions (immutable)
  _shared/                   # fragments included by several agents
  <agent>/<version>/
    manifest.yaml            # model, effort, limits, allowed tools, output schema, changelog
    system.md                # system prompt; may include shared fragments
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
- **Tools are allow-listed** per version in the manifest; the runtime exposes nothing else to the model.
- **Every agent run records** `<agent>@<version>#<hash>` in `agent_execution_records.workflow_version`,
  so any output can be traced to the exact prompt text that produced it.
