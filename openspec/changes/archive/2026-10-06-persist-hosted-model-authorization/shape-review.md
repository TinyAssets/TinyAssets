# Independent shape disposition — September18, 2026 UTC

Reviewer: Claude Fable5.1, dispatched by lead; terminal exit0 after277seconds.
Transcript: `012a2f5f-56d9-45bf-b377-38c759e10819` in this worktree's Claude project.
Lead delivered verdict ADAPT: safe to build with the concrete corrections below.
This is shape permission, not approval of an implemented or released head.

Required: use shared UTC wall clock for begin, take AND post-take exchange expiry;
require created_at <= now < expires_at and duration <=600seconds; sweep rows with
future created_at so clock rollback cannot consume all pending capacity; never
extend expiry. Use bound_home_id (not universe_id) so owner-keyed erasure includes
former-home bindings. SQLite isolation_level=None and BEGIN IMMEDIATE must precede
select/delete; enforce deletion rowcount1, and lock failure must leave row untouched.

The existing per-owner10 pending cap now survives deployment. Record as a
nonblocking usability constraint; do not broaden cap behavior in this repair.

Parallel read-only integration review found the delayed-begin/account-deletion
race. The design now uses existing check_current_home under canonical author-store
BEGIN IMMEDIATE held through satellite commit (author→satellite order), plus the
current founder/admin check. The exact-head release review MUST explicitly cover
that addition and the real deletion-barrier regression; the lead did not claim
Fable's earlier shape verdict reviewed an unseen addition.
