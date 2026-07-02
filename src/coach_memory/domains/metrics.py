"""metrics domain — daily wellness/charge signals (HRV, sleep, readiness, load)."""

from __future__ import annotations

import datetime as dt

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import ValidationError

from ..models import DailyMetricsUpdate
from ..repositories import Repositories


def register(mcp: FastMCP, repos: Repositories) -> None:
    @mcp.tool
    async def upsert_daily_metrics(date: dt.date, fields: dict) -> dict:
        """Insert or update the daily metrics row for a date (one row per day).
        Pass ONLY the fields you have — unspecified/null fields are untouched.

        Allowed fields: hrv_ms, hrv_status, sleep_score (0-100),
        sleep_duration_min, resting_hr, body_battery_start, body_battery_end,
        training_readiness (0-100), ctl, atl, tsb, vo2max_run, vo2max_bike,
        raw (object — the full source payload, kept verbatim).
        """
        try:
            update = DailyMetricsUpdate.model_validate(fields)
        except ValidationError as exc:
            raise ToolError(f"invalid metrics fields: {exc}") from exc
        data = update.model_dump(exclude_unset=True, exclude_none=True)
        if not data:
            raise ToolError("no metrics provided; pass at least one non-null field")
        metrics = await repos.metrics.upsert(date, data)
        return metrics.model_dump(mode="json")

    @mcp.tool
    async def get_daily_metrics(
        start_date: dt.date | None = None, end_date: dt.date | None = None
    ) -> list[dict]:
        """Daily metrics over a date range, newest first.

        Defaults to the last 7 days (end_date defaults to today, start_date to
        end_date - 6 days).
        """
        end = end_date or dt.date.today()
        start = start_date or end - dt.timedelta(days=6)
        if start > end:
            raise ToolError("start_date must be on or before end_date")
        rows = await repos.metrics.range(start, end)
        return [m.model_dump(mode="json") for m in rows]
