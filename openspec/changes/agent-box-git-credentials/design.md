## Context

The served tools execute in `universe_tools`' persistent `/u` workspace. The
separate `agent_loop/box_tools.py` `/cc` interface is opt-in and has no production
provider registration. `LocalBoxProvider` explicitly lacks isolation and cannot
be enabled as a production fallback. Its bind/event contract also differs from
the thin-loop interface, so a local-driver demonstration is not served proof.

`universe_egress` checks and pins public IPs, then relays opaque CONNECT bytes.
It receives a command-center directory but no authenticated per-turn agent
grant. `CredentialBlindBroker` injects typed HTTP auth on authorized requests;
its ordinary response body is decoded UTF-8 (`errors=replace`). Streaming has
secret scanning and bounds, but no git request/response protocol adapter.
`workspace_git` uses a different boundary: trusted git operates outside the
user checkout and exchanges bundles. Its credential helper must never be
mounted into an agent-controlled box.

## Goals / Non-Goals

Goals: a vendor-neutral, revocable route supporting clone/fetch/push using
existing connection custody without giving the agent a token. Keep the same
network namespace isolation, public-address pinning and owner boundaries.

Non-goals for this delivery: enabling that route, implementing a production box
provider, exercising real private repositories, or deploying this draft.

## Decisions

1. Use an explicit broker route with an opaque per-turn surrogate, not TLS
   interception of arbitrary CONNECT traffic. Git URL rewriting can select a
   local smart-HTTP origin backed by the existing Unix-socket forwarder. The
   broker alone opens upstream HTTPS and injects bearer/basic authentication.
   The surrogate is usable only on the authenticated command-center channel;
   possession alone must not authorize another agent or command center.
2. Resolve host via `connection_git_host`, with no GitHub mapping. Bind one
   canonical repository path and `git_read`/`git_write` scope through the existing
   workspace-authority validators. HTTP API permission alone does not authorize
   git. Ambiguous connections fail with an actionable error. PR create/update
   continues through the existing granted HTTP API connection; git push grants
   do not imply PR authority.
3. Read covers GET info/refs for upload-pack and POST git-upload-pack; write
   additionally covers receive-pack discovery and POST git-receive-pack. Reject
   arbitrary methods, services, alternate repositories, encoded traversal,
   mismatched Host, userinfo, redirects, ports and schemes. Parse HTTP once and
   reject ambiguous length/transfer encodings. Pin all resolved upstream IPs;
   keep the existing private/metadata address refusal, including after DNS
   changes. Repository contents and git config never select credentials.
4. Revalidate owner, command center, agent grant, connection incarnation and
   read/write scope before every upstream request and while streaming. Revoke
   closes in-flight streams and invalidates surrogates. Fence broker restarts
   with the broker generation. Retrying a push after an unknown result requires
   checking remote refs first, never automatic replay.
5. Add binary-safe bounded broker request and response streaming, preserving git
   pack bytes while refusing any upstream echo of credential material before
   those bytes reach the box. Define fail-closed scanning for binary bodies and
   header/error paths; never return Authorization or raw broker exceptions.
   Neither argv, environment, filesystem, git config, trace logs nor subprocess
   credential-helper output inside the box may contain the real credential.
6. Stop at this proposal: decisions 1, 4 and 5 need new authority-bearing IPC
   and streaming contracts. A plaintext-only proxy patch would not solve normal
   HTTPS git and would falsely advertise generic credential injection.

## Risks / Trade-offs

- Binary streaming with secret scanning can refuse a coincidental byte match;
  fail closed and report a fixed error instead of corrupting pack data.
- A repo may contain symlinks that today's jail refuses. Do not relax seccomp;
  evaluate checkout representation separately (git's core.symlinks=false).
- The oracle includes development dependencies that production does not. The
  production jail's `/usr/local/bin/python` needs its own pytest installation;
  installing into `/opt/venv` alone does not make it visible to the jail.
- The UID-split lane changes broker peer verification and ledger placement.
  Implement on its final interfaces; never restore direct vault access to the
  coordinator/egress role. The Dockerfile also overlaps textually with that lane.

## Migration Plan

Keep credentials disabled until the binary IPC and bound route are reviewed and
proved. Use only a synthetic smart-HTTP server in the Linux oracle for
authenticated clone/fetch/push, including revoked/wrong-owner/wrong-repo
negatives and token searches. Enable on a real isolating box provider only after
contract and jail proofs. Rollback disables the route and revokes surrogates;
existing checkouts remain intact. No schema migration in this delivery.

## Open Questions

Future implementation must select the exact binary IPC frame shape and prove
bounded streaming and revocation under backpressure. This is the reason for
proposal-only status, not authorization to ship a partial credential route.

## Delivery evidence (2026-10-05, draft PR #4485)

- Linux oracle: 122 passed, no skips, including all five developer probes plus
  box tools, egress, real jail isolation/resource tests and Dockerfile shape.
  Public clone was enabled with `--env TA_DEV_PUBLIC_PROBE=1`.
- Windows: 82 passed, 18 platform skips; these skips are not Linux proof.
- Exact pinned production base image: `/usr/local/bin/python -m pytest --version`
  fails with `No module named pytest`; installing the Dockerfile's pytest 8.4.2
  into that interpreter makes a small pytest project pass. Full production image
  build/deployment remains unverified; oracle already contains dev dependencies.
- Synthetic smart HTTP: host control clone/push/fetch succeeds; registered-grant
  jail clone/fetch/push each exit 128 at authentication, server sees no auth.
  This proves the gap, not successful broker injection. No real private repo or
  remote push was used for verification.
- Ruff clean; OpenSpec strict validation passed; mirror parity 601/601;
  hygiene 6 added, 0 removed, 0 tampering.
- Cross-family review via peer-agents (Claude), exit 0, VERDICT: APPROVE.
  AGREE: no floor/correctness findings; no assertion or isolation relaxation.
- No deployment or real-user app pass claimed. The remaining credential tasks
  and production `/cc` integration are intentionally open.
