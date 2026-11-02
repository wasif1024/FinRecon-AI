#!/usr/bin/env bash
# Rebuild the Docker demo from demo/snapshot.tar.gz: real investigations (made earlier by the agents; loading calls
# no model), every exception category, decided and pending approvals, the policy library and an intact audit trail.
#
#   scripts/demo.sh              wipe the Compose PostgreSQL, load the snapshot, set the demo users' password
#   DEMO_PASSWORD=... scripts/demo.sh --yes   no questions (e.g. for the e2e suite)
#
# Only the Compose database (volume postgres_data) is replaced; Qdrant and your local PostgreSQL are left alone.
# Then open http://localhost:3000 and sign in as manager@ / approver@ / analyst@ / admin@ / auditor@demo.finrecon.dev.
set -euo pipefail
cd "$(dirname "$0")/.."

YES=false
[[ "${1:-}" == "--yes" ]] && YES=true
SNAPSHOT=demo/snapshot.tar.gz
[[ -f $SNAPSHOT ]] || { echo "missing $SNAPSHOT" >&2; exit 1; }
[[ -f .env ]] || { echo "no .env: cp .env.example .env and fill in the values (see README, Docker)" >&2; exit 1; }
if ! command -v docker-credential-desktop >/dev/null && [[ -d /Applications/Docker.app/Contents/Resources/bin ]]; then
  export PATH="$PATH:/Applications/Docker.app/Contents/Resources/bin"
fi

if ! $YES; then
  read -r -p "This replaces the Docker demo database (not your local PostgreSQL). Continue? [y/N] " answer
  [[ "$answer" =~ ^[Yy]$ ]] || exit 1
fi
password="${DEMO_PASSWORD:-}"
if [[ -z "$password" ]]; then
  read -r -s -p "Password for the demo users (12+ characters): " password; echo
fi
(( ${#password} >= 12 )) || { echo "the password needs at least 12 characters" >&2; exit 1; }

echo "==> Fresh database"
docker compose --profile web --profile app rm -sf postgres migrate api frontend >/dev/null 2>&1 || true
volume="$(docker compose config --format json | python3 -c "import sys,json;print(json.load(sys.stdin)['name'])")_postgres_data"
docker volume rm "$volume" >/dev/null 2>&1 || true
docker compose --profile web up -d --build --wait

echo "==> Loading $SNAPSHOT"
# The app container sees the repository's demo/ folder through ./storage only, so stage the file there.
mkdir -p storage && cp "$SNAPSHOT" storage/.demo-snapshot.tar.gz
trap 'rm -f storage/.demo-snapshot.tar.gz' EXIT
docker compose run --rm -T app demo load /app/storage/.demo-snapshot.tar.gz 2>&1 | grep -v '"level"'

echo "==> Demo users' password"
for user in manager approver analyst admin auditor; do
  printf '%s' "$password" | docker compose run --rm -T app users set-password "$user@demo.finrecon.dev" \
    --password-stdin 2>&1 | grep -v '"level"'
done

cat <<DONE

Demo ready: http://localhost:3000  (API http://localhost:8000/docs)
  manager@demo.finrecon.dev    started the investigations; may not approve them (separation of duties)
  approver@demo.finrecon.dev   a second manager: decides the 13 pending recommendations (Approvals)
  analyst@demo.finrecon.dev    works assigned cases; 15 new cases can be investigated live (calls Claude)
  admin@ / auditor@            policies; read-only audit trail and its hash-chain check
DONE
