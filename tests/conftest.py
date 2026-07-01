"""Test fixtures.

Integration tests run against a real Postgres (the compose DB, or any Postgres
pointed at by TEST_DATABASE_URL / DATABASE_URL). If neither is set, the suite
skips rather than failing — so `pytest` is safe to run without a database.

The `client` fixture builds the real server, drives it through FastMCP's
in-memory transport (which runs the lifespan → opens the pool and applies
migrations), then truncates all tables for per-test isolation.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import pytest_asyncio
from fastmcp import Client

from coach_memory.config import Settings
from coach_memory.server import build_server

REPO_ROOT = Path(__file__).resolve().parents[1]
_DB = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

# Truncate order is irrelevant with CASCADE; athlete_profile is a singleton.
_TABLES = (
    "coach.sessions",
    "coach.goals",
    "coach.races",
    "coach.coaching_decisions",
    "coach.analyses",
    "coach.athlete_profile",
)


@pytest.fixture(scope="session")
def settings() -> Settings:
    if not _DB:
        pytest.skip("set TEST_DATABASE_URL or DATABASE_URL to run integration tests")
    return Settings(
        database_url=_DB,
        mcp_auth_token="t" * 32,
        migrations_dir=str(REPO_ROOT / "migrations"),
    )


async def _truncate(pool) -> None:
    async with pool.connection() as conn:
        async with conn.transaction():
            for table in _TABLES:
                await conn.execute(f"TRUNCATE {table} RESTART IDENTITY CASCADE")


@pytest_asyncio.fixture
async def client(settings):
    mcp, pool = build_server(settings)
    async with Client(mcp) as connected:
        # entering the client runs the server lifespan (pool open + migrations)
        await _truncate(pool)
        yield connected
