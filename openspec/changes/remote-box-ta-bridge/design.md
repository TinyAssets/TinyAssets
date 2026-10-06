## Context

The opt-in thin loop consumes a structural BoxExec API. This checkout has a
local unisolated reference BoxProvider and no deployed remote driver. Existing
start/stream/cancel operations bind owner, center, epoch and turn. Local ta uses
the engine's capability dispatcher and its existing effect gates.

## Goals / Non-Goals

Enable ta in remote bash without placing a bearer or connection credential in
the box. Preserve local capability dispatch and remote extension execution.
Provider inventory changes, background executors and deployment are separate.

## Decisions

- A dependency-free worker runs inside the bound box. It supplies ta and a Unix
  socket, emits reverse requests on the execution output channel, and receives
  answers through descriptor-safe BoxProvider.write beneath the box root.
  Requests cannot select a host URL, credential, principal or reply path.
- Trusted code fixes owner, center and turn. It validates the bound handle and
  dispatches through a fixed broker-only program on the existing authenticated
  engine bash route. That program speaks to the existing local ta socket;
  there is no second capability implementation or new public tool. This costs
  one local jail launch per RPC but preserves all existing effect checks.
- A host-side SQLite receipt is reserved before dispatch. A completed request
  returns its recorded answer; a pending receipt after interruption returns
  unknown, never reruns. Request IDs are scoped by owner/center/turn/execution.
  A retry with changed arguments is refused. Cancellation revokes admission
  before cancelling pending trusted calls; already sent effects remain unknown.
- The worker's socket and client are temporary. No process credential is used
  inside the box. The driver MUST isolate each execution's processes and kill
  descendants on cancellation, as required by the existing box contract.

## Risks / Trade-offs

- Uncertain external effects cannot be rolled back: retain unknown receipts.
- A driver without safe write or process isolation cannot satisfy this contract:
  fail loudly; the unisolated local driver is not security evidence.
- Durable receipts consume storage; pruning is outside this change because
  deleting an ambiguous receipt could permit duplicate effects.
- Remote extensions resolve under the bound box root, not the trusted engine's
  filesystem. Provider-specific code and static prompt additions are unnecessary.
