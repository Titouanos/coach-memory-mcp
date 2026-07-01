"""goals domain — race / perf / habit targets with a small status state machine."""

from __future__ import annotations

import datetime as dt

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from ..models import GoalStatus, GoalType
from ..repositories import Repositories


def register(mcp: FastMCP, repos: Repositories) -> None:
    @mcp.tool
    async def set_goal(
        type: GoalType,
        description: str,
        target_value: str | None = None,
        target_date: dt.date | None = None,
        notes: str | None = None,
    ) -> dict:
        """Create a goal.

        type: 'race' | 'perf' | 'habit'.
        target_value: flexible free text, e.g. '36:59', '260W', '8h sleep'.
        target_date: ISO date the goal is aimed at (optional).
        """
        goal = await repos.goals.create(
            type=type,
            description=description,
            target_value=target_value,
            target_date=target_date,
            notes=notes,
        )
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
        """List goals, optionally filtered by status. Ordered by target date."""
        goals = await repos.goals.list(status)
        return [g.model_dump(mode="json") for g in goals]
