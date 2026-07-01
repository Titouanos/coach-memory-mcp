"""Coaching-decisions repository — the anti-staleness core.

Logging a decision that supersedes another flips the old one to ``active=false``
in the SAME transaction, so history is preserved but only current guidance
surfaces in the context digest.
"""

from __future__ import annotations

import datetime as dt

from psycopg.types.json import Jsonb

from ..models import CoachingDecision
from .base import BaseRepository

_COLS = (
    "id, date, decision, rationale, context_snapshot, supersedes_id, active, created_at"
)


class DecisionRepository(BaseRepository):
    async def create(
        self,
        *,
        decision: str,
        rationale: str | None = None,
        context_snapshot: dict | None = None,
        supersedes_id: int | None = None,
        date: dt.date,
    ) -> CoachingDecision:
        async with self.pool.connection() as conn:
            async with conn.transaction():
                if supersedes_id is not None:
                    cur = await conn.execute(
                        "UPDATE coach.coaching_decisions SET active = false WHERE id = %s",
                        (supersedes_id,),
                    )
                    if cur.rowcount == 0:
                        raise LookupError(
                            f"cannot supersede decision {supersedes_id}: not found"
                        )
                cur = await conn.execute(
                    f"""
                    INSERT INTO coach.coaching_decisions
                        (date, decision, rationale, context_snapshot, supersedes_id)
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING {_COLS}
                    """,
                    (
                        date,
                        decision,
                        rationale,
                        Jsonb(context_snapshot) if context_snapshot is not None else None,
                        supersedes_id,
                    ),
                )
                row = await cur.fetchone()
        assert row is not None
        return CoachingDecision(**row)

    async def active(self) -> list[CoachingDecision]:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                f"SELECT {_COLS} FROM coach.coaching_decisions "
                "WHERE active = true ORDER BY date DESC, id DESC"
            )
            rows = await cur.fetchall()
        return [CoachingDecision(**r) for r in rows]
