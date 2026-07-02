-- 0003_daily_metrics.sql — one row per day of wellness/charge metrics
--
-- Feeds the coaching layer with the daily Garmin-derived signals (HRV, sleep,
-- readiness, body battery) and the client-computed load numbers (CTL/ATL/TSB).
-- `raw` keeps the full source blob so nothing is lost when the normalized
-- columns don't cover a field. One row per date → upserts are ON CONFLICT(date).

CREATE TABLE coach.daily_metrics (
    date               date          PRIMARY KEY,
    hrv_ms             integer,
    hrv_status         text,                      -- balanced | unbalanced | low ...
    sleep_score        integer,
    sleep_duration_min integer,
    resting_hr         integer,
    body_battery_start integer,
    body_battery_end   integer,
    training_readiness integer,
    ctl                numeric(6, 2),
    atl                numeric(6, 2),
    tsb                numeric(6, 2),
    vo2max_run         numeric(5, 2),
    vo2max_bike        numeric(5, 2),
    raw                jsonb,                      -- full source payload, untouched
    created_at         timestamptz   NOT NULL DEFAULT now(),
    CONSTRAINT daily_metrics_sleep_score_chk
        CHECK (sleep_score IS NULL OR sleep_score BETWEEN 0 AND 100),
    CONSTRAINT daily_metrics_readiness_chk
        CHECK (training_readiness IS NULL OR training_readiness BETWEEN 0 AND 100)
);

CREATE INDEX daily_metrics_date_idx ON coach.daily_metrics (date DESC);
