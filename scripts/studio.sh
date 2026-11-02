#!/usr/bin/env bash
# Serve the graphs for LangSmith Studio. Ctrl-C stops the server and any running case sandboxes, so you stop
# paying for sandbox compute right away instead of waiting for the idle timeout.
# Run from the project root: scripts/studio.sh
set -e
cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
    echo "No .env found. Run: cp .env.example .env  and fill in your keys." >&2
    exit 1
fi

cleanup() {
    echo "Stopping running case sandboxes ..."
    uv run finrecon sandboxes stop || true
}
trap cleanup EXIT INT TERM

echo "Studio: https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:2024 (Chrome)"
# Case investigations hold a worker slot for their whole run; allow several at once.
uv run langgraph dev --no-browser --n-jobs-per-worker 10
