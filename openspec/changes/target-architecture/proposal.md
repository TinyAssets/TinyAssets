# Target architecture: sealed command-center boxes behind an always-on control plane

## Why

Founder, 2026-10-02: "i would like to move towards the architecture and
dependencies we want later sooner rather than later. i want to do things
correct the first time." He had already approved the sealed box on 2026-10-01:
"approved, go with the sealed box design".

So this change builds the **final shape now, at small capacity**. Later growth
changes capacity only: more cells, more box hosts, bigger machines. It does not
change interfaces, data placement or code paths. PLAN.md already rejects
feature phasing ("Phased rollout — explicitly rejected", Reference:
Full-Platform Architecture). The founder's rule "all accounts, one code path"
also applies, so nothing here branches on tier or stage.

Today's shape is one droplet whose daemon reads and writes every user's folder
on one shared filesystem, through per-call bwrap jails on one shared kernel.
The findings that keep landing are all consequences of that shape:

- **Shared filesystem.** A provider-planted link made the daemon read and write
  another universe's files (#4244). There are ~350 daemon file operations that
  trust a shared filesystem.
- **Platform state inside the agent's folder.** Per-universe platform state
  (vault, run/consent/usage/attention databases) sits inside the folder the
  provider jail binds read-write. Concern #4258 lists three same-universe
  attacks that a launch-time mount mask cannot close, including an agent forging
  owner consent with a pre-created database.
- **Shared disk.** There is no per-universe disk budget, so one account can
  exhaust storage for everyone.
- **Shared kernel.** It was measured on 2026-10-01: before #4245, `bpf`, `ptrace`,
  `mount` and nested user namespaces all reached the host kernel from the tool
  jail. After #4245, `mount`, the new mount API, `memfd_create`,
  `process_vm_*` and the pidfd family still reach it. The provider jail keeps
  nested user namespaces.
- **Uptime.** Deploy restarts cost ~3.7 min/day (81 restarts in 4 days). Backups
  sit in the droplet's own region, the full backup tier fails GitHub's 2 GiB
  limit, and the DR drill has not run since 2026-07-24.
- **Secrets.** The daemon process environment holds an account-wide DigitalOcean
  token, the live Stripe key, the tunnel token and the WorkOS key.
- **SQLite version.** Production links SQLite 3.46.1. The WAL-reset corruption
  race that Tailscale hit is fixed only in 3.51.3.

Three measured studies (2026-10-01/02; evidence summarised in `design.md`
§Evidence) converge on one target:

- a sealed microVM box per command center that is suspended unless working;
- a thin vendor-neutral agent loop and all timers in an always-on control plane;
- platform state outside every agent environment;
- Postgres for the cross-user transactional domains;
- continuous off-region durability.

## What Changes

This is an **umbrella change**. It defines the final interfaces and data
placement, and the slice plan that builds them. Each slice is opened as its own
delivery change, with at most 12 tasks, one owner and one PR. This change's
`tasks.md` tracks the slices.

1. **Sealed command-center box (`BoxProvider`).**
   - One box per command center. Firecracker with snapshot/restore is the
     primary driver; gVisor is the fallback behind the same interface.
   - Each box owns its files, behind a hard per-box disk bound. The account's
     storage quota stays logical: used bytes, as `account-storage-quota`
     defines. A host reservation ledger prevents host exhaustion.
   - The box API separates binding from waking. It has an op-id execution
     lifecycle (start, stream, cancel, status), paginated and streaming file
     operations, and `share`/`migration` export profiles. Every operation is
     authenticated to its owning account and placement epoch. A box suspends
     after ≤60 s idle and restores only from a matching checkpoint.
   - The daemon never touches box contents through host paths.
   - The on-disk layout is agreed with `command-center-cutover` (#4262) so data
     migrates once (design D8a): `cc-<ulid>/` holds user content,
     `.platform/` holds platform state.
2. **Thin vendor-neutral agent loop in the control plane.**
   - It runs in the cell's single execution owner and speaks the standard HTTP
     model protocols.
   - Credentials stay outside the box and out of the loop. Today's credential
     broker, bound to owner, connection and grant, makes the upstream calls.
     API-key CLIs reach it through an in-box endpoint, so no TLS interception
     is needed.
   - A CLI runs in the box only for credential types that need it. A Claude
     subscription is gated by the founder's TOS decision, owner-only.
3. **Platform state outside every box and universe folder**: vault, run,
   consent, usage, attention and conversation databases, rules, activity
   records, sessions. This closes harness design §4.16 and concern #4258 by
   construction.
4. **The control plane is the only always-on layer**: scheduler, triggers,
   inbox, notifications. Boxes keep no timers.
5. **Data stores.**
   - Postgres for catalog, ledger, inbox and market (founder-approved
     2026-07-25).
   - Per-account SQLite where it fits.
   - Litestream v0.5 continuous off-region replication for every SQLite store.
   - SQLite pinned ≥3.51.3.
6. **A user-to-cell routing seam from day one**, with one cell today:
   `home_cell`, ownership generations, ingress dedup and a transactional outbox.
7. **Usage limits stay storage + seats.**
   - Box-lifecycle compute metering is added.
   - Host admission is first-come across accounts, within each account's seats.
     Work waits and is never refused.
   - A compute-hour budget with a spare-capacity lane is a founder decision,
     not built here.
8. **Uptime.**
   - Deploys fail no requests. There is one execution owner under a fenced
     lease, which keeps the single-writer turn journal, behind blue-green
     frontends that queue during handover.
   - A warm standby in a second region with fenced promotion.
   - A scheduled DR drill that restores from off-region backups, boxes included.
9. **Least-privilege secrets per process.** No platform or cloud-account secret
   sits in any process that handles tenant input.
10. **Migration** from today's shared `/data/<universe>` folders. It is a clean
    cutover in one locked, verified, reversible run, aligned with the
    `command-center-cutover` freeze window (rename C4/C5).
11. **Slice 0: DigitalOcean nested-KVM validation.** It decides between
    Firecracker on the droplet and a bare-metal box host such as OVH Hillsboro.
    gVisor is the fallback.

**Superseded when the cutover lands:**
- per-call bwrap jails as the cross-user boundary;
- the per-universe host-path readers (`universe_files.py` becomes the local
  driver of the box file API);
- the global 4-run pool (`runs.py` `TINYASSETS_RUN_MAX_CONCURRENT`) and the
  4 host tool slots (`universe_tools._HOST_SLOTS`);
- the `.universe-sidecars` platform directory;
- PLAN's "Backend stack (target): Supabase" as a vendor commitment. Postgres
  stays; the vendor is open.

## Capabilities

### New Capabilities

- `command-center-box`: the sealed box per command center, the `BoxProvider`
  contract, disk allocation, wake/suspend, snapshot, egress, export and
  deletion.
- `control-plane-agent-loop`: the thin agent loop; credential placement; turn
  binding to a box handle; when a CLI runs in a box.
- `platform-state-placement`: where platform state lives; Postgres for the four
  transactional domains; the SQLite version floor; continuous off-region
  replication.
- `cell-routing`: the home cell, ownership generations, ingress dedup and the
  outbox.
- `account-compute-budget`: box-lifecycle compute metering, and first-come
  host admission within seats. Any budget is a founder decision.

### Modified Capabilities

- `uptime-and-alarms`: one execution owner behind replaceable frontends, a
  fenced warm standby in a second region, and a scheduled off-region DR drill
  that includes boxes.
- `credential-vault`: per-process secret scope; the credential broker is the
  only holder of the vault key, and the narrow exception.

Existing requirements that slices change are MODIFIED in those slices' own
changes (design §"Spec reconciliation owed"). Raw measurements are in
`evidence.md`.

## Impact

- **Storage shape, authority, migration and money.** This change touches all
  four of AGENTS.md's spec-first categories. Every slice that touches them
  carries proposal and design before code.
- **Code:**
  - `tinyassets/universe_tools.py`, `tinyassets/providers/provider_jail.py`,
    `tinyassets/universe_egress.py`, `tinyassets/universe_files.py`,
    `tinyassets/credential_vault.py`, `tinyassets/agent_sessions.py`;
  - `tinyassets/runs.py` (admission), the scheduler and automations stores;
  - `tinyassets/providers/api_key_http_provider.py` (thin loop);
  - `deploy/compose.yml`, `deploy/hetzner-bootstrap.sh`;
  - `.github/workflows/deploy-prod.yml`, `dr-drill.yml`,
    `p0-outage-triage.yml`.
- **New components:**
  - `boxhostd` (box host service);
  - `boxd` (in-box agent);
  - the Firecracker/jailer and gVisor drivers;
  - a Litestream sidecar;
  - a Postgres instance;
  - a local blue-green switch;
  - a standby host.
- **Related active changes:**
  - `rename-universe-to-command-center`: the migration rides its cutover window.
  - `account-storage-quota`: the quota becomes box disk allocation.
  - `two-dimension-usage-limits`: seats are kept and compute-hours added.
  - `universe-agent-harness`: §4.16 placement, §4.17/D11 export from the box.
  - `served-agent-brain-loop`: the thin loop is its execution substrate.
- **Founder spend approvals:** marked per slice in `tasks.md` (S0, S1, S5, S10).
- **PLAN.md:** Daemon Platform, Providers, Uptime & Alarms, Reference: System
  Shape and Design Decisions are edited in this change. The founder approved the
  direction on 2026-10-01/02.
