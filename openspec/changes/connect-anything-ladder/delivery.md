# MCP attachment prerequisite handoff

Inspected 2026-10-04 on `feat/mcp-attach-secret-broker`, based on
`origin/main` at `22560b9d0f7f79582cfea482b3c4efb2cda27e5a`.

## Result

Stopped during task 1.1 under the founder's explicit prerequisite-stop option.
No section 1 task is complete. No MCP metadata, attachment, OAuth extension,
secret-entry route or ta dispatch was added. No capability was shipped, so
there is no as-built capability delta to sync. Task 1.4 is deferred along with
the remaining implementation; no raw-key exception is enabled.

## Blocking prerequisite: usable streaming broker authority

The streaming implementation exists, despite the unchecked build tasks in
`broker-streaming-contract/tasks.md`: `tinyassets/broker/` contains the frame
clients, server, scanner, operation journal, fence and supervisor. It is not
available through production startup:

- `tinyassets/broker/supervisor.py:166` (`start_broker`) returns without starting
  anything when the process broker is unselected, and unconditionally raises
  `BrokerUidSplitRequired` when selected (line 179).
- `tinyassets/universe_server.py:5115` calls this entry point before serving
  Streamable HTTP. Selecting the process broker therefore prevents startup.
- The guard explains the isolation failure: daemon, engine children and broker
  share a UID; an engine child can read `owner.json` and claim another owner's
  principal on the trusted owner channel. Removing the guard or directly
  starting `BrokerSupervisor` would bypass the required cross-user boundary.
- `tinyassets/broker/server.py:343` also refuses non-owner streams; authenticated
  box-to-principal derivation is not implemented there. A future ta bridge must
  use an authenticated daemon-side owner path or the completed box authority,
  never expose an owner-channel token to the agent sandbox.

The required fix is already specified by `per-role-uid-split`: a privileged
launcher with verified identity/environment/descriptor handling; distinct
daemon, engine and broker identities; vault/workspace permission migration;
all relevant child spawn sites routed through the launcher; and launcher-backed
broker startup with the fence token held in memory, not an engine-readable
file. Its tasks 2.1-2.8 remain unchecked. This is an image, filesystem migration
and process-authority change, not a minimal MCP transport addition. The existing
concern `docs/concerns/2026-10-04-engine-mcp-shares-daemon-uid.md` tracks the
related daemon-secret isolation issue; this handoff does not duplicate it.

Resume when that boundary is implemented and proven on Linux, and the normal
daemon startup can serve a broker stream while an actual engine identity is
denied the owner token, vault and other owners' authority. Retain the streaming
scanner, cancellation, operation reconciliation and generation fence. Do not
substitute a buffered request adapter or direct HTTP/SSE path.

## Other prerequisite findings and resumption order

Generic OAuth is implemented in `tinyassets/connection_oauth/`: discovery,
S256, DCR, configured public client IDs, owner/request/action binding, vault
deposit and refresh. The optional provider directory remains intact. Its
change still owes live acceptance (task 12).

MCP-specific resource/path discovery, audience binding and CIMD remain work for
task 1.2. The existing `flow.begin` accepts a caller's challenge and
`flow.complete` accepts its verifier. Server-held PKCE and initiating-session /
flow-cookie validation are still assigned to `inline-connect-and-approve`
task 2.3; do not describe that stronger contract as already available.

The existing inline connect request path remains the authorized UI fallback.
The consolidated card and server-side PKCE work belong to
`inline-connect-and-approve`. This lane did not edit
`tinyassets/onboarding/app.html`. Consume that lane's completed card when it
lands; do not create a second card or request authority.

After the broker prerequisite is usable, resume 1.1 with typed metadata and
HTTP-compatible rollback cleanup, then 1.2-1.9 in order. Prove owner/incarnation
checks, cancellation, revocation fencing, uncertain-call non-replay and
credential-free sandbox/catalog outputs before checking the respective tasks.

## Verification

- Windows: `python -m pytest tests/test_broker_supervisor.py -q` — 2 passed.
  These existing tests prove the selected broker refuses startup and the
  unselected path does not start it. They do not prove role separation.
- Linux oracle: `python scripts/linux_oracle.py --
  tests/test_broker_supervisor.py -q` — 2 passed, Python 3.11.16, UID 1001,
  bubblewrap 0.12.0. The initial snapshot attempt failed with
  `tar: .: file changed as we read it` while these notes were being edited;
  the stable-worktree rerun passed. Neither test was skipped.
- `python scripts/concerns_index.py --check` and `git diff --check` passed.
- No touched Python requires Ruff; an additional Ruff check of the oracle and
  hygiene scripts passed.
- Only planning documentation changed. No Python, test, workflow, package or
  `tinyassets/` edits; no plugin mirror regeneration or as-built sync applies.
