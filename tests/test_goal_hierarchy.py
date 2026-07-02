"""Migration 0002: goal hierarchy (parent_id + horizon) and race→goal linkage."""

from __future__ import annotations

import asyncio

import pytest
from fastmcp.exceptions import ToolError


async def _mk(client, description, **kw):
    res = await client.call_tool("set_goal", {"type": "perf", "description": description, **kw})
    return res.data


async def test_goal_tree_creation(client):
    root = await _mk(client, "podium level long term", horizon="long_term")
    season = await _mk(client, "sub-37 10K", horizon="season", parent_id=root["id"])
    block = await _mk(client, "cadence 172-176", horizon="block", parent_id=season["id"])

    assert root["parent_id"] is None and root["horizon"] == "long_term"
    assert season["parent_id"] == root["id"] and season["horizon"] == "season"
    assert block["parent_id"] == season["id"] and block["horizon"] == "block"

    goals = (await client.call_tool("get_goals", {})).data
    by_id = {g["id"]: g for g in goals}
    assert by_id[block["id"]]["parent_id"] == season["id"]

    with pytest.raises(ToolError):  # unknown parent
        await _mk(client, "orphan", parent_id=999999)
    with pytest.raises(ToolError):  # invalid horizon rejected by schema
        await _mk(client, "bad horizon", horizon="decade")


async def test_update_goal_reparent_and_cycle_guard(client):
    a = await _mk(client, "goal A")
    b = await _mk(client, "goal B", parent_id=a["id"])

    # legitimate edit: set horizon + notes
    edited = (
        await client.call_tool(
            "update_goal", {"goal_id": a["id"], "fields": {"horizon": "long_term", "notes": "racine"}}
        )
    ).data
    assert edited["horizon"] == "long_term" and edited["notes"] == "racine"

    # A -> B would close the cycle A->B->A
    with pytest.raises(ToolError):
        await client.call_tool("update_goal", {"goal_id": a["id"], "fields": {"parent_id": b["id"]}})
    # self-parent
    with pytest.raises(ToolError):
        await client.call_tool("update_goal", {"goal_id": a["id"], "fields": {"parent_id": a["id"]}})
    # unknown goal / empty payload / unknown field
    with pytest.raises(ToolError):
        await client.call_tool("update_goal", {"goal_id": 999999, "fields": {"notes": "x"}})
    with pytest.raises(ToolError):
        await client.call_tool("update_goal", {"goal_id": a["id"], "fields": {}})
    with pytest.raises(ToolError):
        await client.call_tool("update_goal", {"goal_id": a["id"], "fields": {"status": "hit"}})

    # null value means "no change", never a wipe
    kept = (
        await client.call_tool(
            "update_goal", {"goal_id": a["id"], "fields": {"notes": None, "target_value": "X"}}
        )
    ).data
    assert kept["notes"] == "racine" and kept["target_value"] == "X"


async def test_deep_cycle_guard(client):
    a = await _mk(client, "A")
    b = await _mk(client, "B", parent_id=a["id"])
    c = await _mk(client, "C", parent_id=b["id"])
    # A -> C would close A->B->C->A
    with pytest.raises(ToolError):
        await client.call_tool("update_goal", {"goal_id": a["id"], "fields": {"parent_id": c["id"]}})
    # but C can move directly under A (no cycle)
    moved = (
        await client.call_tool("update_goal", {"goal_id": c["id"], "fields": {"parent_id": a["id"]}})
    ).data
    assert moved["parent_id"] == a["id"]


async def test_concurrent_reciprocal_reparent_cannot_commit_cycle(client):
    # Write-skew guard: A→B and B→A fired concurrently must not both succeed
    # (the advisory xact lock serializes the check+update).
    a = await _mk(client, "concurrent A")
    b = await _mk(client, "concurrent B")
    results = await asyncio.gather(
        client.call_tool("update_goal", {"goal_id": a["id"], "fields": {"parent_id": b["id"]}}),
        client.call_tool("update_goal", {"goal_id": b["id"], "fields": {"parent_id": a["id"]}}),
        return_exceptions=True,
    )
    failures = [r for r in results if isinstance(r, BaseException)]
    assert len(failures) == 1, f"expected exactly one rejection, got {len(failures)}"

    goals = {g["id"]: g for g in (await client.call_tool("get_goals", {})).data}
    both_linked = (
        goals[a["id"]]["parent_id"] == b["id"] and goals[b["id"]]["parent_id"] == a["id"]
    )
    assert not both_linked, "cycle committed despite the guard"


async def test_race_goal_linkage(client):
    goal = await _mk(client, "season goal", horizon="season")

    linked = (
        await client.call_tool(
            "add_race",
            {"name": "A-race", "date": "2026-11-29", "priority": "A", "goal_id": goal["id"]},
        )
    ).data
    assert linked["goal_id"] == goal["id"]

    orphan = (await client.call_tool("add_race", {"name": "B-race", "date": "2026-08-01"})).data
    assert orphan["goal_id"] is None

    relinked = (
        await client.call_tool("link_race_to_goal", {"race_id": orphan["id"], "goal_id": goal["id"]})
    ).data
    assert relinked["goal_id"] == goal["id"]

    with pytest.raises(ToolError):  # unknown goal
        await client.call_tool("link_race_to_goal", {"race_id": orphan["id"], "goal_id": 999999})
    with pytest.raises(ToolError):  # unknown race
        await client.call_tool("link_race_to_goal", {"race_id": 999999, "goal_id": goal["id"]})
    with pytest.raises(ToolError):  # add_race with unknown goal
        await client.call_tool(
            "add_race", {"name": "bad", "date": "2026-08-02", "goal_id": 999999}
        )
