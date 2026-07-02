"""Daily-metrics repository (one row per day, keyed by date)."""

from __future__ import annotations

import datetime as dt

from psycopg import sql
from psycopg.types.json import Jsonb

from ..models import DailyMetrics
from .base import BaseRepository

_COLS = sql.SQL(
    "date, hrv_ms, hrv_status, sleep_score, sleep_duration_min, resting_hr, "
    "body_battery_start, body_battery_end, training_readiness, ctl, atl, tsb, "
    "vo2max_run, vo2max_bike, raw, created_at"
)
_JSONB_FIELDS = {"raw"}


class DailyMetricsRepository(BaseRepository):
    async def upsert(self, date: dt.date, fields: dict) -> DailyMetrics:
        """Partial upsert keyed by date. `fields` keys are whitelisted upstream
        by DailyMetricsUpdate (extra='forbid'), so identifiers are trusted."""
        if not fields:
            raise ValueError("no fields provided to upsert")

        columns = list(fields.keys())
        values = [
            Jsonb(fields[c]) if c in _JSONB_FIELDS and fields[c] is not None else fields[c]
            for c in columns
        ]
        ident = [sql.Identifier(c) for c in columns]
        query = sql.SQL(
            """
            INSERT INTO coach.daily_metrics (date, {cols})
            VALUES ({date_ph}, {ph})
            ON CONFLICT (date) DO UPDATE SET {assignments}
            RETURNING {ret}
            """
        ).format(
            cols=sql.SQL(", ").join(ident),
            date_ph=sql.Placeholder(),
            ph=sql.SQL(", ").join(sql.Placeholder() for _ in columns),
            assignments=sql.SQL(", ").join(
                sql.SQL("{c} = EXCLUDED.{c}").format(c=i) for i in ident
            ),
            ret=_COLS,
        )
        async with self.pool.connection() as conn:
            cur = await conn.execute(query, [date, *values])
            row = await cur.fetchone()
        assert row is not None
        return DailyMetrics(**row)

    async def range(self, start: dt.date, end: dt.date) -> list[DailyMetrics]:
        query = sql.SQL(
            "SELECT {cols} FROM coach.daily_metrics "
            "WHERE date BETWEEN {s} AND {e} ORDER BY date DESC"
        ).format(cols=_COLS, s=sql.Placeholder(), e=sql.Placeholder())
        async with self.pool.connection() as conn:
            cur = await conn.execute(query, (start, end))
            rows = await cur.fetchall()
        return [DailyMetrics(**r) for r in rows]

    async def recent_compact(self, *, on_or_after: dt.date) -> list[dict]:
        """Compact digest rows for get_athlete_context (small on purpose)."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                """
                SELECT date, hrv_ms, sleep_score, training_readiness, tsb
                FROM coach.daily_metrics
                WHERE date >= %s
                ORDER BY date DESC
                """,
                (on_or_after,),
            )
            rows = await cur.fetchall()
        return [
            {
                "date": r["date"].isoformat(),
                "hrv": r["hrv_ms"],
                "sleep_score": r["sleep_score"],
                "readiness": r["training_readiness"],
                "tsb": float(r["tsb"]) if r["tsb"] is not None else None,
            }
            for r in rows
        ]
