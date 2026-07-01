"""Analyses repository."""

from __future__ import annotations

import datetime as dt

from psycopg.types.json import Jsonb

from ..models import Analysis
from .base import BaseRepository

_COLS = "id, date, topic, summary, key_metrics, created_at"


class AnalysisRepository(BaseRepository):
    async def create(
        self,
        *,
        topic: str,
        summary: str,
        key_metrics: dict | None = None,
        date: dt.date,
    ) -> Analysis:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                f"""
                INSERT INTO coach.analyses (date, topic, summary, key_metrics)
                VALUES (%s, %s, %s, %s)
                RETURNING {_COLS}
                """,
                (
                    date,
                    topic,
                    summary,
                    Jsonb(key_metrics) if key_metrics is not None else None,
                ),
            )
            row = await cur.fetchone()
        assert row is not None
        return Analysis(**row)

    async def recent(self, limit: int = 3) -> list[Analysis]:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                f"SELECT {_COLS} FROM coach.analyses ORDER BY date DESC, id DESC LIMIT %s",
                (limit,),
            )
            rows = await cur.fetchall()
        return [Analysis(**r) for r in rows]
