> Consolidated into [one-extension-unit](../one-extension-unit/proposal.md), draft
> PR #4519. K1 ports #4513 smart-HTTP git through the current broker and egress as
> an extension connection contribution. The per-owner git_bridge stays with U1.
> Close #4513 once the folded code reaches main; do not continue a parallel lane.

## Why

A command-center agent needs a real checkout to branch, resolve conflicts, test,
and propose changes to its owner's connected repository. A public HTTPS clone
already works in the served jail; a connection grant does not authenticate git.

## What Changes

Propose a credential-blind git smart-HTTP route from the box's egress proxy to
the existing credential broker, bound to a current owner/command-center grant,
one declared git host, one repository, and read/write scopes. Keep tokens in the
broker and make revocation effective on each request.

This lane is **proposal only for credentials**. The existing egress CONNECT
tunnel cannot inject into TLS, and the normal broker response decodes binary
bodies as UTF-8. Wiring them together would require a new protocol boundary,
not a clean use of the existing HTTP injection path. The founder explicitly
directed stopping at a proposal in that case.

The accompanying developer probes cover the existing served `/u` jail, and the
Dockerfile installs pytest on the Python that jail can see. No new egress
authority, host-network sharing, credential helper in the box, or connection
allowlist exception is introduced.

## Capabilities

### New Capabilities

- `agent-box-git-credentials`: grant-scoped smart HTTP without exposing tokens.

### Modified Capabilities

None in this proposal-only delivery.

## Impact

Future implementation touches `universe_egress`, broker IPC/streaming,
`outbound_connections`, and box turn identity binding. Reuse
`storage/workspace_authority.py` for host and repository scope validation.
`feat/per-role-uid-split` changes the broker client fence, peer verification,
ledger paths and access: reconcile with that lane before implementation.

Owner: Codex. Branch: `fix/agent-box-dev-workflow`. One draft PR. Acceptance for
this delivery is reproducible diagnosis, the pytest image fix, and a reviewable
credential proposal; successful credentialed box git remains future work.
