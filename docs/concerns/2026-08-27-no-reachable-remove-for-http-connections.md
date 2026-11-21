---
severity: P1
title: App connection removal and reconnect
filed: '2026-09-19'
summary: the remove primitive exists; unpowered app controls and dependent model lifecycle remain unverified live
---

# App connection removal and guided reconnection are incomplete

**Correction, 2026-09-19:** source inspection of current origin/main
(`rg -n "remove_http" tinyassets tests`) contradicts the historical finding below.
`tinyassets/universe_server.py` routes `remove_http` to
`tinyassets/api/http_connection.py`; approved pending requests also reach it.
It already deletes vault records, ledger grants/capabilities and effector consent,
and tests cover ordinary remove/redeposit. The old no-operation claim is obsolete.

The remaining gap is the unpowered-safe **app control and model lifecycle**:
Account has no connection inventory/disconnect. Removing an HTTP model leaves
dependent assignment/custody/setup metadata; `model_setup.py` calls it recovery,
while `model_bootstrap_binding.py` only accepts an untouched first binding.
The user must be able to disconnect in TinyAssets and later reconnect normally,
preserving other sources and user content, without reviving stale authority.
Partial failures and already-dispatched outcomes must stay explicit. Track the
general fix in [the `remove-universe-connections` change](https://github.com/TinyAssets/TinyAssets/tree/88e74e1cd32b1ad19bb8af9bf88ab3bd68e4e979/openspec/changes/remove-universe-connections); no live test-account
interaction until reviewed general fixes are deployed and end-to-end ready.

## Historical finding (superseded)

**Filed:** 2026-08-27
**Verified:** 2026-08-27 against `claude/paste-anything-implementation`
**Severity:** P2 — nothing is broken today, but a credential a user wants gone
cannot be taken away through any surface they can reach

## The finding

`ConnectionLedger` has `revoke_grant` and `revoke_connection`
(`tinyassets/storage/outbound_connections.py:3045`, `:3058`). **No operation
exposes either for an `http` connection.** `write_graph target=connection`
routes `connect_llm`, `connect_http`, `connect_compute`, `resolve_connection`
and otherwise falls through to `_cloud_connections_impl`, which is GitHub-Pipes
only. So a user who deposits a key — including one deposited against a host they
did not intend — has no way to withdraw it.

Found while implementing the paste-anything deposit: the receipt sentence was
drafted as *"Change or remove it below"*, and remove does not exist. The wording
now promises only what the surface can do. The spec requirement it partially
fails is `connection-inference` → *"The resulting grant is stated back and
revocable"*.

## Why it is not a one-liner

Revoking stamps `revoked_at`, and `connect_http`'s conflict check refuses any
re-provision when `resource.revoked_at is not None`
(`tinyassets/api/http_connection.py:~378`). Connection ids are **deterministic**
on `(universe_id, destination)`. So a naive remove permanently burns that
destination name for that universe: the user removes `github`, then cannot ever
deposit `github` again, and the refusal is the opaque `connection_conflict`
which — per
`docs/concerns/2026-08-27-credential-deposit-refusals-are-unobservable.md` —
carries no `detail` at all.

A correct remove therefore has to decide one of:

* re-provision after revoke is permitted when the new policy is otherwise
  identical (revive the row), or
* remove hard-deletes the connection + grant + vault record so the deterministic
  id is free again, or
* the destination is salted so a removed name is not reused.

That is a storage-shape and authority decision in the credential path — the
category `AGENTS.md` says to specify before building, and one needing the
cross-family gate.

## What matters more now

With the deposit confirmation step cut (founder, 2026-08-27), the receipt is the
only place a wrong inference becomes visible. It is currently visible and *not*
undoable. That is the strongest argument for doing this next.

## Related

- `docs/concerns/2026-08-27-credential-deposit-refusals-are-unobservable.md`
- PR #2604 (the deposit this was found in), change
  [the `paste-anything-connection-deposit` change](https://github.com/TinyAssets/TinyAssets/tree/88e74e1cd32b1ad19bb8af9bf88ab3bd68e4e979/openspec/changes/paste-anything-connection-deposit)
