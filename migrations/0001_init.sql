-- 0001_init.sql — initial coaching-memory schema (schema: coach)
--
-- Migrations are append-only and versioned. This file is applied inside a single
-- transaction by the boot-time runner (see src/coach_memory/db.py) and recorded in
-- coach.schema_migrations. NEVER edit an applied migration retroactively — add a new
-- NNNN_*.sql file instead.
--
-- The `coach` schema and the `coach.schema_migrations` bookkeeping table are created
-- by the runner's idempotent bootstrap BEFORE any migration runs, so domain migrations
-- may assume the `coach` schema already exists.

-- 1. athlete_profile — singleton row (id is always TRUE, enforced by CHECK)
CREATE TABLE coach.athlete_profile (
    id             boolean       PRIMARY KEY DEFAULT true,
    weight_kg      numeric(5, 2),
    height_cm      numeric(5, 1),
    vo2max_run     numeric(5, 2),
    vo2max_bike    numeric(5, 2),
    ftp            integer,                       -- functional threshold power (W)
    hr_max         integer,
    lthr           integer,                       -- lactate threshold HR (bpm) — a "seuil"
    threshold_pace text,                          -- run threshold pace, e.g. '3:55/km' — a "seuil"
    hr_zones       jsonb,                          -- e.g. {"z1": [0,120], "z2": [121,145], ...}
    notes          text,
    updated_at     timestamptz   NOT NULL DEFAULT now(),
    CONSTRAINT athlete_profile_singleton CHECK (id)
);

-- 2. goals — race / perf / habit targets with a small status state machine
CREATE TABLE coach.goals (
    id           bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    type         text        NOT NULL,             -- race | perf | habit (free-form, extensible)
    description  text        NOT NULL,
    target_value text,                              -- flexible: '36:59', '260W', '8h sleep'
    target_date  date,
    status       text        NOT NULL DEFAULT 'active',
    notes        text,
    created_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT goals_status_chk CHECK (status IN ('active', 'hit', 'missed', 'abandoned'))
);
CREATE INDEX goals_status_idx      ON coach.goals (status);
CREATE INDEX goals_target_date_idx ON coach.goals (target_date DESC NULLS LAST);

-- 3. races — planned/completed events with A/B/C priority
CREATE TABLE coach.races (
    id           bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name         text        NOT NULL,
    date         date        NOT NULL,
    distance     text,                              -- '10K', 'Ironman 70.3', ...
    priority     text        NOT NULL DEFAULT 'B',  -- A | B | C
    target_time  text,                              -- 'sub-37', '36:59'
    actual_time  text,
    result_notes text,
    created_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT races_priority_chk CHECK (priority IN ('A', 'B', 'C'))
);
CREATE INDEX races_date_idx     ON coach.races (date DESC);
CREATE INDEX races_priority_idx ON coach.races (priority);

-- 4. sessions — planned vs executed training with subjective load (rpe/felt)
CREATE TABLE coach.sessions (
    id                 bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    date               date        NOT NULL,
    sport              text        NOT NULL,        -- run | bike | swim | strength ... free-form
    type               text,                         -- easy | tempo | VO2 | long | brick ... free-form
    planned            text,
    executed           text,
    rpe                integer,                      -- 1..10
    felt               text,
    garmin_activity_id text,                         -- link to the Garmin MCP's raw activity (nullable)
    created_at         timestamptz NOT NULL DEFAULT now(),
    search_vector      tsvector GENERATED ALWAYS AS (
        to_tsvector('english',
            coalesce(executed, '') || ' ' || coalesce(felt, '') || ' ' || coalesce(type, ''))
    ) STORED,
    CONSTRAINT sessions_rpe_chk CHECK (rpe IS NULL OR (rpe BETWEEN 1 AND 10))
);
CREATE INDEX sessions_date_idx   ON coach.sessions (date DESC);
CREATE INDEX sessions_search_idx ON coach.sessions USING GIN (search_vector);

-- 5. coaching_decisions — the anti-staleness core. supersedes_id + active let a newer
--    decision retire an older one: the retired decision stays in history but stops
--    surfacing in the active context digest.
CREATE TABLE coach.coaching_decisions (
    id               bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    date             date        NOT NULL DEFAULT current_date,
    decision         text        NOT NULL,
    rationale        text,
    context_snapshot jsonb,                          -- {"ctl":.., "atl":.., "tsb":.., "hrv":..}
    supersedes_id    bigint      REFERENCES coach.coaching_decisions (id),
    active           boolean     NOT NULL DEFAULT true,
    created_at       timestamptz NOT NULL DEFAULT now(),
    search_vector    tsvector GENERATED ALWAYS AS (
        to_tsvector('english', coalesce(decision, '') || ' ' || coalesce(rationale, ''))
    ) STORED
);
CREATE INDEX coaching_decisions_date_idx   ON coach.coaching_decisions (date DESC);
CREATE INDEX coaching_decisions_active_idx ON coach.coaching_decisions (active);
CREATE INDEX coaching_decisions_search_idx ON coach.coaching_decisions USING GIN (search_vector);

-- 6. analyses — periodic write-ups (weekly review, block debrief, ...)
CREATE TABLE coach.analyses (
    id            bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    date          date        NOT NULL DEFAULT current_date,
    topic         text        NOT NULL,
    summary       text        NOT NULL,
    key_metrics   jsonb,
    created_at    timestamptz NOT NULL DEFAULT now(),
    search_vector tsvector GENERATED ALWAYS AS (
        to_tsvector('english', coalesce(topic, '') || ' ' || coalesce(summary, ''))
    ) STORED
);
CREATE INDEX analyses_date_idx   ON coach.analyses (date DESC);
CREATE INDEX analyses_search_idx ON coach.analyses USING GIN (search_vector);
