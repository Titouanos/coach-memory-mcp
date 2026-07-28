"""Races repository."""

from __future__ import annotations

import datetime as dt

from ..models import Race
from .base import BaseRepository

_COLS = (
    "id, name, date, distance, priority, status, target_time, actual_time, "
    "result_notes, goal_id, created_at"
)


class RaceRepository(BaseRepository):
    async def _ensure_goal_exists(self, conn, goal_id: int) -> None:
        cur = await conn.execute("SELECT 1 FROM coach.goals WHERE id = %s", (goal_id,))
        if await cur.fetchone() is None:
            raise LookupError(f"goal {goal_id} not found")

    async def create(
        self,
        *,
        name: str,
        date: dt.date,
        distance: str | None = None,
        priority: str = "B",
        target_time: str | None = None,
        goal_id: int | None = None,
    ) -> Race:
        async with self.pool.connection() as conn:
            if goal_id is not None:
                await self._ensure_goal_exists(conn, goal_id)
            cur = await conn.execute(
                f"""
                INSERT INTO coach.races (name, date, distance, priority, target_time, goal_id)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING {_COLS}
                """,
                (name, date, distance, priority, target_time, goal_id),
            )
            row = await cur.fetchone()
        assert row is not None
        return Race(**row)

    async def link_goal(self, race_id: int, goal_id: int) -> Race | None:
        """Attach an existing race to a goal. Returns None if the race is missing."""
        async with self.pool.connection() as conn:
            await self._ensure_goal_exists(conn, goal_id)
            cur = await conn.execute(
                f"UPDATE coach.races SET goal_id = %s WHERE id = %s RETURNING {_COLS}",
                (goal_id, race_id),
            )
            row = await cur.fetchone()
        return Race(**row) if row else None

    async def record_result(
        self, race_id: int, actual_time: str, result_notes: str | None = None
    ) -> Race | None:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                f"""
                UPDATE coach.races
                SET actual_time = %s, result_notes = %s, status = 'done'
                WHERE id = %s
                RETURNING {_COLS}
                """,
                (actual_time, result_notes, race_id),
            )
            row = await cur.fetchone()
        return Race(**row) if row else None

    async def update_status(self, race_id: int, status: str) -> Race | None:
        """Change le statut d une course. Retourne None si elle n existe pas."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                f"UPDATE coach.races SET status = %s WHERE id = %s RETURNING {_COLS}",
                (status, race_id),
            )
            row = await cur.fetchone()
        return Race(**row) if row else None

    async def delete(self, race_id: int) -> Race | None:
        """Supprime definitivement une course.

        Reserve aux saisies erronees. Pour une course annulee, preferer
        update_status(..., "cancelled") : cela conserve la trace qu elle avait
        ete planifiee, ce qui est le propre d une memoire de coaching.
        """
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                f"DELETE FROM coach.races WHERE id = %s RETURNING {_COLS}",
                (race_id,),
            )
            row = await cur.fetchone()
        return Race(**row) if row else None

    async def upcoming(
        self,
        *,
        on_or_after: dt.date,
        priorities: list[str] | None = None,
        limit: int | None = None,
        include_cancelled: bool = False,
    ) -> list[Race]:
        clauses = ["date >= %s"]
        params: list[object] = [on_or_after]
        # Une course annulee n est plus « a venir » : l exclure par defaut evite
        # qu une planification automatique continue de compter avec elle.
        if not include_cancelled:
            clauses.append("status <> 'cancelled'")
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
