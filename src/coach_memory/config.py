"""Application configuration, loaded from the environment via pydantic-settings.

All runtime knobs live here. Field names map case-insensitively to env vars
(``database_url`` <- ``DATABASE_URL``), so the same object serves local `.env`
files and container env injection identically.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, model_validator
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

    # --- auth ---------------------------------------------------------------
    # AUTH_ENABLED=true (default): every /mcp request must carry a valid bearer
    # token (checked by the ASGI middleware). AUTH_ENABLED=false ("network-only"
    # mode): the middleware is NOT installed and requests pass through
    # unauthenticated — defence then relies entirely on the upstream nginx IP
    # allowlist + TLS + an unguessable URL. No OAuth flow is ever advertised in
    # either mode (no FastMCP auth provider is configured).
    auth_enabled: bool = True
    mcp_auth_token: str | None = Field(
        default=None,
        min_length=16,
        description="Static bearer token; required only when AUTH_ENABLED is true.",
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

    @model_validator(mode="after")
    def _require_token_when_auth_enabled(self) -> "Settings":
        if self.auth_enabled and not self.mcp_auth_token:
            raise ValueError(
                "MCP_AUTH_TOKEN is required when AUTH_ENABLED is true "
                "(set AUTH_ENABLED=false for network-only mode)."
            )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide Settings singleton (cached after first load)."""
    return Settings()  # type: ignore[call-arg]  # values come from the environment
