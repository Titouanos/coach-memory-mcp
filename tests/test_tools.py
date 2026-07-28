"""Smoke test per MCP tool, driven end-to-end through the in-memory client.

Each test exercises a tool exactly as claude.ai would call it (validation +
repository + SQL), against the real database.
"""

from __future__ import annotations

import pytest
from fastmcp.exceptions import ToolError


async def test_update_athlete_profile(client):
    res = await client.call_tool(
        "update_athlete_profile",
        {"fields": {"weight_kg": 66, "ftp": 240, "vo2max_run": 61,
                    "hr_zones": {"z2": [121, 145]}}},
    )
    prof = res.data
    assert prof["ftp"] == 240
    assert prof["weight_kg"] == 66.0
    assert prof["hr_zones"] == {"z2": [121, 145]}

    # partial update leaves other fields untouched
    res2 = await client.call_tool("update_athlete_profile", {"fields": {"weight_kg": 65.5}})
    assert res2.data["weight_kg"] == 65.5
    assert res2.data["ftp"] == 240

    # an explicit null must NOT wipe a stored value (treated as "no change")
    res3 = await client.call_tool(
        "update_athlete_profile", {"fields": {"weight_kg": None, "ftp": 250}}
    )
    assert res3.data["ftp"] == 250
    assert res3.data["weight_kg"] == 65.5  # unchanged, not nulled

    with pytest.raises(ToolError):
        await client.call_tool("update_athlete_profile", {"fields": {}})
    with pytest.raises(ToolError):  # a payload of only nulls updates nothing
        await client.call_tool("update_athlete_profile", {"fields": {"weight_kg": None}})
    with pytest.raises(ToolError):
        await client.call_tool("update_athlete_profile", {"fields": {"bogus": 1}})


async def test_sessions_log_and_recent(client):
    await client.call_tool(
        "log_session",
        {"sport": "run", "date": "2026-06-30", "type": "VO2",
         "executed": "5x3min hard intervals", "rpe": 8, "felt": "strong"},
    )
    await client.call_tool(
        "log_session", {"sport": "bike", "date": "2026-07-01", "type": "easy", "rpe": 3}
    )
    res = await client.call_tool("get_recent_sessions", {"limit": 5})
    sessions = res.data
    assert len(sessions) == 2
    assert sessions[0]["date"] == "2026-07-01"  # newest first
    assert sessions[1]["rpe"] == 8

    # RPE constraint (1..10) is enforced by the schema
    with pytest.raises(ToolError):
        await client.call_tool("log_session", {"sport": "run", "rpe": 42})


async def test_goals_lifecycle(client):
    created = await client.call_tool(
        "set_goal",
        {"type": "perf", "description": "10K sub-37", "target_value": "36:59",
         "target_date": "2026-11-29"},
    )
    goal_id = created.data["id"]
    assert created.data["status"] == "active"

    active = await client.call_tool("get_goals", {"status": "active"})
    assert len(active.data) == 1

    updated = await client.call_tool("update_goal_status", {"goal_id": goal_id, "status": "hit"})
    assert updated.data["status"] == "hit"
    assert (await client.call_tool("get_goals", {"status": "active"})).data == []

    with pytest.raises(ToolError):
        await client.call_tool("update_goal_status", {"goal_id": 999999, "status": "hit"})


async def test_races_lifecycle(client):
    a = await client.call_tool(
        "add_race",
        {"name": "La Rochelle 10K", "date": "2026-11-29", "distance": "10K",
         "priority": "A", "target_time": "sub-37"},
    )
    race_id = a.data["id"]
    # a past race should not show up in upcoming
    await client.call_tool("add_race", {"name": "Old race", "date": "2020-01-01"})

    upcoming = await client.call_tool("get_upcoming_races", {})
    names = [r["name"] for r in upcoming.data]
    assert "La Rochelle 10K" in names
    assert "Old race" not in names

    done = await client.call_tool(
        "record_race_result", {"race_id": race_id, "actual_time": "36:44", "notes": "PR!"}
    )
    assert done.data["actual_time"] == "36:44"
    assert done.data["result_notes"] == "PR!"

    with pytest.raises(ToolError):
        await client.call_tool("record_race_result", {"race_id": 999999, "actual_time": "40:00"})


async def test_race_status_cancel_removes_from_upcoming(client):
    """Une course annulee sort des listes a venir sans etre effacee.

    C est le scenario reel qui a motive la migration 0006 : le triathlon du
    27/09 a ete annule, et faute de statut il fallait le neutraliser par une
    decision de coaching tout en le laissant visible dans get_upcoming_races.
    """
    kept = await client.call_tool(
        "add_race", {"name": "Course conservee", "date": "2026-12-01", "priority": "B"}
    )
    doomed = await client.call_tool(
        "add_race", {"name": "Course annulee", "date": "2026-12-02", "priority": "B"}
    )

    # etat par defaut
    assert kept.data["status"] == "planned"

    cancelled = await client.call_tool(
        "update_race_status", {"race_id": doomed.data["id"], "status": "cancelled"}
    )
    assert cancelled.data["status"] == "cancelled"

    # exclue par defaut...
    names = [r["name"] for r in (await client.call_tool("get_upcoming_races", {})).data]
    assert "Course conservee" in names
    assert "Course annulee" not in names

    # ...mais toujours en base, consultable sur demande
    with_cancelled = await client.call_tool(
        "get_upcoming_races", {"include_cancelled": True}
    )
    assert "Course annulee" in [r["name"] for r in with_cancelled.data]

    # reversible : une annulation n est pas une perte
    back = await client.call_tool(
        "update_race_status", {"race_id": doomed.data["id"], "status": "planned"}
    )
    assert back.data["status"] == "planned"

    with pytest.raises(ToolError):
        await client.call_tool(
            "update_race_status", {"race_id": 999999, "status": "cancelled"}
        )


async def test_record_result_marks_race_done(client):
    """Enregistrer un resultat implique que la course a ete courue."""
    r = await client.call_tool(
        "add_race", {"name": "Course courue", "date": "2026-12-03", "priority": "C"}
    )
    assert r.data["status"] == "planned"
    done = await client.call_tool(
        "record_race_result", {"race_id": r.data["id"], "actual_time": "38:12"}
    )
    assert done.data["status"] == "done"


async def test_delete_race_returns_row_and_removes_it(client):
    """La suppression dure rend la ligne, pour ne pas perdre la donnee."""
    r = await client.call_tool(
        "add_race", {"name": "Saisie erronee", "date": "2026-12-04", "priority": "C"}
    )
    race_id = r.data["id"]

    deleted = await client.call_tool("delete_race", {"race_id": race_id})
    assert deleted.data["id"] == race_id
    assert deleted.data["name"] == "Saisie erronee"

    names = [
        x["name"]
        for x in (
            await client.call_tool("get_upcoming_races", {"include_cancelled": True})
        ).data
    ]
    assert "Saisie erronee" not in names

    # supprimer deux fois doit echouer proprement, pas silencieusement
    with pytest.raises(ToolError):
        await client.call_tool("delete_race", {"race_id": race_id})


async def test_decisions_supersede_is_anti_stale(client):
    first = await client.call_tool(
        "log_coaching_decision",
        {"decision": "Add a second VO2 session/week",
         "rationale": "TSB positive, ready to load",
         "context_snapshot": {"ctl": 60, "atl": 55, "tsb": 5}},
    )
    first_id = first.data["id"]

    second = await client.call_tool(
        "log_coaching_decision",
        {"decision": "Back to one VO2 session/week",
         "rationale": "HRV suppressed 3 days",
         "supersedes_id": first_id},
    )

    active = await client.call_tool("get_active_decisions", {})
    ids = [d["id"] for d in active.data]
    assert second.data["id"] in ids
    assert first_id not in ids  # superseded → retired from active context

    with pytest.raises(ToolError):
        await client.call_tool(
            "log_coaching_decision", {"decision": "x", "supersedes_id": 999999}
        )


async def test_record_analysis(client):
    res = await client.call_tool(
        "record_analysis",
        {"topic": "weekly review 2026-W27",
         "summary": "Solid aerobic week, ramp under control.",
         "key_metrics": {"ctl": 63, "ramp": 4.2}},
    )
    assert res.data["topic"] == "weekly review 2026-W27"
    assert res.data["key_metrics"]["ctl"] == 63


async def test_search_memory_across_tables(client):
    await client.call_tool(
        "log_session",
        {"sport": "run", "date": "2026-06-30", "executed": "brutal VO2 intervals on the track"},
    )
    await client.call_tool(
        "log_coaching_decision",
        {"decision": "Cut the interval volume", "rationale": "fatigue after track work"},
    )
    await client.call_tool(
        "record_analysis", {"topic": "track block", "summary": "intervals paying off"}
    )

    hits = (await client.call_tool("search_memory", {"query": "intervals", "limit": 10})).data
    sources = {h["source"] for h in hits}
    assert "session" in sources
    assert "analysis" in sources
    assert all("snippet" in h and "rank" in h for h in hits)


async def test_get_athlete_context_digest(client):
    await client.call_tool("update_athlete_profile", {"fields": {"ftp": 240}})
    await client.call_tool("set_goal", {"type": "race", "description": "sub-37 10K"})
    await client.call_tool(
        "add_race", {"name": "La Rochelle", "date": "2026-11-29", "priority": "A"}
    )
    await client.call_tool("log_session", {"sport": "run", "date": "2026-07-01"})
    await client.call_tool("log_coaching_decision", {"decision": "Deload this week"})
    await client.call_tool("record_analysis", {"topic": "t", "summary": "s"})

    ctx = (await client.call_tool("get_athlete_context", {})).data
    assert ctx["profile"]["ftp"] == 240
    assert len(ctx["active_goals"]) == 1
    assert len(ctx["upcoming_races"]) == 1  # priority A, future date
    assert len(ctx["recent_sessions"]) == 1
    assert len(ctx["active_decisions"]) == 1
    assert len(ctx["recent_analyses"]) == 1
    assert "generated_at" in ctx
