# Design: target architecture

*Revision 4, 2026-10-02.* This revision folds in cross-family refute rounds 1–3 (gpt-6-astra, all **ADAPT**; see Appendix R). Round 3 hit the three-round cap: its consistency fixes are folded without a fourth review, and its residual design tensions go to the founder (Appendix R, "Escalated"). It also folds in the on-disk layout agreed with `command-center-cutover` (D8a), including the lead's correction that `soul.md` and `config.yaml` are agent-editable. Raw measurements are in `evidence.md`.

## Context

**What production looks like today** (`origin/main` `55a526ff`, read 2026-10-02):

- **Host.** One droplet in DigitalOcean sfo3: `s-4vcpu-8gb`, Debian 12, kernel 6.1. It reports `/dev/kvm`, `kvm_intel nested=Y` and `vmx`.
- **One daemon container serves everything:** the MCP and app API, the scheduler, the agent turns, the provider jails and the tool jails.
- **One process writes `agent_turns`.** `storage/agent_turn_boot.py:28` pins this invariant, and `tests/test_orphaned_turn_reconcile.py` holds it. Startup reconciliation (`universe_server.py:4369`, `agent_turn_reconcile.py:100`) settles any turn this boot did not create.
- **State is SQLite plus files** on one volume under `/data`. Each universe is a host directory, `/data/<universe>/`, and it mixes the agent's files with hidden platform state: the vault, the run, consent, usage and attention databases, and locks.
- **Isolation is per-call bubblewrap** on the shared kernel: `universe_tools.py` for the four tools, `provider_jail.py` for CLIs. #4245 adds a shared seccomp denylist.
- **Credentials.** HTTP model calls already run credential-blind. The generic connector calls an owned broker process bound to the exact owner, connection and grant (`providers/api_key_http_provider.py:354-362`). CLI launches materialise a per-launch credential snapshot.
- **Concurrency limits are global:** 4 runs per host (`runs.py:5597`) and 4 tool slots (`universe_tools.py:173`).
- **Daemon reads of universe files** go through `universe_files.py`, with O_NOFOLLOW per component and a size bound. #4247 widens its ratchet to the ~350 remaining sites.

**Approved direction.**

- Founder, 2026-10-01, on the sealed box: "approved, go with the sealed box design".
- Founder, 2026-10-02, on the target shape now: "move towards the architecture and dependencies we want later sooner rather than later… do things correct the first time".

**What this design is.** It fixes the final interfaces and where every piece of data lives. Capacity is what scales later.

## Goals / Non-Goals

**Goals:**

1. **One shape for every account and every stage.** Stages change capacity behind fixed interfaces, never code paths ("all accounts, one code path"; PLAN "Phased rollout — explicitly rejected").
2. **The cross-user floor is held by construction:**
   - a separate kernel per command center;
   - a per-box hard disk bound;
   - no daemon access to box contents through host paths;
   - no platform state inside a box;
   - no cross-tenant host uid;
   - every box operation authenticated to its owning account.
3. **Cost scales with work, not with registered users.** Boxes are suspended unless acting; idle users cost storage only.
4. **Uptime:**
   - deploys without request failures;
   - continuous off-region durability for platform state;
   - a fenced standby;
   - a recovery drill that actually runs.
5. **Least-privilege secrets per process.**

**Non-Goals:**

- **More than one cell or box host now.** The seams exist. A second cell (and the cell-move protocol, D10) is built when its capacity trigger fires; that is capacity work, not an interface change.
- **The Postgres vendor.** Self-hosted versus managed is S10's spend decision.
- **MCP tool surface changes.** The seven canonical handles are unchanged.
- **Private-content custody** (PLAN carve-out 2).
- **Serving a Claude subscription server-side** for anyone but its owner (D6).
- **A compute-hour budget.** Today's founder rule is storage + seats. D9 adds metering only, and the budget is a founder decision.

## Architecture

```
               tinyassets.io (Cloudflare: DNS, Worker router, tunnel)
                          | signed cell claim -> cell c0 (only cell today)
+----------------------------- CELL (always on) ------------------------------------+
| FRONTEND  blue | green   MCP + app API, auth; stateless; replaceable per deploy      |
|     |  (local RPC; queues while the owner hands over)                               |
| COMMAND-CENTER OWNERS (a lease + fence per command center) + ONE PLATFORM OWNER     |
|     thin agent loop, agent_turns writer + reconcile, scheduler/triggers/inbox/      |
|     notifications, outbox pump, metering, storage allocator                        |
| CREDENTIAL BROKER (own process; only holder of the vault key; owner/connection/     |
|     grant-bound requests; OAuth refresh)                                            |
| platform state: .platform/ (D8a)  --Litestream--> off-region                        |
+------------------------------|------------------------------------------------------+
                               | BoxProvider RPC (UDS now; mTLS when remote), per-op
                               | auth: cell credential + account + cc + op id + epoch
+------------------- BOX HOST (boxhostd; same machine today) -------------------------+
| one sealed box per command center: Firecracker via jailer (distinct uid) | gVisor    |
| per-box hard disk bound; no NIC; boxd over vsock; awake only while acting           |
| checkpoint manifests bind memory snapshot to disk state; reservation ledger         |
| box backups from quiesced/suspended images --restic, per-account key--> off-region   |
+-------------------------------------------------------------------------------------+
standby cell + standby box host in a second region: connectors stopped, restoring;
promoted only after BOTH primary hosts are fenced.
```

## Decisions

### D1. One sealed box per command center; Firecracker primary, gVisor fallback

- **What a box is.** Every command center gets exactly one box with its own kernel boundary, a hard disk bound and no network interface.
- **What runs in it.** The box holds the command center's user content (D8a). Every tool call and every CLI run on its behalf executes inside it.
- **Agents.** Several agents of one command center share its box. The floor is cross-user, not per agent (memory `the-floor-is-cross-user-only`).
- **Drivers.**
  - **Firecracker with snapshot/restore is primary.** It gives a hardware VM boundary, and its lazy restore means a woken box pays only for the memory it touches (E3).
  - **gVisor (rootful runsc, systrap) is the fallback** behind the same contract. It needs no KVM, starts in ~50 ms, and frees memory in 5 s. Its restore is eager and cannot run rootless (E4).
  - **The driver is box-host configuration, not a code path.** S0 decides which driver production starts with. The contract suite (S4) runs against both drivers.
- **Rejected:**
  - *OpenShell as runtime:* no snapshot, the VM driver is experimental with gateway-wide sizing, and its placeholder model does not fit subscription CLIs (E5).
  - *Rootless Podman:* shared kernel, and limits fail open without delegation.
  - *Per-call bwrap:* isolation rank 5.

### D2. `BoxProvider`: the only way the platform touches a box

```python
class BoxProvider(Protocol):                       # tinyassets/boxes/provider.py
    # binding is separate from waking: bind never contacts the box
    def bind(self, cc: CommandCenterId, *, account: AccountId, turn: TurnId | None) -> BoxHandle
    def committed_generation(self, h: BoxHandle) -> int     # from boxhostd metadata; works while suspended
    def ensure_awake(self, h: BoxHandle, *, reason: WakeReason) -> None
    # execution lifecycle (idempotent by op_id; a lost reply is resolved by status, never by re-running)
    def start_exec(self, h, op_id: OpId, argv: Sequence[str], *, stdin: StreamIn | None = None,
                   env: Mapping[str, str] = {}, cwd: str = "/cc", limits: ExecLimits) -> ExecId
    def stream(self, h, exec_id: ExecId, *, from_offset: int = 0) -> Iterator[ExecEvent]   # stdout/stderr/exit
    def cancel(self, h, exec_id: ExecId) -> None        # kills the process tree in the box
    def exec_status(self, h, op_id: OpId) -> ExecStatus  # running | exited(code) | unknown_after_restore
    # files (paths are box paths; resolved inside the box)
    def read(self, h, path: str, *, offset: int = 0, max_bytes: int) -> FileRead       # bytes + generation
    def read_many(self, h, paths: Sequence[str], *, max_total: int) -> Snapshot        # one generation
    def write(self, h, op_id: OpId, path: str, data: StreamIn, *, max_bytes: int,
              mode: WriteMode, expect_generation: int | None = None) -> FileWrite  # temp + rename in box
    def download(self, h, path: str) -> Iterator[bytes]                                # bounded streaming
    def list(self, h, path: str, *, cursor: str | None = None, limit: int) -> DirPage  # paginated
    def stat(self, h, path: str) -> FileStat | None
    def remove(self, h, op_id: OpId, path: str) -> None
    # whole-box
    def export(self, h, *, profile: ExportProfile) -> Iterator[bytes]   # 'share' (scrubbed) | 'migration' (complete)
    def import_bundle(self, cc, chunks: Iterable[bytes], *, profile: ExportProfile) -> ImportReport
    def usage(self, h) -> BoxUsage          # used bytes, hard bound, generation
    def suspend(self, h) -> None            # checkpoint + stop; idempotent
    def try_fence_idle(self, cc, *, owner_generation: int) -> bool
        # atomically: if the box has no live execution or pending mutation, persist
        # owner_generation as its fence (lower generations refused from now on) and
        # return True; otherwise change nothing and return False. This is the
        # "command center is idle" proof a per-command-center handover moves on (D11).
    def destroy(self, cc, op_id: OpId) -> DestroyReceipt
```

**Authentication of every operation.**
- `boxhostd` authenticates each call with:
  - the cell's credential;
  - the handle's account and command center;
  - the turn, when there is one;
  - the operation id;
  - the box's **placement epoch**.
- It refuses an operation when:
  - the account does not own the command center;
  - the epoch is stale (the box moved or was re-imported);
  - the handle was minted for another command center.
- **A valid handle passed to the wrong turn still fails at the execution boundary.**
- A turn binds its handle at turn start and never looks a box up by name per call.

**Lost replies.**
- `boxd` records each operation's outcome by `op_id` for a retention window.
- A retry with the same `op_id` returns the recorded outcome and never re-runs it.
- An operation in flight across a host crash reports `unknown_after_restore`, and the turn holds instead of re-issuing it. This is the same rule as the turn journal.

**Cache validity without waking** (fixes refute 1).
- `boxd` keeps a monotonic **change generation**. It is bumped by any write through the API, and by inotify watches on the cache-relevant paths (persona files, `skills/`, `prompts/`), whoever wrote them: a CLI, `bash`, or the API.
- At suspend, `boxhostd` persists the last generation. That makes `committed_generation` answerable while the box sleeps.
- A turn with a warm cache at the committed generation calls `bind`, never `ensure_awake`, so it does not wake the box.

**Transport.**
- Control plane to `boxhostd`: a Unix socket today, mTLS when the host is remote. It is the same RPC.
- `boxhostd` to `boxd`: vsock (Firecracker) or host-uds (gVisor). It reconnects after every restore, because vsock connections close on restore (Firecracker snapshot docs).
- Framing: length-prefixed JSON headers plus raw byte frames. `exec` takes argv, never a command string.

**No host-path access.**
- Box images, snapshots and the reservation ledger live under `<boxes>/`, owned by the `boxhostd` uid with mode 0700.
- The daemon runs as a different uid, so it cannot open them.
- A ratchet test fails any daemon-side open of command-center content outside `BoxProvider`. This is the by-construction close of #4244.

### D3. Disk: per-box hard bound for the floor; logical account quota; physical reservation for the host

Three separate concerns. Revision 3 corrects the accounting, the arithmetic and the resize procedure (refute rounds 1 and 2).

**1. Per-box hard bound — the cross-user floor, enforced by construction.**
- Each box's filesystem has a hard size: the ext4 image size on Firecracker, an XFS project quota on gVisor.
- A full box sees `ENOSPC` inside itself. Nothing else is affected.
- New storage is fresh sparse space that reads as zeros.
- The bound starts at `used + headroom`, where headroom is the smaller of 1 GiB and the account's remaining quota.
- **Growth only happens at a supported quiesced boundary:**
  - *Firecracker:* Firecracker's block-update contract requires the device to be unmounted with no guest I/O. So a grow never touches a mounted drive. When `boxd` reports free space below the threshold, the next suspend marks the memory checkpoint invalid and stops the VM. `boxhostd` then grows the unmounted image on the host (`truncate` + `e2fsck -f` + `resize2fs`), and the next wake cold-boots. The cost is one cold boot per grow. Grows are rare, because headroom is 1 GiB.
  - *gVisor:* changing the XFS project quota is an online, supported operation.
  - **Known-size writes and uploads that would exceed the bound are grown first.** The box suspends, grows and cold-boots before the write runs. An unknown-size execution that fills the bound below the account quota gets `ENOSPC`, with a typed note: "box disk growing, retry". It is not replayed (D2). Whether to start each box at the full remaining quota instead, which removes the mid-operation case but weakens host reservation, is escalated to the founder (Appendix R).
- Shrinking happens only by offline compaction (export, then import).

**2. Logical account quota — `account-storage-quota` semantics, unchanged.**
- An account's usage is **logical bytes**, exactly as that change defines them: file sizes, hard links deduplicated, runtime and scratch excluded. It includes the user-attributable platform bytes that change counts.
- `boxd` maintains the box's logical total incrementally from its change tracking, and recomputes it with a full walk at checkpoint. It is never `statfs` block usage.
- At the quota, new user-driven writes are refused visibly with the inline Upgrade link, and bounds stop growing.
- **The logical quota is enforced at admission and measurement, not by the physical bound.** Known-size writes and uploads are admitted against it. Unknown-size work (`bash`) is admitted with no reservation, as `account-storage-quota` already does, and is measured afterwards. Logical size can exceed physical size: a sparse `truncate` is one way. So the logical quota can be overshot until the next measurement, which then refuses new writes. The physical bound alone protects the host and other users. This is a per-account usage limit, never a floor invariant.

**3. Physical host reservation — remaining commitments against free space.**
- `boxhostd` keeps a durable ledger and admits a grow only while this holds:

  `Σ_boxes (bound − physical_used) + Σ in-flight temporaries + floor ≤ host free space`

- *In-flight temporaries* are the worst case of every operation under way: a checkpoint replacement (the memory size), reflink divergence of each in-progress backup copy (the image's bound), and an offline compaction or grow (the image size).
- Each operation reserves its temporaries before it starts and releases them when it finishes. All operations are idempotent by `op_id`.
- **A wake reserves its next checkpoint up front.** It is admitted only if space for its memory checkpoint (the memory ceiling) is reserved at the same time. A box that is awake can therefore always suspend.
- At startup `boxhostd` reconciles the ledger against the images, checkpoints and in-flight operation records on disk, and cleans up partial operations.
- A grow that would breach the floor is denied (the box meets its own `ENOSPC`) and pages.

**Fencing.** A box host acts on a box only while it holds the box's current **placement epoch**, issued by the cell. After reassignment, an old host refuses to start or write it.

### D4. Lifecycle and checkpoints

```
 absent --create--> cold --ensure_awake--> booting --> awake
 suspended --ensure_awake--> restoring --> awake
 awake --(no op in flight for idle_s)--> checkpointing --> suspended
 any --destroy--> absent
 image dirty (host crashed while awake, or a grow happened) --> cold
```

**Idle and timers.**
- `idle_s` defaults to 30 s, configurable up to 60 s.
- A box has no timers or cron. In-box processes freeze at checkpoint and continue on restore.

**Checkpoint protocol, correctly ordered** (refute round 2: pausing first would deadlock, because paused vCPUs cannot run the freeze).
1. `boxhostd` asks `boxd` to quiesce: `sync`, then `fsfreeze` of `/cc`, then acknowledge.
2. Only after the acknowledgement, `boxhostd` pauses the VM.
3. It creates the snapshot.
4. It **merges** the diff into the base with the release's `rebase-snap`.
5. It **publishes the manifest durably**: write it to a temporary file, `fsync`, then rename.
6. It stops the VM.

On restore, the VM resumes, `boxhostd` reconnects vsock, and `boxd` **thaws** `/cc` before any operation is accepted.

If any step fails before the manifest is published, `boxhostd` resumes and thaws, and the box stays awake. A partial snapshot is discarded and its temporaries are released.

**Disk identity in O(1), with no hashing.**
- `boxhostd` keeps a per-image **dirty epoch**. It persists `dirty = true` before every resume or boot, and records the epoch in the manifest when a checkpoint completes.
- A restore is allowed only when the manifest's epoch equals the image's epoch and the image is not dirty.
- Otherwise the box cold-boots and its turns reconcile.

This adds nothing to the measured restore path (E3: 31–51 ms to resume, running at 52–166 ms). S0 and S5 measure end-to-end wake: queue, plus restore, plus reconnect, plus thaw.

**Memory ceiling:** 1 GiB, or 512 MiB for tools-only boxes. It is a ceiling, not a reservation: the balloon and free-page reporting return memory.

**Admission.** A **seat** is one concurrently running agent turn of the account. At host capacity, a wake waits in a FIFO queue that is fair across accounts. It is never refused, and the waiting state is visible.

### D5. Network and credentials: the existing broker, extended; no TLS interception

**No NIC in any box.** Egress is one socket: vsock or host-uds. The egress floor applies unchanged:
- globally routable destinations only;
- no metadata, private or loopback addresses;
- SMTP ports refused;
- a per-box connection cap.

**The credential broker** is the one privileged process holding the vault key. It extends today's broker (`api_key_http_provider.py:354-362`).

**Loop requests** go through `resolve_exact_scoped_proxy` unchanged (`storage/outbound_connections.py:5086-5108`). That checks:
- the authenticated principal;
- an active grant;
- the grant's owner and command center;
- the connection's identity and owner;
- revocation.

The broker then performs the upstream HTTPS call itself, with upstream certificate validation. It streams the response with backpressure and propagates cancellation. Redirects are followed only within the connection's declared hosts.

**The in-box endpoint for API-key CLIs** uses base-URL mode (`ANTHROPIC_BASE_URL`, `OPENAI_BASE_URL`).
- It runs plain HTTP over the box's private channel.
- The principal is **derived from the authenticated box identity**: box → command center → owning account. It is never taken from the caller.
- The CLI's configured connection id is only a *selector*. On **every request** the broker resolves it through the **same** `resolve_exact_scoped_proxy` check, with that derived principal and command center. An absent, revoked or foreign grant is refused.
- The endpoint is therefore never weaker than the loop path (refute round 2). No per-cell CA is needed, and pinned-certificate CLIs are unaffected.

**File-OAuth CLIs** (e.g. Codex login) have a versioned custody protocol (refute round 2).
- **Storage.** The credential is stored with a **generation** in `.platform/cc-<ulid>/.credentials/`.
- **One holder at a time.** A CLI launch takes the credential's **lease**. Only one running CLI holds a given file-OAuth credential at a time; a second launch **waits** and is never refused.
- **Materialising.** The file is materialised into that box with its generation.
- **Copy-back.** `boxd` watches the file with inotify. On every change it **copies back immediately**, conditionally: the write succeeds only if the stored generation still equals the materialised one. An owner's redeposit in the meantime wins, and a late copy-back is discarded.
- **Custody transition.** A copy-back is an *adopted CLI rotation* under the existing custody rule (`credential-vault` "A same-account rotation carries custody forward", `spec.md:427-440`). It renews the accepted source and refuses in-flight receipts, atomically with the byte write, inside the same exclusive vault hold. The new generation is acknowledged back to the materialisation, so the next launch in that box passes its custody check. The lease is recovered at its expiry, and only after the owning box is confirmed stopped. S6 specifies the full transaction.
- **Crash recovery.** If the host is lost after a rotation but before copy-back, the stored generation is stale. The next refresh may then be refused. That is reported as **needing a new sign-in** (existing sign-in-failure semantics), never as an outage. Immediate copy-back keeps the window to seconds.
- This is the one sanctioned case of a real credential inside a box process: scoped to its owner's box, its run and its lease.

**Owner-generation fencing of effects: an acknowledged barrier.** Broker requests and box operations carry the execution owner's lease generation (D11). Before a new owner at generation G becomes active, it runs a **fencing barrier**. It sends G to every effect executor (the broker, and every `boxhostd`), and waits for each to acknowledge that it has:
- persisted G durably;
- cancelled every execution and closed every upstream or egress stream opened under an older generation;
- begun refusing any operation below G.

An executor that cannot acknowledge blocks activation, and the new owner pages. An executor that restarts reloads its persisted fence before serving. "Highest seen" alone is not the fence; the acknowledged barrier is.

### D6. A thin vendor-neutral loop; CLI in a box only where the credential needs it

**The loop:**
- It runs in the **execution owner** (D11), as asyncio tasks.
- It speaks the standard model protocols through the broker (D5).
- It holds a waiting turn as a coroutine plus a stream plus its context buffer, estimated at ~1 MB; S7 measures it.
- It never executes model output. It forwards each tool call over the turn's bound handle with an `op_id`.

**Effects.** Box memory per awake agent drops from 338–525 MiB (CLI in box) to the tools-only footprint, estimated at 80–130 MiB, and no model credential is in the box (Anthropic's Managed Agents split: p50 TTFT −60%).

**CLI in a box stays for:**
- command adapters (any binary);
- file-OAuth CLIs;
- the Claude subscription CLI, behind the **owner-scoped** `claude_subscription_serving` setting. It defaults off, only the owner's own command center may turn it on, and that is the founder's TOS decision (memory `anthropic-forbids-third-party-subscription-oauth`).

One CLI process never serves two accounts.

**The turn journal keeps its single-writer invariant.** Only the execution owner writes `agent_turns`, and reconciliation runs only after it acquires the lease (D11). After a failover or host crash, an interrupted turn reconciles into a held state. Its unknown-outcome operations stay unknown (D2), and nothing replays.

### D7. The control plane is the only always-on layer

- **Under the execution owner's lease:** the scheduler, the trigger table, the inbox, notifications, the outbox pump, metering and the storage allocator.
- **Coalescing:** one pending run per trigger.
- **Engagement-decayed proactive cadence:** 4/day while engaged, 1/day after a week, weekly after a month, reset on the next interaction. That puts the dormant duty floor at ~0.1%.
- A box is woken only by the control plane.
- The platform-visible record per command center lives in `.platform/`: schedule, routing, unread and notification metadata, quota counters and the activity line. Listing, scheduling and notifying never wake a box.

### D8. Data placement

| Data | Home | Store | Backup |
|---|---|---|---|
| Command center user content | **inside the box** (D8a dir 1 becomes the disk image) | files | restic from quiesced/suspended images only (D11), encrypted under the command center's **DEK** (D12), off-region |
| Box memory state | box host | Firecracker checkpoint (merged) + manifest | not backed up; cold boot rebuilds |
| Vault + file-OAuth CLI credentials | `.platform/cc-<ulid>/` | encrypted under the command center's DEK; key access only in the broker (S6 migrates today's plaintext vault) | Litestream/restic, encrypted |
| Per-command-center platform state (D8a dir 2) | `.platform/cc-<ulid>/` | SQLite + files | **Litestream v0.5** off-region (RPO ~1 s); restic for files |
| Per-account platform state (D8a dir 3) | `.platform/accounts/<id>/` | SQLite | Litestream |
| Catalog, ledger, inbox, market | shared | **Postgres** (PLAN 2026-07-25), self-hosted as a container on our cloud server, never the founder's PC (founder, 2026-10-02) | pgBackRest/dumps off-region |
| Commons (OKF bundle) | shared | files (canonical) + rebuildable index | restic |
| Identity map (subject → user → home cell, ownership generation) | shared | Postgres | as Postgres |
| Root databases (`.tinyassets.db`, `.runs.db`, …) | cell root | SQLite (renamed by the cutover) | Litestream |

**Rules.**
- **The platform never trusts box content.** It reads it only through `BoxProvider`, as untrusted input (memory `daemon-reads-universe-files-as-untrusted`).
- **SQLite ≥3.51.3 in every image**, pinned before Litestream is turned on.
- **Each outbox sits beside its cause** (refute 5). An effect bound for Postgres is written to an `outbox` table **in the same SQLite database, and the same transaction, as its cause**. The pump delivers at least once. The Postgres consumer applies idempotently, deduplicating on `(origin store, outbox id)` with a unique constraint. "Exactly once" is not claimed across an asynchronous failover: an effect whose acknowledgement was lost reconciles into an unknown/held state.

**Hot-path cache.**
- Persona grounding, the skills index and prompts are read with `read_many` at one generation and cached by `(cc, generation)`.
- A turn compares the cache with `committed_generation`, which needs no wake (D2).
- The activity line is platform state.

**The #4247 ratchet maps directly.** `universe_files.py` becomes the **local driver** of `BoxProvider.read/read_many/list/stat`. Its ratchet becomes "no daemon read of command-center content outside `BoxProvider`". The ~350 call sites move once (S3), and switching the driver to a box (S11) changes nothing at those sites.

### D8a. Target on-disk layout (agreed with `command-center-cutover`, 2026-10-02)

*This section is shared with `openspec/changes/command-center-cutover` (PR #4262, design E6), and both changes cite it. The cutover moves data straight into this layout, so the storage migration runs **once**. Later, S11 turns directory 1 into a disk image: an image build, not a second migration. If the cutover's inventory meets an entry it cannot classify into one of the four rows, it stops; nothing is guessed into a box.*

**Rule:**
- anything the daemon **trusts as policy or authority** is platform state;
- anything the person or their agent may write is **user content**, which the daemon reads as untrusted.

The harness is self-improving: the agent edits its own persona, instructions, config and skills, with versioning (founder-approved). So those files are user content.

```
data_dir()/
  cc-<ulid>/                      1. USER CONTENT = the future box volume (box path /cc)
  .platform/
    cc-<ulid>/                    2. per-command-center platform state (daemon-only)
    accounts/<account_id>/        3. per-account platform state (daemon-only)
  .tinyassets.db, .runs.db, ...   4. root databases (unchanged location, renamed schema)
```

| # | Directory | Holds | Never holds |
|---|---|---|---|
| 1 | `cc-<ulid>/` | **Persona and config, agent-editable:** `soul.md`, `soul_versions/`, `config.yaml`. **Agent-owned brain files:** `identity.md`, `founder.md`, `origin.md`, `body.md`, `orgchart.md`, `projects.md`, `goals.md`, `index.md`, `log.md`, `voice.md`, `AGENTS.md`. **Harness dirs:** `skills/`, `prompts/`, `extensions/`, `workflows/`, `bin/`, `notes/`, `wiki/`, plus `notes.json`. **Also:** upload **bytes** (verbatim, Hard Rule 9); files a run or the agent wrote; permanent workspace generations; anything else the agent creates | policy the daemon trusts |
| 2 | `.platform/cc-<ulid>/` | **Trusted policy:** `soul.edit.md` (which persona edits are allowed), `dispatcher_config.yaml` (paid-bid acceptance). **Credentials:** the credential vault, file-OAuth CLI credentials (`.credentials/`, versioned, D5). **Per-home DBs:** runs, consent, usage, attention, conversation custody and session journals, checkpoints, `outbound.db`, `knowledge.db`, `story.db`, `lancedb`. **Worker-supervisor state.** **Records:** rules, auto-review, activity, pending effects, proposals, import quarantine, the browser profile. **Identity and coordination:** the id marker (`.command_center_id`), lease/seat/slot/lock/stamp state, the egress socket (formerly `.universe-sidecars/<id>/`). **Upload custody** records | agent-writable content |
| 3 | `.platform/accounts/<account_id>/` | The storage ledger, the compute meter, seat state | — |
| 4 | root DBs | Unchanged except for the rename. Catalog, ledger, inbox and market move to Postgres (S10). Per-account rows stay in these shared stores, served through **account-scoped views**. I5 `store_for(account)` is defined so that either an account-scoped view over a shared store or a per-account file is a valid implementation. Extraction is not required. A cell move exports the account's rows in the `migration` profile | — |

**There are no projections into the box.** Nothing platform-owned is written into a box image, so checkpoint identity (D4) is never disturbed.

**Tools have exactly two execution destinations** (refute round 3).
1. **Box tools** run in the box: the four tools, and every command adapter or CLI.
2. **Owner-door read tools** run in the control plane. They are read-only, account-bound, and serve platform history such as conversation custody and activity.

A CLI inside a box reaches the owner-door read tools through a read-only route on the box's broker endpoint, authenticated by the box identity. Nothing else changes in the tool contract.

**The config split is a hard prerequisite** (lead, 2026-10-02). Nothing may make `config.yaml` agent-writable before the split lands. That covers:
- the box (S11);
- the self-improving harness's config editing;
- any provider adapter that ships file tools under the read-write default view.

The split ships as a standalone PR right after #4273, outside the cutover's freeze window. Today `config.yaml` is not agent-writable: the tool jail binds it read-only, Claude has no file tools, and Codex gets a read-only workspace. So this ordering only has to hold until the split lands.

**Field-level authority in `config.yaml`.** The file stays agent-editable, but it still carries server-owned authority today: `engine_assignment_state`, `engine_assignment_generation` and `provider_authority_bindings` (`config.py:60-66`). It also carries `allowed_providers`, the routing ceiling the router enforces (`providers/router.py:158-177`).
- The split moves these fields to `.platform/cc-<ulid>/assignment.json`. Readers take authority only from that file; any of these fields left in `config.yaml` are ignored and logged.
- Consumers read authority only from the platform copy.
- What remains in `config.yaml` is the agent's own preference data, read as untrusted.

**Cache validity** covers both kinds of input. The grounding cache key is `(box committed generation, platform revision of the platform inputs grounding reads)`, where those platform inputs are `soul.edit.md` policy and rules.

**Resolvers.** One module owns the three paths: `command_center_dir(id)`, `platform_dir(id)` (I2) and `account_platform_dir(account_id)`. A test fails on any hand-built path.

### D9. Usage limits: storage + seats (unchanged); compute metering; budget is the founder's call

- **The founder rule stands:** account limits are storage GiB and concurrent seats, and work waits and is never refused (memory `usage-limits-are-storage-and-seats`).
- **This change ships metering only.** Box awake seconds, weighted by memory ceiling, give an operational measurement of cost per account.
- **Not built until the founder decides:** a monthly priority compute-hour budget with a spare-capacity lane. Box-cost research §F.3 proposes it, and it would change the two-dimension rule.
- **Admission fairness, stated measurably** (refute 7). Host-capacity admission is **FIFO across accounts**. One account can occupy at most its seats' worth of concurrently awake boxes. A run within its seats is admitted no later than any later-arriving run from another account. Admission bounds concurrency, not the CPU or I/O a running box consumes. Per-box CPU and IO weights (cgroup `cpu.weight`/`io.weight` on the VMM, or gVisor's equivalents) bound noisy neighbours, measured in S5's co-tenancy test.
- **Host admission replaces** `TINYASSETS_RUN_MAX_CONCURRENT` and `_HOST_SLOTS`.

### D10. Cells: the seam now, one cell, the move protocol specified

- **A cell** is one control plane plus its box host capacity. All of a user's command centers live in the user's `home_cell`.
- **The seam, built now in S10:**
  - `home_cell` on the account, and a signed cell claim minted at sign-in;
  - the edge routes by the claim, with one target today;
  - an **ownership generation** in the identity map, so a request or claim with an older generation is refused;
  - **ingress dedup** by idempotency key;
  - the beside-the-cause outbox (D8).
- **Export profiles (refute 5).**
  - `share`: harness §4.17. Scrubbed, credentials excluded, owner-selected memory.
  - `migration`: complete private state, re-encrypted credential custody, pending work, dedup history and ownership metadata.
  - They are one container format with distinct profiles. Only `share` is user-facing.
- **The move protocol (I13).** It is specified now and built when the second cell's trigger fires:
  1. close the account's admission, **and stop every non-turn writer for it**: credential refresh and copy-back, owner edits through the API, scheduler and trigger fires, and outbox pumping;
  2. drain and fence its turns;
  3. suspend its boxes (consistent checkpoints);
  4. snapshot its SQLite stores with the backup API, at one admission-closed point;
  5. transfer in the `migration` profile and import;
  6. **flip `home_cell` and bump the generation atomically** in the identity map;
  7. reopen.

  Writes cannot land between the snapshot and the flip, because admission is closed.

### D11. Uptime: per-command-center execution owners behind replaceable frontends

*Amended 2026-10-02 (lead decision on `execution-owner-lease` Q1): ownership is **per command center**, plus one platform lease. It was a single global owner. With a single owner, a handover made every new request on the platform wait behind the slowest in-flight turn. That is one user's turn affecting another user, which is against the floor. Change of record: `openspec/changes/execution-owner-lease` (S8a, turn-handover).*

**The control plane splits into two roles.**
- **Frontends:** MCP and app API, auth, client streaming.
  - They hold no turn ownership and deploy blue-green.
  - An old frontend keeps its open client connections until those streams end, so a frontend deploy interrupts nothing.
  - They route each turn start and cancel to the **current owner of that command center**, through a small map from command center to owner and lease generation. They **queue** only that command center's requests while it changes hands.
- **Command-center owners:** the loop and the `agent_turns` writer and reconciliation, **for one command center**.
  - Each command center has its own **lease with a monotonic generation**, and its own fence.
  - One process may hold the leases of many command centers.
- **Proof of idleness is the box host's.** A command center moves at an idle instant that `BoxProvider.try_fence_idle` proves atomically (D2). While execution still shares one process across command centers, per-command-center moves are deferred until S4; owner deploys meanwhile use the whole-process wait (`execution-owner-lease` phase 1).
- **The platform owner:** exactly one, under **one platform lease** (`control-plane-scheduler`, S8b). It runs the cross-command-center duties: scheduler, triggers, outbox pump, metering aggregation, the storage allocator.
- **State that spans command centers lives in a shared store, never in process memory.** During a handover, one account's command centers can be split across two owner processes. So two kinds of state need one source of truth:
  - per-account seats (S9), in a shared store under `BEGIN IMMEDIATE`;
  - host admission, in boxhostd's single queue (S4).

**Turn ownership follows the command center's lease generation, not the boot** (refute round 2). This replaces the boot rule in `agent_turn_boot.py:101-106`:
- Every `agent_turns` row records `(command_center, owner_generation)`.
- Reconciliation by a new owner of command center X at generation G settles only X's rows with `owner_generation < G`. It runs only **after** acquiring X's lease, which means X's previous lease was released, or expired with its owner fenced.
- A successor that started in standby therefore cannot mistake the old owner's live rows for its own, nor treat them as alive.
- `tests/test_orphaned_turn_reconcile.py` gains the standby-start case.

**Atomic fencing.**
- *SQLite:* every database a command-center owner writes holds a **fence row for that command center** (the platform owner has its own), and the barrier advances it with a write. Every owner-side mutation runs in a `BEGIN IMMEDIATE` transaction that takes the write lock, reads the fence row, and commits only if the fence still equals its own generation. Advancing the fence and the mutation therefore serialise, and there is no stale read of a WAL snapshot. A stalled owner commits nothing.
- *Restore-safe incarnation.* After any restore or promotion, each command center's new generation is **greater than every generation found for it in any recovered store**: lease, fence rows, and `agent_turns.owner_generation`. It reconciles before serving. A recovered turn store that is ahead of the recovered lease store therefore cannot hide orphaned turns.
- *Downstream:* broker requests and box operations carry `(command_center, generation)`, and are refused below that command center's fenced generation (D5).
- An owner handover does not change box placement epochs. The two fences are independent.

**Owner handover, per command center: a turn runs until it finishes.** This follows the founder law "a turn runs until finished", and S8a `execution-owner-lease`, refuted three rounds.
1. **A command center's key moves only at an idle instant.** The old owner releases that command center's lease in the same transaction that sees it idle. Admission is never closed on a busy key, because that would make the command center's own new requests wait behind its long turn.
2. A command center with nothing in flight moves **immediately**.
3. **There is no automatic drain bound.** A straggler keeps its **old owner process** alive until its turn finishes. The cost is that process's memory. It holds **no other command center or user** hostage, because their keys have already moved.
4. The new owner acquires each released lease at generation+1. It runs the **acknowledged fencing barrier for that command center** (D5): the broker and boxhostd persist the generation and refuse anything lower. Then it reconciles and serves.
5. **Only an explicit operator `--force` cuts work.** It stops the old owner. Its turns reconcile into held states (`agent_turn_reconcile.py:30,66`) with an "interrupted, resume?" notice. They are never silently settled, and never replayed.
6. **New requests never fail.**
7. **Lingering owners are visible and bounded in number, not in time** (lead, 2026-10-02):
   - an **alarm** fires when an old owner process outlives a threshold (default 2 h);
   - **per command center, at most two owner generations coexist.** A further deploy **waits** for that command center (it stays on its current owner) rather than spawning a third;
   - the user sees **"update pending for your command center"** until its key moves.

The platform lease hands over separately, under the same rules. It drains no turns. Its duties pause briefly and resume under the new holder's generation.

The metrics are failed requests (target 0) and interrupted turns per owner handover (measured). Frontend-only deploys interrupt nothing. Owner deploys happen only when owner code changes.

**Schema-changing cutovers are declared maintenance windows** (rename D7/C4b, and S11). The cutover's exclusion protocol extends to both frontends, the owner, `boxhostd`, Litestream and the backup workers.

**Warm standby (second region).**
- It has a standby cell host and box host. Their connectors are stopped. Litestream restores continuously, and restic restores box disks on first wake.
- **Promotion fences every primary execution host** before starting anything: the cell host, plus the box host if it is separate. Each host is fenced through **its own provider's** API with a CI-held credential (DigitalOcean for droplets; OVH's API for an OVH box host, delivered in S5), and blocked from auto-restart.
- If fencing is unconfirmed, promotion stops and pages. Failback is manual.
- Promotion also restores the broker's vault key from escrow (S1) and Postgres (PITR).
- **Recovery points:**
  - ~1 s for platform **SQLite** state (Litestream);
  - for platform **files** (file-OAuth credentials, `soul.edit.md`, `dispatcher_config.yaml`, the browser profile): the restic interval, by default ≤15 min and on change for credentials;
  - the last box backup for box files;
  - box memory is lost (cold boot).
- **Recovery times:** API ≤5 min after detection. A given command center's restore is size-dependent and measured in S5.

**Consistent box backups.** They are taken only from suspended images, or from a `boxd` freeze plus a reflink copy (XFS reflink). restic reads the immutable copy. The reflink copy's divergence is reserved in D3.

**DR drill.**
- Weekly and scheduled.
- It restores into a fresh host from off-region copies only, using a fresh template environment with the pinned image. It never copies the primary's secrets.
- It checks that the canary is green and that sampled box checksums match. A failure pages.

### D12. Deletion, export, migration

**Deletion keeps today's semantics** (`account_deletion.py:199`).
- The schema-derived deletion set decides what goes.
- Command centers that **survive** the person keep their box, under the opaque-fingerprint owner.

**Keys: one per resource, so a surviving box keeps its backups** (refute round 2).
- Each command center's backups and encrypted platform files use a **per-command-center data key (DEK)**. The DEK is wrapped by its owning account's **key-encryption key (KEK)**.
- **Deletion order:**
  1. Re-wrap each surviving command center's DEK under its new custody (the fingerprint owner's escrowed KEK).
  2. Take and **verify a backup** under that custody.
  3. Destroy the deleted command centers' DEKs, their boxes, checkpoints and backup sets.
  4. Finally destroy the account KEK.
- Platform replicas receive the deletions through replication.
- Every restore checks a **tombstone list**, so an old backup cannot resurrect a deleted command center.
- Deletion completes when every destroy receipt, every re-wrap verification and the KEK destruction are recorded.

**Export** (harness D11 / §4.17): `BoxProvider.export(profile="share")`, plus the platform records the manifest selects.

**Migration from shared `/data/<universe>`.**
- **The cutover (#4262)** moves data straight into D8a. That delivers S2's moves.
- **S11** builds box images from `cc-<ulid>/`:
  - copy regular files only, refusing links and special files;
  - verify counts and hashes through `BoxProvider`;
  - flip `box_epoch`;
  - keep the source read-only until the drill passes.
- **Backup:** a volume snapshot plus restic off-region.
- **Rollback:** redeploy the previous image with the snapshot. The C4a guard refuses mixed state.

## Interfaces fixed by this change

| # | Interface | Module (target) | Local implementation now | Scaled implementation |
|---|---|---|---|---|
| I1 | `BoxProvider` (D2) | `tinyassets/boxes/provider.py` | `boxhostd` on the same host (UDS) | remote box hosts (mTLS) |
| I2 | `PlatformStatePaths` + `command_center_dir` + `account_platform_dir` (D8a) | `tinyassets/platform_state.py` | `.platform/…`, `cc-<ulid>/` | same, per cell |
| I3 | Thin loop in the execution owner (D6, D11) | `tinyassets/agent_loop/` | one owner process | one per cell |
| I4 | `home_cell` + signed claim (D10) | `tinyassets/cells.py` | constant `c0` | edge-routed cells |
| I5 | `store_for(account)` | storage factories | account-scoped views over shared root stores, or per-account files (both valid, D8a) | same, per cell |
| I6 | Export profiles `share` / `migration` (D10, D12) | `tinyassets/export_bundle/` | share from box | migration for cell moves |
| I7 | `TransactionalStore` | `tinyassets/txstore/` | one Postgres | Postgres HA |
| I8 | Owner lease (monotonic generation; `owner_generation` on turn rows; transactional re-check) + scheduler/triggers (D7, D11) | `tinyassets/scheduler/` | one owner | per cell |
| I9 | Release state + health per role/origin | `scripts/deployed_sha.py` | per colour + owner | per cell |
| I10 | Ingress dedup + cursors | request-idempotency store | local SQLite | per cell |
| I11 | Ownership generation | identity map (Postgres) | generation 1 | bumped on move |
| I12 | Beside-the-cause outbox + idempotent consumer | each SQLite store + Postgres | in-process pump | same |
| I13 | Cell move protocol (D10) | `tinyassets/cells.py` | specified, not built | built at the second-cell trigger |
| I14 | Credential broker (D5) | `tinyassets/broker/` (extends today's owned broker) | own process | per cell |
| I15 | Box placement epoch + reservation ledger + dirty epoch (D3, D4) | `boxhostd` | one host | N hosts |
| I16 | Per-command-center DEK wrapped by account KEK (D12) | broker key service | one cell | per cell |

## Spec reconciliation owed (existing requirements the slices modify)

The umbrella's delta specs sync only when this change archives, after S11. Each slice that changes as-built behaviour MODIFIES or REMOVES the existing requirement **in its own change**, so the main specs never contradict as-built:

| Existing requirement | Owner slice | Change |
|---|---|---|
| `uptime-and-alarms` "Nightly Two-Tier Backup And Manual Fresh-Host Data-Restore Drill" | S1 (platform state), S5 (boxes) | MODIFIED: scheduled drill from off-region copies, Litestream tier, box sample. The no-secret-transfer and archive-validation clauses are kept |
| `credential-vault` "Per-Universe Provider Auth Env Overlay Without Cross-Universe Leakage" | S6, S11 | MODIFIED: API-key CLIs get a broker base URL instead of the key; file-OAuth materialisation moves into the box. REMOVED at S11 for the bwrap path |
| `credential-vault` "One Shared Single-Flight Credential Refresh" | S6 | MODIFIED: prelaunch refresh of deposited subscription documents moves into the broker; file-OAuth CLIs use the D5 lease + generation + conditional copy-back instead |
| `credential-vault` "Per-Universe Typed Credential Store" | cutover #4262 / S2 | MODIFIED: location `.platform/cc-<ulid>/` instead of inside the universe directory |
| `credential-vault` "Subscription-Home Materialization For CLI Writers" | S6, S11 | MODIFIED: homes live in platform state, materialised into the box per launch under the credential lease |
| `credential-vault` "As-Built Storage Protection Is Filesystem Permissions Only" | S6 | MODIFIED: encrypted under per-command-center DEKs; S6 migrates the existing plaintext vault |
| `account-deletion` (main spec) | S11 | MODIFIED: destroy receipts, crypto-shred, tombstones |
| `account-storage-quota` (change in flight) | S4/S5 | adds the per-box bound and physical reservation (D3) beside its logical quota |
| `universe-seats` / `two-dimension-usage-limits` | S9 | host admission replaces the global pool; FIFO fairness |

## Slice plan

**How slices ship.** Each slice is delivered as one or more delivery changes. Each change has ≤12 tasks, one owner and one PR, with proposal and design first where it touches storage, authority, migration or money. A slice's checkbox in `tasks.md` ticks when **all** of its changes have landed, deployed, been live-verified and archived. Slices marked **(multi)** are expected to need more than one change.

```
S0 ──────────────────────────┐
S1 (platform durability) ────┼─────────────────────────────┐
cutover #4262 / S2 ──> S3 ──> S4 ──┬──> S5 ──┐              │
                                   ├──> S6 ──┼──> S7 ──┐    │
S8 (owner/frontend split) ─────────┼─────────┘         ├──> S11
                                   └──> S9 <── S5      │    │
S10 (Postgres + seam) ─────────────────────────────────┘    │
S0 ──> S5;  S1 + S5 ──> box-inclusive drill;  S9 ──> S11 ───┘
```

### S0. DigitalOcean nested-KVM validation — **founder spend** (staging droplet ~$0.14)

- **Goal:** decide Firecracker on DigitalOcean nested KVM versus a bare-metal box host. Production is not touched.
- **Setup:** a short-lived `s-4vcpu-8gb` droplet in sfo3 for ~2 h, built by `deploy/hetzner-bootstrap.sh` and destroyed after. A quiet-window production benchmark is the alternative, and needs explicit founder approval.
- **Tasks:**
  1. Move the spike scripts into `scripts/box_bench/` with their versions pinned (`evidence.md`).
  2. Run Firecracker + jailer as its own uid.
  3. Measure cold boot and restore p50/p95, exec RPC p50/p95, and checkpoint time and size.
  4. Measure idle CPU and steal for 10 and 30 awake boxes, plus a 30-box restore storm.
  5. Run the same set on gVisor.
  6. Record the raw results.
  7. Destroy the droplet.
  8. Write the decision.
- **Decision rule:**
  - **Firecracker on DO** if restore p95 ≤ 500 ms, exec p95 ≤ 50 ms, awake-idle CPU ≤ 5% of a core per box, and canary p95 is unchanged with 30 awake boxes.
  - **Otherwise a bare-metal box host:** OVH RISE-S Hillsboro, $77/mo, **founder spend**. gVisor on the droplet serves until it exists.

### S1. Platform-state durability and the warm standby — **founder spend** (slim: off-region bucket only, for now) — (multi)

**Slim-spend rule (lead, 2026-10-02).**
- The standby droplet and the Cloudflare LB are **deferred until there are paying users**.
- Off-region storage is **DO Spaces nyc3**.
- S1b below stays the specified target and is built when that trigger fires.

- **S1a durability:**
  1. Pin SQLite ≥3.51.3 and assert it at startup.
  2. Litestream v0.5 for platform state.
     - **Deferred** until `command-center-cutover` (#4262) lands `.platform/` (D8a). Then it replicates `.platform/` only.
     - **Interim, shipped:** the brain tier goes off-region hourly, an RPO of about 1 hour.
     - **Constraints:** `docs/design-notes/2026-10-02-litestream-platform-state.md` §Refute outcome. Restores must replay the deletion ledger (`docs/concerns/2026-10-02-restores-resurrect-deleted-accounts.md`).
  3. Move `BACKUP_DEST` off-region. The GitHub copy keeps the brain tier only.
  4. Restore test, including point in time.
  5. Schedule `dr-drill.yml` weekly, restoring from the off-region copy (MODIFIED requirement, see reconciliation).
  6. Alarms for replication and backup lag.
  7. Define the vault-key escrow.
- **S1b standby:**
  1. Provision the standby through bootstrap, with connectors stopped.
  2. Continuous restore on the standby.
  3. Fence-every-primary-host promotion for DigitalOcean hosts, using a scoped DO token in CI. A non-DO box host's fencing is delivered with that host in S5, and the promotion drill re-runs then.
  4. A Cloudflare LB health-check detector.
  5. A promotion drill.
  6. The runbook.
- **Acceptance:** the drill is green from off-region; promotion reaches API-green ≤5 min after detection; RPO is measured.
- The box-inclusive drill lands in S5.

### S2. Platform state out of the universe directory (§4.16, #4258)

- **If the cutover lands first, it delivers S2's moves.** The cutover (#4262) moves data straight into D8a. S2 then shrinks to tasks 4, 5 and 7.
- **Tasks:**
  1. The `PlatformStatePaths` resolver.
  2. Move the D8a dir-2 items through it.
  3. A locked, verified startup migration with backup.
  4. Refuse to open a platform store found inside user content (#4258 attack 3).
  5. Prove no jail mounts `.platform/`, in the Linux oracle.
  6. Fold in `.universe-sidecars`.
  7. Update the deletion set.
  8. Spec sync.
- **Acceptance:** the three #4258 reproductions fail in the oracle.

### S3. One accessor for command-center content, plus the hot-path cache — (multi, one change per module batch)

- **Tasks:**
  1. The `BoxProvider` read-side signatures (`read`, `read_many`, `list` with cursor, `stat`) as a local driver over `universe_files.py`.
  2. Module-batched migrations of the ~350 sites, each its own change.
  3. Ratchet the count to zero.
  4. The generation cache.
  5. A latency check.
- **Acceptance:** the ratchet holds at 0; no turn-latency regression.

### S4. `BoxProvider` complete, `boxd`, gVisor driver, allocator and admission — (multi)

- **Tasks:**
  1. `boxhostd` (own uid, RPC, per-operation authentication, placement epochs, op-id outcome log).
  2. `boxd` (execution lifecycle, files, inotify generation, usage).
  3. The gVisor driver: rootful runsc, a distinct uid/userns per box, `--network=none`, the egress socket over host-uds, and an XFS-project-quota hard bound.
  4. The driver-agnostic reservation ledger and the grow protocol (D3).
  5. FIFO host admission (D9).
  6. The contract suite. It covers path escape, links resolving in-box only, a stale or foreign handle being refused, a lost-reply retry not re-running, cancel killing the tree, and `ENOSPC` at the bound with no effect on a neighbour.
  7. Re-point `universe_tools.RUNNER` and `provider_jail.confine_launch` at `BoxProvider`.
  8. Spec sync.
- **Acceptance:** the contract suite is green on gVisor in CI; a tool loop runs through a box on staging.

### S5. Firecracker driver, host build, box backups, box-host operations — **founder spend if S0 picks bare metal** — (multi)

- **Depends on:** S0, S4, and S6 (key service) for the backup tasks.
- **Tasks:**
  1. Firecracker + jailer at a pinned version, one uid per VMM, cgroup CPU/IO weights.
  2. The base rootfs plus a per-box image; images are fresh sparse files.
  3. The ordered checkpoint protocol (quiesce → pause → snapshot → merge → durable manifest), the dirty epoch, thaw on restore, and partial-failure recovery (D4).
  4. Idle suspend.
  5. Balloon and free-page reporting.
  6. vsock `boxd` with reconnect.
  7. The quiesced grow procedure: invalidate the checkpoint, grow the unmounted image, cold boot (D3).
  8. The box host on Debian 13 with XFS reflink.
  9. Consistent restic backups encrypted under the command center's DEK. This depends on S6's key service.
  10. The box-inclusive DR drill.
  11. Box-host operations:
      - metrics: awake boxes, admission wait, reservation headroom, restore p95;
      - a partition and reconnect path;
      - a compromised-host quarantine runbook (fence, rotate the cell credential, re-image);
      - checkpoint compatibility across VMM upgrades: cold boot when versions differ.
  12. A co-tenancy test: one box saturating CPU, memory, disk and egress while a neighbour completes a turn.
  12b. If S0 chose a non-DO box host: fencing through that provider's API, with the S1b promotion drill re-run.
  13. Spec sync.
- **Acceptance:** the contract suite is green on both drivers; restore p95 meets S0's rule; the co-tenancy test passes.

### S6. Credential broker extension and per-process secret scope

- Coordinated with the secret-scope lane. **Depends on:** S4 and S1 (key escrow).
- **Tasks:**
  1. The broker as its own process with key access; per-command-center DEKs wrapped by account KEKs; escrow (S1); migrate today's plaintext vault.
  2. In-box broker endpoints for API-key CLIs. The principal is derived from the box identity, and every request goes through `resolve_exact_scoped_proxy`.
  3. The file-OAuth credential lease, generations, inotify conditional copy-back, and the crash path.
  4. The owner-scoped `claude_subscription_serving` gate, default off.
  5. Credential rotation: per-cell broker credentials, box-socket identities, revoke.
  6. Keep `DO_API_TOKEN`, `STRIPE_SECRET_KEY`, `WORKOS_API_KEY` and `CLOUDFLARE_TUNNEL_TOKEN` out of every process handling tenant input; the broker's exception is narrow and stated.
  7. A `/proc/<pid>/environ` names test per process.
  8. MODIFIED vault requirements (see reconciliation).
- **Acceptance:** no real credential in the loop or in any box process, **except** a file-OAuth CLI's own token inside its owner's box during its run (asserted). API-key CLIs reach their API through the broker endpoint.

### S7. The thin loop

- **Depends on:** S4, S6, S8.
- **Tasks:**
  1. An asyncio loop through the broker in the execution owner.
  2. Bind the handle at turn start, then forward tool calls by `op_id` over `start_exec`/`stream`/`cancel`.
  3. The per-round journal (single writer kept).
  4. Streaming to clients via the frontend.
  5. CLI-in-box for command adapters and file-OAuth CLIs.
  6. Memory per waiting turn: 500 concurrent against a mock SSE server.
  7. A live rendered conversation (`ui-test`).
  8. Retire the per-turn provider jail for HTTP connections.
  9. Spec sync.
- **Acceptance:** live proof; memory measured; cancellation and reconcile work.

### S8. Command-center owners + platform owner + frontends, the leases, the always-on duties, deploys — (multi)

- **Delivered as two changes:**
  - **S8a** `execution-owner-lease` (turn-handover): per-command-center leases, fences, reconcile, handover and barrier.
  - **S8b** `control-plane-scheduler` (cp-scheduler): the platform lease, scheduler, triggers, outbox, metering, coalescing and cadence decay.
  Their specs are the source of truth for these tasks; this list is the umbrella view.
- **This must land before any second writer exists.** It replaces the "independent blue-green" plan that refute 6 broke.
- **Tasks:**
  1. A lease per command center with a monotonic generation, plus one platform lease. Owner-store mutations re-check their command center's fence transactionally, and broker and box operations carry `(command_center, generation)`.
  2. `(command_center, owner_generation)` on `agent_turns` replaces the boot rule. Reconciliation settles only that command center's older generations, after its lease is acquired. Add the standby-start and restore-order cases to `test_orphaned_turn_reconcile.py`.
  3. Frontend routing by command center to its current owner, with per-command-center queueing during a handover.
  4. The scheduler, triggers, outbox pump and metering run under the platform lease. Per-account seats and host admission live in shared stores.
  5. Coalescing and cadence decay.
  6. No in-box timers (asserted).
  7. Blue/green frontends behind the local switch.
  8. `deploy-prod.yml`: frontends blue-green, owner handover.
  9. The maintenance-exception protocol for schema cutovers.
  10. A request-level error probe during scripted deploys.
  11. Spec sync.
- **Acceptance:** a scripted deploy loop shows 0 failed requests, no duplicate effects and no live turn settled. Interrupted turns per owner handover are measured and reported.

### S9. Admission and metering

- **Founder decision** before any compute budget exists.
- **Tasks:**
  1. Meter box awake seconds weighted by memory ceiling.
  2. Seat and FIFO host admission replacing `TINYASSETS_RUN_MAX_CONCURRENT` and `_HOST_SLOTS`.
  3. A visible waiting state.
  4. Storage refusal when a grow is denied (D3).
  5. Spec sync.
- **Only if the founder adopts it:** a compute-hour budget and spare-capacity lane, as its own change.
- **Acceptance:** FIFO fairness holds under a 20-agent fan-out (measured).

### S10. Postgres domains and the cell seam — **no spend: self-hosted on our cloud server**

**Founder, 2026-10-02:** Postgres runs **self-hosted as a container on our cloud server** (the droplet, $0). It never runs on the founder's desktop: his PC is never a platform dependency. Managed Postgres is not used.

- **Tasks:**
  1. Inventory which of the four domains exist today.
  2. `TransactionalStore` and Postgres.
  3. A locked, verified migration of the existing domains.
  4. Beside-the-cause outboxes plus the idempotent consumer.
  5. The identity map with the ownership generation.
  6. `home_cell` plus the signed claim plus the Worker route (one target).
  7. Ingress dedup.
  8. Off-region Postgres backups (pgBackRest or dumps to the S1 bucket), included in the DR drill.
  9. Spec sync.
- **Acceptance:**
  - a stale-generation request and a duplicate webhook are refused or deduplicated;
  - a lost-ack outbox delivery applies once.
  - (The cell move itself is I13, built at the second-cell trigger.)

### S11. Cutover: user content into sealed boxes

- **Depends on:** the cutover (#4262) or S2, plus S1, S3, S4/S5 (per S0's decision), S6, S7, S8, S9 and S10.
- **Tasks:**
  1. The derived inventory of `cc-<ulid>/`.
  2. Build the images in **S11's own** declared freeze window (not the rename window), under the extended exclusion protocol (D11).
  3. Verify each box through `BoxProvider`.
  4. The `box_epoch` flip.
  5. A rollback rehearsal on staging.
  6. Export (`share`) and deletion through `destroy` + crypto-shred + tombstones.
  7. Retire the bwrap jails and the host-path local driver.
  8. Live proof: a rendered conversation using every tool, plus `mcp_public_canary.py --assert-handles`.
  9. `deployed_sha.py --assert-contains`.
  10. Sync the umbrella specs, then archive.
- **Acceptance:** every command center serves from its box; the old paths are gone; the DR drill restores boxes.

## What this supersedes, and the interim hardening

| Interim item | Status |
|---|---|
| #4245 seccomp denylist for the bwrap jails | Kept until S11. Add the measured delta (`evidence.md` E2) only if S11 is more than ~4 weeks out |
| #4247 safe-reader ratchet | Becomes S3; its module becomes the local `BoxProvider` driver |
| #4252/#4253 hidden-file masks | Retired by the cutover/S2 |
| `openat2` helper, per-universe project quota (spike §9) | Not built; D3 and S3 replace them |
| `TINYASSETS_RUN_MAX_CONCURRENT` / `_HOST_SLOTS` | Replaced by S9 |
| `.universe-sidecars/` | Folded into `.platform/cc-<ulid>/` |
| PLAN "Backend stack (target): Supabase" | Postgres stays; the vendor is open (S10) |

## Risks / Trade-offs

- **DO nested virtualization is unsupported.** S0 measures it; gVisor is the fallback behind the same contract.
- **Firecracker host CVEs** (2026-5747, 2026-1386). Mitigations: a pinned version, jailer, seccomp, a uid per VMM, patch cadence, and S5's quarantine runbook.
- **The broker is a high-value process.** It handles requests on tenants' behalf and holds the vault key. Its surface is narrow: owner/connection/grant-bound requests, the box-socket identity, and no execution of content.
- **Checkpoints are CPU-model-bound.** On failover or a VMM upgrade, boxes cold-boot. Memory loss is accepted, and turns reconcile.
- **Box-file RPO is the backup interval.** It is weaker than platform state, and stated.
- **Account quota can overshoot** by bounded headroom (D3). This is per account, never cross-user.
- **Operations labour:** `boxhostd` is our orchestrator. E2B's outage was its orchestrator, so ours stays one process with no external scheduler.
- **The migration touches every user's data.** It runs once (D8a), with backup, verify, rollback and rehearsal.

## Open questions (founder)

1. **S0:** *decided*: approved (~$0.14). It runs from `.github/workflows/box-kvm-validation.yml` (#4288).
2. **S1:** *decided (slim spend)*: off-region storage is DO Spaces nyc3; standby and LB are deferred until paying users. Still open: the vault-key escrow holder.
3. **S5, if S0 fails on DO:** OVH RISE-S Hillsboro ($77/mo, 12-month term).
4. **S9:** whether to add a compute-hour budget and spare lane to storage + seats.
5. **S10:** *decided*: self-hosted as a container on our cloud server; never the founder's PC.
6. **D6:** the Claude subscription stays owner-only (founder's command center) unless he decides otherwise.
7. **D3 box disk sizing** (escalated from refute round 3):
   - start each box at the full remaining account quota, so there is no mid-operation growth but host reservation is overcommitted; or
   - keep used-plus-headroom with offline growth, accepting a rare typed `ENOSPC` below quota for unknown-size work. This is the current design.

## Appendix R. Cross-family refute, round 1 (gpt-6-astra, 2026-10-02): **ADAPT**

The disposition of each point is above. In summary:

| # | Finding | Disposition |
|---|---|---|
| 1 | BoxProvider lacks the execution lifecycle, pagination, streaming transfers, bulk reads and cache validation; caching contradicted waking; the handle bound the destination, not the principal | **Accepted.** D2 rewritten: `bind` separate from `ensure_awake`, `committed_generation`, op-id lifecycle, `read_many`, cursors, streaming, per-operation authentication with epochs |
| 2 | Overcommit made the host guarantee false; the allocator was not crash-safe; the quota semantics changed | **Accepted.** D3 rewritten into three concerns: per-box bound, logical quota, physical reservation ledger plus epochs |
| 3 | Diff chain, vsock reconnect, memory/disk mismatch after a crash, inconsistent backups, RTO meaning, box-host fencing | **Accepted.** D4 checkpoint manifest and merge; D11 consistent backups, split RTO, fence every host |
| 4 | Selective TLS interception is unworkable; the existing broker binding was ignored; OAuth refresh had no owner; S6 acceptance contradicted file OAuth | **Accepted.** D5 now builds on the existing broker; base-URL mode for CLIs; no CA; refresh in the broker; S6 acceptance carries the explicit exception |
| 5 | The outbox transaction boundary; export versus migration; consistent moves; deletion semantics; backup resurrection | **Accepted.** Beside-the-cause outbox; `share`/`migration` profiles; the move protocol; today's deletion semantics kept, plus crypto-shred and tombstones |
| 6 | Blue-green breaks the `agent_turns` single writer (`agent_turn_boot.py:28`) | **Accepted, blocker.** D11 adds one execution owner behind replaceable frontends; reconciliation only after the lease; schema cutovers are a maintenance exception |
| 7 | Dependencies (S8, S10, S11, S1/S5, gVisor allocator); D9 broke storage + seats; the fairness claim; oversized slices; missing operations work | **Accepted.** Graph fixed; D9 is metering only, budget a founder decision; FIFO fairness stated; multi-change slices; operations work in S1/S5/S6 |
| 8 | ADDED-only deltas contradict existing specs; drill secret transfer; vault overlay; the broker exception; the gVisor representation; the compute example; exactly-once | **Accepted.** Reconciliation table (owner slices MODIFY in their own changes); PLAN drill wording fixed; specs reworded (driver-neutral bound, broker exception, at-least-once + idempotent apply, metering example) |
| — | Evidence summarised without reproducible reports | **Accepted.** `evidence.md` added with versions, commands and raw results; S0 commits the scripts |

### Round 2 (gpt-6-astra, 2026-10-02): **ADAPT**

Round 2 found round-1 findings 1 and 3 **RESOLVED**, and the rest PARTIAL. It also reported eight new defects. Dispositions:

| # | Finding | Disposition |
|---|---|---|
| 1 | A standby successor breaks `BootTurns` (`agent_turn_boot.py:101-106`); the handover promised more than it can keep; fencing needs an enforcement point | **Accepted.** Turn rows carry `owner_generation`; reconcile settles only older generations, after the lease. Owner-store transactions re-check the lease. The broker and `boxhostd` refuse lower generations. Turns past the drain bound are interrupted and held, and stated honestly; new requests never fail (D11) |
| 2 | The in-box endpoint skipped the exact grant check (`outbound_connections.py:5086-5108`); file-OAuth copy-back had no rotation protocol | **Accepted.** The principal is derived from the box identity and every request goes through `resolve_exact_scoped_proxy`. File-OAuth gets a lease, generations, conditional inotify copy-back and a crash path (D5) |
| 3 | `statfs` is not logical bytes; the reservation double-counted; mounted online growth is unsupported by Firecracker | **Accepted.** Logical bytes per `account-storage-quota`. The ledger counts remaining commitments plus in-flight temporaries against free space. Growth happens at a quiesced boundary: an unmounted image plus a cold boot (D3) |
| 4 | Pause-then-freeze deadlocks; the disk hash cost was unmeasured | **Accepted.** Quiesce, acknowledge, pause, snapshot, merge, durable manifest, then thaw on restore with partial-failure recovery; an O(1) dirty epoch replaces hashing (D4) |
| 5 | Shredding the account key strands surviving command centers' backups | **Accepted.** Per-command-center DEKs wrapped by account KEKs; re-wrap and verify survivors before the KEK is destroyed (D12) |
| 6 | Projections had no lifecycle | **Resolved by removal.** The lead's correction keeps `soul.md`/`config.yaml` agent-editable in `cc-<ulid>/`; platform history is read through owner-door tools; nothing is projected into a box (D8a) |
| 7 | Per-account rows had no owner slice; S11 omitted S10; non-DO fencing; S6 omitted S1; rename window | **Accepted.** `store_for` admits account-scoped views (no extraction slice); S11 depends on S10; non-DO fencing lands in S5 with a drill re-run; S6 depends on S1; S11 uses its own window |
| 8 | Placement spec contradicted the root DBs; reconciliation missed three vault requirements and the encryption migration | **Accepted.** The spec scopes per-command-center and per-account records explicitly; three vault rows were added; S6 migrates the plaintext vault |

This is round 2 of at most 3.

### Round 3 (gpt-6-astra, 2026-10-02): **ADAPT** — cap reached

Round 3 found these round-2 findings **RESOLVED**: R2.4 (checkpoint order), R2.5 (deletion keys), R2.6 (projections) and R2.7 (dependencies). R2.1, R2.2, R2.3 and R2.8 were PARTIAL. These consistency fixes were folded **without a fourth review**:

| # | Finding | Fold |
|---|---|---|
| 1 | "Highest seen" left an unfenced interval; the SQLite re-check could read a stale snapshot | Acknowledged fencing barrier at every executor (cancels older streams, durable fence, blocks activation); per-database fence rows with `BEGIN IMMEDIATE` (D5, D11) |
| 2 | Restore order could hide orphaned turns; platform files lacked an RPO | Restore-safe incarnation above every recovered generation, reconciled before serving; file RPOs stated (D11) |
| 3 | Copy-back had no custody transition (`credential-vault` `spec.md:427-440`) | Adopted-rotation renewal, atomic with the byte write, with an acknowledged generation and lease recovery; S6 owns the transaction (D5) |
| 4 | The logical overshoot bound was false; checkpoint capacity was not guaranteed | Bound removed: logical quota enforced at admission and measurement. A wake reserves its checkpoint (D3) |
| 5 | Offline grow causes `ENOSPC` below quota mid-operation | Pre-grow for known-size writes; typed `ENOSPC` for unknown-size work. **Escalated** (below) |
| 6 | Owner-door tools sat outside the tool contract; `config.yaml` still carries authority | Two declared tool destinations plus a read-only in-box route; field-level audit moves authority fields out in the cutover (D8a) |
| 7 | The placement spec contradicted account-scoped views; S5 per-account keys; the broker streaming contract | Spec permits views; S5 uses DEKs and depends on S6. The streaming and duplex broker contract is fixed in S6's design before S7 (**escalated as an open interface**) |

**Escalated to the founder** (not reviewed in a fourth round, per AGENTS "three rounds, then escalate"):
- **(a) Box disk sizing.** Start each box at the full remaining account quota, so no mid-operation growth is needed but host reservation becomes overcommitted. Or keep used-plus-headroom with offline growth, accepting a rare typed `ENOSPC` below quota for unknown-size work.
- **(b) Fencing and custody contract detail.** The fencing barrier and the custody transition are specified at the contract level here. Their full protocols land in S8 and S6, and each gets its own cross-family review in those changes.
- **(c) The broker streaming and duplex contract (I14).** It is an open interface until S6's design fixes it. S7 depends on it.
