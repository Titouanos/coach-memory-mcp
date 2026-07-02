-- 0005_sessions_garmin_unique.sql — one session per Garmin activity
--
-- Backstop for ingest idempotency: ingest_garmin_activity dedups by
-- garmin_activity_id before inserting, and this partial unique index guarantees
-- it at the database level (a retry after a mid-pipeline failure can never
-- duplicate the training log). NULLs stay unconstrained — manually logged
-- sessions have no Garmin id.

CREATE UNIQUE INDEX sessions_garmin_activity_uq
    ON coach.sessions (garmin_activity_id)
    WHERE garmin_activity_id IS NOT NULL;
