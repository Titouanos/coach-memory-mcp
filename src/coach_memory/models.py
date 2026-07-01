"""Shared Pydantic v2 models.

Two roles:
* **Row/output models** (``Goal``, ``Session``, ...) validate & type rows coming
  out of the repositories and serialize cleanly to JSON for MCP tool results.
* **Input models** (``AthleteProfileUpdate``) validate caller-supplied payloads;
  ``model_dump(exclude_unset=True)`` yields exactly the fields the caller set,
  which powers partial updates without clobbering unspecified columns.

Constrained scalar inputs on the tools themselves (RPE 1..10, A/B/C priority,
status enum) are expressed as the aliases below so FastMCP's generated JSON
schema advertises the constraints and rejects bad input before it reaches SQL.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

# --- shared constrained types ----------------------------------------------
GoalType = Literal["race", "perf", "habit"]
GoalStatus = Literal["active", "hit", "missed", "abandoned"]
RacePriority = Literal["A", "B", "C"]
Rpe = Annotated[int, Field(ge=1, le=10, description="Rate of perceived exertion, 1-10")]

_STRICT = ConfigDict(extra="ignore")  # tolerate unselected extra columns defensively


# --- row / output models ----------------------------------------------------
class AthleteProfile(BaseModel):
    model_config = _STRICT
    weight_kg: Optional[float] = None
    height_cm: Optional[float] = None
    vo2max_run: Optional[float] = None
    vo2max_bike: Optional[float] = None
    ftp: Optional[int] = None
    hr_max: Optional[int] = None
    lthr: Optional[int] = None
    threshold_pace: Optional[str] = None
    hr_zones: Optional[dict] = None
    notes: Optional[str] = None
    updated_at: Optional[dt.datetime] = None


class Goal(BaseModel):
    model_config = _STRICT
    id: int
    type: str
    description: str
    target_value: Optional[str] = None
    target_date: Optional[dt.date] = None
    status: GoalStatus
    notes: Optional[str] = None
    created_at: dt.datetime


class Race(BaseModel):
    model_config = _STRICT
    id: int
    name: str
    date: dt.date
    distance: Optional[str] = None
    priority: RacePriority
    target_time: Optional[str] = None
    actual_time: Optional[str] = None
    result_notes: Optional[str] = None
    created_at: dt.datetime


class Session(BaseModel):
    model_config = _STRICT
    id: int
    date: dt.date
    sport: str
    type: Optional[str] = None
    planned: Optional[str] = None
    executed: Optional[str] = None
    rpe: Optional[int] = None
    felt: Optional[str] = None
    garmin_activity_id: Optional[str] = None
    created_at: dt.datetime


class CoachingDecision(BaseModel):
    model_config = _STRICT
    id: int
    date: dt.date
    decision: str
    rationale: Optional[str] = None
    context_snapshot: Optional[dict] = None
    supersedes_id: Optional[int] = None
    active: bool
    created_at: dt.datetime


class Analysis(BaseModel):
    model_config = _STRICT
    id: int
    date: dt.date
    topic: str
    summary: str
    key_metrics: Optional[dict] = None
    created_at: dt.datetime


class SearchHit(BaseModel):
    model_config = _STRICT
    source: Literal["session", "decision", "analysis"]
    id: int
    date: Optional[str] = None
    snippet: str
    rank: float


# --- input models ------------------------------------------------------------
class AthleteProfileUpdate(BaseModel):
    """Partial update. Only fields explicitly set are written (upsert)."""

    model_config = ConfigDict(extra="forbid")
    weight_kg: Optional[float] = Field(default=None, gt=0)
    height_cm: Optional[float] = Field(default=None, gt=0)
    vo2max_run: Optional[float] = Field(default=None, gt=0)
    vo2max_bike: Optional[float] = Field(default=None, gt=0)
    ftp: Optional[int] = Field(default=None, gt=0)
    hr_max: Optional[int] = Field(default=None, gt=0)
    lthr: Optional[int] = Field(default=None, gt=0)
    threshold_pace: Optional[str] = None
    hr_zones: Optional[dict] = None
    notes: Optional[str] = None
