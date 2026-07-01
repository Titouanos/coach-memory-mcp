"""profile domain — the athlete's physiological baseline."""

from __future__ import annotations

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import ValidationError

from ..models import AthleteProfileUpdate
from ..repositories import Repositories


def register(mcp: FastMCP, repos: Repositories) -> None:
    @mcp.tool
    async def update_athlete_profile(fields: dict) -> dict:
        """Upsert the athlete's profile (a single persistent row). Pass ONLY the
        fields you want to change — unspecified fields are left untouched.

        Allowed fields: weight_kg, height_cm, vo2max_run, vo2max_bike, ftp,
        hr_max, lthr (lactate-threshold HR, bpm), threshold_pace (e.g. '3:55/km'),
        hr_zones (object, e.g. {"z2": [121,145]}), notes.

        Unspecified fields — and fields passed as null — are left untouched (a
        null is treated as "no change", never as "erase this value").

        Returns the full profile after the update.
        """
        try:
            update = AthleteProfileUpdate.model_validate(fields)
        except ValidationError as exc:
            raise ToolError(f"invalid profile fields: {exc}") from exc
        # exclude_none guards against an explicit null overwriting stored data
        # with SQL NULL; exclude_unset keeps this a genuine partial update.
        data = update.model_dump(exclude_unset=True, exclude_none=True)
        if not data:
            raise ToolError("no updatable fields provided; pass at least one non-null field")
        profile = await repos.profile.upsert(data)
        return profile.model_dump(mode="json")
