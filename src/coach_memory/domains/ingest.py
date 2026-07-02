"""ingest domain — one-call ingestion of a Garmin activity (+ optional daily blob).

Composite tool that removes client-side orchestration: parse the activity,
create the session row, upsert the day's metrics if provided, and auto-match a
'planned' workout (same sport, ±1 day) to flip it to 'done'.

Garmin payloads vary by watch/endpoint, so extraction is deliberately tolerant:
each field is looked up under several known key spellings (including one level
of nesting via dotted paths), unknown fields are ignored, and the full daily
blob is preserved verbatim in daily_metrics.raw.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import ValidationError

from ..models import DailyMetricsUpdate
from ..repositories import Repositories

logger = logging.getLogger("coach_memory.ingest")

_SPORT_MAP = {
    "running": "run", "trail_running": "run", "treadmill_running": "run",
    "track_running": "run", "street_running": "run",
    "cycling": "bike", "road_biking": "bike", "indoor_cycling": "bike",
    "virtual_ride": "bike", "mountain_biking": "bike", "gravel_cycling": "bike",
    "lap_swimming": "swim", "open_water_swimming": "swim", "swimming": "swim",
    "strength_training": "strength", "indoor_cardio": "cardio",
    "multi_sport": "triathlon", "triathlon": "triathlon",
}


def _pick(payload: dict, *paths: str) -> Any:
    """First non-None value among candidate keys ('a' or one-level 'a.b')."""
    for path in paths:
        node: Any = payload
        for part in path.split("."):
            if not isinstance(node, dict):
                node = None
                break
            node = node.get(part)
        if node is not None:
            return node
    return None


def _as_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    f = _as_float(value)
    return round(f) if f is not None else None


def _parse_date(value: Any) -> dt.date | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):  # epoch ms (Garmin beginTimestamp)
        try:
            return dt.datetime.fromtimestamp(value / 1000, tz=dt.timezone.utc).date()
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str) and len(value) >= 10:
        try:
            return dt.date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _fmt_duration(seconds: float) -> str:
    total = round(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _fmt_pace_min_km(speed_ms: float) -> str:
    sec_per_km = 1000 / speed_ms
    m, s = divmod(round(sec_per_km), 60)
    return f"{m}:{s:02d}/km"


def _normalize_activity(activity: dict) -> dict:
    type_node = _pick(activity, "activityType", "activityTypeDTO.typeKey", "type")
    type_key = (
        type_node.get("typeKey") if isinstance(type_node, dict) else type_node
    )
    type_key = str(type_key).lower() if type_key else None

    return {
        "activity_id": _pick(activity, "activityId", "activity_id", "activityUuid"),
        "name": _pick(activity, "activityName", "name", "title"),
        "date": _parse_date(
            _pick(activity, "startTimeLocal", "startTimeGMT", "startTimeGmt",
                  "beginTimestamp", "date")
        ),
        "type_key": type_key,
        "sport": _SPORT_MAP.get(type_key, type_key) if type_key else None,
        "duration_s": _as_float(_pick(activity, "duration", "movingDuration",
                                      "elapsedDuration")),
        "distance_m": _as_float(_pick(activity, "distance", "distanceInMeters")),
        "avg_hr": _as_int(_pick(activity, "averageHR", "avgHr", "averageHeartRate",
                                "avgHeartRate")),
        "max_hr": _as_int(_pick(activity, "maxHR", "maxHr", "maxHeartRate")),
        "avg_speed_ms": _as_float(_pick(activity, "averageSpeed", "avgSpeed")),
        "load": _as_float(_pick(activity, "activityTrainingLoad", "trainingLoad")),
        "calories": _as_int(_pick(activity, "calories", "activeKilocalories")),
        "avg_power": _as_int(_pick(activity, "avgPower", "averagePower")),
    }


def _build_summary(n: dict) -> str:
    parts: list[str] = []
    if n["name"]:
        parts.append(str(n["name"]))
    if n["duration_s"]:
        parts.append(_fmt_duration(n["duration_s"]))
    if n["distance_m"]:
        parts.append(f"{n['distance_m'] / 1000:.2f} km")
    if n["avg_speed_ms"] and n["avg_speed_ms"] > 0:
        if n["sport"] == "run":
            parts.append(f"allure {_fmt_pace_min_km(n['avg_speed_ms'])}")
        elif n["sport"] == "bike":
            parts.append(f"{n['avg_speed_ms'] * 3.6:.1f} km/h")
    if n["avg_hr"]:
        parts.append(f"FC {n['avg_hr']}/{n['max_hr']}" if n["max_hr"] else f"FC {n['avg_hr']}")
    if n["avg_power"]:
        parts.append(f"{n['avg_power']} W")
    if n["load"]:
        parts.append(f"charge {n['load']:.0f}")
    if n["calories"]:
        parts.append(f"{n['calories']} kcal")
    return ", ".join(parts)


def _gauge(value: Any) -> int | None:
    """Gauge metrics (HRV, sleep, readiness, resting HR, body battery, VO2max):
    Garmin emits 0 as a 'no reading' sentinel — treat any non-positive value as
    missing rather than letting it fail validation downstream."""
    n = _as_int(value)
    return n if n is not None and n > 0 else None


def _gauge_f(value: Any) -> float | None:
    f = _as_float(value)
    return f if f is not None and f > 0 else None


def _normalize_daily(daily: dict) -> dict:
    candidates = {
        "hrv_ms": _gauge(_pick(daily, "hrv_ms", "lastNightAvg", "avgOvernightHrv",
                               "hrvSummary.lastNightAvg")),
        "hrv_status": _pick(daily, "hrv_status", "hrvStatus", "hrvSummary.status"),
        "sleep_score": _gauge(_pick(daily, "sleep_score", "sleepScore",
                                    "overallSleepScore", "sleepScores.overall.value")),
        "sleep_duration_min": _gauge(_pick(daily, "sleep_duration_min", "sleepTimeMinutes")),
        "resting_hr": _gauge(_pick(daily, "resting_hr", "restingHeartRate", "restingHR")),
        "body_battery_start": _gauge(_pick(daily, "body_battery_start", "bodyBatteryAtWake")),
        "body_battery_end": _gauge(_pick(daily, "body_battery_end", "bodyBatteryMostRecent")),
        "training_readiness": _gauge(_pick(daily, "training_readiness",
                                           "trainingReadiness", "trainingReadiness.score")),
        # load-balance numbers: 0 and negatives are meaningful, keep verbatim
        "ctl": _as_float(_pick(daily, "ctl")),
        "atl": _as_float(_pick(daily, "atl")),
        "tsb": _as_float(_pick(daily, "tsb")),
        "vo2max_run": _gauge_f(_pick(daily, "vo2max_run", "vo2MaxValue")),
        "vo2max_bike": _gauge_f(_pick(daily, "vo2max_bike", "vo2MaxCyclingValue")),
    }
    sleep_seconds = _gauge(_pick(daily, "sleepTimeSeconds"))
    if candidates["sleep_duration_min"] is None and sleep_seconds is not None:
        candidates["sleep_duration_min"] = sleep_seconds // 60
    normalized = {k: v for k, v in candidates.items() if v is not None}
    normalized["raw"] = daily
    return normalized


def register(mcp: FastMCP, repos: Repositories) -> None:
    @mcp.tool
    async def ingest_garmin_activity(
        activity: dict, daily: dict | None = None
    ) -> dict:
        """One-call ingestion of a Garmin activity (+ optional daily wellness blob).

        Does three things in one round-trip:
        1. Parses the activity JSON (tolerant to watch/endpoint variations) and
           logs a session (executed = structured summary, garmin_activity_id).
        2. If `daily` is given (sleep/HRV/readiness blob), upserts the day's
           daily_metrics (recognized fields normalized, full blob kept in raw).
        3. If a 'planned' workout of the same sport exists within ±1 day, marks
           it 'done' and links the session — the matched workout is returned so
           the coach can compare planned vs executed.

        Idempotent: re-ingesting an activity already in the log (same
        garmin_activity_id) reuses the existing session instead of duplicating
        it, then completes the remaining steps (metrics, plan match) — so a
        retry after a partial failure self-heals.

        Returns {session, session_reused, daily_metrics, matched_workout}
        (nulls where not applicable).
        """
        # ---- parse & validate EVERYTHING before writing anything ------------
        norm = _normalize_activity(activity)
        if norm["date"] is None:
            raise ToolError(
                "could not extract the activity date (looked at startTimeLocal, "
                "startTimeGMT, beginTimestamp, date)"
            )
        if not norm["sport"]:
            raise ToolError(
                "could not extract the sport (activityType.typeKey missing); "
                "pass a normalized 'activityType' in the payload"
            )

        metrics_data: dict | None = None
        if daily is not None:
            try:
                update = DailyMetricsUpdate.model_validate(_normalize_daily(daily))
            except ValidationError as exc:
                raise ToolError(f"invalid daily metrics after normalization: {exc}") from exc
            metrics_data = update.model_dump(exclude_unset=True, exclude_none=True)

        # ---- session: dedup by garmin id, else create -----------------------
        garmin_id = str(norm["activity_id"]) if norm["activity_id"] is not None else None
        session = None
        reused = False
        if garmin_id is not None:
            session = await repos.sessions.find_by_garmin_id(garmin_id)
            reused = session is not None
        if session is None:
            summary = _build_summary(norm)
            try:
                session = await repos.sessions.create(
                    date=norm["date"],
                    sport=norm["sport"],
                    type=None,
                    executed=summary or None,
                    garmin_activity_id=garmin_id,
                )
            except ValueError:
                # lost a race with a concurrent ingest of the same activity —
                # the unique index caught it; reuse the winner's row
                assert garmin_id is not None
                session = await repos.sessions.find_by_garmin_id(garmin_id)
                reused = session is not None
                if session is None:  # pragma: no cover — defensive
                    raise ToolError(
                        f"session for activity {garmin_id} exists but could not be read"
                    ) from None

        # ---- daily metrics (validated above, cannot fail on bad input) ------
        metrics_out = None
        if metrics_data is not None:
            metrics_row = await repos.metrics.upsert(norm["date"], metrics_data)
            metrics_out = metrics_row.model_dump(mode="json")

        # ---- pair prévu ↔ réalisé -------------------------------------------
        matched_out = None
        match = await repos.plan.find_planned_match(date=norm["date"], sport=norm["sport"])
        if match is not None:
            marked = await repos.plan.mark(match.id, "done", session.id)
            if marked is not None:
                matched_out = marked.model_dump(mode="json")
                logger.info(
                    "ingest matched planned workout",
                    extra={"workout_id": marked.id, "session_id": session.id},
                )

        return {
            "session": session.model_dump(mode="json"),
            "session_reused": reused,
            "daily_metrics": metrics_out,
            "matched_workout": matched_out,
        }
