## Context

K1 consolidates the audit's authoring paths. `ta_cli.py` currently discovers
mutable v1 tools in the jail; `ta_capabilities.py` binds its socket to one launch.
`command_center_packages.py` already provides private content-addressed blobs
and package pins. UI rows, workflows and the outbound ledger remain backends.

## Goals / Non-Goals

One authored manifest, revision and lifecycle for all contributions. No new
public MCP handle, host execution, credential custody implementation or resident
prompt content. U1 owns process admission and package-scoped broker authority.

## Decisions

### Manifest and revision

Schema v2 has `schema_version`, `name`, optional `description`, `executable`,
and arrays `tools`, `hooks`, `commands`, `cards`, `connections`, `mcp_servers`.
Unknown fields, duplicate names/JSON keys, malformed schemas and unsafe relative
paths fail closed. Tools/commands declare name, description and argument schema.
Hooks additionally declare a supported lifecycle event; cards declare a relative
HTML asset. Connection requirements declare a logical slot and required verbs,
never a credential or a transferable connection ID. MCP declarations name a
remote HTTPS endpoint plus optional slot, or a relative stdio executable/args.
No raw secrets or arbitrary environment injection are accepted.

A revision is SHA-256 over canonical manifest plus the complete path/byte map,
including helper files and UI assets, not merely the entrypoint. Installation
copies bytes into the existing private package blob store. No symlinks, absolute
paths, traversal or platform-reserved destinations; files are inert data at
installation. Working-file edits cannot change an installed revision. Installed
agent directory resolution uses authenticated center/agent identity and existing
package installation evidence, never a caller-supplied absolute directory.

### Lifecycle and permissions

State is private control-plane metadata alongside the package store, keyed by
owner, center, agent, extension name and revision. It is not workspace-editable
or exportable. `installed -> active -> revoked`; reactivation is an explicit
exact-revision action. Activating a new revision atomically replaces the active
revision for that binding. Expected revision/generation prevents stale updates.
Revoke increments the generation before cleanup and fences every contribution.

The authenticated launch supplies identity. Install/activate/revoke through ta
require current serving-owner authority; research/delegated callers cannot
mutate lifecycle state. Activation only uses already granted launch capabilities
and recipient-local connection grants. It cannot approve protected requests,
create credentials, widen scopes/endpoints/classifications, grant another
owner's data, or transfer the author's authority. Any additional authority goes
through existing protected owner approval bound to the exact revision. Every
dispatch intersects activation ceiling with current launch and live backend
grants, and rechecks revocation. Package approval and connection approval remain
distinct. Code updates invalidate activation and test receipts.

### One dispatch and backend projection

`ta` exposes extension install/inspect/activate/revoke plus revision-qualified
contributions. Search/describe identify kind, revision and availability, including
unbound connection requirements and unavailable MCP transports. Hook events and
commands use the same dispatch authority as tools; UI is a projection into the
existing isolated renderer, and its calls return through ta's capability gate.
Protected approval chrome stays first-party. Backend rows carry the installation
identity/generation; stale projections cannot serve after revoke, even if cleanup
fails. Workflow-backed effects retain their existing approval/effect machinery.
Hooks cannot suppress audit, Stop, or approval enforcement. Ordered hooks receive
versioned bounded JSON; errors remain visible and do not silently approve effects.

### Execution boundary and U1 handoff

No daemon imports or executes package code. Before U1, ordinary code runs only
inside today's bash jail with its existing launch authority, and no new secret
access. An implementation must not claim a narrower package credential ceiling
while sharing the broader launch socket. Credential-scoped executable dispatch,
remote attachment with newly scoped authority, and persistent stdio admission
remain explicitly unavailable until U1 supplies the boundary.

U1 admission consumes owner/center/agent, installation ID, content digest,
activation generation, capability ceiling and recipient-local slot bindings.
It verifies immutable bytes, checks current generation before starting and on
broker effects, binds package revision to peer process identity, and revokes
leases/cancels processes on Stop/revoke. K1 supplies metadata, not a competing
launcher. Raw-key stdio requires separately protected exact-configuration opt-in
and U1's separate identity/filesystem/process view plus scanned output; absent
that support it is rejected. No fake connected/success status for blocked work.

### Sharing and consolidation

Existing command-center packages carry extension files and logical needs only.
Recipient installation is inert and uses local bindings; no activation state,
credentials, connection IDs, grants or private self-test responses travel.
`command-center-harness-control` (#4503/#4508) extension/hook/card tasks,
`saved-agent-connectors` (#4511), `connect-anything-ladder` (#4496) attachment
lifecycle, and `command-center-agent-templates` (#4515) directory resolution are
superseded by K1. Their settings, generic transport and unrelated copy work
remain intact. No second connector registry or UI authoring protocol is added.

## Risks / Trade-offs

- Shared-jail authority cannot prove package-scoped custody: keep that admission
  unavailable until U1, and report availability separately from installation.
- Backend projections can partially fail: generation fencing is authoritative;
  rebuild projections idempotently and expose failures.
- Legacy v1 remains compatible for now; it cannot claim v2 contributions or
  activation by changing only a version field. Migration is explicit installation.

## Migration Plan

Add schema/state first, then wire ta and backend adapters under the same lifecycle.
No destructive conversion of existing UI, workflows, connections or v1 files.
Rollback disables v2 dispatch while retaining installed blobs, history and revoke.
No deploy claim until deployed SHA and a real-user app pass; draft PR may precede
U1-dependent execution. Decisions and remaining tasks are recorded in the PR.

## Proposal review dispositions (Claude, 2026-10-06)

Verdict ADAPT; AGREE with findings 1-8. Concrete refinements:

1. Exact executable bytes require a read-only per-launch snapshot mount through
   existing jail construction. That mount plumbing is permitted; privileged
   launcher policy stays with U1. Until implemented, executable contributions
   are discoverable but explicitly unavailable, never run from mutable files.
2. Daemon-side dispatch checks the installation generation. Pre-U1 revoke
   fences new dispatch/effects, but cannot unmount or terminate code already
   running in a bash invocation. Strong process cancellation remains U1 work.
3. Automatic hooks need a separate existing-jail invocation with the triggering
   turn's grant, and no authority when there is no turn. They remain unavailable
   until that integration exists; metadata activation alone is not hook execution.
4. Browser UI dispatch must bind the authenticated owner session and active
   installation generation at the existing UI backend, not reuse an absent bash
   socket. Until integrated, cards expose metadata only and cannot claim rendering.
5. Workspace settings only narrow the active set, never activate/reactivate.
   `starter.hooks` is separately dependent on the starter loader; K1 does not
   implement that lane's editable starter behavior.
6. The first install API accepts a complete byte map, not arbitrary blob digests
   or host paths. The jailed client owns workspace reading; the daemon validates
   supplied bytes. Shared imports retain existing package consent/screening.
   No lookup API accepts another owner's revision without a bound installation.
7. Extension blobs use the existing command-center package format with files
   rooted at `extensions/<name>/`; existing build/check functions own encoding.
   The extension revision is the whole deterministic package blob digest. Charge
   the recipient's packages storage before writing, using existing accounting.
   A shared enclosing package has its own digest; extraction/reinstallation
   reconstructs this extension envelope from bytes, without inherited authority.
8. Lifecycle mutation requires `delegated_authority == "serving-owner"`, no
   `approval_id`, and current binding authority. Addressed agents retain their
   own authenticated identity; an addressed agent is not inherently delegated.

Executable updates require explicit activation of the new revision in K1.
Existing presentation-only recipient update policy is unchanged; no automatic
executable update policy is introduced. Superseded proposal notices are recorded
in all four source changes. Their unchecked tasks remain historical pending
work, not falsely marked implemented; K1 is the delivery owner for folded scope.

## Implementation review dispositions

Claude returned ADAPT, reporting no floor break and three runtime correctness
issues plus a mirror synchronization observation. AGREE with containment and
recovery fixes: absent extension authority skips mounting, malformed settings or
unreadable revisions produce visible catalog diagnostics without hiding lifecycle
commands, and revoke checks the installed record without requiring readable code.
The research-specific premise was broader than the existing behavior: ordinary
bash already refuses research in `universe_tools.bash`; that refusal is retained.
No new research execution is introduced.

AGREE that mounts must be launch-local. Mount identity now lives in the bridge's
copied context and propagates with each daemon dispatch, rather than mutable shared
backend state. Tests cover two independent launch contexts and real jailed calls.
AGREE on mirror parity; regenerated after the on-demand handbook addition.
No second review round is claimed. Full review is in review-implementation.md.
