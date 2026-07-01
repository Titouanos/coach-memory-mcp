"""analyses domain — periodic write-ups (weekly review, block debrief, ...)."""

from __future__ import annotations

import datetime as dt

from fastmcp import FastMCP

from ..repositories import Repositories


def register(mcp: FastMCP, repos: Repositories) -> None:
    @mcp.tool
    async def record_analysis(
        topic: str,
        summary: str,
        key_metrics: dict | None = None,
        date: dt.date | None = None,
    ) -> dict:
        """Store a coaching analysis / write-up.

        topic: short label, e.g. 'weekly review 2026-W27', 'VO2 block debrief'.
        summary: the narrative analysis.
        key_metrics: structured figures behind it, e.g.
            {"ctl": 63, "ramp": 4.2, "avg_hrv": 51}.
        date: ISO date; defaults to today.
        """
        analysis = await repos.analyses.create(
            topic=topic,
            summary=summary,
            key_metrics=key_metrics,
            date=date or dt.date.today(),
        )
        return analysis.model_dump(mode="json")
