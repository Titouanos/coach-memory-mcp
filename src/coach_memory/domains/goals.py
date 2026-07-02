"""goals domain — race / perf / habit targets, organised as a hierarchy.

Goals form a tree via ``parent_id`` with a ``horizon`` per level, e.g.:
long_term root ("reach podium level") → season goal ("sub-37 10K") →
block goal ("cadence 172-176 spm").
"""

from __future__ import annotations

import datetime as dt

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import ValidationError

from ..models import GoalHorizon, GoalStatus, GoalType, GoalUpdate
from ..repositories import Repositories


def register(mcp: FastMCP, repos: Repositories) -> None:
    @mcp.tool
    async def set_goal(
        type: GoalType,
        description: str,
        target_value: str | None = None,
        target_date: dt.date | None = None,
        notes: str | None = None,
        parent_id: int | None = None,
        horizon: GoalHorizon | None = None,
    ) -> dict:
        """Create a goal.

        type: 'race' | 'perf' | 'habit'.
        target_value: flexible free text, e.g. '36:59', '260W', '8h sleep'.
        target_date: ISO date the goal is aimed at (optional).
        parent_id: id of the broader goal this one serves (goals form a tree,
            e.g. long_term -> season -> block).
        horizon: 'long_term' | 'season' | 'block' (optional).
        """
        try:
            goal = await repos.goals.create(
                type=type,
                description=description,
                target_value=target_value,
                target_date=target_date,
                notes=notes,
                parent_id=parent_id,
                horizon=horizon,
            )
        except LookupError as exc:
            raise ToolError(str(exc)) from exc
        return goal.model_dump(mode="json")

    @mcp.tool
    async def update_goal(goal_id: int, fields: dict) -> dict:
        """Edit an existing goal. Pass ONLY the fields to change — unspecified
        fields (and fields passed as null) are left untouched.

        Allowed fields: description, target_value, target_date, notes,
        parent_id (re-parent in the hierarchy; cycles are rejected),
        horizon ('long_term' | 'season' | 'block').
        Use update_goal_status for status changes.
        """
        try:
            update = GoalUpdate.model_validate(fields)
        except ValidationError as exc:
            raise ToolError(f"invalid goal fields: {exc}") from exc
        data = update.model_dump(exclude_unset=True, exclude_none=True)
        if not data:
            raise ToolError("no updatable fields provided; pass at least one non-null field")
        try:
            goal = await repos.goals.update(goal_id, data)
        except (LookupError, ValueError) as exc:
            raise ToolError(str(exc)) from exc
        if goal is None:
            raise ToolError(f"goal {goal_id} not found")
        return goal.model_dump(mode="json")

    @mcp.tool
    async def update_goal_status(goal_id: int, status: GoalStatus) -> dict:
        """Move a goal to a new status: 'active' | 'hit' | 'missed' | 'abandoned'."""
        goal = await repos.goals.update_status(goal_id, status)
        if goal is None:
            raise ToolError(f"goal {goal_id} not found")
        return goal.model_dump(mode="json")

    @mcp.tool
    async def get_goals(status: GoalStatus | None = None) -> list[dict]:
        """List goals, optionally filtered by status. Each goal carries
        parent_id and horizon, which encode the goal hierarchy."""
        goals = await repos.goals.list(status)
        return [g.model_dump(mode="json") for g in goals]
