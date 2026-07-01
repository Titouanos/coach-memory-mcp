"""races domain — planned / completed events with A/B/C priority."""

from __future__ import annotations

import datetime as dt

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from ..models import RacePriority
from ..repositories import Repositories


def register(mcp: FastMCP, repos: Repositories) -> None:
    @mcp.tool
    async def add_race(
        name: str,
        date: dt.date,
        distance: str | None = None,
        priority: RacePriority = "B",
        target_time: str | None = None,
    ) -> dict:
        """Add a race to the calendar.

        priority: 'A' (peak target), 'B' (important tune-up), or 'C' (training race).
        distance: free text, e.g. '10K', 'Half', 'Ironman 70.3'.
        target_time: free text, e.g. 'sub-37', '36:59'.
        """
        race = await repos.races.create(
            name=name,
            date=date,
            distance=distance,
            priority=priority,
            target_time=target_time,
        )
        return race.model_dump(mode="json")

    @mcp.tool
    async def record_race_result(
        race_id: int, actual_time: str, notes: str | None = None
    ) -> dict:
        """Record the outcome of a race: actual finishing time and result notes."""
        race = await repos.races.record_result(race_id, actual_time, notes)
        if race is None:
            raise ToolError(f"race {race_id} not found")
        return race.model_dump(mode="json")

    @mcp.tool
    async def get_upcoming_races() -> list[dict]:
        """List races on or after today, soonest first (all priorities)."""
        races = await repos.races.upcoming(on_or_after=dt.date.today())
        return [r.model_dump(mode="json") for r in races]
