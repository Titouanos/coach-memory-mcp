"""Races repository."""

from __future__ import annotations

import datetime as dt

from ..models import Race
from .base import BaseRepository

_COLS = (
    "id, name, date, distance, priority, target_time, actual_time, result_notes, created_at"
)


class RaceRepository(BaseRepository):
    async def create(
        self,
        *,
        name: str,
        date: dt.date,
        distance: str | None = None,
        priority: str = "B",
        target_time: str | None = None,
    ) -> Race:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                f"""
                INSERT INTO coach.races (name, date, distance, priority, target_time)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING {_COLS}
                """,
                (name, date, distance, priority, target_time),
            )
            row = await cur.fetchone()
        assert row is not None
        return Race(**row)

    async def record_result(
        self, race_id: int, actual_time: str, result_notes: str | None = None
    ) -> Race | None:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                f"""
                UPDATE coach.races
                SET actual_time = %s, result_notes = %s
                WHERE id = %s
                RETURNING {_COLS}
                """,
                (actual_time, result_notes, race_id),
            )
            row = await cur.fetchone()
        return Race(**row) if row else None

    async def upcoming(
        self,
        *,
        on_or_after: dt.date,
        priorities: list[str] | None = None,
        limit: int | None = None,
    ) -> list[Race]:
        clauses = ["date >= %s"]
        params: list[object] = [on_or_after]
        if priorities:
            clauses.append("priority = ANY(%s)")
            params.append(priorities)
        where = " AND ".join(clauses)
        limit_sql = ""
        if limit is not None:
            limit_sql = "LIMIT %s"
            params.append(limit)
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                f"SELECT {_COLS} FROM coach.races WHERE {where} ORDER BY date ASC {limit_sql}",
                params,
            )
            rows = await cur.fetchall()
        return [Race(**r) for r in rows]
