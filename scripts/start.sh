#!/usr/bin/env bash
# Start the whole stack for local development in one command:
#   checks .env, installs dependencies, starts PostgreSQL and Docker (Qdrant) if needed, applies pending
#   migrations, then runs the API (:8000) and the dashboard (:3000) together. Ctrl-C stops both.
#
#   scripts/start.sh            API + dashboard (next dev)
#   scripts/start.sh --prod     dashboard as a production build (next build + start)
#   scripts/start.sh --studio   also the LangGraph Agent Server for LangSmith Studio (:2024)
#   scripts/start.sh --otel     also Grafana for OpenTelemetry (:3001), with the API and dashboard exporting to it
#   scripts/start.sh --check    run every check and migration, start nothing
#
# Values are read from .env (and frontend/.env.local); variables already exported in the shell win, as they do
# for the app itself. Secrets are only checked for being set, never printed.
set -euo pipefail
cd "$(dirname "$0")/.."

PROD=false STUDIO=false OTEL=false CHECK_ONLY=false
for arg in "$@"; do
  case "$arg" in
    --prod) PROD=true ;;
    --studio) STUDIO=true ;;
    --otel) OTEL=true ;;
    --check) CHECK_ONLY=true ;;
    -h|--help) sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $arg (see --help)" >&2; exit 2 ;;
  esac
done

if [[ -t 1 ]]; then B=$'\e[1m' R=$'\e[31m' G=$'\e[32m' Y=$'\e[33m' N=$'\e[0m'; else B="" R="" G="" Y="" N=""; fi
step() { echo "${B}==>${N} $*"; }
ok() { echo "    ${G}✓${N} $*"; }
warn() { echo "    ${Y}!${N} $*"; }
fail() { echo "    ${R}✗${N} $*" >&2; exit 1; }

# The value of KEY: the shell's environment first, then the last KEY=... line of the file (quotes stripped).
value() {
  local key=$1 file=$2
  if [[ -n "${!key:-}" ]]; then printf '%s' "${!key}"; return; fi
  [[ -f $file ]] || return 0
  grep -E "^${key}=" "$file" | tail -1 | cut -d= -f2- | sed -E "s/^[\"'](.*)[\"']$/\1/" || true
}

port_free() { ! lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1; }

# --- tools ---
step "Tools"
# Docker Desktop's credential helper (needed to pull images) lives in the app bundle; add it if it is not on PATH.
if ! command -v docker-credential-desktop >/dev/null && [[ -d /Applications/Docker.app/Contents/Resources/bin ]]; then
  export PATH="$PATH:/Applications/Docker.app/Contents/Resources/bin"
fi
for tool in uv pnpm docker lsof; do
  command -v "$tool" >/dev/null || fail "$tool is not installed"
done
ok "uv, pnpm, docker"

# --- environment ---
step "Environment"
[[ -f .env ]] || fail "no .env: cp .env.example .env and fill in the values"
if [[ ! -f frontend/.env.local ]]; then
  cp frontend/.env.example frontend/.env.local
  sed -i.bak "s|^API_URL=.*|API_URL=http://127.0.0.1:8000|" frontend/.env.local && rm -f frontend/.env.local.bak
  warn "created frontend/.env.local (API_URL=http://127.0.0.1:8000)"
fi

missing=()
need() { [[ -n "$(value "$1" "${2:-.env}")" ]] || missing+=("$1${3:+ ($3)}"); }
need DATABASE_URL
need QDRANT_URL
need JWT_SECRET
need ANTHROPIC_API_KEY "" "agent investigations call Claude"
tracing=$(value LANGSMITH_TRACING .env)
workspace=$(value AGENT_WORKSPACE .env)
if [[ "$(printf %s "$tracing" | tr "[:upper:]" "[:lower:]")" == "true" || "${workspace:-langsmith_sandbox}" == "langsmith_sandbox" ]]; then
  need LANGSMITH_API_KEY "" "LANGSMITH_TRACING=true or AGENT_WORKSPACE=langsmith_sandbox"
fi
need API_URL frontend/.env.local
if (( ${#missing[@]} )); then
  printf '    %s✗%s not set: %s\n' "$R" "$N" "${missing[@]}" >&2
  fail "fill these in .env (API_URL in frontend/.env.local)"
fi
secret=$(value JWT_SECRET .env)
(( ${#secret} >= 32 )) || fail "JWT_SECRET must be at least 32 characters (python -c \"import secrets; print(secrets.token_urlsafe(48))\")"
unset secret
ok "required variables are set"
if [[ "$(value RECOVER_ON_STARTUP .env)" != "false" ]]; then
  warn "RECOVER_ON_STARTUP is on: the API finishes interrupted agent runs at startup, which calls Claude"
fi

# --- dependencies ---
step "Dependencies"
uv sync --frozen --quiet
ok "Python (uv sync)"
(cd frontend && pnpm install --frozen-lockfile --silent)
ok "frontend (pnpm install)"

# --- PostgreSQL ---
step "PostgreSQL"
read -r PG_HOST PG_PORT < <(uv run --frozen python -c "
from sqlalchemy.engine import make_url; import sys
u = make_url(sys.argv[1]); print(u.host or 'localhost', u.port or 5432)" "$(value DATABASE_URL .env)")
pg_ready() { pg_isready -q -h "$PG_HOST" -p "$PG_PORT"; }
if ! pg_ready; then
  service=$(command -v brew >/dev/null && brew services list 2>/dev/null | awk '/^postgresql/ {print $1}' | sort -V | tail -1)
  [[ -n "${service:-}" && "$PG_HOST" =~ ^(localhost|127\.0\.0\.1)$ ]] \
    || fail "PostgreSQL is not accepting connections at $PG_HOST:$PG_PORT; start it and run this again"
  warn "starting $service (brew services)"
  brew services start "$service" >/dev/null
  for _ in $(seq 30); do pg_ready && break; sleep 1; done
  pg_ready || fail "PostgreSQL did not come up at $PG_HOST:$PG_PORT"
fi
ok "accepting connections at $PG_HOST:$PG_PORT"

# --- Docker and Qdrant ---
step "Docker (Qdrant)"
if ! docker info >/dev/null 2>&1; then
  [[ "$(uname)" == "Darwin" ]] || fail "the Docker daemon is not running; start it and run this again"
  warn "starting Docker Desktop"
  open -a Docker
  for _ in $(seq 90); do docker info >/dev/null 2>&1 && break; sleep 2; done
  docker info >/dev/null 2>&1 || fail "Docker did not start within 3 minutes"
fi
docker compose up -d --wait qdrant >/dev/null 2>&1 || fail "Qdrant did not become healthy (docker compose logs qdrant)"
ok "Qdrant is healthy"
if $OTEL; then
  docker compose --profile otel up -d otel-lgtm >/dev/null 2>&1 || fail "could not start Grafana (docker compose logs otel-lgtm)"
  for _ in $(seq 60); do curl -fs http://127.0.0.1:3001/api/health >/dev/null 2>&1 && break; sleep 2; done
  curl -fs http://127.0.0.1:3001/api/health >/dev/null 2>&1 || fail "Grafana did not come up on :3001"
  # The API and the dashboard export to it (unless .env or the shell already names a collector).
  export OTEL_EXPORTER_OTLP_ENDPOINT="${OTEL_EXPORTER_OTLP_ENDPOINT:-$(value OTEL_EXPORTER_OTLP_ENDPOINT .env)}"
  export OTEL_EXPORTER_OTLP_ENDPOINT="${OTEL_EXPORTER_OTLP_ENDPOINT:-http://127.0.0.1:4318}"
  ok "Grafana is up; exporting to $OTEL_EXPORTER_OTLP_ENDPOINT"
fi

# --- migrations and logins ---
step "Database migrations"
# As the owner (MIGRATION_DATABASE_URL): pending migrations, LangGraph's checkpoint tables, and the application
# role's rights. Idempotent; prints what it applied.
migrate_log=$(uv run --frozen finrecon db migrate 2>&1) || { echo "$migrate_log" >&2; fail "finrecon db migrate failed"; }
applied=$(printf '%s\n' "$migrate_log" | sed -n 's/.*Running upgrade \(.*\)$/\1/p')
if [[ -n "$applied" ]]; then printf '%s\n' "$applied" | while read -r line; do ok "applied $line"; done; else ok "schema up to date"; fi
if uv run --frozen finrecon db check >/dev/null 2>&1; then
  ok "the API's login reads and writes rows only"
else
  warn "DATABASE_URL owns the tables: give the API its own role (README, Database logins)"
fi

if $CHECK_ONLY; then
  echo "${G}All checks passed.${N}"
  exit 0
fi

# --- run ---
step "Starting"
ports=(8000 3000)
$STUDIO && ports+=(2024)
for port in "${ports[@]}"; do
  port_free "$port" || fail "port $port is in use (lsof -nP -iTCP:$port -sTCP:LISTEN)"
done

pids=()
stop() {
  trap - INT TERM EXIT
  echo
  step "Stopping"
  # Each service runs in its own process group (set -m below): signal the whole group, so uv's Python and
  # Next's workers stop too, then give them a few seconds before forcing it.
  for pid in ${pids[@]+"${pids[@]}"}; do kill -TERM -- "-$pid" 2>/dev/null || true; done
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    alive=false
    for pid in ${pids[@]+"${pids[@]}"}; do kill -0 -- "-$pid" 2>/dev/null && alive=true; done
    $alive || break
    sleep 1
  done
  for pid in ${pids[@]+"${pids[@]}"}; do kill -KILL -- "-$pid" 2>/dev/null || true; done
  ok "stopped"
  exit "${1:-0}"
}
trap 'stop 130' INT TERM
trap 'stop $?' EXIT
set -m  # background jobs get their own process groups

# Each process's output, prefixed with its name.
run() {
  local name=$1; shift
  ( "$@" 2>&1 | sed -u "s/^/[$name] /" ) &
  pids+=($!)
  disown $!  # tracked here, not as a shell job (no "Terminated" notices)
}

run api uv run --frozen finrecon serve --port 8000
if $PROD; then
  (cd frontend && pnpm build) || fail "frontend build failed"
  run web bash -c "cd frontend && pnpm start"
else
  run web bash -c "cd frontend && pnpm dev"
fi
$STUDIO && run studio scripts/studio.sh

for _ in $(seq 60); do curl -fs http://127.0.0.1:8000/health >/dev/null 2>&1 && break; sleep 1; done
curl -fs http://127.0.0.1:8000/health >/dev/null 2>&1 && ok "API      http://127.0.0.1:8000 (docs: /docs)" \
  || warn "API not healthy yet; see the [api] lines"
for _ in $(seq 60); do curl -fs -o /dev/null http://127.0.0.1:3000/login 2>/dev/null && break; sleep 1; done
ok "Dashboard http://localhost:3000"
$STUDIO && ok "Studio    https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:2024 (Chrome)"
$OTEL && ok "Grafana   http://localhost:3001 (Explore: Tempo for traces, Prometheus for metrics, Loki for logs)"
echo "    Ctrl-C stops everything."

# Exit (and stop the rest) as soon as any of them dies.
while true; do
  for pid in ${pids[@]+"${pids[@]}"}; do
    kill -0 "$pid" 2>/dev/null || { warn "a process exited; stopping the others"; stop 1; }
  done
  sleep 2
done
