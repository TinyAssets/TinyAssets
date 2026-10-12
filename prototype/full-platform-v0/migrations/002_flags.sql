-- Track A — Moderation hook-point.
-- Spec: 2026-04-19-track-a-schema-auth-rls.md (archived in git) §3.4 / §7.
-- Minimal moderation entry-point. Full moderation surface ships post-first-draft.
--
-- Control plane reads state='flagged' on flags.target_kind. Flagged rows
-- are excluded from broadcast. Target-row state transitions (requests.state
-- / bids.state / nodes.status='flagged') are done by manual host-admin SQL
-- per spec §7 — no trigger ships in first-draft; moderation automation is
-- post-first-draft feature work.

CREATE TABLE IF NOT EXISTS public.flags (
  flag_id       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  target_id     uuid NOT NULL,
  target_kind   text NOT NULL CHECK (target_kind IN (
                  'node','request','bid','user','settlement')),
  flagger_id    uuid NOT NULL REFERENCES public.users(user_id),
  reason        text NOT NULL,
  state         text NOT NULL DEFAULT 'open'
                  CHECK (state IN ('open','reviewing','upheld','dismissed')),
  resolved_by   uuid NULL REFERENCES public.users(user_id),
  resolved_at   timestamptz NULL,
  notes         text,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (target_id, target_kind, flagger_id)  -- one open flag per user per target
);
