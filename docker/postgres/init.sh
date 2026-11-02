#!/bin/bash
# First start of the Compose PostgreSQL only (an empty data volume): the two logins the application uses.
#   finrecon      owns the database and its tables; runs migrations (finrecon db migrate)
#   finrecon_app  the API's login: rows only, granted by finrecon db migrate
# Passwords come from .env (FINRECON_DB_PASSWORD, FINRECON_APP_DB_PASSWORD) and are not echoed.
set -euo pipefail

psql -v ON_ERROR_STOP=1 --quiet --username "$POSTGRES_USER" --dbname postgres \
     -v owner_password="$FINRECON_DB_PASSWORD" -v app_password="$FINRECON_APP_DB_PASSWORD" <<'SQL'
CREATE ROLE finrecon LOGIN PASSWORD :'owner_password' NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
CREATE ROLE finrecon_app LOGIN PASSWORD :'app_password' NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
CREATE DATABASE finrecon OWNER finrecon;
SQL
psql -v ON_ERROR_STOP=1 --quiet --username "$POSTGRES_USER" --dbname finrecon \
     -c "REVOKE ALL ON DATABASE finrecon FROM PUBLIC"
