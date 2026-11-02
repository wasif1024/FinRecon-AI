from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str

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
    langsmith_hide_content: bool = False  # keep trace structure, send no inputs/outputs (for real data)

    log_level: str = "INFO"
    log_format: Literal["console", "json"] = "console"  # terminal output; the file is always JSON lines
    log_file: str | None = "logs/finrecon.log"  # empty string disables the file


@lru_cache
def get_settings() -> Settings:
    return Settings()
