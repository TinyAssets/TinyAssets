## Context

The opt-in thin loop consumes BoxProvider's bound exec protocol. This checkout
contains the local reference driver, not a deployed remote driver. K2 needs ta
before projecting remote agents to four tools.

## Goals / Non-Goals

Enable remote ta with the same capability dispatcher and grants as local ta,
without placing credentials in the box. Inventory changes, background executors
and deployment remain separate lanes.

## Decisions

- The worker runs inside the bound box. It receives public source and the bash
  command on stdin, installs ta in ephemeral scratch, and opens an abstract Unix
  socket. The box runtime requires Python 3, bash, isolated process/network
  namespaces, ephemeral /tmp and process-tree cancellation. Missing worker
  support fails explicitly; no alternate executor is selected.
- Requests travel on framed execution output; replies use the execution's open
  stdin. BoxProvider gains interactive_stdin and idempotent send_stdin. Replies
  require the exact owner/center/epoch/turn handle of the running execution.
  They never mutate the command-center filesystem or its generation.
- Trusted code fixes owner, center and turn. The existing authenticated engine
  session transports JSON in a private ta-bridge resource, which calls the SAME
  engine_dispatch and Capabilities used by local ta. The signed launch must
  grant bash. No new model-facing tool, provider branch, shell relay, stdout
  parser or extra prompt is involved.
- A host SQLite receipt reserves each owner/center/turn/execution/request identity
  before dispatch. Same-ID retries return a completed receipt or unknown; changed
  payloads are refused. The client retains an ID across a connection retry.
  Re-running a command with a new identity is a new intent, not deduplicated.
- Delivery IDs are distinct from request IDs: a reconnected socket can receive a
  cached result while a retried send_stdin cannot corrupt the byte stream. After
  host restart, orphaned execs are unknown, never restarted. Partial stdin writes
  stay unknown. Cancellation revokes admission before cancelling pending calls.

## Risks / Trade-offs

- Already-sent effects may complete after cancellation. Unknown receipts prevent
  redispatch; no rollback is claimed.
- Receipt pruning needs an explicit turn-retention design. This change retains
  receipts rather than risk deleting an ambiguous effect record.
- A deployed remote driver must implement the extended protocol and isolation
  contract. Linux proofs exercise actual processes and the reference host behind
  real bwrap namespaces; they are not deployed remote-driver evidence.
- Engine and box transports retain the existing 1 MiB request / 8 MiB response
  bounds. Decoded bash output keeps its separate 64 KiB user-output bound.
- Extensions resolve and execute in the box. Credentials remain entirely on the
  trusted side of the existing authenticated engine session.

## Review adaptation

Claude's one cross-family round returned ADAPT. TB-1 through TB-4 were AGREE.
The initial bash relay and durable reply mailboxes were replaced as above;
worker readiness distinguishes startup failure; reconnect IDs now survive retry.
The original verdict is retained rather than relabeled as reviewer approval.
