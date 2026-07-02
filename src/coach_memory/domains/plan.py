"""plan domain — planned workouts, weeks, and the prévu/réalisé lifecycle."""

from __future__ import annotations

import datetime as dt

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import ValidationError

from ..models import PlannedWorkoutCreate, PlannedWorkoutUpdate, PlanStatus
from ..repositories import Repositories


def _week_bounds(anchor: dt.date) -> tuple[dt.date, dt.date]:
    """Monday..Sunday of the ISO week containing `anchor`."""
    start = anchor - dt.timedelta(days=anchor.weekday())
    return start, start + dt.timedelta(days=6)


def register(mcp: FastMCP, repos: Repositories) -> None:
    @mcp.tool
    async def plan_workout(
        date: dt.date,
        sport: str,
        title: str,
        type: str | None = None,
        description: str | None = None,
        target_load: int | None = None,
        block_label: str | None = None,
        goal_id: int | None = None,
    ) -> dict:
        """Schedule one workout.

        title: short label, e.g. '6x800m @ 3:45'. description: detailed content
        and target zones. target_load: expected load (TSS-like). block_label:
        training-block tag, e.g. 'Bloc Saint-Gilles S1'. goal_id: goal served.
        """
        try:
            workout = PlannedWorkoutCreate(
                date=date, sport=sport, title=title, type=type,
                description=description, target_load=target_load,
                block_label=block_label, goal_id=goal_id,
            )
            created = await repos.plan.create(workout.model_dump())
        except ValidationError as exc:
            raise ToolError(f"invalid workout: {exc}") from exc
        except LookupError as exc:
            raise ToolError(str(exc)) from exc
        return created.model_dump(mode="json")

    @mcp.tool
    async def plan_week(workouts: list[dict]) -> list[dict]:
        """Schedule a batch of workouts atomically (all or nothing).

        Each item takes the same fields as plan_workout: date, sport, title
        (required) + type, description, target_load, block_label, goal_id.
        """
        if not workouts:
            raise ToolError("workouts list is empty")
        try:
            validated = [PlannedWorkoutCreate.model_validate(w).model_dump() for w in workouts]
        except ValidationError as exc:
            raise ToolError(f"invalid workout in list: {exc}") from exc
        try:
            created = await repos.plan.create_many(validated)
        except LookupError as exc:
            raise ToolError(str(exc)) from exc
        return [w.model_dump(mode="json") for w in created]

    @mcp.tool
    async def get_week(date: dt.date | None = None) -> dict:
        """The training week (Monday-Sunday) containing `date` (default today):
        planned workouts with their status, joined day-by-day with the daily
        metrics (hrv, sleep, readiness, tsb)."""
        start, end = _week_bounds(date or dt.date.today())
        workouts = await repos.plan.week(start, end)
        metrics = {m["date"]: m for m in await repos.metrics.recent_compact(on_or_after=start)}

        days = []
        for offset in range(7):
            day = start + dt.timedelta(days=offset)
            iso = day.isoformat()
            days.append({
                "date": iso,
                "metrics": metrics.get(iso),
                "workouts": [
                    w.model_dump(mode="json") for w in workouts if w.date == day
                ],
            })
        return {"week_start": start.isoformat(), "week_end": end.isoformat(), "days": days}

    @mcp.tool
    async def mark_workout(
        workout_id: int, status: PlanStatus, session_id: int | None = None
    ) -> dict:
        """Set a planned workout's status: 'planned' | 'done' | 'skipped' |
        'modified'. Optionally link the executed session via session_id
        (kept if already set and omitted here)."""
        try:
            workout = await repos.plan.mark(workout_id, status, session_id)
        except LookupError as exc:
            raise ToolError(str(exc)) from exc
        if workout is None:
            raise ToolError(f"workout {workout_id} not found")
        return workout.model_dump(mode="json")

    @mcp.tool
    async def update_workout(workout_id: int, fields: dict) -> dict:
        """Edit a planned workout. Pass ONLY the fields to change — unspecified
        (and null) fields are untouched.

        Allowed fields: date, sport, type, title, description, target_load,
        block_label, goal_id. Use mark_workout for status changes.
        """
        try:
            update = PlannedWorkoutUpdate.model_validate(fields)
        except ValidationError as exc:
            raise ToolError(f"invalid workout fields: {exc}") from exc
        data = update.model_dump(exclude_unset=True, exclude_none=True)
        if not data:
            raise ToolError("no updatable fields provided; pass at least one non-null field")
        try:
            workout = await repos.plan.update(workout_id, data)
        except (LookupError, ValueError) as exc:
            raise ToolError(str(exc)) from exc
        if workout is None:
            raise ToolError(f"workout {workout_id} not found")
        return workout.model_dump(mode="json")
