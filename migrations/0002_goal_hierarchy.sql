-- 0002_goal_hierarchy.sql — goal hierarchy + race→goal linkage
--
-- Adds:
--   * goals.parent_id — self-referencing FK so goals form a tree
--     (e.g. long_term root → season goal → block goal)
--   * goals.horizon   — long_term | season | block (nullable: legacy goals
--     and one-off goals may have no horizon)
--   * races.goal_id   — which goal a race serves (e.g. an A-race realizes a
--     season goal)
--
-- Purely additive; existing rows keep NULLs. Deeper cycle prevention (beyond
-- self-reference) is enforced in the repository layer with a recursive
-- ancestor check, since a CHECK constraint cannot walk the tree.

ALTER TABLE coach.goals
    ADD COLUMN parent_id bigint REFERENCES coach.goals (id),
    ADD COLUMN horizon   text;

ALTER TABLE coach.goals
    ADD CONSTRAINT goals_horizon_chk
        CHECK (horizon IS NULL OR horizon IN ('long_term', 'season', 'block')),
    ADD CONSTRAINT goals_no_self_parent_chk
        CHECK (parent_id IS NULL OR parent_id <> id);

CREATE INDEX goals_parent_idx ON coach.goals (parent_id);

ALTER TABLE coach.races
    ADD COLUMN goal_id bigint REFERENCES coach.goals (id);

CREATE INDEX races_goal_idx ON coach.races (goal_id);
