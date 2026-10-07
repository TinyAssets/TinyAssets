# Cloud-only runtime admission

## Why

ADR-008 (founder directive, 2026-09-21 PDT) requires
that the hosted platform run exclusively on cloud infrastructure and that
`DESKTOP-KCPMGP3` — the founder's personal home desktop — never serve platform
traffic, execute platform/universe work, supply a model relay, hold required
runtime state, schedule recovery, or become a temporary/emergency/fallback
dependency. AGENTS.md carries the matching prohibition. The directive is
explicit that a hostname denylist is not the boundary and that "unknown
provenance is not proof of a cloud executor". It is also explicit that this is
the *required design*, not a claim the code already enforces it.

Today it does not. Every "cloud" marker in the tree is self-asserted by the
process asserting it:

- `daemon_registry.ensure_daemon_runtime` (`tinyassets/daemon_registry.py:489-530`)
  writes `"runtime_registration": "cloud_worker"` into runtime metadata
  unconditionally, on any machine, revalidated on this branch at base
  `02b1e628`.
- `claim_assigned` (`tinyassets/branch_tasks_v2.py:462-511`) is the CAS that
  actually transfers task ownership. Its in-transaction predicate is
  `_transaction_allows_assigned_consumer` plus an **optional** `authority_claim`
  callback; **executor class is a parameter of neither**. `transaction_check`
  returns the existing predicate result unchanged when the callback is absent
  (`branch_tasks_v2.py:489`), so a mandatory gate placed only in
  `authority_claim` is opt-out by construction. The only executor-class check in
  the claim path is `_consumer_skip_reason`
  (`tinyassets/runtime/assigned_queue_consumer.py:110-119`), evaluated in the
  claimer's own Python before the transaction — a diagnostic, not a gate.
- Foreground and served provider execution call no `claim_assigned` at all and
  stamp the class as a literal: `foreground_run_provider.py:484,595`,
  `background_served_provider.py:1336,1547`. Queue and registration checks
  cannot reach these paths.
- Public ingress accepts *token possession*: `scripts/run-tunnel.sh:55-58` execs
  `cloudflared tunnel run --token`, and `deploy/compose.yml:182` passes
  `CLOUDFLARE_TUNNEL_TOKEN`. Possession is a credential fact, not provenance.
- `automation_executor_class` is a nullable descriptive column whose CHECK still
  admits `'tray'` (`tinyassets/storage/request_admissions.py:264-267`, read back
  at `branch_tasks_v2.py:1368`). Cloud-only is not expressible as a storage
  constraint today.

Two corrections bound the claim this change makes. A dev-local clone of the DB
is **not** the live platform DB, and no path from a local claim to production
authority or routing has been demonstrated — so the present state is an
unenforced boundary, not a proved production breach. And a droplet-injected
secret or a copied tunnel token remains copyable; nothing available here is
hardware attestation, and this proposal does not describe anything as such.

No live desktop dependency was proved either: the local inventory shows no
TinyAssets or `cloudflared` service/process and the legacy drain/guard tasks are
disabled. The gap is therefore an *architecture* gap to close before the next
accident, not an active outage.

**Live milestone (2026-09-22).** PR #3913, sha
`dfa22598c35aabad7be27aacbff75d300e17b584`, removed daemon/tray/plugin tunnel
startup. Hosted build `35689968798` and deploy `35690255704` passed public
handles and the protected-SHA gate at 05:20 UTC. Ordinary primary-app retest 8
completed 22:28 PDT: five controls pass, sequential 37.3s, parallel 158.4s;
intermittents remain open. **This is not cloud-boundary or free-user proof.**
The cloud-side tunnel remains and routing, credential and data custody are all
still open. PR #3914 mergedfdb6ff15; hosted run35694437735 on2026-09-22 at06:21UTC
observed reachable container metadata matching the expected droplet and correct
public Worker path bindings. Tunnel/DNS unknown (missing account/tunnel IDs),
credential custody unknown and SSH trust TOFU-unverified. Overall unknown,
boundary_closed=false. This clears only the runtime builder's metadata gate.

## What Changes

One fail-closed provenance resolver, consumed at the four boundaries a registry
or claim guard alone would miss, plus an explicit separation of application
enforcement from cloud network/credential custody.

1. **Provenance resolver** — `resolve_platform_runtime_provenance()`,
   fail-closed: absent or unverifiable evidence ⇒ not cloud ⇒ refuse, never a
   host fallback. Hostname, container name, compose label and env naming are
   never evidence (they travel with a checkout).
   Its record-only verdict is also **readable back** from the process that holds
   it, because a startup log line alone is not evidence that the main serving
   process cached anything: an optional sanitized `platform_runtime_provenance`
   field on the existing authenticated `/mcp/pulse`, emitted only for the
   operational probe principal, through a **non-mutating peek** that never
   resolves, never initializes the cache and reports explicit `unknown` for
   unobserved, failed or PID-inherited state. No new route, workflow, secret,
   credential or principal; no auth widening; nothing branches on it.
2. **Claim admission bound to the non-optional refusal path** — the invariant
   binds to `_transaction_allows_assigned_consumer` /
   `_assigned_consumer_refusal_reason` (`branch_tasks_v2.py:1170`), which every
   claim traverses. It SHALL NOT live only in `authority_claim`; there is no
   opt-out when that callback is `None`. Bounded metadata resolves **before** the
   SQLite write transaction opens, and only the resulting trusted,
   process-owned evidence is evaluated inside the claim CAS. No HTTP I/O runs
   under the database write lock. `_consumer_skip_reason` stays as the
   refusal-ledger diagnostic.
3. **Runtime registration** — `ensure_daemon_runtime` writes the *resolved*
   registration and refuses rather than writing `cloud_worker`. An existing
   registration row grants no provenance: authority is re-resolved on read. The
   consumer's `boot_id` (`assigned_queue_consumer.py:209`) is `uuid.uuid4().hex`
   — a process incarnation / liveness marker, **not** an identity, an ordered
   epoch or cloud proof — so it carries no independent anti-replay claim, and no
   new storage schema or registry is introduced merely to encode it. Legitimate
   restarts and simultaneous cloud workers keep working unchanged; staleness
   stays with the **existing** descriptor expiry.
4. **Startup / foreground / served execution** — because the foreground and
   served paths never reach the queue, they are covered by exactly two
   boundaries: serving startup, which resolves provenance once and refuses to
   serve unadmitted with no degraded local mode; and the last provider-authority
   boundary, where the four literal `executor_class="cloud"` sites
   (`foreground_run_provider.py:484,595`,
   `background_served_provider.py:1336,1547`) take the resolver's result.
   Per-universe, user-bound authority is preserved exactly as it is — this adds
   a platform-provenance condition, it does not relocate authority away from the
   universe's owner.
5. **Off-cloud ingress is prevented, not merely refused** — the repo removes its
   own ability to enroll a Cloudflare connector or publish a public ingress,
   because a connector that *receives then refuses* has already absorbed public
   availability. **Landed** in PR #3913 (`dfa22598c35aabad7be27aacbff75d300e17b584`):
   daemon, tray and plugin tunnel startup are gone. This removes an
   accidental-start path only — the cloud-side tunnel remains, and tunnel-token
   custody is untouched and still open. Origin refusal stays as the backstop for
   what deletion and custody cannot cover.
6. **Recovery and operator retirement are different things.** *Automatic*
   recovery — the daemon watchdog and the assigned consumer's startup/poll
   paths — leaves work **pending** when no admitted cloud successor exists; it
   never re-homes work to an unadmitted runtime, not even momentarily. An
   *explicit operator retirement* (`tinyassets.runtime_reconcile stale-fleet
   --apply`) is a different act: it is a digest- and count-confirmed
   cancellation of exactly the reviewed stale tasks, it assigns no work, and it
   is admitted like any other platform write. Two clarifications the earlier
   wording blurred: `release-reconcile.yml` reconciles the **deployed release**
   against `main` and is not a universe-reassignment path at all; and
   `deploy/daemon-watchdog.sh` restarts the **same cloud service/container**, so
   an admission refusal producing no heartbeat and a restart loop is the
   intended fail-closed outcome, never a licence to add a local fallback.
7. **Custody, stated and verified, not coded here** — the cloud network and
   credential controls (Cloudflare tunnel/Access, DO firewall, GitHub secrets)
   are named as invariants and verified read-only from hosted CI by a bounded
   preflight (`scripts/cloud_only_preflight.py`, hosted run35694437735).
   Production authority rests on this custody layer; the resolver is an
   accidental-start guard, not attestation.

## Impact

- Affected specs: new capability `cloud-only-runtime-admission` and the existing
  `live-mcp-connector-surface` release-read requirement (delta and as-built sync).
- Affected code (implementation follows review, not in this change):
  `tinyassets/branch_tasks_v2.py`, `tinyassets/daemon_registry.py`,
  `tinyassets/runtime/assigned_queue_consumer.py`,
  `tinyassets/background_served_provider.py`,
  `tinyassets/foreground_run_provider.py`, serving startup, one new
  read-only verification workflow.
- Risk: an over-strict resolver takes production down. Mitigated by landing the
  resolver plus sanitized startup record/readback first in record-only mode on the droplet,
  confirming it resolves CLOUD there, and only then flipping the refusal sites.
  **Record-only is not enforcement and is not guaranteed risk-free**: it still
  adds a metadata read and a startup log record on a live path, so it is staged and
  observed, never asserted as inert.
- Non-goals: no new privileged agent fleet, no new provider account, no new MCP
  tool, no refusal flip in the first record-only slice, no custody mutation.

## What this change does *not* claim

The resolver is an **accidental-start guard**, not attestation: unsigned
link-local metadata plus a deploy-copied expected id is forgeable by a local
root operator. Unsigned metadata is only the backstop — the boundary is not
closed until actual cloud routing, credential custody and data custody close it
too, and none of those is closed today. The removal of the in-repo
connector-enrollment path (PR #3913) narrows accidents; it does not establish
custody. Record-only preflight and record-only resolver are **observation, and
explicitly incomplete** — the boundary is not closed until the refusals are
flipped and a deployed sha proves them live.

The provenance readback establishes one fact only: the process that answered one
authenticated probe holds a cached startup verdict. It is **not** binary
freshness (`/mcp/pulse` `git_sha` comes from the mutable release receipt, not the
running binary), **not** the current container incarnation (`uptime_seconds` is
measured from app construction, not process birth), **not** a statement about all
workers (one response samples one responding worker), and **not** attestation or
credential/data custody. An `unknown` verdict is the absence of an observation,
never a pass. Fluentd/log-driver configuration is likewise not a runtime
observation that `docker logs` is available — this change neither uses nor claims
that route.

Nothing here is established by a code label or by a passing local diagnostic.
A local fixture or cloned data root satisfying these checks demonstrates no
production authority, and a green preflight run demonstrates a reachable fact,
not a custody policy. Full enforcement and free onboarding are **not** complete. Free-user acceptance requires the
user's own OpenRouter OAuth authorization, an eligible free-model approval and a
first actual tool-capable response — not a "zero-setup" or "no-credential"
provider, which does not exist.
