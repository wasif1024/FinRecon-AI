from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    # The login that owns the tables: migrations and checkpoint setup (finrecon db migrate). Unset: DATABASE_URL
    # owns them too (one login, as before). Set it so DATABASE_URL can be a role that owns nothing (db/roles.py).
    migration_database_url: str | None = None

    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-opus-5-5"

    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str | None = None
    embedding_model: str = "BAAI/bge-small-en-v1.5"  # local fastembed model; changing it means re-indexing
    document_storage_dir: str = "storage/documents"

    # API authentication. No default: the API refuses to start without a secret (fail-closed).
    jwt_secret: str | None = None
    jwt_ttl_minutes: int = 60
    cors_origins: str = "http://localhost:3000"  # comma-separated; the Phase 5 dashboard

    # LangSmith tracing (exported to os.environ by finrecon_ai.tracing.configure_tracing).
    langsmith_api_key: str | None = None
    langsmith_tracing: bool = False
    langsmith_project: str = "finrecon-ai"
    langsmith_endpoint: str | None = None
    langsmith_hide_content: bool = False
    # OpenTelemetry (telemetry.py): on when set, e.g. http://localhost:4318. Standard OTEL_* variables also apply.
    otel_exporter_otlp_endpoint: str | None = None  # keep trace structure, send no inputs/outputs (for real data)

    # Long-running workflows: at API startup, finish runs a previous process left mid-way.
    recover_on_startup: bool = True

    # Where agents keep working papers: one LangSmith sandbox per case (files only; agents never run code), or the
    # run's own state. Sandboxes stop after SANDBOX_IDLE_TTL_SECONDS idle and are deleted the given time later.
    agent_workspace: Literal["langsmith_sandbox", "state"] = "langsmith_sandbox"
    sandbox_idle_ttl_seconds: int = 600
    sandbox_delete_after_stop_seconds: int = 7 * 24 * 3600

    # LangGraph Agent Server (`langgraph dev`) and LangSmith Studio: a development tool. Production runs every
    # workflow in the API process (README, Deployment). Studio runs act as this user, through the same permission
    # checks as the API.
    environment: Literal["development", "production"] = "development"
    agent_server_url: str | None = "http://127.0.0.1:2024"  # empty: no Agent Server
    studio_tenant_slug: str = "demo"
    studio_actor_email: str = "manager@demo.finrecon.dev"
    batch_max_cases: int = 5  # case investigations one batch-investigator run may start (each calls Claude)

    log_level: str = "INFO"
    log_format: Literal["console", "json"] = "console"  # terminal output; the file is always JSON lines
    log_file: str | None = "logs/finrecon.log"  # empty string disables the file

    @property
    def uses_agent_server(self) -> bool:
        """Whether runs may live on an Agent Server: development with AGENT_SERVER_URL set. Never in production."""
        return self.environment == "development" and bool(self.agent_server_url)

    @property
    def owner_database_url(self) -> str:
        return self.migration_database_url or self.database_url


@lru_cache
def get_settings() -> Settings:
    return Settings()
