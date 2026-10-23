LANE: router unavailable + generic schema drift. Worktree C:\Users\Jonathan\Projects\wf-router-drift, branch
conv/router-503-schema-drift (from conv/source-records 9ab39b4b886; it merges back into that branch later, so avoid
the files other lanes own: broker/model_connect.py, connection_oauth/model_discovery.py, onboarding/source_connect.py,
api/connection_uses.py, api/model_access_requests.py, providers/served_model_plan.py, providers/subscription_*.py,
onboarding/subscription_connect.py, onboarding/app/main.js). Commit each item separately with a test that fails first.

1. Router: when the owner's box is asleep-and-cannot-wake, draining, failing, or its socket refuses, the router answers
   503 with a short JSON error ("owner_box_unavailable") and Retry-After, never 500 or a generic error. Production
   showed /app/api/read and /app/api/status returning 500 ("owner_read_failed") when a box was full or draining, and
   503 for minutes while a box drained. The router only maps and forwards (T1): no new business logic, just the
   transport outcome mapped to one status.
2. Generic schema drift: three cutover breaks came from SQLite tables copied with an old layout
   (hosted_model_flows and connection_oauth_flows in .hosted-model-auth.db, inline_model_flows in
   .runtime/owner-sessions.db); each was fixed one table at a time (see tinyassets/connection_oauth/pkce.py
   _FLOW_COLUMNS and _ensure_schema). Find the shared pattern each store uses to create its tables and make it generic:
   a store whose existing table's columns differ from its declared columns rebuilds that table (for ephemeral flow or
   cache tables: drop and recreate; for durable tables: refuse loudly with the table name, never silently drop data).
   Each store declares which kind its tables are. Replace the per-table special cases with the generic mechanism and
   delete them. Add a test that a store opened over an old layout recovers (ephemeral) or fails loudly (durable).
