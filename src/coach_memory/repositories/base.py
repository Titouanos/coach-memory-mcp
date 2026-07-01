"""BaseRepository — the common base every repository extends.

Repositories are thin data-access objects: they own the SQL and return typed
Pydantic models. Tools stay free of SQL and business rules. Everything shares
one async pool acquired via ``async with self.pool.connection() as conn``.
"""

from __future__ import annotations

from psycopg_pool import AsyncConnectionPool


class BaseRepository:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self.pool = pool
