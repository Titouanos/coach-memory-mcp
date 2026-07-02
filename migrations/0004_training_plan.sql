-- 0004_training_plan.sql — planned workouts (the "prévu" side of prévu/réalisé)
--
-- A planned workout can be linked to the executed session (`session_id`) once
-- done — usually by the ingest_garmin_activity composite tool — and to the goal
-- it serves (`goal_id`). `block_label` groups workouts into training blocks
-- (e.g. 'Bloc Saint-Gilles S1').

CREATE TABLE coach.training_plan (
    id          bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    date        date        NOT NULL,
    sport       text        NOT NULL,              -- run | bike | swim | strength ...
    type        text,                               -- easy | tempo | VO2 | long | brick ...
    title       text        NOT NULL,
    description text,                               -- detailed content, target zones
    target_load integer,                            -- expected training load (TSS-like)
    status      text        NOT NULL DEFAULT 'planned',
    session_id  bigint      REFERENCES coach.sessions (id),
    block_label text,
    goal_id     bigint      REFERENCES coach.goals (id),
    created_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT training_plan_status_chk
        CHECK (status IN ('planned', 'done', 'skipped', 'modified')),
    CONSTRAINT training_plan_target_load_chk
        CHECK (target_load IS NULL OR target_load >= 0)
);

CREATE INDEX training_plan_date_idx    ON coach.training_plan (date DESC);
CREATE INDEX training_plan_status_idx  ON coach.training_plan (status);
CREATE INDEX training_plan_goal_idx    ON coach.training_plan (goal_id);
CREATE INDEX training_plan_session_idx ON coach.training_plan (session_id);
