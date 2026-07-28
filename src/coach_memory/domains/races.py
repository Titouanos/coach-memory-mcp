"""races domain — planned / completed events with A/B/C priority."""

from __future__ import annotations

import datetime as dt

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from ..models import RacePriority, RaceStatus
from ..repositories import Repositories


def register(mcp: FastMCP, repos: Repositories) -> None:
    @mcp.tool
    async def add_race(
        name: str,
        date: dt.date,
        distance: str | None = None,
        priority: RacePriority = "B",
        target_time: str | None = None,
        goal_id: int | None = None,
    ) -> dict:
        """Add a race to the calendar.

        priority: 'A' (peak target), 'B' (important tune-up), or 'C' (training race).
        distance: free text, e.g. '10K', 'Half', 'Ironman 70.3'.
        target_time: free text, e.g. 'sub-37', '36:59'.
        goal_id: id of the goal this race serves (e.g. the A-race realizing a
            season goal).
        """
        try:
            race = await repos.races.create(
                name=name,
                date=date,
                distance=distance,
                priority=priority,
                target_time=target_time,
                goal_id=goal_id,
            )
        except LookupError as exc:
            raise ToolError(str(exc)) from exc
        return race.model_dump(mode="json")

    @mcp.tool
    async def link_race_to_goal(race_id: int, goal_id: int) -> dict:
        """Attach an existing race to a goal (sets races.goal_id)."""
        try:
            race = await repos.races.link_goal(race_id, goal_id)
        except LookupError as exc:
            raise ToolError(str(exc)) from exc
        if race is None:
            raise ToolError(f"race {race_id} not found")
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
    async def update_race_status(race_id: int, status: RaceStatus) -> dict:
        """Move a race to a new status: 'planned' | 'done' | 'cancelled'.

        Use 'cancelled' when a race is called off or you withdraw: the race
        stops appearing in get_upcoming_races and is ignored by planning, but
        the record that you had planned it is kept. This is almost always what
        you want — prefer it over delete_race.

        'done' is set automatically by record_race_result; set it by hand only
        to correct a mistake.
        """
        race = await repos.races.update_status(race_id, status)
        if race is None:
            raise ToolError(f"race {race_id} not found")
        return race.model_dump(mode="json")

    @mcp.tool
    async def delete_race(race_id: int) -> dict:
        """Permanently delete a race. Prefer update_race_status(..., 'cancelled').

        Reserved for genuine data-entry mistakes (duplicate, wrong date typed
        twice). A cancelled race should be cancelled, not deleted: the coaching
        history of what was planned and then dropped is worth keeping.

        Returns the deleted row so the data is not lost from the conversation.
        """
        race = await repos.races.delete(race_id)
        if race is None:
            raise ToolError(f"race {race_id} not found")
        return race.model_dump(mode="json")

    @mcp.tool
    async def get_upcoming_races(include_cancelled: bool = False) -> list[dict]:
        """List races on or after today, soonest first (all priorities).

        Cancelled races are excluded by default. Pass include_cancelled=True to
        see them, e.g. to review what was dropped from the season.
        """
        races = await repos.races.upcoming(
            on_or_after=dt.date.today(), include_cancelled=include_cancelled
        )
        return [r.model_dump(mode="json") for r in races]
