"""Migration 0004: training_plan tools (plan/mark/update/get_week)."""

from __future__ import annotations

import datetime as dt

import pytest
from fastmcp.exceptions import ToolError

TODAY = dt.date.today()
MONDAY = TODAY - dt.timedelta(days=TODAY.weekday())


async def test_plan_workout_and_mark_lifecycle(client):
    w = (
        await client.call_tool(
            "plan_workout",
            {"date": TODAY.isoformat(), "sport": "run", "title": "6x800m @ 3:45",
             "type": "VO2", "description": "Échauffement 20', 6x800m r=2', zones 5",
             "target_load": 95, "block_label": "Bloc Saint-Gilles S1"},
        )
    ).data
    assert w["status"] == "planned" and w["target_load"] == 95

    s = (
        await client.call_tool("log_session", {"sport": "run", "date": TODAY.isoformat()})
    ).data
    done = (
        await client.call_tool(
            "mark_workout", {"workout_id": w["id"], "status": "done", "session_id": s["id"]}
        )
    ).data
    assert done["status"] == "done" and done["session_id"] == s["id"]

    # marking again without session_id keeps the existing link
    kept = (
        await client.call_tool("mark_workout", {"workout_id": w["id"], "status": "modified"})
    ).data
    assert kept["session_id"] == s["id"]

    with pytest.raises(ToolError):
        await client.call_tool("mark_workout", {"workout_id": 999999, "status": "done"})
    with pytest.raises(ToolError):  # unknown session
        await client.call_tool(
            "mark_workout", {"workout_id": w["id"], "status": "done", "session_id": 999999}
        )
    with pytest.raises(ToolError):  # invalid status rejected by schema
        await client.call_tool("mark_workout", {"workout_id": w["id"], "status": "maybe"})


async def test_plan_workout_goal_link_and_update(client):
    goal = (
        await client.call_tool("set_goal", {"type": "perf", "description": "sub-37"})
    ).data
    w = (
        await client.call_tool(
            "plan_workout",
            {"date": TODAY.isoformat(), "sport": "run", "title": "tempo", "goal_id": goal["id"]},
        )
    ).data
    assert w["goal_id"] == goal["id"]

    with pytest.raises(ToolError):  # unknown goal
        await client.call_tool(
            "plan_workout",
            {"date": TODAY.isoformat(), "sport": "run", "title": "x", "goal_id": 999999},
        )

    edited = (
        await client.call_tool(
            "update_workout",
            {"workout_id": w["id"], "fields": {"title": "tempo 2x15'", "target_load": 80}},
        )
    ).data
    assert edited["title"] == "tempo 2x15'" and edited["target_load"] == 80

    with pytest.raises(ToolError):  # status not editable here
        await client.call_tool(
            "update_workout", {"workout_id": w["id"], "fields": {"status": "done"}}
        )
    with pytest.raises(ToolError):
        await client.call_tool("update_workout", {"workout_id": w["id"], "fields": {}})
    with pytest.raises(ToolError):
        await client.call_tool("update_workout", {"workout_id": 999999, "fields": {"title": "x"}})


async def test_plan_week_atomic(client):
    created = (
        await client.call_tool(
            "plan_week",
            {"workouts": [
                {"date": MONDAY.isoformat(), "sport": "run", "title": "EF 50'"},
                {"date": (MONDAY + dt.timedelta(days=2)).isoformat(), "sport": "bike",
                 "title": "Sweet spot 3x12'"},
            ]},
        )
    ).data
    assert len(created) == 2

    # one bad item → the whole batch must be rejected (atomicity)
    with pytest.raises(ToolError):
        await client.call_tool(
            "plan_week",
            {"workouts": [
                {"date": MONDAY.isoformat(), "sport": "swim", "title": "technique"},
                {"date": MONDAY.isoformat(), "sport": "run", "title": "x", "goal_id": 999999},
            ]},
        )
    week = (await client.call_tool("get_week", {"date": MONDAY.isoformat()})).data
    all_titles = [w["title"] for d in week["days"] for w in d["workouts"]]
    assert "technique" not in all_titles  # rolled back with the batch

    with pytest.raises(ToolError):
        await client.call_tool("plan_week", {"workouts": []})


async def test_get_week_joins_metrics(client):
    await client.call_tool(
        "plan_workout", {"date": MONDAY.isoformat(), "sport": "run", "title": "EF"}
    )
    await client.call_tool(
        "upsert_daily_metrics",
        {"date": MONDAY.isoformat(), "fields": {"hrv_ms": 60, "tsb": 1.5}},
    )

    week = (await client.call_tool("get_week", {})).data  # default = current week
    assert week["week_start"] == MONDAY.isoformat()
    assert len(week["days"]) == 7
    monday = week["days"][0]
    assert monday["workouts"][0]["title"] == "EF"
    assert monday["metrics"]["hrv"] == 60 and monday["metrics"]["tsb"] == 1.5
    # a day with nothing planned still appears (rest days visible)
    assert any(d["workouts"] == [] for d in week["days"])


async def test_context_week_plan_is_compact(client):
    await client.call_tool(
        "plan_workout",
        {"date": TODAY.isoformat(), "sport": "run", "title": "seuil",
         "description": "très longue description détaillée des zones cibles..."},
    )
    ctx = (await client.call_tool("get_athlete_context", {})).data
    plan = ctx["current_week_plan"]
    assert len(plan) == 1
    assert plan[0]["title"] == "seuil"
    assert "description" not in plan[0]  # big text stays out of the digest
