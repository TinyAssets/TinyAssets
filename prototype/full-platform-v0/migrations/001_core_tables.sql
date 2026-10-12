-- Track A — Daemon-economy first-draft core tables.
-- Spec: 2026-04-19-track-a-schema-auth-rls.md (archived in git) §2.
-- Scope: 9 in-scope tables. Foundation shape — alter/add only, never re-shape.
-- Subset of 2026-04-18-full-platform-schema-sketch.md (archived in git).
--
-- In-scope: users, host_pool, capabilities, requests, bids, ledger,
--           settlements, nodes (minimal slice), flags (see 002_flags.sql).
-- Out of scope (deferred): domains, artifact_field_visibility, comments,
--           uploads, branches, goals, gate_claims, node_activity,
--           provider_plan_tiers, public_demand_ranked, capability_grants.

CREATE EXTENSION IF NOT EXISTS pgcrypto;   -- gen_random_uuid

-- -----------------------------------------------------------------------
-- users — thin projection over Supabase auth.users.
--
-- Production: user_id references auth.users(id) ON DELETE CASCADE.
-- v0 prototype: auth.users may not exist; FK added iff target is present
-- so this migration runs against bare Postgres used for tests too.
-- -----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.users (
  user_id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  github_handle     text UNIQUE,
  display_name      text NOT NULL,
  account_age_days  int NOT NULL DEFAULT 0,
  interaction_count int NOT NULL DEFAULT 0,
  trust_tier        text NOT NULL DEFAULT 't1' CHECK (trust_tier IN ('t1','t2','t3')),
  version           bigint NOT NULL DEFAULT 1,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now()
);

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables
             WHERE table_schema = 'auth' AND table_name = 'users')
     AND NOT EXISTS (
       SELECT 1 FROM pg_constraint
       WHERE conname = 'users_user_id_fkey_auth'
         AND conrelid = 'public.users'::regclass
     )
  THEN
    EXECUTE 'ALTER TABLE public.users
             ADD CONSTRAINT users_user_id_fkey_auth
             FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE';
  END IF;
END$$;

-- -----------------------------------------------------------------------
-- capabilities — (node_type, llm_model) registry.
-- Empty seed; first daemon registration auto-inserts rows it declares.
-- -----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.capabilities (
  capability_id text PRIMARY KEY,
  node_type     text NOT NULL,
  llm_model     text NOT NULL,
  description   text,
  created_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (node_type, llm_model)
);

-- -----------------------------------------------------------------------
-- nodes — minimal slice for first-draft.
-- Defers dual-layer concept/instance_ref jsonb (Track L).
-- -----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.nodes (
  node_id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  slug            text NOT NULL,
  name            text NOT NULL,
  domain          text NOT NULL,
  node_type       text NOT NULL,
  status          text NOT NULL DEFAULT 'draft'
                    CHECK (status IN ('draft','published','deprecated','superseded')),
  owner_user_id   uuid NOT NULL REFERENCES public.users(user_id),
  version         bigint NOT NULL DEFAULT 1,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (owner_user_id, slug)
);

-- -----------------------------------------------------------------------
-- host_pool — daemon registrations.
-- Explicitly no last_heartbeat column (derived from Supabase Presence).
-- -----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.host_pool (
  host_id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_user_id  uuid NOT NULL REFERENCES public.users(user_id),
  provider       text NOT NULL CHECK (provider IN ('local','claude','codex','gemini')),
  capability_id  text NOT NULL REFERENCES public.capabilities(capability_id),
  visibility     text NOT NULL DEFAULT 'self'
                   CHECK (visibility IN ('self','network','paid')),
  price_floor    numeric(18,6) NULL,
  max_concurrent int NOT NULL DEFAULT 1,
  always_active  bool NOT NULL DEFAULT false,
  version        bigint NOT NULL DEFAULT 1,
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now()
);

-- -----------------------------------------------------------------------
-- requests — user-posted work requests.
-- State machine: pending → bidding → claimed → running →
--                completed | failed | flagged | cancelled.
-- Split from sketch's request_inbox: bid_price + claim columns live on
-- bids, not requests, per Track A exec-spec §2 (bids as foundation #2).
-- -----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.requests (
  request_id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  requester_user_id  uuid NOT NULL REFERENCES public.users(user_id),
  capability_id      text NOT NULL REFERENCES public.capabilities(capability_id),
  node_id            uuid NULL REFERENCES public.nodes(node_id),
  visibility         text NOT NULL CHECK (visibility IN ('self','network','paid','public')),
  reserve_price      numeric(18,6) NULL,
  deadline           timestamptz NULL,
  state              text NOT NULL DEFAULT 'pending'
                       CHECK (state IN (
                         'pending','bidding','claimed','running',
                         'completed','failed','flagged','cancelled')),
  inputs             jsonb NOT NULL,
  inputs_visibility  text NOT NULL DEFAULT 'owner-only'
                       CHECK (inputs_visibility IN ('owner-only','public')),
  version            bigint NOT NULL DEFAULT 1,
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now()
);

-- -----------------------------------------------------------------------
-- bids — daemon bids on requests.
-- Claim semantics via SELECT FOR UPDATE SKIP LOCKED on bids.state.
-- -----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.bids (
  bid_id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  request_id       uuid NOT NULL REFERENCES public.requests(request_id) ON DELETE CASCADE,
  bidder_user_id   uuid NOT NULL REFERENCES public.users(user_id),
  host_id          uuid NOT NULL REFERENCES public.host_pool(host_id),
  price            numeric(18,6) NOT NULL,
  state            text NOT NULL DEFAULT 'offered'
                     CHECK (state IN (
                       'offered','claimed','running',
                       'completed','failed','flagged','expired','withdrawn')),
  claimed_at       timestamptz NULL,
  deadline         timestamptz NULL,
  version          bigint NOT NULL DEFAULT 1,
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now(),
  UNIQUE (request_id, bidder_user_id)
);

-- -----------------------------------------------------------------------
-- ledger — write-once economic truth.
-- settlement_mode enum per §11 Q4-follow ($1 threshold for batching).
-- Append-only; balance derived via SUM(amount) over user_id.
-- -----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.ledger (
  entry_id         bigserial PRIMARY KEY,
  user_id          uuid NOT NULL REFERENCES public.users(user_id),
  entry_kind       text NOT NULL CHECK (entry_kind IN (
                     'reserve','release','debit','credit','refund','bonus','adjustment')),
  amount           numeric(18,6) NOT NULL,
  currency         text NOT NULL DEFAULT 'tinyassets_credit',
  settlement_mode  text NOT NULL DEFAULT 'immediate'
                     CHECK (settlement_mode IN ('immediate','batched')),
  related_request  uuid NULL REFERENCES public.requests(request_id),
  related_bid      uuid NULL REFERENCES public.bids(bid_id),
  reason           text,
  at               timestamptz NOT NULL DEFAULT now()
);

-- -----------------------------------------------------------------------
-- settlements — per-bid settlement events tied to ledger entries.
-- v1 shape must outlive token-launch migration byte-for-byte.
-- UNIQUE (bid_id) enforces settlement immutability (§8 invariant 4).
-- -----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.settlements (
  settlement_id    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  bid_id           uuid NOT NULL REFERENCES public.bids(bid_id),
  request_id       uuid NOT NULL REFERENCES public.requests(request_id),
  requester_id     uuid NOT NULL REFERENCES public.users(user_id),
  bidder_id        uuid NOT NULL REFERENCES public.users(user_id),
  gross_amount     numeric(18,6) NOT NULL,
  platform_fee     numeric(18,6) NOT NULL DEFAULT 0,
  net_amount       numeric(18,6) NOT NULL,
  currency         text NOT NULL DEFAULT 'tinyassets_credit',
  mode             text NOT NULL CHECK (mode IN ('immediate','batched')),
  debit_entry_id   bigint NOT NULL REFERENCES public.ledger(entry_id),
  credit_entry_id  bigint NOT NULL REFERENCES public.ledger(entry_id),
  settled_at       timestamptz NOT NULL DEFAULT now(),
  UNIQUE (bid_id)
);
