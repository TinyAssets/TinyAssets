# Exact ordinary-send recovery

Design gate, not an implementation or merge approval. Owner: mobile recovery lane,
branch `fix/ordinary-send-receipts`, main baseline `8a8ec27532902cd4b61b9886ef194919dcf93e2e`.
The observational repair remains frozen at `cf5ff2a5` on its own branch.

## Why

An ordinary phone send can be accepted and complete after its reply stream is
lost. Text/history matching cannot prove which request completed. Reposting can
repeat providers or external effects. The app needs a durable exact receipt whose
read never starts work, and identical-key POSTs must never grant a second start.

## Scope

Versioned ordinary request key on `converse`; receipt admission before consumption
of any queued inputs; internal journal correlation; terminal envelope and exact
history projection; owner-only `/app/turn/receipt`; corresponding browser recovery.
Existing custom-consumer request keys, authority, provider/effect review and Stop
identities remain independent. No new credential, privilege, setting or authority
DB. No automatic replay, even after a read reports no receipt.

## Integration dependencies

Held #4308 at `cedc4f6dff8849051042de08eafba425141138fe` owns the journal's
transaction fence. Do not edit that branch, duplicate its lease machinery, or
silently omit fences because main lacks them. The structural implementation must
be based on the coordinator-approved fenced baseline, or wait for that dependency.
Queue/carryover durability and reset/deletion coverage require coordination with
the integration lead; foreground provider/effect-review code is excluded.
Placement owns preference routes. This lane adds only a separate receipt handler
and one registration, preserving all preference and shared handler bodies.
