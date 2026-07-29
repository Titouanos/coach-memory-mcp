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
GoalHorizon = Literal["long_term", "season", "block"]
PlanStatus = Literal["planned", "done", "skipped", "modified"]
RacePriority = Literal["A", "B", "C"]
# "cancelled" retire la course des listes a venir sans effacer la trace de sa
# planification, comme "abandoned" le fait pour un objectif (migration 0006).
RaceStatus = Literal["planned", "done", "cancelled"]
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
    parent_id: Optional[int] = None
    horizon: Optional[GoalHorizon] = None
    created_at: dt.datetime


class Race(BaseModel):
    model_config = _STRICT
    id: int
    name: str
    date: dt.date
    distance: Optional[str] = None
    priority: RacePriority
    status: RaceStatus = "planned"
    target_time: Optional[str] = None
    actual_time: Optional[str] = None
    result_notes: Optional[str] = None
    goal_id: Optional[int] = None
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
    # Payload Garmin brut, conserve depuis la migration 0007 : permet de
    # recalculer splits, cadence et FC par lap sans reinterroger Garmin.
    raw: Optional[dict] = None
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


class DailyMetrics(BaseModel):
    model_config = _STRICT
    date: dt.date
    hrv_ms: Optional[int] = None
    hrv_status: Optional[str] = None
    sleep_score: Optional[int] = None
    sleep_duration_min: Optional[int] = None
    resting_hr: Optional[int] = None
    body_battery_start: Optional[int] = None
    body_battery_end: Optional[int] = None
    training_readiness: Optional[int] = None
    ctl: Optional[float] = None
    atl: Optional[float] = None
    tsb: Optional[float] = None
    vo2max_run: Optional[float] = None
    vo2max_bike: Optional[float] = None
    raw: Optional[dict] = None
    created_at: Optional[dt.datetime] = None


class PlannedWorkout(BaseModel):
    model_config = _STRICT
    id: int
    date: dt.date
    sport: str
    type: Optional[str] = None
    title: str
    description: Optional[str] = None
    target_load: Optional[int] = None
    status: PlanStatus
    session_id: Optional[int] = None
    block_label: Optional[str] = None
    goal_id: Optional[int] = None
    created_at: dt.datetime


class SearchHit(BaseModel):
    model_config = _STRICT
    source: Literal["session", "decision", "analysis"]
    id: int
    date: Optional[str] = None
    snippet: str
    rank: float


# --- input models ------------------------------------------------------------
class DailyMetricsUpdate(BaseModel):
    """Partial daily-metrics upsert. Only fields explicitly set (and non-null)
    are written; a null never erases a stored value."""

    model_config = ConfigDict(extra="forbid")
    # Bornes HAUTES ajoutees le 28/07/2026. Sans elles, une valeur aberrante
    # traversait Pydantic et se cassait sur la colonne Postgres, dont l'erreur
    # remontait telle quelle au client (colonnes etroites : ctl/atl/tsb sont en
    # numeric(6,2), donc plafonnees a 9999.99). Les bornes sont volontairement
    # GENEREUSES : elles servent a attraper une faute de frappe grossiere et a
    # publier une plage sensee dans le JSON Schema des outils, pas a imposer une
    # physiologie stricte.
    hrv_ms: Optional[int] = Field(default=None, ge=0, le=500)  # RMSSD nocturne ~20-120, 200 chez l'elite
    hrv_status: Optional[str] = None
    sleep_score: Optional[int] = Field(default=None, ge=0, le=100)
    # 1440 minutes = 24 h : on ne peut pas dormir davantage dans une journee.
    sleep_duration_min: Optional[int] = Field(default=None, ge=0, le=1440)
    resting_hr: Optional[int] = Field(default=None, gt=0, le=250)  # plafond clinique
    body_battery_start: Optional[int] = Field(default=None, ge=0, le=100)
    body_battery_end: Optional[int] = Field(default=None, ge=0, le=100)
    training_readiness: Optional[int] = Field(default=None, ge=0, le=100)
    # numeric(6,2) : charges cumulees, positives par nature.
    ctl: Optional[float] = Field(default=None, ge=0, le=1000)  # un pic de pro ~150-180
    atl: Optional[float] = Field(default=None, ge=0, le=1000)
    # tsb = ctl - atl, donc legitimement negatif en surcharge.
    tsb: Optional[float] = Field(default=None, ge=-1000, le=1000)
    vo2max_run: Optional[float] = Field(default=None, gt=0, le=100)  # record humain ~97,5
    vo2max_bike: Optional[float] = Field(default=None, gt=0, le=100)
    raw: Optional[dict] = None


class PlannedWorkoutCreate(BaseModel):
    """One workout of a plan (used by plan_workout and each plan_week item)."""

    model_config = ConfigDict(extra="forbid")
    date: dt.date
    sport: str
    title: str
    type: Optional[str] = None
    description: Optional[str] = None
    target_load: Optional[int] = Field(default=None, ge=0, le=2000)  # un Ironman ~500-600 TSS
    block_label: Optional[str] = None
    goal_id: Optional[int] = None


class PlannedWorkoutUpdate(BaseModel):
    """Partial workout edit. Status transitions go through mark_workout."""

    model_config = ConfigDict(extra="forbid")
    date: Optional[dt.date] = None
    sport: Optional[str] = None
    type: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    target_load: Optional[int] = Field(default=None, ge=0, le=2000)  # un Ironman ~500-600 TSS
    block_label: Optional[str] = None
    goal_id: Optional[int] = None


class GoalUpdate(BaseModel):
    """Partial goal edit. Only fields explicitly set (and non-null) are written.

    Status is deliberately excluded — update_goal_status is the single path for
    status transitions.
    """

    model_config = ConfigDict(extra="forbid")
    description: Optional[str] = None
    target_value: Optional[str] = None
    target_date: Optional[dt.date] = None
    notes: Optional[str] = None
    parent_id: Optional[int] = None
    horizon: Optional[GoalHorizon] = None


class AthleteProfileUpdate(BaseModel):
    """Partial update. Only fields explicitly set are written (upsert)."""

    model_config = ConfigDict(extra="forbid")
    # Bornes hautes ajoutees le 28/07/2026 : les colonnes sont etroites
    # (weight_kg / vo2max en numeric(5,2) donc <= 999.99, height_cm en
    # numeric(5,1)), et sans plafond l'erreur Postgres remontait au client.
    weight_kg: Optional[float] = Field(default=None, gt=0, le=300)
    height_cm: Optional[float] = Field(default=None, gt=0, le=250)  # record humain 272 cm
    vo2max_run: Optional[float] = Field(default=None, gt=0, le=100)
    vo2max_bike: Optional[float] = Field(default=None, gt=0, le=100)
    ftp: Optional[int] = Field(default=None, gt=0, le=1000)  # record de l'heure ~440-450 W
    hr_max: Optional[int] = Field(default=None, gt=0, le=250)
    lthr: Optional[int] = Field(default=None, gt=0, le=250)  # lthr <= hr_max
    threshold_pace: Optional[str] = None
    hr_zones: Optional[dict] = None
    notes: Optional[str] = None
