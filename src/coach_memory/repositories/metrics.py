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
    async def dates_with_content(self, dates: list[dt.date]) -> set[dt.date]:
        """Sous-ensemble de `dates` portant REELLEMENT une metrique.

        Une ligne integralement NULL ne compte pas comme couverte : sinon une
        sonde de couverture validerait sa propre pollution. Sert au rapport de
        reprise, pas a decider d'ecrire — c'est `fill_holes` qui protege.
        """
        if not dates:
            return set()
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                """
                SELECT date FROM coach.daily_metrics
                WHERE date = ANY(%s)
                  AND (hrv_ms IS NOT NULL OR sleep_score IS NOT NULL
                       OR resting_hr IS NOT NULL OR training_readiness IS NOT NULL
                       OR ctl IS NOT NULL OR sleep_duration_min IS NOT NULL)
                """,
                (dates,),
            )
            rows = await cur.fetchall()
        return {r["date"] for r in rows}

    async def _write(
        self, date: dt.date, fields: dict, *, fill_holes: bool
    ) -> tuple[DailyMetrics, bool]:
        """Upsert partiel par date. `fields` est filtre en amont par
        DailyMetricsUpdate (extra='forbid'), donc les identifiants sont surs.

        Deux regimes, selon l'INTENTION de l'appelant :

        `fill_holes=False` — `col = EXCLUDED.col`, l'entrant gagne. C'est ce que
        veulent `upsert_daily_metrics` (une saisie explicite) et
        `ingest_garmin_activity` sur la journee courante, ou la lecture la plus
        recente EST la meilleure valeur.

        `fill_holes=True` — `col = coalesce(existant, entrant)`, l'existant
        gagne. C'est le regime d'une REPRISE d'historique. Deux benefices :
        une valeur corrigee a la main ne peut pas etre ecrasee par un blob
        retrospectif, ET une seconde passe COMPLETE la ligne au lieu de la
        sauter. Ce dernier point est decisif : les 16 journees en base n'avaient
        aucun ctl ni tsb — une logique « on saute ce qui existe » aurait rendu
        ces trous definitivement inaccessibles.

        Renvoie (ligne, insertion) — `xmax = 0` distingue une insertion d'une
        mise a jour, ce qui permet de compter honnetement dans le rapport.
        """
        if not fields:
            raise ValueError("no fields provided to upsert")

        columns = list(fields.keys())
        values = [
            Jsonb(fields[c]) if c in _JSONB_FIELDS and fields[c] is not None else fields[c]
            for c in columns
        ]
        ident = [sql.Identifier(c) for c in columns]

        def affectation(col: str) -> sql.Composable:
            c = sql.Identifier(col)
            if not fill_holes:
                return sql.SQL("{c} = EXCLUDED.{c}").format(c=c)
            if col == "raw":
                # Fusion des blobs plutot qu'ecrasement : les deux sources
                # peuvent porter des cles differentes (charge d'un cote,
                # sommeil de l'autre). L'existant gagne cle par cle.
                return sql.SQL(
                    "raw = CASE "
                    "WHEN EXCLUDED.raw IS NULL THEN coach.daily_metrics.raw "
                    "WHEN coach.daily_metrics.raw IS NULL THEN EXCLUDED.raw "
                    "ELSE EXCLUDED.raw || coach.daily_metrics.raw END"
                )
            return sql.SQL(
                "{c} = coalesce(coach.daily_metrics.{c}, EXCLUDED.{c})"
            ).format(c=c)

        query = sql.SQL(
            """
            INSERT INTO coach.daily_metrics (date, {cols})
            VALUES ({date_ph}, {ph})
            ON CONFLICT (date) DO UPDATE SET {assignments}
            RETURNING {ret}, (xmax = 0) AS insertion
            """
        ).format(
            cols=sql.SQL(", ").join(ident),
            date_ph=sql.Placeholder(),
            ph=sql.SQL(", ").join(sql.Placeholder() for _ in columns),
            assignments=sql.SQL(", ").join(affectation(c) for c in columns),
            ret=_COLS,
        )
        async with self.pool.connection() as conn:
            cur = await conn.execute(query, [date, *values])
            row = await cur.fetchone()
        assert row is not None
        return DailyMetrics(**row), bool(row["insertion"])

    async def upsert(self, date: dt.date, fields: dict) -> DailyMetrics:
        """Ecrasement : l'entrant gagne. Comportement historique, inchange."""
        row, _ = await self._write(date, fields, fill_holes=False)
        return row

    async def fill_holes(self, date: dt.date, fields: dict) -> tuple[DailyMetrics, bool]:
        """Reprise : ne remplit que les trous. Renvoie (ligne, insertion)."""
        return await self._write(date, fields, fill_holes=True)

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
