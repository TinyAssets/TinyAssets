-- Track A — Seed data for daemon-economy first-draft.
-- Spec: 2026-04-19-track-a-schema-auth-rls.md (archived in git) §6 / §7.
--
-- Per §7 OPEN resolution: empty capability seed. First daemon registration
-- auto-inserts capability rows it declares. This file is intentionally a
-- no-op placeholder so the migration order has an anchor for future seeds
-- (taxonomy expansion, well-known nodes, etc.) without re-numbering.

-- Future seed content goes here.
-- Example (when node-type taxonomy is agreed):
--   INSERT INTO public.capabilities (capability_id, node_type, llm_model, description)
--     VALUES ('goal_planner:claude-4-opus', 'goal_planner', 'claude-4-opus', ...)
--   ON CONFLICT (capability_id) DO NOTHING;

SELECT 1;
