-- Track A — RLS policies for the 9 in-scope tables.
-- Spec: 2026-04-19-track-a-schema-auth-rls.md (archived in git) §5.
-- Subset of 2026-04-18-full-platform-schema-sketch.md (archived in git) §2.
--
-- Production: auth.uid() reads request.jwt.claims.sub (Supabase).
-- v0 / tests: auth.uid() reads current_setting('app.current_user_id')
-- so the same call site works for both paths.
--
-- PUBLIC role is used (matches prototype 002_rls.sql); production uses
-- 'authenticated'. The sketch uses 'authenticated'; first-draft keeps
-- PUBLIC for test harness compatibility. Service-role is any caller that
-- has BYPASSRLS or is the table owner.

CREATE SCHEMA IF NOT EXISTS auth;

CREATE OR REPLACE FUNCTION auth.uid() RETURNS uuid
  LANGUAGE sql STABLE
  AS $$
    SELECT NULLIF(current_setting('app.current_user_id', true), '')::uuid;
  $$;

-- -----------------------------------------------------------------------
-- users: self-readable + minimal public projection; self-only writes.
-- -----------------------------------------------------------------------
ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;

CREATE POLICY users_select_self ON public.users
  FOR SELECT TO PUBLIC
  USING (auth.uid() = user_id);

-- Minimal public projection for attribution: display_name + github_handle.
-- Any non-null auth.uid() (authenticated caller) can read any user's row;
-- private fields are not in this schema (e.g., email lives in auth.users).
CREATE POLICY users_select_public_projection ON public.users
  FOR SELECT TO PUBLIC
  USING (auth.uid() IS NOT NULL);

CREATE POLICY users_insert_self ON public.users
  FOR INSERT TO PUBLIC
  WITH CHECK (auth.uid() = user_id);

CREATE POLICY users_update_self ON public.users
  FOR UPDATE TO PUBLIC
  USING (auth.uid() = user_id);

-- -----------------------------------------------------------------------
-- capabilities: public-readable registry; service-role-only writes.
-- -----------------------------------------------------------------------
ALTER TABLE public.capabilities ENABLE ROW LEVEL SECURITY;

CREATE POLICY capabilities_select_all ON public.capabilities
  FOR SELECT TO PUBLIC USING (true);

-- No INSERT/UPDATE/DELETE policy for PUBLIC → only service_role can write.

-- -----------------------------------------------------------------------
-- nodes: public-readable for published; self-only otherwise.
-- -----------------------------------------------------------------------
ALTER TABLE public.nodes ENABLE ROW LEVEL SECURITY;

CREATE POLICY nodes_select_owner ON public.nodes
  FOR SELECT TO PUBLIC
  USING (auth.uid() = owner_user_id);

CREATE POLICY nodes_select_published ON public.nodes
  FOR SELECT TO PUBLIC
  USING (status = 'published');

CREATE POLICY nodes_insert_owner ON public.nodes
  FOR INSERT TO PUBLIC
  WITH CHECK (auth.uid() = owner_user_id);

CREATE POLICY nodes_update_owner ON public.nodes
  FOR UPDATE TO PUBLIC
  USING (auth.uid() = owner_user_id);

CREATE POLICY nodes_delete_owner ON public.nodes
  FOR DELETE TO PUBLIC
  USING (auth.uid() = owner_user_id);

-- -----------------------------------------------------------------------
-- host_pool: visibility=paid rows public; self/network owner-only.
-- -----------------------------------------------------------------------
ALTER TABLE public.host_pool ENABLE ROW LEVEL SECURITY;

CREATE POLICY host_pool_select_paid ON public.host_pool
  FOR SELECT TO PUBLIC
  USING (visibility = 'paid');

CREATE POLICY host_pool_select_self ON public.host_pool
  FOR SELECT TO PUBLIC
  USING (auth.uid() = owner_user_id);

CREATE POLICY host_pool_insert_owner ON public.host_pool
  FOR INSERT TO PUBLIC
  WITH CHECK (auth.uid() = owner_user_id);

CREATE POLICY host_pool_update_owner ON public.host_pool
  FOR UPDATE TO PUBLIC
  USING (auth.uid() = owner_user_id);

CREATE POLICY host_pool_delete_owner ON public.host_pool
  FOR DELETE TO PUBLIC
  USING (auth.uid() = owner_user_id);

-- -----------------------------------------------------------------------
-- requests: own-readable + paid/public visible to all authenticated.
-- Direct UPDATE restricted — state transitions go through service-role
-- RPCs only; PUBLIC can only INSERT (place) and SELECT.
-- -----------------------------------------------------------------------
ALTER TABLE public.requests ENABLE ROW LEVEL SECURITY;

CREATE POLICY requests_select_own ON public.requests
  FOR SELECT TO PUBLIC
  USING (auth.uid() = requester_user_id);

CREATE POLICY requests_select_paid_public ON public.requests
  FOR SELECT TO PUBLIC
  USING (visibility IN ('paid','public'));

CREATE OR REPLACE FUNCTION auth.is_request_bidder(p_request_id uuid)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
  SELECT EXISTS (
    SELECT 1 FROM public.bids
    WHERE request_id = p_request_id
      AND bidder_user_id = auth.uid()
  );
$$;

CREATE POLICY requests_select_bidder ON public.requests
  FOR SELECT TO PUBLIC
  USING (auth.is_request_bidder(request_id));

CREATE POLICY requests_insert_self ON public.requests
  FOR INSERT TO PUBLIC
  WITH CHECK (auth.uid() = requester_user_id);

-- Requester may cancel their own request; state transitions beyond
-- cancellation go through service-role RPCs (not a PUBLIC UPDATE policy).
CREATE POLICY requests_cancel_own ON public.requests
  FOR UPDATE TO PUBLIC
  USING (auth.uid() = requester_user_id);

-- -----------------------------------------------------------------------
-- bids: requester-readable (bids on own requests) + bidder-readable (own).
-- Bidder-only INSERT. State transitions via service-role only.
-- -----------------------------------------------------------------------
ALTER TABLE public.bids ENABLE ROW LEVEL SECURITY;

CREATE POLICY bids_select_bidder ON public.bids
  FOR SELECT TO PUBLIC
  USING (auth.uid() = bidder_user_id);

CREATE OR REPLACE FUNCTION auth.is_request_owner(p_request_id uuid)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
  SELECT EXISTS (
    SELECT 1 FROM public.requests
    WHERE request_id = p_request_id
      AND requester_user_id = auth.uid()
  );
$$;

CREATE POLICY bids_select_requester ON public.bids
  FOR SELECT TO PUBLIC
  USING (auth.is_request_owner(request_id));

CREATE POLICY bids_insert_self ON public.bids
  FOR INSERT TO PUBLIC
  WITH CHECK (auth.uid() = bidder_user_id);

-- Bidder may withdraw (set state='withdrawn') via UPDATE; further state
-- transitions are service-role only. Application-layer enforces allowed
-- column/state writes.
CREATE POLICY bids_withdraw_own ON public.bids
  FOR UPDATE TO PUBLIC
  USING (auth.uid() = bidder_user_id);

-- -----------------------------------------------------------------------
-- ledger: self-readable only; service-role-only writes.
-- -----------------------------------------------------------------------
ALTER TABLE public.ledger ENABLE ROW LEVEL SECURITY;

CREATE POLICY ledger_select_own ON public.ledger
  FOR SELECT TO PUBLIC
  USING (auth.uid() = user_id);

-- No INSERT/UPDATE/DELETE policy for PUBLIC → service_role only.

-- -----------------------------------------------------------------------
-- settlements: both parties (requester + bidder) readable; service-role writes.
-- -----------------------------------------------------------------------
ALTER TABLE public.settlements ENABLE ROW LEVEL SECURITY;

CREATE POLICY settlements_select_parties ON public.settlements
  FOR SELECT TO PUBLIC
  USING (auth.uid() IN (requester_id, bidder_id));

-- No INSERT/UPDATE/DELETE policy for PUBLIC → service_role only.

-- -----------------------------------------------------------------------
-- flags: flagger-self readable; target-owner readable for own resources.
-- flagger_id = auth.uid() write.
-- -----------------------------------------------------------------------
ALTER TABLE public.flags ENABLE ROW LEVEL SECURITY;

CREATE POLICY flags_select_flagger ON public.flags
  FOR SELECT TO PUBLIC
  USING (auth.uid() = flagger_id);

CREATE POLICY flags_select_target_owner_node ON public.flags
  FOR SELECT TO PUBLIC
  USING (
    target_kind = 'node' AND EXISTS (
      SELECT 1 FROM public.nodes n
      WHERE n.node_id = flags.target_id
        AND n.owner_user_id = auth.uid()
    )
  );

CREATE POLICY flags_select_target_owner_request ON public.flags
  FOR SELECT TO PUBLIC
  USING (
    target_kind = 'request' AND EXISTS (
      SELECT 1 FROM public.requests r
      WHERE r.request_id = flags.target_id
        AND r.requester_user_id = auth.uid()
    )
  );

CREATE POLICY flags_select_target_owner_bid ON public.flags
  FOR SELECT TO PUBLIC
  USING (
    target_kind = 'bid' AND EXISTS (
      SELECT 1 FROM public.bids b
      WHERE b.bid_id = flags.target_id
        AND b.bidder_user_id = auth.uid()
    )
  );

CREATE POLICY flags_insert_self ON public.flags
  FOR INSERT TO PUBLIC
  WITH CHECK (auth.uid() = flagger_id);

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_roles WHERE rolname = 'tinyassets_fixture_app'
  ) THEN
    CREATE ROLE tinyassets_fixture_app NOLOGIN;
  END IF;
END
$$;

GRANT USAGE ON SCHEMA public, auth TO tinyassets_fixture_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON
  public.users,
  public.capabilities,
  public.nodes,
  public.host_pool,
  public.requests,
  public.bids,
  public.ledger,
  public.settlements,
  public.flags
TO tinyassets_fixture_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public
  TO tinyassets_fixture_app;
GRANT EXECUTE ON FUNCTION auth.uid() TO tinyassets_fixture_app;
REVOKE ALL ON FUNCTION auth.is_request_bidder(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION auth.is_request_owner(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION auth.is_request_bidder(uuid)
  TO tinyassets_fixture_app;
GRANT EXECUTE ON FUNCTION auth.is_request_owner(uuid)
  TO tinyassets_fixture_app;
