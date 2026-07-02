"""Migration 0003: daily_metrics upsert/range + context integration."""

from __future__ import annotations

import datetime as dt

import pytest
from fastmcp.exceptions import ToolError

TODAY = dt.date.today()


async def test_upsert_and_partial_update(client):
    first = (
        await client.call_tool(
            "upsert_daily_metrics",
            {"date": "2026-07-01",
             "fields": {"hrv_ms": 52, "sleep_score": 81, "tsb": -4.5,
                        "raw": {"source": "garmin", "x": 1}}},
        )
    ).data
    assert first["hrv_ms"] == 52 and first["raw"] == {"source": "garmin", "x": 1}

    # second upsert same date: only provided fields overwrite
    second = (
        await client.call_tool(
            "upsert_daily_metrics",
            {"date": "2026-07-01", "fields": {"training_readiness": 66, "hrv_ms": None}},
        )
    ).data
    assert second["training_readiness"] == 66
    assert second["hrv_ms"] == 52          # null = no change
    assert second["sleep_score"] == 81     # untouched

    with pytest.raises(ToolError):
        await client.call_tool("upsert_daily_metrics", {"date": "2026-07-01", "fields": {}})
    with pytest.raises(ToolError):  # constraint violation caught by Pydantic
        await client.call_tool(
            "upsert_daily_metrics", {"date": "2026-07-01", "fields": {"sleep_score": 140}}
        )
    with pytest.raises(ToolError):  # unknown field
        await client.call_tool(
            "upsert_daily_metrics", {"date": "2026-07-01", "fields": {"bogus": 1}}
        )


async def test_get_daily_metrics_range_and_defaults(client):
    for offset in (0, 1, 2, 9):
        day = (TODAY - dt.timedelta(days=offset)).isoformat()
        await client.call_tool(
            "upsert_daily_metrics", {"date": day, "fields": {"hrv_ms": 40 + offset}}
        )

    default_window = (await client.call_tool("get_daily_metrics", {})).data
    assert len(default_window) == 3          # offsets 0..2 within last 7 days
    assert default_window[0]["date"] == TODAY.isoformat()  # newest first

    explicit = (
        await client.call_tool(
            "get_daily_metrics",
            {"start_date": (TODAY - dt.timedelta(days=10)).isoformat(),
             "end_date": TODAY.isoformat()},
        )
    ).data
    assert len(explicit) == 4

    with pytest.raises(ToolError):
        await client.call_tool(
            "get_daily_metrics",
            {"start_date": TODAY.isoformat(),
             "end_date": (TODAY - dt.timedelta(days=1)).isoformat()},
        )


async def test_context_metrics_digest_is_compact(client):
    await client.call_tool(
        "upsert_daily_metrics",
        {"date": TODAY.isoformat(),
         "fields": {"hrv_ms": 55, "sleep_score": 88, "training_readiness": 70,
                    "tsb": -2.0, "resting_hr": 44, "raw": {"big": "blob"}}},
    )
    ctx = (await client.call_tool("get_athlete_context", {})).data
    digest = ctx["daily_metrics_7d"]
    assert len(digest) == 1
    entry = digest[0]
    # compact: exactly the 5 promised keys, no raw blob leaking into context
    assert set(entry.keys()) == {"date", "hrv", "sleep_score", "readiness", "tsb"}
    assert entry == {"date": TODAY.isoformat(), "hrv": 55, "sleep_score": 88,
                     "readiness": 70, "tsb": -2.0}
