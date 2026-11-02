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

    log_level: str = "INFO"
    log_format: Literal["console", "json"] = "console"  # terminal output; the file is always JSON lines
    log_file: str | None = "logs/finrecon.log"  # empty string disables the file


@lru_cache
def get_settings() -> Settings:
    return Settings()
