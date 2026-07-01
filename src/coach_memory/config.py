"""Application configuration, loaded from the environment via pydantic-settings.

All runtime knobs live here. Field names map case-insensitively to env vars
(``database_url`` <- ``DATABASE_URL``), so the same object serves local `.env`
files and container env injection identically.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- required -----------------------------------------------------------
    database_url: str = Field(
        ...,
        description="psycopg-style DSN, e.g. postgresql://user:pass@postgres:5432/coach_memory",
    )
    mcp_auth_token: str = Field(
        ...,
        min_length=16,
        description="Static bearer token required on every /mcp request.",
    )

    # --- server -------------------------------------------------------------
    host: str = "0.0.0.0"
    port: int = 8000
    mcp_path: str = "/mcp"
    server_name: str = "coach-memory"

    # Optional explicit path to the migrations/ directory. Defaults (when None)
    # to the repo-relative location resolved in db.py — set MIGRATIONS_DIR when
    # the package is installed away from the source tree (e.g. in a container).
    migrations_dir: str | None = None

    # --- pool ---------------------------------------------------------------
    db_pool_min: int = 1
    db_pool_max: int = 10
    db_connect_timeout: float = 10.0

    # --- observability ------------------------------------------------------
    log_level: str = "INFO"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide Settings singleton (cached after first load)."""
    return Settings()  # type: ignore[call-arg]  # values come from the environment
