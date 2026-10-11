-- Track A — Hot-path indexes for daemon-economy first-draft.
-- Spec: 2026-04-19-track-a-schema-auth-rls.md (archived in git) §6.
-- Idempotent (CREATE INDEX IF NOT EXISTS).

-- nodes — owner lookup + domain/status listing.
CREATE INDEX IF NOT EXISTS nodes_owner          ON public.nodes (owner_user_id);
CREATE INDEX IF NOT EXISTS nodes_domain_status  ON public.nodes (domain, status);
CREATE INDEX IF NOT EXISTS nodes_node_type      ON public.nodes (node_type);

-- host_pool — capability + visibility is the dispatch join surface.
CREATE INDEX IF NOT EXISTS host_pool_owner       ON public.host_pool (owner_user_id);
CREATE INDEX IF NOT EXISTS host_pool_cap_vis     ON public.host_pool (capability_id, visibility);

-- requests — broadcast layer filters by (state, capability_id).
-- Partial index on pending is the hot path (§7 state-flagged skip).
CREATE INDEX IF NOT EXISTS requests_state_cap_pending
  ON public.requests (state, capability_id)
  WHERE state = 'pending';
CREATE INDEX IF NOT EXISTS requests_requester    ON public.requests (requester_user_id);
CREATE INDEX IF NOT EXISTS requests_capability   ON public.requests (capability_id);
CREATE INDEX IF NOT EXISTS requests_node         ON public.requests (node_id)
  WHERE node_id IS NOT NULL;

-- bids — (request_id, state) is the claim-SKIP-LOCKED scan path.
CREATE INDEX IF NOT EXISTS bids_request_state    ON public.bids (request_id, state);
CREATE INDEX IF NOT EXISTS bids_bidder           ON public.bids (bidder_user_id);
CREATE INDEX IF NOT EXISTS bids_host             ON public.bids (host_id);

-- ledger — per-user balance scan + per-request audit trail.
CREATE INDEX IF NOT EXISTS ledger_user_at        ON public.ledger (user_id, at DESC);
CREATE INDEX IF NOT EXISTS ledger_by_request
  ON public.ledger (related_request) WHERE related_request IS NOT NULL;
CREATE INDEX IF NOT EXISTS ledger_by_bid
  ON public.ledger (related_bid) WHERE related_bid IS NOT NULL;

-- settlements — both-parties query + per-bid uniqueness (already PK/UNIQUE).
CREATE INDEX IF NOT EXISTS settlements_requester ON public.settlements (requester_id);
CREATE INDEX IF NOT EXISTS settlements_bidder    ON public.settlements (bidder_id);
CREATE INDEX IF NOT EXISTS settlements_request   ON public.settlements (request_id);

-- flags — target lookup + open-state moderation queue.
CREATE INDEX IF NOT EXISTS flags_target
  ON public.flags (target_kind, target_id);
CREATE INDEX IF NOT EXISTS flags_open
  ON public.flags (state, created_at DESC) WHERE state IN ('open','reviewing');
CREATE INDEX IF NOT EXISTS flags_flagger         ON public.flags (flagger_id);
