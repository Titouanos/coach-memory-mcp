"""Composite ingest_garmin_activity: parsing variants, metrics, plan matching."""

from __future__ import annotations

import datetime as dt

import pytest
from fastmcp.exceptions import ToolError

TODAY = dt.date.today()

# Garmin Connect "modern" shape
ACTIVITY_CONNECT = {
    "activityId": 987654321,
    "activityName": "Sortie tempo",
    "activityType": {"typeKey": "running"},
    "startTimeLocal": f"{TODAY.isoformat()} 07:15:00",
    "duration": 2732.0,          # 45:32
    "distance": 10020.0,         # m
    "averageHR": 156, "maxHR": 172,
    "averageSpeed": 3.668,       # m/s ≈ 4:33/km
    "activityTrainingLoad": 98.4,
    "calories": 640,
}

# older / export shape: string type, GMT time, alternate HR keys
ACTIVITY_LEGACY = {
    "activity_id": "abc-123",
    "name": "Vélo doux",
    "type": "cycling",
    "startTimeGMT": f"{TODAY.isoformat()} 06:00:00",
    "movingDuration": 3600,
    "distance": 30000,
    "avgHr": 118, "maxHr": 141,
    "averageSpeed": 8.33,
}

DAILY = {
    "hrvSummary": {"lastNightAvg": 54, "status": "BALANCED"},
    "sleepScore": 82,
    "sleepTimeSeconds": 27000,
    "restingHeartRate": 43,
    "trainingReadiness": 68,
    "vo2MaxValue": 61.2,
    "extraUnknownField": {"kept": "in raw"},
}


async def test_ingest_connect_shape_with_daily(client):
    res = (
        await client.call_tool(
            "ingest_garmin_activity", {"activity": ACTIVITY_CONNECT, "daily": DAILY}
        )
    ).data

    session = res["session"]
    assert session["sport"] == "run"
    assert session["date"] == TODAY.isoformat()
    assert session["garmin_activity_id"] == "987654321"
    for fragment in ("Sortie tempo", "45:32", "10.02 km", "4:33/km", "FC 156/172",
                     "charge 98", "640 kcal"):
        assert fragment in session["executed"], f"missing {fragment!r}"

    metrics = res["daily_metrics"]
    assert metrics["hrv_ms"] == 54 and metrics["hrv_status"] == "BALANCED"
    assert metrics["sleep_score"] == 82 and metrics["sleep_duration_min"] == 450
    assert metrics["resting_hr"] == 43 and metrics["training_readiness"] == 68
    assert metrics["vo2max_run"] == 61.2
    assert metrics["raw"]["extraUnknownField"] == {"kept": "in raw"}

    assert res["matched_workout"] is None  # nothing planned


async def test_ingest_legacy_shape_no_daily(client):
    res = (
        await client.call_tool("ingest_garmin_activity", {"activity": ACTIVITY_LEGACY})
    ).data
    session = res["session"]
    assert session["sport"] == "bike"
    assert session["garmin_activity_id"] == "abc-123"
    assert "30.00 km" in session["executed"] and "30.0 km/h" in session["executed"]
    assert res["daily_metrics"] is None


async def test_ingest_matches_planned_workout(client):
    planned = (
        await client.call_tool(
            "plan_workout",
            {"date": (TODAY - dt.timedelta(days=1)).isoformat(),  # ±1 day window
             "sport": "run", "title": "Tempo 3x10'", "type": "tempo"},
        )
    ).data
    decoy_other_sport = (
        await client.call_tool(
            "plan_workout", {"date": TODAY.isoformat(), "sport": "bike", "title": "Z2"}
        )
    ).data

    res = (
        await client.call_tool("ingest_garmin_activity", {"activity": ACTIVITY_CONNECT})
    ).data
    match = res["matched_workout"]
    assert match is not None
    assert match["id"] == planned["id"]
    assert match["status"] == "done"
    assert match["session_id"] == res["session"]["id"]
    assert match["title"] == "Tempo 3x10'"  # full workout returned for comparison

    # the bike decoy is untouched
    week = (await client.call_tool("get_week", {})).data
    bike = [w for d in week["days"] for w in d["workouts"] if w["id"] == decoy_other_sport["id"]]
    assert bike[0]["status"] == "planned"

    # re-ingesting the same activity: session deduplicated, no 'planned' run left
    res2 = (
        await client.call_tool("ingest_garmin_activity", {"activity": ACTIVITY_CONNECT})
    ).data
    assert res2["session_reused"] is True
    assert res2["session"]["id"] == res["session"]["id"]
    assert res2["matched_workout"] is None


async def test_ingest_exact_date_wins_over_adjacent(client):
    near = (
        await client.call_tool(
            "plan_workout",
            {"date": (TODAY + dt.timedelta(days=1)).isoformat(), "sport": "run", "title": "J+1"},
        )
    ).data
    exact = (
        await client.call_tool(
            "plan_workout", {"date": TODAY.isoformat(), "sport": "run", "title": "J0"}
        )
    ).data
    res = (
        await client.call_tool("ingest_garmin_activity", {"activity": ACTIVITY_CONNECT})
    ).data
    assert res["matched_workout"]["id"] == exact["id"]
    del near


async def test_ingest_tolerates_garmin_zero_sentinels(client):
    # Garmin emits 0 for "no reading" — must be treated as missing, never fail,
    # and MUST NOT leave a half-written state (this was a confirmed review bug).
    daily_with_sentinels = {
        "restingHeartRate": 0, "vo2MaxValue": 0, "bodyBatteryAtWake": 0,
        "sleepScore": 82, "hrvSummary": {"lastNightAvg": 54},
    }
    res = (
        await client.call_tool(
            "ingest_garmin_activity",
            {"activity": ACTIVITY_CONNECT, "daily": daily_with_sentinels},
        )
    ).data
    metrics = res["daily_metrics"]
    assert metrics["resting_hr"] is None       # sentinel dropped
    assert metrics["vo2max_run"] is None       # sentinel dropped
    assert metrics["body_battery_start"] is None
    assert metrics["sleep_score"] == 82        # real values kept
    assert metrics["hrv_ms"] == 54
    assert metrics["raw"]["restingHeartRate"] == 0  # source blob intact

    sessions = (await client.call_tool("get_recent_sessions", {})).data
    assert len(sessions) == 1                  # exactly one session, no orphan


async def test_ingest_survives_out_of_range_wellness_fields(client):
    """Non-regression : un champ de bien-etre aberrant ne doit PAS faire perdre
    la seance d entrainement.

    Le commit 1c0eb7b avait pose des bornes sur DailyMetricsUpdate sans filtre
    correspondant dans _normalize_daily, qui passait ctl/atl/tsb verbatim. Un
    ctl negatif envoye par Garmin levait donc une ValidationError AVANT la
    creation de la seance : toute l ingestion etait perdue pour un champ
    accessoire. Le filtre jette desormais le champ hors plage comme le
    sentinel 0, et le blob source reste intact.
    """
    daily_aberrant = {
        "ctl": -3.0,                    # negatif : hors plage ge=0
        "atl": -0.5,
        "tsb": float("inf"),            # infini : passait sous une simple borne basse
        "lastNightAvg": 6000,           # artefact de capteur
        "sleepTimeSeconds": 10**9,      # duree absurde
        "sleepScore": 86,               # ces deux-la sont valides et doivent survivre
        "restingHeartRate": 45,
    }
    res = (
        await client.call_tool(
            "ingest_garmin_activity",
            {"activity": ACTIVITY_CONNECT, "daily": daily_aberrant},
        )
    ).data

    metrics = res["daily_metrics"]
    # champs hors plage jetes, pas d erreur
    assert metrics["ctl"] is None
    assert metrics["atl"] is None
    assert metrics["tsb"] is None
    assert metrics["hrv_ms"] is None
    assert metrics["sleep_duration_min"] is None
    # champs valides conserves
    assert metrics["sleep_score"] == 86
    assert metrics["resting_hr"] == 45
    # rien n est perdu : le blob source garde les valeurs brutes
    assert metrics["raw"]["ctl"] == -3.0
    assert metrics["raw"]["lastNightAvg"] == 6000

    # et surtout : la SEANCE existe bien
    sessions = (await client.call_tool("get_recent_sessions", {})).data
    assert len(sessions) == 1


async def test_ingest_idempotent_no_duplicate_sessions(client):
    for _ in range(3):
        res = (
            await client.call_tool("ingest_garmin_activity", {"activity": ACTIVITY_CONNECT})
        ).data
    assert res["session_reused"] is True
    sessions = (await client.call_tool("get_recent_sessions", {})).data
    assert len(sessions) == 1


async def test_ingest_retry_self_heals_plan_match(client):
    # First ingest: nothing planned yet → session created, no match.
    first = (
        await client.call_tool("ingest_garmin_activity", {"activity": ACTIVITY_CONNECT})
    ).data
    assert first["matched_workout"] is None

    # Plan arrives afterwards; re-ingest completes the pairing with the SAME session.
    planned = (
        await client.call_tool(
            "plan_workout", {"date": TODAY.isoformat(), "sport": "run", "title": "Tempo"}
        )
    ).data
    retry = (
        await client.call_tool("ingest_garmin_activity", {"activity": ACTIVITY_CONNECT})
    ).data
    assert retry["session_reused"] is True
    assert retry["session"]["id"] == first["session"]["id"]
    assert retry["matched_workout"]["id"] == planned["id"]
    assert retry["matched_workout"]["session_id"] == first["session"]["id"]


async def test_log_session_duplicate_garmin_id_rejected(client):
    await client.call_tool(
        "log_session",
        {"sport": "run", "date": TODAY.isoformat(), "garmin_activity_id": "dup-1"},
    )
    with pytest.raises(ToolError):
        await client.call_tool(
            "log_session",
            {"sport": "bike", "date": TODAY.isoformat(), "garmin_activity_id": "dup-1"},
        )


async def test_ingest_rejects_unusable_payloads(client):
    with pytest.raises(ToolError):  # no date anywhere
        await client.call_tool(
            "ingest_garmin_activity",
            {"activity": {"activityType": {"typeKey": "running"}, "duration": 100}},
        )
    with pytest.raises(ToolError):  # no sport
        await client.call_tool(
            "ingest_garmin_activity",
            {"activity": {"startTimeLocal": f"{TODAY.isoformat()} 08:00:00"}},
        )
