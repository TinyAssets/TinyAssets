## Delivery

`BoxTools.bash` asks the `TurnBridge` for the launch's extensions before
`start_exec`. The bridge refuses a foreign handle, closed turn or cancelled
execution, then reads the private `ta-bridge://` resource with
`{"deliver":"extensions"}`. The engine builds the set with the same
`ExtensionCapabilities.deliverable()` the local jail mounts: current
serving-owner authority, the store bound to the signed owner/center/agent,
active state and `settings.yaml` narrowing. It returns revision blobs, bounded at
4 MiB in total; revisions beyond it are listed undelivered and stay
`runtime_unavailable`.

The host recomputes `sha256(blob)` against the revision digest and unpacks it with
the existing package checks. Anything malformed, mismatched or unreachable raises
`remote_extension_delivery_failed` with outcome `not_sent`; the command never runs.

## Mount claim

The engine resource now accepts only host envelopes: `{"ta": <box message>,
"mounts": [[name, revision, generation], ...]}` or the delivery request. A box
message is always nested under `ta`, so a box cannot claim a delivery. The
engine binds the claim with `delivered()` for that one dispatch and
`ExtensionCapabilities.call` keeps every existing check, so a forged or stale
claim for a revision that is not active for this owner is refused.

## In the box

The worker stages files under its launch temp directory, mode 0555 for files
and directories, and exports `TA_EXTENSION_ROOT`. The engine keeps returning
`/ta/extensions/<name>/<revision>` launches. `ta` maps that prefix to the staged
root. Revoke fences new dispatch at the engine and the next launch no longer
receives the bytes. The box user owns the staged copy, so 0555 is a guard
against accidental edits, not a mount boundary. It runs with the box's authority
and is replaced on every launch, so editing it widens nothing.

## Hooks

`_turn_event` opens `coordinator._open_tools`. That is the engine route for
engine turns, with no change. On the thin loop it is the box, with the op id
`<turn>:hook:<event>`. Tool-level `before_tool`/`after_tool` events still fire
in engine middleware for engine-routed tools; loop-served box tools fire none,
as before.

## Hook integrity

On the thin loop, turn hooks run in the owner's box with the box's integrity: best-effort and influenceable by the agent's own code, never a tamper-proof audit. See `docs/concerns/remote-box-tool-hooks.md`.
