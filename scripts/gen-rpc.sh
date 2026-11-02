#!/usr/bin/env bash
# Lint proto/ and regenerate the Python and TypeScript code from it. Needs `uv sync` and `pnpm install` (in frontend/).
#   scripts/gen-rpc.sh          lint + generate
#   scripts/gen-rpc.sh --check  also fail if the committed generated code differs (CI)
set -euo pipefail
cd "$(dirname "$0")/.."
BUF=frontend/node_modules/.bin/buf

$BUF lint proto
rm -rf src/finrecon/v1 frontend/src/gen/finrecon
$BUF generate
# Generated packages carry no code of their own.
touch src/finrecon/__init__.py src/finrecon/v1/__init__.py

if [[ "${1:-}" == "--check" ]]; then
  git diff --exit-code -- src/finrecon frontend/src/gen
  test -z "$(git status --porcelain -- src/finrecon frontend/src/gen)"
fi
