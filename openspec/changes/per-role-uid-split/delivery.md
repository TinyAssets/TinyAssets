# Current U1 delivery: D82 offline provider metadata discovery

Final merged-tree verification: origin/main merged without conflicts at
2add14ea70. Sequential plugin regeneration/import probe and changed-file Ruff
PASS. Root oracle 189 passed, zero skips; additional uid1001 identity/provider/
disk-accounting oracle 181 passed, zero skips (one legacy teardown warning).
Whole branch versus origin/main hygiene: 266 added, zero removed, zero tampering.
Rebuilt production image tinyassets-uid-d82:merged:
sha256:c477b1f408085c0ba5a3a3cd07e7f5980b48c630fdc8fe54352bbb94696f636a.
Actual metadata and video probes PASS with ZERO FOREIGN_BYTES on this image.
D76 independent lifetime probe also PASS with zero foreign reads, including
EOF revocation, reaping, reuse, concurrency and fixed deadline enforcement.
Two probe invocations preceded completion of image export and had no image to
inspect; they were rerun after build exit 0 and are not acceptance evidence.

D81 is pushed at d3f99e9134d1743c39f9b6cd22a756b2788d7de9. D82 adds the
actual native metadata API to the dedicated provider-discovery owner cell.
Installed Codex app-server model/list returns 11 models for both Alice and Bob.
The source credential snapshot is pinned, daemon-owned and read-only; a bounded
regular-file copy supplies disposable private SQLite/auth state. Ambient tokens
and loader variables are filtered. No scratch is promoted. The cell has strict
cell-deny, fixed CPU/process/fd/file bounds and a 35-second mapper lifetime.
START retains descriptor ownership; one receipt reader survives cancellation;
EOF revokes without the late queued-CANCEL/reset race. Provider inference,
auth refresh, network metadata and other engines are NOT completed by D82.

Production Dockerfile image tinyassets-uid-d82:private-state:
sha256:bd099fb59b9d6f409fd8140bdb4c080002b4c351c728b844b7a2cdc3ae8a1521.
role_provider_discovery_probe: actual installed API for both owners, foreign
center/snapshot refusal, cancellation and repeated discovery PASS; ZERO
FOREIGN_BYTES. Video application probe PASS, ZERO FOREIGN_BYTES. Independent
D76 lifetime probe PASS: simultaneous owner streams, repeated receipts, EOF
reaping, deadlines, concurrency limit and foreign START refusal, zero foreign
reads. All report daemon capabilities zero and startup_activated false.
The first metadata image failed because Codex needs writable SQLite state and
late CANCEL could reset the receipt socket; diagnostic runs are not acceptance.

Root Linux oracle: 74 passed, zero skips (role_provider_discovery,
universe_path_io_guard, role_video, role_tools, role_launcher, converse_turn_cost).
Additional uid1001 oracle: 99 passed, zero skips (owner_launcher_client,
native_model_discovery, native_discovery_integration,
provider_real_adapter_deadline_reap). One pre-existing legacy transport teardown
warning remains in the malformed-readiness test. Ruff, plugin regeneration and
OpenSpec audit pass; no static prompt budget changed. D82 hygiene: 15 tests
added, zero removed, zero tampering. D82 pushed at 1a095dfc40.
Independent Claude floor/correctness review (peer-agents, read-only, 183s):
VERDICT: APPROVE, no floor/correctness findings. AGREE. Receipt:
C:/Users/Jonathan/AppData/Local/Temp/uid-d82-review.md. Reviewed D81/D82 owner
isolation, sealed snapshot/config boundary, resource bounds and receipt lifetime.
Non-blocking observations retained: synchronous admission can block the event
loop for its bounded exchange; validate absolute CLI argv and proof-object shape
more explicitly; V8 cannot use the current address-space cap, and aggregate
memory/tmpfs accounting still needs the general capacity gate.

Exactly remaining for activation, in execution order:
1. Provider CLI execution, auth/refresh and network metadata; engine-MCP thin
   proxy with canonical daemon handlers; workspace provision/registry/worker;
   remote git/local box; remaining ingestion/caller coverage. Complete actual
   class/site, writable-path/daemon-reader, scope-reuse and denial matrix.
2. Tool owner-directory preparation, persistent brain-file promotion and
   chmod/storage-accounting recovery, retaining daemon custody and settlement.
3. Immutable exact-revision package cells for stdio MCP/user-installed packages,
   owner UID/GID, narrow credential slots, pinned egress, foreign-access denial
   and resource caps; K1 depends on this.
4. Dynamic center admission and remaining broker readers, plus U2 D61 quarantine,
   migration, two-pass deletion, old-image rollback, startup and health checks.
5. Aggregate cell memory/tmpfs capacity enforcement, integrated production proof
   for every class/caller, review of the remaining floor changes and
   spec sync. Startup stays OFF; no deploy or ready/final PR is authorized.

---

# Current U1 delivery: D81 data-only video ingestion

Continuation starts at 6f33b092f2. Draft PR #4523 is open; startup stays OFF,
no deploy, no ready-for-review promotion. This is one additional actual engine
class, not completion of requested item (1); items (2) and (3) remain ordered
behind the engine inventory. No full task checkbox is newly complete.

D81 routes extract_text/extract_video_description video calls through the fixed
ingestion-video cell with explicit admitted center and owner-scoped description
callback. ffprobe/ffmpeg receive only verbatim bounded bytes at a fixed scratch
filename. No owner data, credential, shared store or relay mount; strict
cell-deny, dedicated owner UID/GID, private namespaces, capability/fd retirement,
CPU/address-space/file-size/process/fd limits and fixed lifetime. Selected
failures cannot fall back to daemon subprocess or the legacy platform vision
endpoint. The daemon consumes bounded frame bytes, never cell scratch paths.

Repeated metadata extraction failure reached the AGENTS handoff threshold.
Claude implementation handoff fixed the exact missing Debian BLAS/LAPACK
alternatives with two video-only read-only binds resolving under /usr/lib.
Receipt: C:/Users/Jonathan/AppData/Local/Temp/uid-d81-handoff-result.md.
The diagnostic containers were not acceptance evidence. No filter/capability
relaxation or generic /etc mount was used. The handoff's correctness review found
no defects; a separate final cross-family review remains due before final push.

Verified production Dockerfile image tinyassets-uid-d81:video2:
sha256:0791f5eef97ee0930511732213897e8af1e0014ba03cdd2156cc9575503e17a1.
Privileged chain PASS; scripts/role_video_launcher_probe.py exit 0: Alice/Bob
actual ffprobe/ffmpeg and public extraction callback, foreign scope and playlist
refusal, post-refusal reuse, ZERO FOREIGN_BYTES, startup_activated false.
Root Linux oracle (--as-root, --basetemp /tmp/b): tests/test_role_video.py,
tests/test_ingestion.py, tests/test_role_launcher.py,
tests/test_universe_path_io_guard.py: 104 passed, zero skips.
The existing tests/test_owner_launcher_client.py explicitly assert uid1001;
separate unprivileged Linux oracle: 5 passed, zero skips. The initial combined
root selection failed those identity assertions, not product isolation; no test
was weakened or skipped. An earlier oracle source copy was invalidated by
concurrent plugin regeneration and is not a test receipt.
On the same image, reader alias probe: 132 denied, 22 own reads, zero foreign
reads and foreign unchanged; all three namespace profiles deny read/relabel/copy
and out-of-range mappings. Changed-file Ruff and sequential plugin
regeneration/import probe PASS.
The raw-I/O allowance shrank by one after factoring the bytes-only PDF adapter;
legacy video scratch reads/writes use the existing no-follow helpers.

Exactly remaining for activation, in execution order:
1. U1 actual provider CLI/discovery/auth, engine-MCP thin proxy with canonical
   daemon handlers, workspace provision/registry/worker, remote git/local box,
   and remaining ingestion-format/caller coverage; complete actual class/site,
   writable-path/daemon-reader, scope-reuse and denial matrix.
2. Tool owner-directory preparation, persistent brain-file promotion and
   chmod/storage-accounting recovery, retaining daemon custody and settlement.
3. Immutable exact-revision package cells for stdio MCP and user-installed
   packages, owner UID/GID, narrowed broker credential slots, exact pinned
   egress, foreign-access denial and resource caps; K1 consumers depend on this.
4. Dynamic center admission and remaining broker-reader integration, then U2's
   complete D61 provenance/quarantine/migration, two-pass deletion after chmod,
   actual old-image rollback, service lifetime/startup and healthcheck proofs.
5. Full integrated production-image acceptance, final cross-family floor review,
   spec sync, and separately authorized activation/deployment with SHA assertion
   and one real-user app pass. Deployment is not authorized by this request.

Release-critical files in D81: Dockerfile, deploy/role_owner_launcher.py,
deploy/role_decoder.py (3). U2 implementation files remain untouched.

---
# Current U1 delivery: D80 exact tool relay sockets verified

D80 is pushed at f65af2de5308c35d0f135d1b50fc96026a08a88f, exact remote SHA
asserted. Hygiene: 4 tests added, 0 removed, 0 tampering. Final U2 check:
PR #4509 is OPEN and draft at 064fd8dc40e599b2858cad779f08cd3d726f99ac;
not merged. Both implementation slices below are committed and pushed without
rewriting history. Remaining-class source inspection confirms that provisioning
still launches its existing subprocess and engine-MCP still launches its full
server; neither is claimed as owner-cell acceptance. No package cell was added.

D79 is pushed at 220f612a818d8816c502a29079a588e5a13b0ccc, exact remote SHA
asserted. Hygiene: 5 tests added, 0 removed, 0 tampering. Continued in the same
run into public bash/ta/egress rather than stopping at the offline slice.

D80 extends only the fixed tool class with pinned egress/ta socket descriptors.
Socket ACLs name one dedicated owner; group and other access are removed.
The sidecar grants that owner traverse only, never listing/write, and the cell
mounts only exact sockets. Daemon sender/center binding, exact protected socket
paths, inode proof, fixed fd slots and descriptor closure remain enforced.
The existing egress address policy and per-invocation ta authority stay intact.

Initial installed image built successfully but socket entry failed before its
cell proof. A diagnostic preserving only mapper-child stderr (no acceptance
claim) found: bwrap cannot resolve /proc/self/fd/4: Permission denied. Added
named-owner traverse-only parent ACL, with exact readback and mode assertions.
A first broader stderr diagnostic failed the service bootstrap and is not an
acceptance run. No policy/capability was relaxed to make a probe pass.

Claude peer-agents review returned APPROVE, no floor/correctness finding;
receipt C:/Users/Jonathan/AppData/Local/Temp/uid-d80-review.md. Its optional
Windows observation was addressed by validating missing scope before creating
an AF_UNIX socket; two focused Windows checks pass. No skip was added to an
existing test. Review predates the measured parent-traverse correction.
Linux selection: 73 passed, zero skips, both before and after that correction.
Targeted Ruff and strict OpenSpec pass; plugin import/parity pass.

Corrected production Dockerfile build exit 0, privileged chain PASS:
sha256:553820e780a81e4824172f3c55971b89e05599c259c59c774cf1620d2928b16f
(tinyassets-uid-d80:traverse). Reader probe on this image: 132 denied, 22 own,
zero foreign reads, foreign unchanged. Final commands on this image (exit 0):
- python scripts/role_tool_socket_probe.py --image tinyassets-uid-d80:traverse:
  Alice/Bob actual bash, ta CLI callback (correct captured owner), HTTP proxy
  roundtrip on a Docker-internal synthetic network; metadata address and direct
  networking denied; foreign-center and revoked socket descriptors refused by
  application/mapper; post-refusal reuse succeeds; ZERO FOREIGN_BYTES. Covers
  no-socket, egress-only, ta-only and combined descriptor layouts, plus all D79
  offline controls including exact 20 MiB image transport. Final log:
  C:/Users/Jonathan/AppData/Local/Temp/uid-d80-socket-final.log.
- python scripts/role_node_launcher_probe.py --image tinyassets-uid-d80:traverse:
  actual compiler/authoring/data/workspace/RPC/cancellation PASS, zero foreign bytes.
- python scripts/role_preview_launcher_probe.py --image tinyassets-uid-d80:traverse:
  Alice/Bob sandbox-enabled Chromium and screenshot writes PASS; profile override
  refused for every other D9 class; zero foreign reads.
- python scripts/role_reader_alias_probe.py --image tinyassets-uid-d80:traverse:
  132 denied, 22 own reads, zero foreign reads; foreign unchanged.
- python scripts/role_owner_namespace_probe.py --image tinyassets-uid-d80:traverse:
  all three profiles deny foreign read/relabel/copy; out-of-range mapping denied.
- python scripts/linux_oracle.py -- tests/test_role_tool_sockets.py tests/test_role_tools.py tests/test_role_relays.py tests/test_ta_capabilities.py tests/test_ta_capabilities_jail.py tests/test_owner_launcher_client.py tests/test_role_launcher.py tests/test_universe_path_io_guard.py -q -rs:
  73 passed, zero skips after the final product corrections.

No new full task checkbox is complete. Tool owner-directory preparation,
persistent brain-file promotion and chmod/accounting recovery still need proof.
Remaining actual classes: provider CLI/discovery/auth, engine-MCP thin proxy,
workspace provision/registry/worker, remote git/local box and ingestion/video;
then immutable package cells with narrowed broker slots/egress. U2 PR #4509 is
not merge-ready, and its migration/deletion/old-image/startup work remains U2-owned.
No final build PR or deployment; no production volume or user data was touched. Release-critical files: the same two deploy class files.
U2 files, startup, healthcheck, migration and rollback remain untouched/off.

---
# Current U1 delivery: D79 staged offline tool-jail integration

Started at 7df4be37d7 with the requested ff-only pull, already current. No history
rewrite, no U2 code edit, no deployment. U2 PR #4509 remains draft and explicitly
NOT merge-ready at cfd7967fbd83012a0371fb1ae1fbd043c30b0e9f. Its D213 repeated
rollback-provenance finding remains a handoff; U1 did not patch or merge it.

D79 implements actual read/write/edit/image operations through dedicated owner
cells, with the existing strict inner tool jail and daemon-owned queue slots,
storage reservation/polling/settlement. The cell mounts pinned exact-owner
content only, closes source descriptors before application imports and closes
inner descriptors before payload execution. No protected center root is mounted.
The fixed class deadline is 660 seconds. Limits preserve the existing 20 MiB
image allowance; transport carries its base64 representation with bounded slack.

This is NOT full tool-class acceptance: relay sockets refuse; prepared harness
directories are required; new brain-file names remain in .agent-workspace and
persistent promotion is pending. Public bash/ta/egress are unverified. No full
2.x task checkbox is newly complete. Startup remains OFF.

Production Dockerfile build exited 0 with privileged chain PASS. Reviewed image:
sha256:1650d2cadd7e20ec07fe2d78c9a97b8d394b1c6051208fe341c3d8ed7a9dde8a
(tinyassets-uid-d79:reviewed). A later line-wrap-only source edit has no behavior
change. Initial build also exited 0; its wrapper then failed decoding a UTF-8
log as cp1252. The corrected final wrapper reads UTF-8 and exited 0.

Verified commands (exit 0, synthetic data only):
- python scripts/role_tool_launcher_probe.py --image tinyassets-uid-d79:reviewed:
  actual Alice/Bob read/write/edit/image decoding, stdin, timeout/output limits,
  disk-floor refusal, exact 20 MiB output transport, foreign scope/aliases and
  relabel denied; inner nested-userns denied, descriptors 0/1/2 only, ZERO
  FOREIGN_BYTES. Existing actual decoder and local git/bridge controls PASS.
- python scripts/linux_oracle.py -- tests/test_role_tools.py tests/test_universe_tools.py tests/test_universe_tools_jail.py tests/test_universe_path_io_guard.py tests/test_tool_images.py -q -rs:
  125 passed, zero skips, after review corrections.
- python scripts/linux_oracle.py -- tests/test_owner_launcher_client.py tests/test_role_launcher.py tests/test_role_decoder.py tests/test_role_git.py tests/test_role_preview.py tests/test_role_node.py tests/test_ta_capabilities_jail.py -q -rs:
  58 passed, zero skips.
- Initial source selection: 93 passed, zero skips. Initial image
  sha256:9f35134ff7fa85c90ddefe1eb4aa41acec1c0552215189bd59b302fddc6a0931
  passed offline tools; reader alias matrix 132 denied / 22 own / zero foreign;
  all three profile read/relabel/copy and out-of-range mapping denials PASS.
- Targeted Ruff, strict OpenSpec, diff checks and plugin import/parity PASS.
  Full Ruff retains 55 unchanged findings. No existing test weakened or removed.

Cross-family peer-agents review (Claude), exit 0: ADAPT, no floor finding.
Receipt: C:/Users/Jonathan/AppData/Local/Temp/uid-d79-review.md. AGREE with image
allowance, refusal type and deadline findings; all corrected and production
probe rerun. AGREE with chmod/accounting recovery note: an owner can remove the
daemon ACL by chmod, leading to truthful storage refusal; preparation/recovery
remains pending and privileges are not widened to bypass it.

Release-critical files in this slice: 2, deploy/role_owner_launcher.py (class
admission only) and deploy/role_decoder.py (fixed cell bootstrap). No image
recipe, migration, rollback, startup or healthcheck code changed. U2 migration
dry-run/deletion/old-image-CMD rollback were not run here. No final build PR.

Remaining U1: complete tool sockets/preparation/promotion; provider CLI,
discovery/auth, engine-MCP thin proxy, workspace provision/registry/worker,
remote git/local box, ingestion/video; then exact-revision package cells with
broker-scoped credential slots and egress. U2 remains responsible for full
migration/quarantine/two-pass deletion, real old-image rollback and startup.
The reader concern remains open until the full actual-class/path matrix passes.

---
# Current U1 delivery: D78 actual code-node engine integration

Implementation pushed: **ff6506c7572c2fd6accf2bbb0c87f8d62bc3145a**; exact
remote branch SHA asserted with `git ls-remote`. Hygiene against d1f84c63e5:
**5 tests added, 0 removed, 0 tampering findings**, exit 0. The implementation
worktree was clean after push. No history rewrite; the MCP stack is preserved.

Started at d1f84c63e5 with the requested ff-only pull, already current. U1 only:
fixed node-sandbox admission, its existing D9 nested jail, graph and authoring
callers, and acceptance probes. U2 migration/rollback/startup files are untouched.
PR #4509 is draft at 827d84c8797def5a6962124c4cc0732e4ac62e99 and explicitly
not merge-ready; it was not merged. Package cells remain after actual classes.

D78 records the mechanics in design.md. This is actual node execution rather
than a transport-only slice: dedicated owner outer cell, optional exact-owner
pinned workspace, existing nested node jail/resource checks, bounded data/RPC
transport, and cancellation/reaping independent of blocked daemon callbacks.
Graph compilation passes the center and skips daemon bwrap probes when selected.
Authoring supplies its session owner's protected home binding; absent admission
still refuses. No token or raw credential enters the cell. Other classes are
not implicitly admitted, and no full task checkbox is newly complete.

Initial production probe exposed the inner jail's missing read-only
`/etc/ld.so.cache`: the copied production Python could not locate libpython.
Added that public system file to the existing system read-only set. A corrected
probe fixture also uses a unique post-git marker. Neither failure was acceptance.
The subsequent image `sha256:7c2411a135a52a9cc07c3078d71a5fe8b34914b117b3549b1f53435737cc75a9`
passed actual Alice/Bob node data, git, venv/native descendant, RPC, blocked-RPC
cancellation and post-cancel reuse; zero foreign bytes. That image predates
the review corrections and is not final evidence.

Cross-family peer-agents review (Claude, exit 0): ADAPT, no floor breach found.
Receipt: `C:/Users/Jonathan/AppData/Local/Temp/uid-d78-review.md`.
- **AGREE** compiler factory finding: skip the daemon probe and cover the real
  compiler workspace entry, not only direct NodeSandbox calls.
- **AGREE** transport robustness: check proof before sending data; nonblocking
  deadline/cancellation-aware writes; UTF-8 encoding and a structured failed
  SandboxResult on transport result overflow. Authoring scope is now carried.
- **DISAGREE_EVIDENCE** request to remove cumulative output protection:
  `node_sandbox._BoundedDrain.run` explicitly counts RPC bytes before its
  `on_line` callback discards them. The existing inner ceiling is 8 MiB; the
  proposed 700 x 100 KB workload already fails there. Keep both guards.

Linux source receipts so far (all exit 0, zero skips): 141 initial node/launcher
tests; 178 workspace plus affected heavy tests; 309 expanded node/workspace/
graph-diagnostic/heavy tests; then 358 tests including new compiler coverage
and authoring tests after review corrections. Full Ruff remains
55 unchanged findings; targeted Ruff and strict OpenSpec pass. Alias probe on
the intermediate image: 132 denied, 22 own reads, zero foreign reads.

Final production Dockerfile image, build exit 0 and privileged chain PASS:
`sha256:523e79ad5a95bbbf39e79881e1abaf39bf28c2b4449b21e44b97759d61e77572`
(`tinyassets-uid-d78:guarded`). Final commands, each exit 0:
- `python scripts/role_node_launcher_probe.py --image tinyassets-uid-d78:guarded`:
  Alice/Bob actual compiler workspace nodes AND authoring draft node execution,
  data-only nodes, git, venv/native descendants, scoped RPC, cancellation while
  callback blocks, post-cancel reuse. Dedicated identities, zero caps, NNP,
  descriptors 0/1/2, host-parent traversal/foreign aliases/relabel/network denied;
  **ZERO FOREIGN_BYTES**. Synthetic state only.
- `python scripts/role_preview_launcher_probe.py --image tinyassets-uid-d78:guarded`:
  actual sandbox-enabled Chromium, screenshot writes, existing git/bridge pass.
- `python scripts/role_reader_alias_probe.py --image tinyassets-uid-d78:guarded`:
  **132 denied, 22 own reads, zero foreign reads**, foreign bytes unchanged.
- `python scripts/role_owner_namespace_probe.py --image tinyassets-uid-d78:guarded`:
  all three profiles deny foreign read/relabel/copy and out-of-range mapping.
- `python scripts/role_service_bootstrap_probe.py --image tinyassets-uid-d78:guarded --snapshots --git --stream`:
  sealed snapshots, decoder/git, broker HTTPS GET/POST, accounting/replay refusal
  and two OAuth rotations PASS.
- `python scripts/linux_oracle.py -- tests/test_universe_path_io_guard.py tests/test_role_node.py tests/test_node_sandbox.py tests/test_node_sandbox_workspace.py tests/test_authoring_sandbox.py tests/test_authoring_sessions.py tests/test_node_enqueue_concurrency.py tests/test_nodes_real.py -q -rs`:
  **362 passed, zero skips** after review and guard fixes.
- `python scripts/linux_oracle.py -- tests/test_universe_tools.py tests/test_universe_tools_jail.py -q -rs`:
  **85 passed, zero skips**, covering shared process-tree monitoring.
- `python scripts/linux_oracle.py -- tests/test_owner_launcher_client.py tests/test_role_launcher.py tests/test_role_decoder.py tests/test_role_git.py tests/test_role_preview.py -q -rs`:
  **47 passed, zero skips** on final source.
- Plugin regenerated with import probe; all 618 canonical files mirror-match.
  Targeted Ruff, strict OpenSpec and diff checks PASS.

The raw-I/O guard run found four existing node-module operations newly
in scope because of the owner path, and the new in-cell workspace open. Routed
them through existing filesystem helpers without changing any guard/test pin:
fixed `/proc` reads through read_data_path; temporary script unlink through
unlink_data_path; scratch cleanup through RealPoolFilesystem; cell mount reopen
through open_dir_nofollow. The final guarded image above includes those changes;
all five production probes were rerun on it and passed. No test pin was edited.

Release-critical files in this slice: **2**, `deploy/role_owner_launcher.py`
(engine dispatch only), `deploy/role_decoder.py` (unprivileged fixed node entry).
No Dockerfile, migration, rollback, startup or healthcheck edit. Migration
dry-run/two-pass deletion/actual old-image rollback are U2 work, not run here.
Startup stays OFF. No final build PR or deployment.

Remaining U1: actual provider CLI/discovery/auth, engine-MCP proxy, tool-jail,
workspace provision/registry/worker, remote git and local-box, ingestion/video,
then immutable package revision cells with scoped broker credential slots and
egress (PR #4511). The complete all-class matrix is still unfinished; the reader
concern stays open. U2 still owns full migration/quarantine/two-pass deletion,
old-image CMD rollback and startup/healthcheck. General ffmpeg/ffprobe remains
absent from the image; coordinate image dependencies with U2. No activation or
final build PR until the complete acceptance set is verified.

---
# Prior U1 delivery: D77 application adoption of independent cells

D76 is pushed at **eeeeb0ff49dedf2a9b5b8b487e4b8fd65c7b08cb**, remote SHA
asserted. Hygiene: **2 tests added, 0 removed, 0 tampering**. Continued into
D77 in the same run rather than leaving START as unused transport scaffolding.

Decoder, preview renderer/writer and workspace-git now use independent START
lifetimes. Input/output limits and all descriptor/cell proof predicates remain.
Active STOP refuses without poisoning the client. A mismatched broker/mapper
identity is cancelled and reaped before a reusable refusal; no payload is sent.
These are existing engine classes, **not completion of a remaining new class**.

Final production Dockerfile image (build exit 0, chain PASS):
`sha256:2c4f5a5327b43ee093137cf193f30a214aab9097f9ce083743c2079e375dc88b`
(`tinyassets-uid-d77:final`). All following commands exited 0:
- `python scripts/linux_oracle.py -- tests/test_owner_launcher_client.py tests/test_role_launcher.py tests/test_role_decoder.py tests/test_role_git.py tests/test_role_preview.py tests/test_ui_preview.py tests/test_universe_path_io_guard.py -q -rs`:
  **87 passed, zero skips** on the final source.
- `python scripts/role_cell_lifetime_probe.py --image tinyassets-uid-d77:final`:
  D76 lifetime matrix plus actual application Bob decode while Alice blocks PASS.
- `python scripts/role_owner_launcher_probe.py --image tinyassets-uid-d77:final --client`:
  authenticated receipts, wrong owner and mismatched identity refusal recovery,
  fork closure, concurrent Alice/Bob decodes, terminal acknowledgement PASS.
  The first run found an outdated zero-retained-FD assumption in this probe:
  START now retains exactly one registered status socket per live job. The
  assertion now counts those precise sockets after every request and requires
  the original FD baseline and zero jobs after STOP; no unregistered FD is allowed.
- `python scripts/role_preview_launcher_probe.py --image tinyassets-uid-d77:final`:
  real Alice/Bob sandboxed Chromium and owner screenshot writes, aliases,
  application round trip and existing local git/bridge operations PASS.
- `python scripts/role_service_bootstrap_probe.py --image tinyassets-uid-d77:final --snapshots --git --stream`:
  sealed snapshots, real decoders/git, broker HTTPS GET/POST, accounting/replay
  refusal and two OAuth rotations PASS.
- `python scripts/role_reader_alias_probe.py --image tinyassets-uid-d77:final`:
  **132 denied, 22 own reads, zero foreign reads**, foreign bytes unchanged.
- Targeted Ruff, mirror regeneration/parity, strict OpenSpec and diff checks PASS.
  Full Ruff's unchanged baseline is 55 findings (D76 receipt below).

D77 cross-family peer-agents review (Claude): **APPROVE**, no floor/correctness
findings. Receipt: `C:/Users/Jonathan/AppData/Local/Temp/uid-d77-review.md`.
Release-critical files in D77: **0**; only the daemon client/mirror and probes
changed. D76's one critical file remains `deploy/role_owner_launcher.py`.

U2 advanced to **0080f20a3706daab2c700795e378ab909b6e346d**. PR #4509 now has
91-pass Linux evidence, installed-image migration-substep evidence and a resolved
review, but explicitly remains draft/not merge-ready because full migration,
deletion, old CMD boot and startup/healthcheck are unfinished. It is not merged.
U1 did not edit or run U2's migration/deletion/rollback implementation here.

No full task checkbox is newly complete. All remaining actual engine classes,
their complete path/reader matrices and then exact-revision package cells with
broker-scoped credential slots/egress still require implementation. The full
request is **unfinished**. Startup remains inactive; no final build PR or deploy.

---
# Prior U1 delivery: D76 independent bounded cell lifetimes

Started from b714cfc571 with the requested ff-only pull (already current).
Docker Linux is available again. U2 PR #4509 remains draft at
34a85c9e356297f8e2ee237f4e3602c569cd7c4e with review/installed-image evidence
pending; it was not merged. No U2 migration, rollback or startup file changed.

D76 provides independent bidirectional I/O and mapper-supervised per-cell
lifetime channels using the same fixed class admissions and dedicated owner
identities. This is necessary groundwork for the remaining interactive provider,
tool-RPC, worker and stdio-package integrations; it is **not another accepted
engine class**. The original application APIs still use legacy SPAWN. START
has fixed global/per-owner concurrency ceilings, inherited-handle closure,
deadline/revocation kill and reaping, and authenticated completion receipts.

Final production Dockerfile image (build exit 0, privileged chain PASS):
`sha256:93f86ca0e5ef9ac4894ad0cb3bf57497336cb6d4a6675874bc4eb879b43b7c36`
(`tinyassets-uid-d76:reviewed`). Synthetic container state only.

Verified commands, all exit 0:
- `python scripts/role_cell_lifetime_probe.py --image tinyassets-uid-d76:reviewed`:
  Bob's real PNG decode completes while Alice waits for input; exact dedicated
  identity and FDs 0/1/2; foreign START and active STOP refuse; cancellation and
  fixed deadline return reaped -9; fork descendants lose both handles; EOF-only
  revocation succeeds while data stays open; subsequent legacy decode proves
  jobs drained; four Alice cells refuse a fifth while Bob remains launchable.
- `python scripts/linux_oracle.py -- tests/test_owner_launcher_client.py tests/test_role_launcher.py tests/test_role_decoder.py tests/test_role_git.py tests/test_role_preview.py -q -rs`:
  **47 passed, zero skips**, including independent completion impersonation and
  both concurrency bounds before descriptor use/fork.
- `python scripts/role_preview_launcher_probe.py --image tinyassets-uid-d76:reviewed`:
  Alice/Bob sandboxed Chromium, owner screenshot writers, aliases and actual
  application round trip PASS; existing local git/bridge proof also passes.
- `python scripts/role_service_bootstrap_probe.py --image tinyassets-uid-d76:reviewed --snapshots --git --stream`:
  actual sealed snapshots, decoder/git, broker HTTPS GET/POST accounting,
  replay refusal and two OAuth rotations PASS.
- `python scripts/role_reader_alias_probe.py --image tinyassets-uid-d76:reviewed`:
  **132 denied, 22 own reads, zero foreign reads**, foreign bytes unchanged.
- `python scripts/role_owner_namespace_probe.py --image tinyassets-uid-d76:reviewed`:
  all three profiles deny foreign read/relabel/copy; out-of-range mapping denied.
- Targeted Ruff, plugin regeneration/parity, strict OpenSpec and diff checks PASS.
  Full Ruff retains **55 unchanged findings**.

Cross-family review via peer-agents (Claude): ADAPT, no cross-user defect.
**AGREE** on all correctness/evidence findings: prepare lifetime endpoints before
fork; kill/reap on failed start acknowledgement; bound concurrent retention;
prove EOF with data still open. All corrected in this slice. The EOF probe's
successful legacy decode is a direct assertion that the mapper's job set is
empty (legacy SPAWN refuses otherwise), without stopping the staged service.
Review receipt: `C:/Users/Jonathan/AppData/Local/Temp/uid-d76-review.md`.

Release-critical files: **1**, `deploy/role_owner_launcher.py` (dispatch and
supervision only). Runtime client and its mirror also changed. No full task
checkbox newly complete. Migration dry-run/deletion/old-CMD rollback: not run
in U1; U2 owns them. Startup remains OFF, no build PR and no deployment.

Remaining: adopt independent lifetimes in actual provider/discovery/auth,
engine-MCP proxy, node/tool, provision/registry/worker, remote git/local box and
ingestion/video integrations, with the complete reader/path matrices. Then
immutable exact-revision package cells with broker-scoped slots and egress
(PR #4511 boundary). The referenced harness-control concern is absent from this
checkout; #4511 records the missing revision/slot dispatch contract. General
ffmpeg/ffprobe is absent from the image; Playwright's bundled ffmpeg is codec
limited and is not evidence for the existing ingestion implementation. That
dependency needs coordination with U2's image lane, not a silent replacement.

---
# Prior U1 delivery: founder D73 preview renderer and owner screenshot writer

Implementation pushed at **91c244c079**, exact remote SHA verified.
Hygiene against b8f9c258bd: **4 tests added, 0 removed, 0 tampering**.

Started at b8f9c258bd with the requested ff-only pull (already current).
U1 owns engine integration only; U2 owns migration/quarantine/deletion,
old-image rollback and startup/healthcheck. No U2 PR was available at the
initial check. Startup remains inactive; no deploy or PR is authorized until
all prior acceptance is complete.

Founder D73 now amends only ui-preview's D9 row to cell-nested. The old
snapshot entry also numbered D73 is explicitly qualified as historical.
D75 records data-only preview transport and a separate fixed cell-deny
preview-write operation, needed to preserve D60 identity on screenshots.
The daemon sends admitted UI/asset bytes without mounting owner stores in
Chromium's cell; the writer pins the exact admitted center descriptor and
atomically writes as its dedicated owner, without executing UI code.

Final production Dockerfile image:
`sha256:03e2eaa47e23a76ba6ada1993b9ad94da1e7d9939bd78ccf655bf2c577abb44b`.
Native Docker build return code independently captured by Python: **0**.
Intermediate PowerShell stderr redirection produced misleading exit records;
Docker also reported a missing cache snapshot during disk-usage inspection.
Final image RootFS equals the already-probed `:cells` image below.

Verified commands (all exit 0; synthetic volumes only):
- `python scripts/role_preview_launcher_probe.py --image tinyassets-uid-d73-preview:final`:
  actual Alice/Bob Chromium renders with sandbox enabled, admitted assets,
  private namespaces, dedicated UID/GID, zero caps/groups and FDs 0/1/2 only.
  Actual application render/write/readback passes; screenshot files have exact
  dedicated UID/GID and nlink1. Preplanted symlinks/hardlinks/FIFOs are replaced
  without changing Bob's bytes. Both owner-owned and protected daemon-owned
  canonical roots pass. Renderer nested namespace positive, image/git/writer
  strict namespace negatives, and profile-override refusals for every other D9
  kind pass. Unimplemented-kind refusal is not actual-class acceptance.
- `python scripts/linux_oracle.py -- tests/test_role_preview.py tests/test_role_decoder.py tests/test_role_launcher.py tests/test_role_git.py tests/test_ui_preview.py tests/test_universe_path_io_guard.py -q -rs`:
  **82 passed, zero skips**.
- `python scripts/role_reader_alias_probe.py --image tinyassets-uid-d73-preview:cells`:
  **132 denied, 22 own reads, zero foreign reads**, including preview output.
- `python scripts/role_owner_namespace_probe.py --image tinyassets-uid-d73-preview:cells`:
  all three profile relabel/copy diagnostics retain zero foreign reads;
  out-of-range owner mapping denied. Not extra actual-class evidence.
- `python scripts/role_service_bootstrap_probe.py --image tinyassets-uid-d73-preview:final --snapshots --git --stream`:
  snapshots, actual decoder/git classes, broker HTTPS GET/accounted POST,
  replay refusal and two OAuth rotations pass in one service lifetime.
- `python scripts/linux_oracle.py --production-image tinyassets-uid-d73-preview:final`:
  existing foundation/egress/legacy-launcher receipts pass. Not full D60 migration.
- Targeted Ruff, plugin generation/parity, strict OpenSpec and diff checks pass.
  Full Ruff retains **55 unchanged baseline findings**.

The intermediate image exposed the old daemon writer's UID1001 screenshot
rejection; the owner writer fixes this without weakening D60. The protected-root
fixture initially retained `group::---` behind an ACL mask; explicit owner-group
read/traverse plus the precreated owner-writable preview subtree proves D65.
U2 must supply those actual ACL/subtree prerequisites; no product permission
was widened to make the probe pass.

Renderer cross-family review via peer-agents: APPROVE, no floor/correctness
finding. The later screenshot-writer slice received its own bounded review:
APPROVE, no floor/correctness finding. The reader concern remains open until
the complete class/path/reader matrix passes.
Release-critical files in this U1 slice: deploy/role_owner_launcher.py
(engine dispatch only) and deploy/role_decoder.py. No bootstrap, migration,
rollback, Dockerfile, deployment or healthcheck change.

All other remaining actual classes and the full class/path/reader matrix
remain open. No full 2.x task checkbox is newly complete.

## U1/U2 handoff after the verified preview slice

U2 PR **#4509** targets this branch; at head 34a85c9e356297f8e2ee237f4e3602c569cd7c4e
it remains explicitly draft/not merge-ready, with cross-family review and the
installed-image migration probe pending. Do not merge that draft merely because
GitHub reports a conflict-free merge. #4510 is U2's separate main-target draft,
not a completed U1 build PR. No U1 PR was opened, and no deployment occurred.

Both supported session coordination routes are unavailable (local proxy 10061,
app discovery pipe ENOENT); no direct U2 message delivery is claimed. Branch
records remain the coordination backstop. U2's current migration keeps root
metadata daemon-owned and grants the owner read/traverse through a named UID
ACL. That satisfies preview traversal; it must also classify/precreate the
dedicated writable `previews` subtree. The U1 probe's equivalent group traversal
is evidence of the writer's protected-root support, not an installed U2 migration
receipt. U2's request for an owner-delete launcher admission remains pending a
fixed deletion entry contract; U1 has not edited deletion code or routed it
through git/decoder as an arbitrary command.

After the successful build/probes/push, the Docker Linux API disappeared:
`open //./pipe/dockerDesktopLinuxEngine: The system cannot find the file specified`.
Further actual-class production-image acceptance cannot run in this venue.
Do not count Windows, a refused unknown kind, or the generic namespace diagnostic
as another actual class. No Docker daemon restart, cache/volume purge or unrelated
session takeover was attempted. Next U1 work remains actual provider/discovery/
auth, engine-MCP proxy, node/tool, provision/registry/worker, remote git, local box
and ingestion/video integration plus their complete path/reader matrices.

---
# Prior delivery: D74 raw-I/O guard closed without changing its assertions

D73 pushed at **8c4d724eac**, remote SHA verified; hygiene 3 added tests,
0 removed, 0 tampering. Continued into the previously failing raw-I/O gate.
Snapshot directory opens now check every ancestor, failed vault-temp cleanup
uses the no-follow unlink helper from the filesystem anchor, broker capability
readback uses the bounded reader at its numeric procfs PID, and image parsing
has a bytes-only entry that rejects paths/streams before Pillow. The raw-I/O
guard and its shrink-only inventory are unchanged. Its concern file is resolved
and deleted; the separate reader-alias concern remains open.

Final production Dockerfile image:
`sha256:acb6a78042074461f9c4c45862ed697838a84fcd0558c90b4825699503860814`.

Verification (all listed commands exit 0):
- `python scripts/linux_oracle.py -- tests/test_universe_path_io_guard.py tests/test_tool_images.py tests/test_role_decoder.py tests/test_role_snapshot.py tests/test_credential_vault.py tests/test_broker_process.py tests/test_role_launcher.py -q -rs`:
  **105 passed, zero skips**.
- D73's exact seven-file affected caller/heavy Linux selection rerun:
  **244 passed, zero skips**, including the previously failing raw-I/O guard.
- `python scripts/role_service_bootstrap_probe.py --image tinyassets-uid-d74:guard --snapshots --git --stream`:
  dedicated snapshot prerequisite, real Alice/Bob PNG decoders and local git
  classes, broker HTTPS GET/accounted POST, replay refusal and two OAuth
  rotations pass. This staged-bootstrap run has one service lifetime.
- `python scripts/linux_oracle.py --production-image tinyassets-uid-d74:guard --production-stream`:
  full existing legacy-launcher/foundation/egress substep receipts pass, including
  D12/D29/D45 dry-run/repeat/crash/reverse boundaries, broker IPC consumers,
  accounting/refresh and broker restart. This is not D60's full class matrix.
- `python scripts/role_reader_alias_probe.py --image tinyassets-uid-d74:guard`:
  **114 denied, 19 own reads, zero foreign reads**, foreign bytes unchanged.
- `python scripts/role_old_image_rollback_probe.py --image tinyassets-uid-d74:guard --old-image ghcr.io/tinyassets/tinyassets-daemon@sha256:199755799ebadd71f42f239536a1e55c0b85e4d101af83e79726bc316cd774da`:
  actual previously production-pinned old image retains committed WAL and new
  broker writes; UID1001 with no work group/caps reads/writes ledger+journals and
  reads/writes/deletes proxy state after reverse migration. **Egress substep
  only: owner_tree_rollback=false, old_cmd_boot=false.** Synthetic volume only.
- Targeted Ruff, plugin regeneration/parity and strict OpenSpec validation pass.
  The full Ruff baseline remains 55 unchanged findings.

Cross-family peer-agents review: **APPROVE**, no floor/correctness defects.
Reviewer Windows skip is not acceptance evidence; the Linux suite above has
zero skips. One attempted parallel HTTPS run failed on the fixture's fixed
Docker subnet; serialized rerun passed. No product permissions were weakened.

Release-critical files in D74: **0**. Changed runtime: broker/supervisor.py,
credential_vault.py, tool_images.py, new image_bytes.py and mirrors. No gate,
Dockerfile, deployment or privileged launcher file changes.

This run completed D73 and D74 prerequisites, **not** a new actual engine class.
All remaining engine classes, dynamic admissions, the complete path/reader
matrix, full D61 migration/quarantine and two-pass deletion, restrictive
owner-tree rollback/old CMD, then startup/healthcheck remain. ui-preview is the
one founder-deferred class, still unadmitted. No full 2.x checkbox is newly
complete. Startup inactive; no PR or deployment; finish-all remains incomplete.

---
# Prior delivery: D73 dedicated-owner sealed snapshot prerequisite

Started at **891476fe23**; requested ff-only pull was already current. D73
replaces shared work-group access to sealed provider snapshots when the bounded
client is installed. Broker-resolved custody must match the protected migrated
center label. Descriptor ACLs give the dedicated owner read/traverse only;
parents deny listing, shared groups and other users have no access, and inherited
foreign/default ACLs are removed. Daemon ownership, writes and cleanup remain.

Production Dockerfile image:
`sha256:ba4bb30d59ae54fede1f70c0c2b7fa8f791832c9f8faa0b88a41f31a099dc0fa`.

Verified commands (exit 0):
- `python scripts/role_service_bootstrap_probe.py --image tinyassets-uid-d73:snapshots --snapshots --git`
  Real daemon snapshot creation/custody checks through live broker identity IPC;
  Alice/Bob dedicated identities read only their own snapshot, installed CLI
  lock/version succeeds, foreign/vault/write/list/shared-group/broker access
  denies, seeded foreign/default ACLs disappear, repeat preparation and daemon
  cleanup succeed. These snapshot reader children are pre-retired fixture
  processes, **not additional launcher engine-class acceptance**.
- Same command with `--stream`: git_bridge/workspace-git, both actual decoders,
  HTTPS GET and accounted POST, replay refusal, source-bound evidence and two
  OAuth refresh rotations pass. One service lifetime; historical fixture output
  mentioning restart is not a restart claim for this run.
- `python scripts/role_reader_alias_probe.py --image tinyassets-uid-d73:snapshots`:
  **114 denied, 19 own reads, zero foreign reads**, foreign bytes unchanged.
- `python scripts/linux_oracle.py -- tests/test_role_snapshot.py tests/test_credential_vault.py -q -rs`:
  **37 passed, zero skips**.
- Targeted Ruff, mirror parity and strict OpenSpec validation pass. Full Ruff
  retains the same **55 baseline findings** in unchanged files.
- Affected caller/heavy suite (`test_background_served_provider`,
  `test_custody_carries_across_rotation`, `test_native_model_discovery`,
  `test_run_provider_session`, `test_subscription_credential_refresh`,
  `test_universe_path_io_guard`, `test_provider_work_authority`, all under
  `python scripts/linux_oracle.py -- ... -q -rs`): **243 passed, 1 failed,
  zero skips**. The failure is the existing raw-I/O inventory guard: supervisor
  `_protect_daemon: .read_text()`, vault `_persist_role_vault: .unlink()` and
  `_set_snapshot_directory_mode: os.open()`, decoder `_shown: .open()`.
  Running the same AST inventory against `git show 891476fe23:<file>` reproduces
  every extra entry. D73 adds none. The gate is not weakened or counted as passed;
  it still needs closure before the build PR.

Cross-family peer-agents review: **APPROVE**, no floor/correctness finding.
Review notes that replacing `.runtime` ACLs removes legacy work-group traversal;
the remaining runtime consumer/class matrix must verify dedicated access before
activation. No retained authority or profile change. The first new probe failed
because its public synthetic manifest inherited umask 007; the fixture now
publishes that path-only manifest atomically at 0644. No product guard changed.

Release-critical files for this slice: **0** (no deployment/privileged-chain/gate
file edit). Runtime files: credential_vault.py, new role_snapshot.py and mirrors;
probe, regression tests, design and credential-vault delta updated.

Remaining in requested order: every other actual engine class through the
launcher (provider CLI/discovery/auth, engine-MCP, node/tool, provision/registry/
worker, remote git, box, ingestion/video), dynamic admissions and complete
class/path/reader matrix; full D61 quarantine/migration plus two-pass deletion;
restrictive owner-tree reverse migration and actual old CMD boot; startup and
healthcheck integration. **ui-preview is the one founder-deferred class** and
remains unadmitted. No full task checkbox newly complete, no startup activation,
no PR, no deployment. Finish-all remains incomplete.

---
# Prior delivery: D72 local git_bridge through the bounded launcher

D72 implementation pushed at **93c6dd98b2**; remote SHA verified. Hygiene:
3 tests added, 0 removed, 0 tampering findings.
Started from 340318fe4d with the requested ff-only pull (already current).
All git_bridge subprocess sites now route through D71's authenticated pinned
workspace-git cell when the bounded client is installed. Selected capability
probes bypass the process-global cache and recheck owner admission each time.
Absolute repository pathspecs map into /workspace; message/ref strings stay
opaque. Missing scope, foreign scope, unsupported gh and transport refusals
return failure without daemon subprocess fallback. Remote transport remains open.

Production Dockerfile image:
`sha256:6f79a8c34580e1a894c679e3c51ee573b81fac330cddadc028869e81e730ab66`.

Acceptance (all native probe exits 0):
- `python scripts/role_service_bootstrap_probe.py --image tinyassets-uid-d72:bridge --git --stream`
  Actual Alice/Bob bridge detect/diff/stage/unstage/commit passed, including
  absolute pathspecs and owner-engine writes. Cached success cannot admit a
  foreign repository. Foreign hardlink/symlink reads refuse; a preplanted FIFO
  hits the bounded deadline and the next request succeeds. Actual workspace git
  operations and both PNG decoders also pass with dedicated identities.
  Broker HTTPS GET, accounted POST, source-bound evidence, replay refusal and
  two OAuth refresh rotations pass in one service lifetime. Historical stream
  fixture strings mention restart; this run does not establish a restart.
- `python scripts/role_reader_alias_probe.py --image tinyassets-uid-d72:bridge`
  **114 denied, 19 own reads, 0 foreign reads**, foreign bytes unchanged.
- `python scripts/role_owner_namespace_probe.py --image tinyassets-uid-d72:bridge`
  All three unchanged D9 profiles deny foreign read/relabel/copy; bounded map
  refuses out-of-range identity. This synthetic diagnostic is not an additional
  actual engine-class acceptance claim.
- `python scripts/linux_oracle.py -- tests/test_git_bridge.py tests/test_role_git.py -q -rs`
  **24 passed, zero skips**. Targeted Ruff, mirror parity and strict OpenSpec
  validation pass. Full Ruff retains 55 baseline findings.
- Affected backend caller suite: `python scripts/linux_oracle.py --
  tests/test_backend_factory.py tests/test_git_author_identity.py
  tests/test_outcome_gate_git_backend.py tests/test_storage_phase7_backend.py
  tests/test_storage_phase7_git_integration.py tests/test_storage_git_commit_failure.py
  -q -rs`: **73 passed, zero skips**.

Cross-family review via peer-agents: **AGREE** with the correctness finding that
out-of-scope catalog probes must return git-disabled rather than raising through
catalog writes. Fixed structured refusal, added the outside-data regression and
production-image row, then rebuilt and reran. Reviewer found no floor violation.
No second review. Initial fixture tried to write an owner file from the daemon
with a read-only ACL and correctly failed; fixture now performs that write in
the owner's actual git cell. No ACL or guard weakened.

Release-critical files: **0** by the existing deployment/privileged-chain scope;
product git_bridge and its plugin mirror, probe, tests and records changed.
D72 is one verified local engine-utility integration, not completion of task 2.5.
The reader concern remains open and includes this receipt. ui-preview remains
unadmitted, with no D9 profile change.

Remaining, in priority order: provider CLI/discovery/auth, engine-MCP proxy,
node-sandbox/tool-jail, workspace provision/registry/worker and remote git,
local box and ingestion/video classes; dynamic center admissions and the full
class/path/reader matrix; full D61 quarantine/owner-tree migration and two-pass
deletion; restrictive owner-file reverse migration and actual old CMD boot;
startup/healthcheck integration. No full 2.x checkbox newly complete. No startup
activation, PR or deployment. The requested finish-all outcome is NOT complete.

---
# Current delivery: D71 local workspace-git through the bounded launcher

D70 pushed at **5b020514d9** (3 tests added, 0 removed, 0 tampering). Continued
into the actual local workspace_git.run_git class path. The installed client
admits principal/center through application authorization and broker identity IPC,
passes an open no-follow owner directory, and the mapper requires exact UID/GID
plus a source beneath that center. The static git entry binds only that directory
at /workspace using --bind-fd and checks its device/inode before application code.
The daemon also checks the source inode in the completion. Existing cell-links,
private namespaces, fd closure, zero caps/groups and umask 007 remain unchanged.

Production Dockerfile image:
`sha256:cadc9f5cf07d7b6abeb82a50fffd6aab3b89ab8fc3d96c22d7eaf1bcee5f9d44`.

```
python scripts/role_service_bootstrap_probe.py --image tinyassets-uid-d71:git --git --stream
exit 0
workspace-git: actual run_git init/add/commit/show/checkout for Alice/Bob;
foreign aliases denied; cell-links and descriptor checks passed
bootstrap=true; daemon_pid=1; daemon_caps=zero; live_identity_ipc=true;
application_png_owners=2; foreign_application_refused=true;
legacy_launcher_absent=true; startup_activated=false
```

The real mapper also refuses a Bob directory and a same-owner directory outside
the admitted center, sent directly on the authenticated transport to bypass client
validation. Alice's actual git refuses preplanted foreign hardlink/symlink reads.
A real sleeping git alias hits the inner deadline, refuses, and the next git
request succeeds. The common daemon file reader returns both owners' normal files
and denies the foreign aliases despite explicit daemon ACL read access to the
foreign inode. These rows do not complete the whole class/path/reader matrix.
The same run passes real broker HTTPS GET, accounted inference POST, one-use
claims/replay refusal, source-bound daily evidence and two OAuth refresh rotations.
Historical fixture output says restart; this run has one service lifetime.

Verification:
- Linux oracle targeted role/git/launcher/chain suite: **206 passed**, zero skips
  with the sole Windows-only test deselected. That exact Windows-only transport
  test ran separately on Windows: **1 passed**. The initial broad Linux selection
  reported 206 passed / 1 Windows-only skip; that skip is not counted as a pass.
- Final-image role_reader_alias_probe: **114 denied, 19 own reads, 0 foreign reads**,
  foreign data unchanged. The concern remains open for the full reader matrix.
- Targeted Ruff, mirror regeneration/parity and strict OpenSpec validation pass.
  D70 full Ruff retains the 55 baseline findings; no baseline fixes mixed in.

One cross-family review via peer-agents: **AGREE** with the textual bind-source
race finding. Replaced it with the installed bubblewrap 0.12.0 --bind-fd operation,
plus independent cell and client inode checks. Final image acceptance above is
after that fix. No new capability or seccomp exception, no second review round.
The first git fixture used a nonempty HOME and failed the existing runner guard;
it now supplies a fresh empty daemon-side HOME, with a separate empty home inside
the cell. No existing guard or test assertion was loosened.

Release-critical files for D71: **4**: Dockerfile, deploy/role_owner_launcher.py,
deploy/role_decoder.py, deploy/role_git.py. The privileged-chain gate file
scripts/check_privileged_chain.py also changed and was included in review.

Limitations and remaining work: this admits local run_git calls with canonical
owner work directories, not the entire workspace worker/remote checkout class.
Custom launchers, other binaries, caller preexec functions and caller-held/inherited
lease descriptors refuse in selected mode, with no daemon fallback. Those callers
need their own complete scoped integration. Every other actual engine class,
dynamic center admissions, remaining broker readers, full D61 quarantine/migration
and two-pass deletion, restrictive owner-file rollback and actual old CMD boot,
then startup/healthcheck remain. ui-preview remains the one founder-deferred class.
No full 2.x checkbox newly complete. No startup activation, PR or deployment.

---
# Current delivery: D70 staged broker/mapper bootstrap and application path

D69 was already current at b4b430f727. D70 forks the existing broker and bounded
mapper before PID1 retires to daemon UID1001 with zero capabilities. No host-root
launcher survives. The lease proof is minted after both forks; the broker receives
only its hash. Exact broker PID/UID/GID verification pins the live service. Any
bootstrap exception or role death terminates PID1; there is no privileged restart.
This is staged code, not CMD activation. ui-preview remains unadmitted.

Production Dockerfile candidate:
`sha256:db2abd43e9d2cac426b96f5b7269a21aece49d263d1d7ee3ae29b0e695885961`.

The new `scripts/role_service_bootstrap_probe.py --image tinyassets-uid-d70:bootstrap`
exercises real application admission, fenced broker identity IPC and actual PNG
decoding for Alice and Bob through D69's installed client. Foreign application
scope refuses. Missing identity lookup refuses; explicit allocation returns a
durable identity through IPC. Daemon opens of private ledger/map/token paths deny.
No legacy launcher socket exists. No full engine-class matrix is claimed.

Acceptance commands (all exited 0 on the final candidate):
- `python scripts/role_service_bootstrap_probe.py --image tinyassets-uid-d70:bootstrap --stream`
  reuses the isolated HTTPS fixture: actual broker GET and accounted POST, one-use
  claims/replay refusal, source-bound daily evidence, and two OAuth rotations.
  The fixture's historical output mentions restart; D70 runs one service lifetime,
  so it does not claim in-place broker restart or full owner-tree migration.
- `--bootstrap-failure`: injected exception immediately before daemon retirement
  exits the container 78, even while PID1 still has entry capabilities.
- `--broker-death`: kill the actual broker from a test-only Docker exec; PID1 exits
  78. `--service-death`: authenticated mapper STOP causes the same container exit.

Cross-family code review via peer-agents: **AGREE** with the failure-unwind finding;
bootstrap now catches BaseException and unconditionally exits PID1, including if
stderr is unavailable. **AGREE** with mirror drift found during the build; mirror
regenerated after Ruff sorting. Reviewer found no cross-user floor violation.
Also strengthened IPC ancestor owner/mode checks. No second review round.

The first fixture attempt lacked setgid after chmod without CAP_FSETID: use the
existing migration permission helper, retaining the capability set. The second
attempt incorrectly statted private owner.json from the daemon: absence is checked
before retirement and denied access afterward. Neither failed setup is acceptance.

Verification: `python scripts/linux_oracle.py -- tests/test_broker_bootstrap.py
 tests/test_broker_supervisor.py tests/test_owner_launcher_client.py
 tests/test_role_decoder.py tests/test_role_launcher.py tests/test_privileged_chain.py -q -rs`
reported **43 passed, zero skips**. The unchanged raw owner-launcher probe and
`--client` mode pass, including actual decodes, credential/refusal recovery,
descriptor baseline and timeout reaping. `role_reader_alias_probe.py` passes
114 denials, 19 own reads and zero foreign reads; `role_owner_namespace_probe.py`
passes all three unchanged D9 profiles with zero foreign reads/relabel/copy.
Targeted Ruff, mirror parity and strict OpenSpec validation pass. Full Ruff
retains the 55 unchanged baseline findings.

Release-critical files in D70: **1**, `deploy/role_owner_launcher.py`. Product
supervisor and its plugin mirror, the synthetic probe and tests also changed.
No deployment, PR, production-volume mutation, profile change or new privilege.

Remaining in priority order: every other actual engine class through the bounded
launcher (ui-preview remains the one founder-deferred class); dynamic center
admissions and remaining broker reader matrix; full D61 quarantine/owner migration
and two-pass deletion; restrictive owner-file reverse migration and actual old CMD
boot; startup and healthcheck integration/activation only after prerequisites pass.
No full 2.x task newly complete. The reader concern remains open.

---
# Current delivery: D69 authenticated daemon client and refusal recovery

D68 is pushed at **cfc766bf2d**. This slice adds the startup-installed in-memory
client and routes role_decoder through it when installed. The client resolves
expected owner identity via the existing broker IPC in the application path,
authenticates exact launcher PID/300000:300000 reply credentials, serializes
requests, closes its channel in fork children, and bounds input/output. Transport
or authentication failure closes the channel; a complete authenticated refusal
or mismatched identity fails only that call. No legacy fallback on selected-client
failure. No startup activation or additional engine-class admission.

Production Dockerfile image:
`sha256:b5b3eb2a7d7450ce18754040be1e285e376fdaeef64ecb349010bcd2d564f36d`.

```text
python scripts/role_owner_launcher_probe.py --image tinyassets-uid-d69:client --client
exit 0; actual_png_owners=2; authenticated_replies=true; fork_channel_closed=true;
serialized_concurrency=true; refusal_recovery=true; terminal_ack=true;
startup_activated=false
```

The final image also passes the unchanged default raw-protocol probe (including
empty-packet/STOP authentication, fd baseline and 35-second timeout), and
`python scripts/linux_oracle.py --production-image tinyassets-uid-d69:client --production-stream`
exits 0 with foundation/egress, real broker HTTPS/accounting/refresh/restart and
existing egress migration/crash proofs. This remains the legacy staged broker
harness, not the still-pending D60 service bootstrap or full rollback.

The real client refuses an unadmitted owner/center and a wrong expected identity,
then completes four real PNG decodes across two concurrent caller threads for
Alice and Bob. The fork child has no channel and cannot invoke the client.
This fixture passes identities allocated by the real broker identity store;
it does NOT exercise the full application admission plus live broker IPC path.
That service-bootstrap acceptance remains open.

The first client fixture failed `invalid daemon launcher bootstrap`. Diagnostic
showed Linux SO_PASSCRED auto-binding the already-used peer to an abstract name.
The client now initializes on its pristine startup pair, before any exchanges.
The raw adversarial packet suite remains the default probe mode, with all its
assertions intact. No client validation was weakened.

Linux oracle: initial 34 targeted tests passed with zero skips; after refusal
handling changes, `python scripts/linux_oracle.py -- tests/test_owner_launcher_client.py tests/test_role_decoder.py -q -rs`
reported **17 passed, zero skips**. Real credential tests reject same-UID daemon
impostors and prove received-fd cleanup. Targeted Ruff, mirror regeneration and
parity, diff checks and strict OpenSpec validation pass. Full Ruff remains the
55 baseline findings recorded in D68.

One cross-family review of this new client slice via peer-agents returned
DISAGREE_EVIDENCE: a normal refusal closed the shared client permanently.
**AGREE**, corrected and proven by production-image refusal recovery followed
by successful concurrent Alice/Bob decoding. No cross-user leak was found.
Release-critical files for D69: **0; none**. No full 2.x task newly complete.

Remaining: complete daemon/broker/mapper service bootstrap and dynamic admissions;
every other actual engine class through the bounded launcher; ui-preview remains
unadmitted; full broker reader inventory; full owner migration with D61 quarantine
and two-pass deletion; restrictive owner-file rollback and actual old CMD boot;
then startup/healthcheck. No PR until those acceptance prerequisites pass, no
deployment. The reader concern stays open. D68 evidence follows.

---
# Current delivery: D68 bounded launcher and actual per-owner image decoder

D68 pushed as **cfc766bf2d**. Hygiene: 0 tests removed, 0 tampering.
Started at fedd717970; requested ff-only pull was already current. D68 replaces
host-wide serving authority in the new staged owner launcher with D62's fixed
user namespace map. It does not activate startup or admit ui-preview. No full
2.x task is newly complete.

Production Dockerfile candidate: `sha256:c950733eb7c42e49cee75e1beb35d73f41afc545a5f333908fa774b6baabcf4c`.
The broker identity store allocated Alice 300001:300001 and Bob 300002:300002.
Both actual image decoders ran through OwnerLauncher in separate bubblewrap
cells as inner 1:1 and 2:2, decoded real PNGs, held only stdio descriptors and
zero capabilities/groups, and used unchanged cell-deny. Host labels in replies
are the broker binding plus the asserted kernel map, not a second measurement.

Commands/evidence:
- `python scripts/role_owner_launcher_probe.py --image tinyassets-uid-d68:launcher`:
  actual Alice/Bob PNG decodes; out-of-range mappings refused; descendant,
  empty-packet-with-fd, descendant STOP, forged UID, foreign scope and ui-preview
  requests refused; idle survival and 35-second timeout reaping; descriptor
  baseline restored. Decoder's actual open attempts test each listed host/
  foreign path with O_RDONLY and O_WRONLY, plus network/abstract sockets and
  planted-link/FIFO attempts. No data bind exists for this class.
- `python scripts/linux_oracle.py -- tests/test_role_launcher.py tests/test_role_decoder.py tests/test_privileged_chain.py -q -rs`:
  32 passed, zero skips.
- Pre-review-fix image `sha256:9eb6a5ed405f5c57743d8782d9f159d212e308b7ca459f25b700346bfa16c5df`:
  `python scripts/linux_oracle.py --production-image tinyassets-uid-d68:launcher --production-stream`
  passed the foundation/egress/actual broker HTTPS accounting/refresh/restart
  regression. Existing forward/reverse egress and crash proofs pass; this is
  still the legacy staged broker harness, not D60 startup acceptance.
- Same pre-review-fix image: `role_reader_alias_probe.py --image tinyassets-uid-d68:launcher`:
  114 denied, 19 own reads, zero foreign reads, unchanged foreign data.
  `role_owner_namespace_probe.py --image tinyassets-uid-d68:launcher`:
  all three D9 profiles deny retired-inode read/relabel/copy, zero foreign reads.
- Targeted Ruff and strict OpenSpec validation pass. Full Ruff retains the 55
  unchanged baseline findings. No tinyassets source changed; no mirror update.

One cross-family code review using peer-agents returned DISAGREE_EVIDENCE.
AGREE: empty seqpackets bypassed credentials and fd cleanup; now all packets
reach authentication/cleanup and shutdown needs authenticated STOP. The probe
sends an empty packet carrying an fd and a STOP from a descendant. Also fixed
reported pre-setresuid kill race (try mapper identity then final owner identity)
and ancillary cleanup. DISAGREE_EVIDENCE on "no cell attempts foreign reads":
deploy/role_decoder.py::decode already attempts O_RDONLY/O_WRONLY on every
listed denied path, and raises on any successful open. Changed the receipt's
ambiguous foreign_reads field to explicitly identify those actual open checks.
Private channel holders can consume each other's replies; the integrated daemon
must close it in non-daemon descendants and serialize client request/reply pairs.
That client and full service lifecycle remain pending; no runtime activation.

Release-critical files: **3**: Dockerfile, deploy/role_owner_launcher.py,
deploy/role_decoder.py. The chain checker also changes (gate/authority review
included). No history rewrite, PR, deployment, or production data mutation.

Remaining in order: daemon client plus bounded-launcher service integration;
every other actual engine class through that path (ui-preview the one deferred
class); full broker reader inventory; full D61 quarantine/owner migration and
two-pass deletion; restrictive engine-created-file rollback and actual old CMD
boot; startup/healthcheck only after all prerequisites pass. Existing reader
concern stays open. The D67 egress-only old-image proof is not full rollback.

---
# Current delivery: D61 scan, D65 readers, D67 actual old-image egress rollback

Pushed **52fcbfd47e** (legacy inventory/provenance model) and **4e112da74c**
(dedicated descriptor enforcement plus D66 snapshot probe repair). Both hygiene
receipts: 3 tests added, 0 removed, 0 tampering. This continuation also proves
D12's ledger/proxy reverse move with the actual deployed image, not merely a
uid-1001 process in the new image. No history rewrite; MCP stack preserved.

D67 production image discovery was read-only:

```text
TINYASSETS_DROPLET_KEY=~/.ssh/workflow_deploy_ed25519 python scripts/droplet.py ssh -- docker image inspect --format={{.RepoDigests}} 199755799eba
[ghcr.io/tinyassets/tinyassets-daemon@sha256:199755799ebadd71f42f239536a1e55c0b85e4d101af83e79726bc316cd774da]
```

Pulled that exact digest locally. The probe uses only a fresh synthetic Docker
volume with networking disabled; the production volume is never mounted.

```text
python scripts/role_old_image_rollback_probe.py --image tinyassets-uid-d61:readers --old-image ghcr.io/tinyassets/tinyassets-daemon@sha256:199755799ebadd71f42f239536a1e55c0b85e4d101af83e79726bc316cd774da
exit 0
old-image-seed: uid=1001; caps=zero; uncheckpointed_wal=true
candidate-relocate-reverse: forward_dry_repeat=true; reverse_dry_repeat=true;
  broker_write=true; full_startup_admitted=false
actual-old-image-rollback: uid=1001; groups=[1001]; capabilities=zero;
  retained_wal=true; retained_broker_write=true; ledger_read_write=true;
  journals=true; proxy_read_write_delete=true; owner_tree_rollback=false;
  old_cmd_boot=false
```

Candidate: `sha256:acf2491ff6729c7d8d105b100eb1924a831b7ed09704d53b5a54fb47fa2a7392`.
The old image seeds real ConnectionLedger state and committed uncheckpointed
WAL. The candidate performs actual forward/dry-run/repeat, broker writes,
reverse/dry-run/repeat. The old image then preserves both writes, creates working
WAL/SHM, and reads/writes/deletes proxy test data with zero capabilities and no
work-group membership. The synthetic volume was removed after completion.
Targeted Ruff and diff checks pass. Release-critical files: **0; none**.

**Not complete:** bounded mapper integrated into the launcher; every actual
engine class under D60 (ui-preview remains unadmitted); full broker reader
inventory; full owner migration including crash-safe quarantine and two-pass
deletion; rollback of engine-created restrictive owner files and actual old CMD
boot; startup/healthcheck. No full build task is checked off. This egress proof
is not substituted for the full rollback requirement. Startup stays inactive;
no final build PR or deployment. The reader concern remains until all class/path/
reader rows pass. Full evidence and previous failure receipts follow.

---
# Current delivery: D65 dedicated reader enforcement; D66 oracle repair

D61 scan/diagnostic slice pushed as **52fcbfd47e**. Hygiene: 3 tests added,
0 removed, 0 tampering. Continued into reader enforcement, production-image
rebuild, broker stream regression and an actual old-image egress rollback probe.
No startup activation, ui-preview admission, D9 profile change or deployment.

D65 adds exact UID/GID checks on OPEN file descriptors beneath dedicated owner
roots, keeping owner identity across directory descent and nested reader roots.
It also removes pre-resolution of a potentially symlinked universe read root.
Common file/platform/API/inspect, workspace manifest and copy readers inherit
the guard. Legacy roots remain legacy; this does not complete startup's required
broker-map/root-binding validation or the full broker reader inventory.

Production Dockerfile images:
- First D65 guard image: `sha256:d86c77199d6d4d210f18a83ca478745bd778c2cf4cab02d187caebe1f04c5658`.
- Final D66 fixture image: `sha256:acf2491ff6729c7d8d105b100eb1924a831b7ed09704d53b5a54fb47fa2a7392`.

```text
python scripts/role_reader_alias_probe.py --image tinyassets-uid-d61:readers
exit 0; denied=114; positive_reads=19; foreign_reads=0; failures=[]
uid=1001; groups=[1100,1101,1102]; capabilities=zero; nnp=1; foreign_unchanged=true
```

Passed on both images. Six paths, actual file/platform/API readers and inspect,
with symlink/FIFO/hardlink/retired-hardlink/wrong-UID/wrong-GID fixtures. Foreign
ACLs deliberately allow daemon reads, so ordinary DAC cannot hide a failed guard.
The original shared-ID diagnostic remains `--legacy`; it is historical evidence,
not D60 fixture setup. The concern stays open for the complete class/reader matrix.

On the first D65 image, both commands below exit 0:

```text
python scripts/role_owner_migration_provenance_probe.py --image tinyassets-uid-d61:readers
foreign_reads=0; assigned_legacy_reads=114; profiles=3; attacks=2
quarantined_inodes=1; quarantined_names=2; owner_assigned=false; daemon_denied=true
python scripts/role_owner_namespace_probe.py --image tinyassets-uid-d61:readers
foreign_reads=0; mapping=0 300000 100000; out_of_range_denied=true
retained=SETUID/SETGID in owner userns only; bounding=zero; actual_engine_classes=false
```

Full final-image regression:

```text
python scripts/linux_oracle.py --production-image tinyassets-uid-d61:readers --production-stream
exit 0; foundation/egress, D54 snapshots, D55 relays, actual image decoder,
HTTPS streaming/accounting/daily evidence/refresh/restart: PASS
forward/reverse egress dry-run/apply/repeat and six crash boundaries: PASS
```

The first D65 stream run failed D54 at a missing snapshot `.lock`. D66 changes
only fixture selection: the creator passes the exact returned snapshot name over
a pipe, instead of choosing `next(iterdir())`. All original lock/CLI/permission
assertions remain. The full rebuilt-image run then passed. This fixes the
observed nondeterministic sibling-selection hazard; prior D54 failure receipts
remain recorded rather than being erased by a retry.

Linux verification:
- `python scripts/linux_oracle.py --as-root -- tests/test_role_reader_identity.py -q -rs`:
  **3 passed in 0.14s; zero skips** (real UID/GID, retired alias, read/copy,
  nested root, root symlink and cross-owner directory cases).
- Root broader run of that file plus workspace_fs/workspace_resolver/bounded-reader
  tests: **122 passed, 2 existing off-POSIX skips**.
- Unprivileged run of legacy scan plus those three broader files: **122 passed,
  2 existing off-POSIX skips**. D64 now uses O_PATH on unlisted ancestors and
  O_NOATIME only on listed directories, preserving metadata without requiring
  ownership of `/` or `/tmp` for scans of one's own tree.
- Targeted Ruff, strict OpenSpec validation, regenerated plugin mirror parity,
  and diff whitespace checks pass. Full Ruff retains the 55 baseline findings.

One cross-family code review via peer-agents: **DISAGREE_EVIDENCE** on missing
root prerequisite for the newly added identity tests; **AGREE**, corrected the
new file's prerequisite, with the mandatory root acceptance run still zero-skip.
Reviewer found the initial pair guard and actual reader probe sound. Nested-root
ancestry and root-symlink additions have regression/production evidence above.
Main rechecked at **b945fb3b3ab73d184e4bb91e4558d8e7f4ea8e96**; its shared reader
still lacks nlink/identity checks. Today's single-tree jail qualification stays
in the concern; no current-production exploit is claimed.

Release-critical files in this slice: **0; none** (scope-guard regex inspected).
No full 2.x task is newly complete. Remaining: bounded mapper integrated into
the launcher; every actual engine class except unadmitted ui-preview; broker
reader completion; full D61 quarantine/owner migration/two-pass deletion; full
old-image rollback; startup/healthcheck only after all prior acceptance passes.
The build PR prerequisites remain unmet. D61 scan/model receipts follow.

---
# Current delivery: founder D61 provenance rule implemented in scan/diagnostic

Started at **a904cb73ff**; requested ff-only pull was already current. Founder
D61 supersedes the historical D63 stop below; the earlier allocation decision
also labelled D61 remains historical. D64 records scan mechanics. No startup
activation, ui-preview admission, profile change, PR or deployment.

Production READ-ONLY scan, 2026-10-05, via the requested deployment SSH key:

```powershell
$env:TINYASSETS_DROPLET_KEY = "$env:USERPROFILE/.ssh/workflow_deploy_ed25519"
Get-Content -Raw scripts/role_legacy_alias_scan.py | python scripts/droplet.py ssh -- 'python3 -I -B - --root /var/lib/docker/volumes/tinyassets-data/_data'
```

The source is the `/data` volume path verified by Docker inspect on the healthy
`tinyassets-daemon` container. Metadata only; no payload reads/printing, no link
following, no production writes. Root host scanning preserves directory atimes
with O_NOATIME; a first container-root attempt refused EPERM because production
correctly drops FOWNER. No privileges were added to that container.

Receipt: **5 owner trees; 65,742 entries; 52,167 regular names; 52,162 sole-owner
inodes; 0 cross-owner inodes; 0 unseen inode names; 0 foreign identities; 0 scan
errors**. **57 special entries = 33 symlinks + 24 sockets**. Scan exit 3 reports
those entries; this is not a clean migration/assignment claim. D9 skips work
symlinks, and socket handling still requires the exact stale-runtime inventory.
Live scanning is observational; migration must scan again while quiescent.

```text
python scripts/role_owner_migration_provenance_probe.py --image tinyassets-uid-d60:foundation
exit 0; foreign_reads=0; assigned_legacy_reads=114; profiles=3; attacks=2
unchanged_outside=true; positive_control=true; foreign_metadata_denied=true
quarantined_inodes=1; quarantined_names=2; owner_assigned=false; daemon_denied=true
```

Image: `sha256:2ded0b0cdd8628d4bc77eba7d4cae55dfb2f51578448b7f906453256ae1be1b7`.
The revised matrix preserves the 114 original actual reader operations and
explicitly reports ALLOWED sole-reachability legacy bytes, as the founder now
requires. These are not 114 denials. A separate two-owner alias is quarantined
in the diagnostic model, preserving both names/inode/bytes and alarming. This
is not a product migration or crash/reverse proof. `--historical` preserves the
prior D63 classification. The first setup attempt found a root-owned synthetic
outside control; assigning its intended legacy identity fixed the fixture.

`python scripts/linux_oracle.py --as-root -- tests/test_role_legacy_alias_scan.py -q -rs`:
**3 passed in 0.12s, zero skips**. Covers cross-tree links, unseen names, same-tree
links, surviving names, foreign IDs, FIFO/symlink refusal, legacy discovery,
linked roots and unchanged atime/mtime/ctime/identity. The initial non-root run
refused O_NOATIME on `/`; the scanner requires the documented root venue.
Targeted Ruff, strict OpenSpec validation and diff whitespace checks pass.
Release-critical files this slice: **0; none**. No tinyassets code changed.

Continuing prerequisites: actual descriptor identity enforcement and bounded
launcher integration; then every actual class except deferred ui-preview; full
migration/quarantine/two-pass deletion; actual old-image rollback; startup and
healthcheck only after all acceptance passes. The concern remains. No full
2.x task is newly complete. The final build PR prerequisites remain unmet.

---
# Current delivery: D60 foundations pushed; D63 legacy-provenance stop

Pushed implementation slices: **4cd932f044** (durable identity store) and
**39a1ce6887** (fenced identity IPC, reserved mapper slot, bounded namespace
feasibility). D62 hygiene: 2 tests added, 0 removed, 0 tampering. No history
rewrite; the MCP stack remains mergeable. No PR, startup activation or deploy.

**STOP under the founder's cross-user-exposure rule.** Dedicated UID/GID
protects a foreign inode while it retains that foreign label. Legacy shared
identity data can already have a retired-original alias. A migration that
infers the new identity from the surviving pathname launders that inode into
the requesting owner's exact UID/GID. D63 records the concrete counterexample
and leaves trusted legacy provenance unresolved; no unsafe migration was added.

```text
python scripts/role_owner_migration_provenance_probe.py --image tinyassets-uid-d60:foundation
native exit 3; foreign_reads=114; profiles=3; attacks=2
unchanged_outside=true; positive_control=true; foreign_metadata_denied=true
```

Image: `sha256:2ded0b0cdd8628d4bc77eba7d4cae55dfb2f51578448b7f906453256ae1be1b7`.
The diagnostic MODELS pathname-based chown on synthetic retired aliases, then
runs the actual D59 reader/profile matrix with dedicated Alice 300001:300001.
It does not run a product migration. All final open descriptors satisfy exact
UID/GID/nlink1. Two initial diagnostic setup runs failed an indentation check;
they are not acceptance evidence. No live data, host mounts or external traffic.

```text
python scripts/role_reader_alias_probe.py --image tinyassets-uid-d60:foundation
native exit 3; 19 foreign reads (original unchanged reader probe)
```

Completed: D60/D61/D62 recorded; append-only broker identity allocation and
fenced IPC; 28 passing Linux tests (zero skips); bounded mapping feasibility
with zero foreign bytes in all three existing profiles; rebuilt foundation /
HTTPS broker streaming-accounting-refresh rerun passed. Details and the first
intermittent D54 failure are retained below. Release-critical files per slice:
**0; none**, including this diagnostic/docs slice. No task 2.x newly complete.

Not completed: full descriptor enforcement; bounded mapping integrated into the
launcher; EVERY actual engine class under D60; full owner migration and two-pass
deletion; actual old-image rollback; startup/healthcheck. Existing D12/D29/D45
substep dry-run/repeat/reverse/crash receipts are not substitutes. ui-preview
remains unadmitted, with its founder profile decision still pending. The concern
is retained. Full PR prerequisites are unmet; do not open the final build PR.

Handoff: establish a trusted legacy provenance source (or explicitly justified
legacy-snapshot trust precondition) before enabling owner-tree chown. Current
names/link counts cannot establish retired inode origin. No privilege expansion,
quarantine/deletion policy or weakening of the acceptance matrix was inferred.

---
# D62 continuation: fenced identity IPC and bounded mapping proof

D61 foundation pushed as **4cd932f044**. Hygiene: 5 added, 0 removed,
0 tampering. Continued without stopping after that slice.

Implemented OWNER-only, fenced broker identity lookup/allocation and the daemon
client. Missing initialization, stale authority and UID/GID/path override fields
refuse without allocating. The broker loads an existing private map and never
automatically recreates a lost one. D62 reserves 300000 for the mapper; owner
pairs now start at **300001** (through 399999), avoiding a launcher/owner collision.

`python scripts/linux_oracle.py -- tests/test_owner_identities.py tests/test_owner_identity_ipc.py tests/test_broker_server.py -q -rs`:
**28 passed in 2.97s**, zero skips, one existing broker-fixture loop-close warning.
This includes the corrected allocation range. Targeted Ruff passes.

`python scripts/role_owner_namespace_probe.py --image tinyassets-uid-readers:d57`:
**exit 0** on immutable image
`sha256:832b7055dce8a3eb48e6a0af5c776aa81a55f7cd8fa3dc397304e42f7c62e37f`.
Mapping `0 300000 100000`; out-of-range UID/GID changes denied; candidate mapper
retains only SETUID/SETGID in its bounded user namespace and has zero bounding,
ambient and inheritable sets. Alice/Bob children have all five sets zero, own
read/write controls pass, foreign owner and broker directory access fail.
Actual bubblewrap under cell-deny, cell-links and cell-nested: **zero foreign
reads**, retired-name read/relabel/copy denied, NNP=1, seccomp=2. No profile edits.
This is a kernel feasibility probe, NOT actual engine-class launcher acceptance
or the full daemon reader matrix. The old D59 diagnostic is preserved.

A production Dockerfile rebuild was also exercised (`tinyassets-uid-d60:foundation`,
image `sha256:2ded0b0cdd8628d4bc77eba7d4cae55dfb2f51578448b7f906453256ae1be1b7`).
The first full stream oracle failed D54's engine snapshot read (PermissionError).
An isolated rerun of liveness plus D54 on the SAME image passed; the complete
rerun completed **exit 0**: foundation/egress, existing image-decoder, actual
HTTPS streaming/accounting/refresh, restart and relocation crash proofs pass.
The initial intermittent snapshot denial remains a qualification, not erased
by the rerun. This image predates the final 300001 range
correction, so it is not a production receipt for that correction.

Release-critical files for this slice: **0; none**. No startup, class admission,
PR or deployment. Remaining order and deferred ui-preview are unchanged below.

---
# Current delivery: D60 accepted; D61 durable identity foundation (2026-10-05)

Started at **48c9b0a225**; `git pull --ff-only origin feat/per-role-uid-split`
reported already up to date; clean worktree. D60 supersedes D8 shared identity
and D58. D12 already exists and is retained without duplicate numbering.

Implemented broker-private append-only UID/GID allocation in
`tinyassets/broker/owner_identities.py`. D61 reserves pairs 300000..399999,
disjoint from per-box 200000..299999. Concurrent allocation is serialized and
committed before return; retries/restarts preserve the pair. No delete/reuse
API exists, exhaustion refuses, and ordinary opening refuses a missing map.
Explicit first-volume initialization remains a startup migration integration
step, not an automatic runtime fallback. No launcher admission is changed.

Verification:
- `python scripts/linux_oracle.py -- tests/test_owner_identities.py -q -rs`:
  **5 passed in 0.21s**, zero skips; Linux Python 3.11.16, bwrap 0.12.0, uid1001.
  Real SQLite concurrency/restart, lost map refusal, exhaustion, symlink/hardlink
  and public parent refusal, process death before commit covered.
- Targeted Ruff, strict OpenSpec validation and `git diff --check`: pass.
- Full Ruff: the same **55 pre-existing findings**; no unrelated fixes.
- Plugin rebuilt; no production Dockerfile or startup policy change in this slice.

Remaining in order: broker identity IPC and descriptor enforcement; bounded
launcher mapping with authority retirement; every actual engine class except
ui-preview through launcher; full owner migration and two-pass deletion; actual
old-image rollback; startup/healthcheck only after prior probes pass. Original
alias and D59 production diagnostics have NOT yet passed under D60. The concern
remains. ui-preview remains the sole deferred founder profile decision, stays
unadmitted, and D9 profiles are unchanged. No task checkbox is newly complete.
Release-critical files in D61: **0; none**.
No PR or deployment; PR prerequisites remain unmet. This section will be updated
with subsequent verified slices in the current run.

---
# Current delivery: D58 authorized; D59 shared-UID acceptance failure (2026-10-05)

Started at **2b24d108f94cfdf08135c8a3914bcbaa365b6abe**; requested ff-only pull
was already current. The worktree was clean. D58 is recorded as the lead's
dedicated durable owner-GID decision, replacing the earlier identity ambiguity
and D8's shared-group clause only. The historical section previously numbered
D58 is now labelled D57 follow-up. D10/D11/D12 and the preview deferral remain.

**STOP under the explicit cross-user-exposure rule:** the shared-UID option in
D58 fails the prescribed preplanted-inode scenario even when all requested
descriptor checks pass. A synthetic Bob engine-owned file at uid1003/gid200001
is pre-aliased into Alice's setgid200000 tree and its original name retired.
Alice's capability-free uid1003 cell can read it by UID ownership. It can then
chgrp it to gid200000 **or copy the bytes into a new gid200000 file**. Both
variants yield uid1003/gid200000/nlink1, satisfying the proposed descriptor
predicate. Denying chown alone would leave the copy variant.

`role_owner_gid_probe.py` verifies this under each unchanged D9 profile with
private mount/PID/IPC/network namespaces, NNP, seccomp and all capability sets
zero. Bob's host path is invisible. The real no-follow descriptor is inspected
before invoking the actual readers. This is a production-image **diagnostic**,
not an actual engine class launched through the launcher, not evidence of alias
creation after admission, and not a current-production exploit. Main's identical
reader gap is not exploitable through today's single-tree jail because engines
cannot see cross-owner paths to create the alias; the concern now says so.

## Receipts

Image for all commands:
`sha256:832b7055dce8a3eb48e6a0af5c776aa81a55f7cd8fa3dc397304e42f7c62e37f`.
The diagnostic executes its repository source from stdin against that immutable
image, with no host mounts/network, the seven declared entry capabilities and
production seccomp/AppArmor/systempaths options. It adds no runtime policy.

```text
python scripts/role_owner_gid_probe.py --image tinyassets-uid-readers:d57
native exit 3
foreign_reads=114 profiles=3 attacks=2
unchanged_outside=true positive_control=true foreign_metadata_denied=true
cell uid=1003 gid=200000 groups=[] capabilities=zero nnp=1 seccomp=2
host_path_denied=true

python scripts/role_reader_alias_probe.py --image tinyassets-uid-readers:d57
native exit 3; 19 FOREIGN_BYTES; positive_control=true foreign_unchanged=true

python scripts/linux_oracle.py --production-image tinyassets-uid-readers:d57 --production-stream
exit 0; existing foundation/egress/launcher decoder and HTTPS broker probes PASS
```

114 = three profiles x two variants x 19 reader/path pairs: universe-file,
platform-text and file API over six paths, plus authenticated activity inspect.
The original alias probe remains unchanged. These are completed security
failures, not test skips. The baseline still passes D12 relocation and D29/D45
accounting/liveness dry-run/apply/repeat, 6+8+3+4 crash boundaries, broker
streaming/accounting/refresh and decoder launch. It does **not** prove full
owner migration, D10 two-pass deletion or actual old-image rollback.

## Scope and handoff

Release-critical files in this slice: **0; none**. Only the diagnostic and
OpenSpec/concern documentation change; no runtime or generated plugin edit.
No task newly completed. No allocator, engine admission, migration activation,
startup/healthcheck activation, PR or deployment is claimed. A draft reader
change was removed before delivery because it cannot close this failure.

Lead decision needed: whether owner identity must include a dedicated UID as
well as D58's GID, or another mechanism that prevents access to foreign inodes
before engine execution. This changes the security design, so it is not
inferred as a mechanical implementation detail. Merely relabeling every inode
under its surviving pathname at migration can erase the foreign provenance.

Remaining: resolve D59, durable identities and descriptor enforcement, all
other actual engine classes through launcher plus paired readers, full
migration/two-pass deletion, actual production old-image rollback, then
startup/healthcheck integration. ui-preview remains the outstanding founder
profile decision and stays unadmitted. PR criteria remain unmet. The retained
concern includes both the new evidence and the earlier promotion recovery issue.

Validation: targeted Ruff passes; strict OpenSpec validation passes after
correcting the new requirement's opening normative sentence; whole-tree plugin
mirror parity passes (611 files); diff whitespace passes. Full Ruff retains
55 findings in unchanged files. No affected application/heavy test file exists
for this diagnostic/docs-only diff; the real production-image diagnostic and
baseline oracle above are its verification. Evidence commit **789ae47bda**;
`python scripts/test_hygiene_gate.py --base 2b24d108f94cfdf08135c8a3914bcbaa365b6abe --head 789ae47bda`
passes: tests added 0, removed 0, tampering findings 0, product lines added 200
(the diagnostic script). All pre-commit hooks passed; worktree was clean after
the evidence commit. The subsequent receipt commit changes this document only.

---
# Prior delivery: descriptor hardlink refusal (historically D58, 2026-10-05)

Implementation/evidence pushed as **8414c9c3c1**. Hygiene against 3f4dc2c451:
2 test functions added (3 parametrized cases), 0 removed, 0 tampering findings.
Pre-commit checks passed. Cross-family review ADAPT is addressed in the retained
evidence/concern below; the isolation blocker remains open.

Started at **3f4dc2c451**, ff-only pull already current. D12 relocation and
D10/D11 are retained; no duplicate D12 or history rewrite. D57's lead repair
is partially implemented: the shared regular-file open rejects `st_nlink != 1`
on the OPEN descriptor before read/copy. Its callers include universe files,
platform text, authenticated inspect, file API, provision manifests and export
bundle copying. No hardlink exception is documented or added. Plugin mirror
regenerated. Three regression cases cover read/copy and pathname replacement
after open. No existing test was removed or weakened.

## Final acceptance result: STOP, retired-original alias still leaks

Cross-family peer-agents review returned **ADAPT**. AGREE with the independent
link-count hardening and lack of known steady-state hardlink requirements.
AGREE with the evidence limit: deleting/replacing Bob's original filename
leaves the preplanted Alice alias with one link. Added that acceptance row,
without changing the original controls. On the same immutable image the final
probe returns native **exit 3**, a completed summary with **19 FOREIGN_BYTES**
and 57 DENIED rows. All positive controls and restored foreign metadata pass.
This is a failed full alias probe, not zero-leak acceptance. The earlier 57-row
receipt below covers only aliases whose original name remains present.

The standing cross-user-exposure stop applies. No additional class admission,
identity allocator, migration rollout or startup integration was attempted.
The requested descriptor owner validation cannot be satisfied with the stated
per-owner group until the shared-D1 vs per-owner identity conflict is resolved.

AGREE also with the review's availability caveat: `_promote_brain_files` uses
link-then-unlink; interruption can leave a two-link brain file now refused by
the guard. Tracked in the retained concern; no in-owner exception is inferred.
The descriptor check defeats pathname replacement, not all alias-lifecycle
races; no broader claim from the review is adopted.

## Verified evidence

```
python scripts/role_reader_alias_probe.py --image tinyassets-uid-readers:d57
exit 0; 57 DENIED rows; failures=[]; zero FOREIGN_BYTES
positive_control=true foreign_metadata_denied=true foreign_unchanged=true
uid=1001 groups=[1100,1101,1102] capabilities=zero nnp=1

python scripts/linux_oracle.py --production-image tinyassets-uid-readers:d57 --build --production-stream
exit 0; actual broker HTTPS streaming/accounting/refresh and image-decoder PASS
existing migration substeps dry-run/apply/repeat and 6+8+3+4 crash boundaries PASS

python scripts/linux_oracle.py -- tests/test_workspace_fs.py tests/test_platform_reads_refuse_links.py tests/test_universe_file_reads_are_bounded.py tests/test_workspace_resolver.py tests/test_workspace_staging.py -q -rs
173 passed, 2 skipped in 4.73s
```

The two skips are existing Windows-only off-POSIX refusal tests, not acceptance
probes; the production reader matrix has zero skips. An initial test command
named nonexistent tests/test_universe_files.py and collected nothing; the
corrected command above is the receipt. Production image pinned to
`sha256:832b7055dce8a3eb48e6a0af5c776aa81a55f7cd8fa3dc397304e42f7c62e37f`.
The external reader probe runs from stdin against the immutable image. Its six
path categories and three readers, plus inspect's activity-log row, are a
shared-reader submatrix, not every actual engine class or git/publish path.
No new full migration, two-pass deletion or actual old-image rollback proof.

## Identity-design clarification and remaining work

D58 records the mechanical no-alias decision and the unresolved identity
requirement. The latest lead instruction says the owner's per-owner group;
D8 explicitly requires shared uid1003/gid1100, with no per-owner identity
allocator, and D9 defines no such group. Asked whether to validate existing D1
role identities or amend the design for per-owner groups. No answer yet. A
per-owner allocator is a security-design change, not a mechanical mode/path
choice, so it has not been inferred. The no-alias guard alone does not prove
owner identity for a foreign inode with only one remaining name.

Current main fetched at 26993ec47c71a8fa36d62410bb361cf5ee8898c9 has the same
reader gap; the concern records why it matters under single UID too. This is
source analysis, not a live production exploit or deployment. The concern
remains open, per the lead's requirement, until the full reader/class matrix.

ui-preview remains unadmitted, its D9 profile unchanged, and its founder
decision still open. Remaining build: descriptor identity validation once the
identity contract is reconciled; every other engine class through the launcher
and paired readers; full migration/two-pass deletion; actual production old-image
rollback; startup/healthcheck integration. No startup activation, deployment,
PR or newly completed task is claimed. PR criteria are not yet satisfied.

Release-critical files in this slice: **0**; runtime edit is workspace_fs.py
and its generated mirror. Branch's previously recorded total remains 8.
Targeted Ruff, mirror parity, strict OpenSpec and diff whitespace checks pass.
Full Ruff still reports 55 errors in unchanged files.

Additional affected-reader/sink and heavy API verification:
```
python scripts/linux_oracle.py -- tests/test_workspace_effector.py tests/test_workspace_end_to_end.py tests/test_api.py tests/test_api_edge_cases.py -q -rs
422 passed, 3 skipped in 42.57s
```
The three existing skips are Windows-specific doubles/refusal branches; actual
Linux paths ran. Total passing tests across the two receipts: 595. Neither
pytest receipt substitutes for the failed production alias acceptance.

---
# Prior delivery: D57 preplanted hardlink reader exposure (2026-10-05)

Evidence pushed as **694570f028**. Hygiene against 89b1cffa94: tests added 0,
removed 0, tampering findings 0. Pre-commit checks passed. The corrected probe's
native exit code was explicitly read back as 3 with the completed failure summary.

Started at **89b1cffa94**; requested ff-only pull was already current. The
founder's preview deferral is preserved: ui-preview stays unadmitted and its
D9 profile is unchanged. While tracing remaining provider classes and their
required reader matrix, found an additional cross-user acceptance failure.
The founder explicitly requires STOP/report for cross-user exposure; this
continuation records that evidence rather than admitting more classes.

`scripts/role_reader_alias_probe.py` runs the real universe file reader,
platform-text reader and authenticated inspect handler against disposable
synthetic owners inside the immutable production Dockerfile image. Alice's
ordinary inspect succeeds; Bob's metadata is denied to Alice. A preplanted
hardlink from Alice/activity.log to Bob/private.txt then returns Bob bytes
through all three readers. No-follow traversal checks type and size but not
hardlink aliases. Symlink/FIFO controls deny all six reads without hanging.

```text
python scripts/role_reader_alias_probe.py --image tinyassets-uid-relays:d55
exit 3 (completed summary lists the three hardlink failures)
symlink: universe-file/platform-text/inspect-universe = DENIED
fifo: universe-file/platform-text/inspect-universe = DENIED
hardlink: universe-file/platform-text/inspect-universe = FOREIGN_BYTES
uid=1001 groups=[1100,1101,1102] capabilities=zero nnp=1
positive_control=true foreign_metadata_denied=true foreign_unchanged=true

python scripts/linux_oracle.py --production-image tinyassets-uid-relays:d55 --production-stream
exit 0
```

Both pin image
`sha256:7c8bb8846244365fc3c2806468f886767749304471342ef4d9aa96a0218c7a60`.
The alias probe has no host mounts/network, uses the seven declared entry
capabilities, then the installed launcher's retirement to daemon identity.
It uses the production oracle security options (nnp and unconfined
seccomp/AppArmor/systempaths). It is a **failed preplanted-reader acceptance**,
not proof of engine planting after migration, not full class acceptance and
not a production exploit test. No real user data or credentials are accessed.
The synthetic foreign bytes/owner/group/mode/mtime are unchanged.

Existing production substeps pass again: image/chain/capabilities, broker
private storage, decoder launcher, HTTPS streaming/accounting/refresh,
account erasure, snapshots/relays, and relocation/accounting/liveness
dry-run/apply/repeat and 6+8+3+4 crash boundaries. Full migration, D10 two-pass
deletion and actual old-image rollback remain unproven. The passing baseline
does not override the new reader failure.

Finding: `docs/concerns/2026-10-05-role-reader-hardlink-alias.md`. D57 records
the stop and evidence limits in design.md. Remaining work: resolve the reader
alias failure; every other engine class through the launcher with all paired
reader probes; full migration/two-pass deletion; actual old-image rollback;
startup/healthcheck integration. Preview remains the explicitly deferred
founder decision, but is no longer the only known open acceptance blocker.

Release-critical files this continuation: **0** (diagnostic and documentation
only); branch's previously recorded total remains **8**. No tinyassets package
edit, mirror regeneration, heavy-test change, test removal or expectation
loosening. Targeted Ruff, strict OpenSpec and whitespace checks pass. Full Ruff
still reports 55 errors in unchanged files. No task is newly checked complete.
Startup remains inactive. No PR, deployment, rebase or force-push.

Cross-family peer-agents review: **ADAPT**; AGREE with the reproduction and
evidence limits. AGREE with both diagnostic corrections: a distinct leak exit
code plus completed-summary requirement distinguishes setup failure; assert
uid/gid/groups explicitly before the reader matrix. Both corrections applied
and the diagnostic rerun. No runtime patch or second review round.

---
# Prior delivery: D56 preview security-scope stop (2026-10-05)

Evidence committed as **0abd204364**. The committed diagnostic was rerun:
exit 0 with the same three-profile result. Hygiene against 71df3620de:
0 tests added, 0 removed, 0 tampering findings. Pre-commit checks passed.

Started at **71df3620de**; ff-only pull was already current. While reconciling
the remaining actual engine classes, found that D9 assigns preview `cell-deny`
but its real Chromium renderer requires nested user namespaces. Added a durable,
standalone diagnostic, not a runtime policy change or a launcher admission.

```text
python scripts/role_preview_profile_probe.py --image tinyassets-uid-relays:d55
exit 0
cell-deny: No usable sandbox!; unavailable; PNG bytes 0
cell-links: No usable sandbox!; unavailable; PNG bytes 0
cell-nested: PNG base64 bytes 5780; delivery_error empty; uncaught_errors []
DIAGNOSTIC CONFIRMED: preview fails cell-deny/cell-links; cell-nested renders.
No launcher acceptance, policy change, or startup activation.

python scripts/linux_oracle.py --production-image tinyassets-uid-relays:d55 --production-stream
exit 0
```

Both use immutable production image
`sha256:7c8bb8846244365fc3c2806468f886767749304471342ef4d9aa96a0218c7a60`.
The diagnostic uses no host mounts, network none, root entry with exactly
CHOWN/DAC_OVERRIDE/FOWNER/SETUID/SETGID/SETPCAP/KILL, then the installed
launcher's capability retirement and uid-1003 transition before bubblewrap.
Security options are no-new-privileges plus seccomp/AppArmor/systempaths
unconfined, as in the production oracle. All three processes report uid1003,
zero capability sets, nnp=1 and distinct mount/PID/IPC/network namespaces.
The renderer is the shipped `_child`, with Chromium sandbox on and the same
synthetic 320x240 markup in each run. No asset/store read or owner tree was
mounted, and no owner-class denial or daemon-reader acceptance is claimed.
The alternate profile is an isolated diagnostic control only.

The baseline production oracle again passes actual broker HTTPS streaming,
accounting, refresh, account-ledger erasure, decoder launcher acceptance,
snapshot/relay permissions, and forward/reverse migration substeps including
dry-run/apply/repeat and 6+8+3+4 crash boundaries. These remain substep evidence;
full migration, two-pass deletion and actual old-image rollback are not proven.

**Stop under the founder's explicit security/isolation-scope rule:** admitting
preview with `cell-nested` would relax D9's namespace policy for another class.
D56 records a pending proposal for **ui-preview only**, retaining Chromium's
own sandbox, all existing capability retirement, and every remaining class and
daemon-reader probe. No scope relaxation has been approved or implemented.
The unresolved finding is
`docs/concerns/2026-10-05-role-preview-seccomp-policy.md`.

No build task newly completed. Release-critical files this step: **0**;
branch's previously recorded total remains **8**. No product package edits,
so no plugin mirror regeneration. No test names, skips or expectations changed.
Targeted Ruff, strict OpenSpec validation and diff whitespace checks pass.
Full Ruff reports the same 55 pre-existing errors in unchanged files.
Startup remains inactive; no PR, deployment, rebase or force-push. Remaining
work is still all other launcher classes, full migration/two-pass deletion,
actual old-image rollback, and startup/healthcheck after all prerequisites.

---
# Prior delivery: D53 account erasure, D54 snapshots, D55 owner relay creation

Implementation pushed as **58839ec354**. Test hygiene against 69ee880edc:
12 tests added, 0 removed, 0 tampering findings. Pre-commit mirror parity,
mojibake, import-graph, path-resolver, cross-provider and skill checks passed.

Continued from 69ee880edc after ff-only pull (already current). D12 relocation
was already implemented and recorded; no duplicate decision or history rewrite.
D53 closes the missing daemon account-deletion ledger-row consumer through
ERASE_ACCOUNT: fixed principal/scope, one broker transaction, foreign-grant
refusal, committed counts, all four accounting tables, and no local ledger.
A lost acknowledgement leaves the existing durable phase unfinished; explicit
retry is idempotent. Actual launcher probes erase separate synthetic owners
before/after broker restart and preserve a third owner's connection/accounting.

D54 makes snapshot creation/re-preparation publish and verify daemon:work
2750 directories / 0440 files using descriptors, with mandatory file fsync.
The actual uid-1003 installed CLI lock/version succeeds; snapshot writes and
broker snapshot reads fail. This is a permission prerequisite, not discovery
or provider-cli class acceptance. D55 similarly pins nofollow daemon-owned
relay directories, sets 0711/2710 and 1001:1100/0660 sockets before listen,
refuses planted entries, and checks cached inode identity. Real daemon-created
egress and engine relays accept uid1003, deny directory listing, and refuse
uid1002. Exact socket-only cell binding is still required for every class.

Cross-family peer-agents: three bounded read-only reviews, all APPROVE.
AGREE with D53 transaction/foreign/fence/ambiguous-outcome controls, D54
descriptor/readback/fsync controls, and D55 nofollow/identity/mode controls.
No finding was dismissed. D53 oracle diagnosis was handed off after repeated
failure: its probe ran before broker selection, accidentally using a local
ledger. Moved it after selection and added an explicit selected/no-local guard.
The earlier missing COPY and failing runs are not acceptance receipts.

Verified Linux commands (zero skips):
```text
python scripts/linux_oracle.py -- tests/test_broker_account_erasure.py tests/test_account_deletion.py tests/test_broker_server.py tests/test_broker_disconnect.py -q -rs
93 passed in 25.04s
python scripts/linux_oracle.py -- tests/test_credential_vault.py tests/test_native_model_discovery.py -q -rs
69 passed in 1.94s
python scripts/linux_oracle.py -- tests/test_role_relays.py tests/test_universe_egress.py tests/test_provider_jail_network.py tests/test_universe_tools_jail.py -q -rs
62 passed in 27.68s
python scripts/linux_oracle.py --production-image tinyassets-uid-snapshots:d54 --build --production-stream
exit 0; sha256:a618e830f15f980d33f102d6d464143acee55984bfc29bb96afe9f1ef1f949b6
python scripts/linux_oracle.py --production-image tinyassets-uid-relays:d55 --build --production-stream
exit 0; sha256:7c8bb8846244365fc3c2806468f886767749304471342ef4d9aa96a0218c7a60
```
Both production runs use the immutable production Dockerfile image, seven
entry caps then role retirement, no-new-privileges, network-none foundation
and the internal HTTPS fixture. D55's only subsequent harness edit wraps its
long print line for Ruff; runtime bytes are unchanged. D51 decoder, D22/D24
HTTPS, D46 accounted inference, D47 history and D49/D50 refresh all pass again.
Forward/reverse migration dry/apply/repeat and 6+8+3+4 crash boundaries pass;
these remain substep evidence, not full migration or actual old-image rollback.
Targeted Ruff, strict OpenSpec and plugin generation/import (613 files) pass.
Full Ruff still has the same 55 unrelated errors. No affected heavy-list file.
Windows account tests: 49 pass / 6 platform skips; Linux supplies acceptance.
Windows combined vault/native test was stopped after failures/hang and is not
claimed as a pass; corresponding Linux run above passed.

Release-critical files this continuation: D53 **1**, Dockerfile (oracle COPY);
D54 **0**; D55 **0**. Branch total remains **8**: Dockerfile,
.github/workflows/docker-build.yml, deploy/backup.sh, deploy/broker_main.py,
deploy/compose.yml, deploy/role_decoder.py, deploy/role_egress_migration.py,
deploy/role_launcher.py. No privilege/security-scope change or startup activation.

Remaining, in order: actual provider CLI/discovery/auth, thin engine-MCP,
node/tool, workspace provision/registry/worker/git, preview, local box and
utility class launcher integration with every paired daemon-reader probe;
full migration and D10 two-pass deletion; actual old-image rollback; then
startup/healthcheck. Native discovery's bounded parser/process transport was
traced, but no discovery class is admitted by this continuation. Full deletion
must also quiesce admitted work before erasure (otherwise late USAGE can
recreate rows) and clear broker-private proxy runtime. No task 2.1-2.8 marked
complete. No PR or deploy; startup remains inactive.

---
# Prior delivery: D52 named cell profiles for the remaining engine matrix

D51 pushed as b32cea4ff4; hygiene: 5 tests added, 0 removed, 0 tampering.
D52 implements D9's cell-deny/cell-links/cell-nested names in the shared
seccomp compiler. Image-decoder explicitly selects cell-deny. No remaining
engine kind is admitted and no payload can select its own profile. Both
legacy filter byte streams match commit b32cea4ff4 exactly. cell-links
permits symlink creation but keeps namespace, clone3, FIFO/device and kernel
interface restrictions. Unknown or explicitly contradictory options refuse
before pipe allocation; pipe write failure closes both ends.

Cross-family peer-agents: ADAPT. AGREE with CI wiring: moved the new kernel
cases into existing tests/test_provider_jail_network.py, already real_jail
marked and covered by linux-jail-proof's paths and zero-skip assertion.
No new skip on any existing test, no workflow or heavy-list change. Also
fixed the minor explicit-False/nested contradiction using an unset default.
No second round. Release-critical files in this slice: **1**,
deploy/role_decoder.py (already changed in D51). Branch total against local
origin/main: **8**, unchanged: .github/workflows/docker-build.yml, Dockerfile,
deploy/backup.sh, deploy/broker_main.py, deploy/compose.yml,
deploy/role_decoder.py, deploy/role_egress_migration.py, deploy/role_launcher.py.
No affected heavy-list file.

```text
python scripts/linux_oracle.py -- tests/test_jail_seccomp.py tests/test_role_decoder.py tests/test_role_launcher.py tests/test_provider_jail_network.py tests/test_universe_tools_jail.py -q -rs
107 passed in 32.09s; zero skips
```
This includes real-kernel symlink/FIFO/io_uring/new-user/clone3 decisions for
all three profiles, plus the shipping provider and tool jail regression
proofs. Initial new clone3 fixture used size zero and received EINVAL;
corrected to a valid structure size with null pointer to distinguish EFAULT
from the filter's ENOSYS. The runtime policy was not weakened. Marker-based
CI collection includes the containing module. Targeted Ruff, mirror parity
(609), plugin import and strict OpenSpec pass. Full Ruff still reports the
same 55 unrelated errors.

```text
python scripts/linux_oracle.py --production-image tinyassets-uid-profiles:d52 --build --production-stream
exit 0; sha256:114b5f77154b2f276b46dbfc92009d3cabe77883c816358cafbe976f43af325e
```
All D51 decoder, D49/D50 refresh, broker consumers/accounting, HTTPS streaming
and migration-substep probes pass again. Forward/reverse dry/apply/repeat and
6+8+3+4 crash boundaries remain substep evidence, not full migration/deletion
or actual old-image rollback. Additional production-image profile probe:
`python $env:TEMP/uid-d52-production-profiles.py` exit 0. It extracts the
committed ROLE_PROFILE_PROBE from test_provider_jail_network.py and runs each
profile from the immutable image's jail_seccomp.py via runpy, at uid 1001,
cap-drop ALL, network none, nnp and the same seccomp/AppArmor/systempaths
options. All three profile results match the 107-test Linux receipt. Initial
ad-hoc import under python -I failed because /app is intentionally absent from
sys.path; loading the fixed stdlib-only module by path matches the decoder.
This probe uses a synthetic read-only runtime view, not an additional actual
engine class. Startup inactive.
Remaining: actual provider CLI/discovery/auth, thin engine-MCP, node/tool,
workspace provision/registry/worker/git, preview, local box and utility class
launcher integration with every paired daemon-reader probe; full migration
and two-pass deletion; actual old-image rollback; startup/healthcheck only
after all prior acceptance. No full task 2.1-2.8 checked off. No PR or deploy.

---
# Current delivery: D51 actual data-free image decoder through the launcher

D49 pushed as 692b2d201b; D50 pushed as 8281f758fd (8 added tests,
0 removed, 0 tampering). D51 admits only the fixed image-decoder kind with
one authenticated anonymous socketpair, no mounted owner data, engine uid
1003 and no supplementary groups. Unsupported engine kinds remain refused.
Actual bound_image uses this launcher path when broker mode is selected.
The launcher polls decoder lifetimes asynchronously with a two-child bound.

Cross-family peer-agents: ADAPT; AGREE and fixed all three findings: removed
unneeded work group and pre-cell application imports, admitted the founder's
public home using the existing canonical home authority, and mapped errors to
existing refusals while removing blocking child waits from the launcher loop.
Also closed the duplicated fd if socket construction fails. No second round.
Release-critical files in this slice: **3**: Dockerfile,
deploy/role_launcher.py, deploy/role_decoder.py. The privileged-chain checker
also changed and was included in review. No affected heavy-list test file.

```text
python scripts/linux_oracle.py -- tests/test_role_decoder.py tests/test_role_launcher.py tests/test_privileged_chain.py tests/test_tool_images.py -q -rs
60 passed in 5.12s; zero skips
python scripts/linux_oracle.py --production-image tinyassets-uid-decoder:d51 --build --production-stream
exit 0; sha256:5ffbe0cd1b628981202396d49bc087f7ad875386ebaa7a7a8f2db7ad6f8e9804
D51 actual image-decoder through launcher: uid1003 zero capabilities, private mount/PID/IPC/network, stdio-only fds/openat denial, foreign data/vault/token absence, host abstract socket denial, cell-deny planted link/FIFO refusal, real PNG decode, concurrent input wait and foreign scope/file-fd refusal: PASS
```
The same decoder process checks read/write and hardlink denial, host TCP denial,
all capability sets, no-new-privileges and descriptors before importing the
image decoder. Daemon-side foreign scope and arbitrary-file-fd requests fail.
All previous broker, HTTPS refresh/accounting and migration-substep probes
pass again, including the forward/reverse dry-run/apply/repeat and 6+8+3+4
crash boundaries. This is not full migration, two-pass deletion or old-image
rollback. Successful broker streaming is proven; startup remains inactive.

The first image run exposed a missing dynamic-linker cache in the cell. Added
fixed read-only /etc/ld.so.cache; diagnostic runs are not acceptance. One
PowerShell redirected invocation misreported native stderr as an error; the
final unredirected full production run above has explicit exit 0. Targeted
Ruff, plugin build/import and mirror parity (609) pass. Full Ruff's previously
recorded 55 unrelated errors remain. No test removal or weakened guard.

No complete task 2.1-2.8 checked off. Remaining: other actual engine classes
through the launcher (including paired daemon-reader denial), full migration
and two-pass deletion, actual old-image rollback, then startup/healthcheck only
after all prior proofs. No PR, deployment, rebase or force-push.

---
# Current delivery: D50 coordinated refresh through the admitted broker stream

D49 pushed as 692b2d201b; hygiene added 4 tests, removed 0, tampering 0.
D50 adds REFRESH/REFRESH_ACK to the existing authenticated stream, with an exact
pre-OPEN custody snapshot and daemon-only lock/admission/reread/spend/write.
Only a destination and rejected-token digest cross the wire; the broker refuses
local refresh without a daemon callback. Sync/async clients save before ACK;
a lost ACK keeps the new vault and does not replay the single-use token.
D49 runtime vault replacement is exercised by actual daemon uid 1001 here.
Cross-family peer-agents: AGREE, APPROVE; no floor/correctness findings.
Reviewer's Windows skips are not acceptance; Linux receipts follow.

Release-critical files: **0; none**. Runtime broker refresh/client/aclient/server/
process, connection_oauth/tokens and outbound_connections plus generated mirrors;
refresh tests and HTTPS oracle. No affected heavy-list test file.

```text
python scripts/linux_oracle.py -- tests/test_broker_refresh.py tests/test_broker_server.py tests/test_broker_discovery_http.py tests/test_outbound_connection_ledger.py tests/test_generic_oauth_connections.py tests/test_platform_oauth_clients.py -q -rs
144 passed in 50.16s; zero skips
python scripts/linux_oracle.py --production-image tinyassets-uid-refresh:d50 --build --production-stream
exit 0; sha256:e58b1cf1ba316821a58d7ddb56847cb77a1db00144c3028aeeb6e0d533edee18
D49/D50 actual daemon vault publication and broker HTTPS OAuth refresh: expiry and 401, exactly two single-use rotations, persisted daemon-owned 1001:1102/0640 vault, reuse across broker restart: PASS
```
New tests additionally cover concurrent single-flight rotation, admission failure
before spend, write retry under the same vault hold, foreign/revoked/stale scope,
lost ACK, digest-based 401 retry, async coordination and broker fallback refusal.
The earlier isolated run (33 passed) printed existing fixture shutdown pending-task
warnings; the complete 144-test acceptance run above completed without them.
Initial new assertions were corrected to the existing typed/sanitized error
contract; no existing test or guard was loosened. One command named a nonexistent
async-test file and ran no tests; its corrected run included test_broker_server.

All previous production-image substeps pass: egress/accounting/liveness forward
and reverse dry-run/apply/repeat; 6+8+3+4 crash boundaries; hostile input refusal;
accounted HTTPS, consumers and restart. Same seven capabilities and compose
security options; synthetic HTTPS network/CA cleaned by harness. These remain
substep proofs, not full role migration, deletion or actual old-image rollback.
Targeted Ruff, plugin build/import, mirror parity (608), strict OpenSpec and
whitespace pass. Full Ruff still reports the same 55 untouched errors.

No full task checked off. Remaining in order: every actual engine class through
the launcher (including daemon-reader denial pairs), full role migration and
capability-free two-pass deletion, actual old-image rollback, then startup and
healthcheck integration only after every prior proof passes. Startup inactive.
No PR, deploy, rebase or force-push.

---
# Current delivery: D49 runtime vault publication prerequisite

D49 retains broker read-only access on every daemon vault replacement: the
unique private temp receives group 1102 and shared mode 0640 before writing,
fsync and atomic publication. Prepublication faults preserve the old inode and
clean the temp; postpublication failures preserve existing commit semantics.
Cross-family peer-agents: AGREE, APPROVE; no blocking findings.
Release-critical files: **0; none**. No affected heavy-list file.

```text
python scripts/linux_oracle.py -- tests/test_broker_vault_modes.py tests/test_credential_vault.py tests/test_vault_account_deletion_guard.py -q -rs
44 passed in 1.88s; zero skips
```
Targeted Ruff, plugin build/import, mirror parity pass. Production runtime
rotation proof is next with coordinated refresh; no production-image acceptance
is claimed by this unit slice. D50 refresh implementation is in progress.
No complete build task checked off. Startup inactive; no PR or deployment.
Remaining: refresh, each actual engine class, full migration/two-pass deletion,
actual old-image rollback, then startup/healthcheck only after all proofs.

---
# Current delivery: D48 runtime provider metadata publication

D46/D47 pushed as e31def9356; additive hygiene correction 7be718b2b8
removes an accidental skip from the existing D44 test (no history rewrite).
D48 applies daemon-owned 1001:1102/0640 to each new provider-definition
inode before atomic replacement. Unsplit creation remains private 0600;
permission failure preserves the old definition and removes the unpublished temp.
The HTTPS oracle now registers and replaces definitions as the actual daemon,
then proves broker-local source validation and accounted POST before/after restart.
No seeded provider-definition permissions remain in that proof.

Cross-family peer-agents: ADAPT; AGREE and corrected the resource-consumer
fixture to seed its foreign definition before broker mode is selected. Its
cross-owner refusal assertions are unchanged. Same-uid transport fixtures use
their actual group; the production oracle proves the real 1102 group.
Release-critical files: **0; none**. No affected heavy-list test file.

```text
python scripts/linux_oracle.py -- tests/test_broker_definition_modes.py tests/test_provider_definition_registry.py tests/test_broker_usage_ipc.py tests/test_broker_usage_source.py tests/test_broker_discovery_http.py tests/test_broker_compute_consumers.py tests/test_broker_usage_evidence.py -q -rs
77 passed in 4.85s; zero skips
python -m pytest tests/test_broker_resource_consumers.py -q
13 passed in 0.83s
python scripts/linux_oracle.py -- tests/test_broker_resource_consumers.py tests/test_broker_serving_consumers.py tests/test_broker_bootstrap_ipc.py tests/test_broker_graph_connections.py tests/test_broker_usage_ipc.py tests/test_broker_definition_modes.py -q -rs
48 passed in 8.40s; zero skips
python scripts/linux_oracle.py --production-image tinyassets-uid-metadata:d48 --build --production-stream
exit 0; sha256:4db53b5ca656e60b72d7a86c49cf7897e32e968930fdf70d789a97abbfb26553
D48 actual daemon definition registration/replacement retains broker read mode before atomic publish; broker-local source binding succeeds: PASS
```
All prior production-image probes pass again, including D46/D47 before and
after broker restart, using the same seven-capability startup harness. This
proves runtime metadata publication, not existing-metadata migration.

Remaining in order: coordinated refresh with admission before spending a
single-use token and daemon-only durable vault publication; every actual engine
class through the launcher; full migration and capability-free two-pass deletion;
actual old-image rollback; then startup/healthcheck activation only after every
prior proof passes. Startup remains inactive. No PR or deployment.

---
# Current delivery: D46 accounted inference POST and D47 daily evidence

D45 pushed as e2780ee541; hygiene added 3 tests, removed 0, tampering 0.
D46 proves real HTTPS inference POST with source-bound references, runtime kernel
leases, broker-local claims, send and settlement. The synthetic HTTPS server
counts requests: omitted references, duplicate operation IDs and reusing a
reference under a fresh operation ID produce no extra POST. Exactly two accepted
POSTs per pass, before and after broker restart. Metadata modes are seeded here;
runtime provider-definition replacement and full migration remain prerequisites.
D47 routes daily history through owner-only bounded broker pages, with exact
owner/center/turn membership batches to exclude linked daemon legacy rounds.
Any failed page/link or unreadable legacy store returns the existing unknown
advisory result; no grant or quota is implied. Absence of a legacy store does not
hide complete broker evidence. All four accounting tables must move together.

Cross-family peer-agents D46: ADAPT; AGREE and fixed the proof gaps with exact
sanitized authority refusal, server-side POST counts, and fresh-op reference
replay. The broker intentionally scrubs detailed authority messages, so asserting
the suggested internal error text at the client would be false. D47: AGREE,
APPROVE; no floor/correctness findings. Added first-page/unreadable-store tests.

Release-critical files: **0; none**. Runtime broker usage/usage_evidence and
request_budget plus mirrors; stream oracle and accounting/evidence tests, docs.
No affected heavy-list test file. No full task checked off. Startup inactive.

```text
python scripts/linux_oracle.py -- tests/test_broker_usage_evidence.py tests/test_request_budget.py tests/test_request_budget_broker.py tests/test_broker_usage_ipc.py -q -rs
68 passed in 5.29s; zero skips
python scripts/linux_oracle.py --production-image tinyassets-uid-evidence:d47 --build --production-stream
exit 0; sha256:f97dc257c522086b1917133dddf21d1400b88bcad7017eabc4ff5bdce60229af
D46 actual accounted HTTPS inference POST via launcher broker: kernel leases, source binding, one-use claims, dispatch/settlement receipts, missing/replay refusal: PASS (seeded metadata modes)
D47 actual daily evidence via launcher broker: counted HTTPS attempts across restart, foreign history absent, daemon tables untouched: PASS
```
All previous migration substeps, four liveness crash boundaries and launcher
consumer proofs pass again. Same seven capabilities and compose security options;
synthetic network/CA cleaned. This is not engine-class, full migration/two-pass
deletion or actual old-image acceptance. Targeted Ruff, plugin build/import,
strict OpenSpec and whitespace pass. Windows-only skips are declared for the
new Unix socket tests; all reported acceptance runs are Linux, with zero skips.

Next: runtime provider-definition replacement must retain broker read modes;
refresh admission-before-spend with daemon-only durable vault writes; then every
engine class, full migration/deletion, actual old-image rollback and gated
startup/healthcheck. No PR or deployment and no history rewriting.

---
# Current delivery: D45 runtime liveness modes and reverse migration

D44 pushed as 52136836ae; hygiene added 5 tests, removed 0, tampering 0.
D45 replaces fixture lock preparation with no-follow daemon runtime creation:
1001:1102 directories 2750, proofs 0640, no new PID sidecars. Same-inode process
proof upgrades preserve the held lock. The startup migration substep shares the
literal role_modes declaration and restores 1001:1001 0700/0600 on reverse.
Cross-family peer-agents: AGREE, APPROVE; no floor/correctness findings.
No full build task complete; startup inactive. No PR or deployment.

Release-critical files: **1: deploy/role_egress_migration.py**. Runtime changes:
process_liveness, universe_files, role_modes, storage/agent_request_usage and
mirrors; both role oracles, liveness creation tests and accounting fixture.
No affected heavy-list test file.

```text
python scripts/linux_oracle.py -- tests/test_broker_liveness_creation.py tests/test_broker_usage_ipc.py tests/test_broker_readonly_liveness.py tests/test_request_usage_store.py tests/test_parent_turn_request_budget.py -q -rs
125 passed in 59.00s; zero skips
python scripts/linux_oracle.py -- tests/test_universe_file_reads_are_bounded.py tests/test_role_accounting_schema.py tests/test_broker_liveness_creation.py -q -rs
30 passed in 0.55s; zero skips
python scripts/linux_oracle.py --production-image tinyassets-uid-locks:d45 --build --production-stream
exit 0; sha256:d54afe8fd44066ea9b0dbcb14d932d364256706b755aafa7d1a95cb74504c638
D42/D45 runtime-created read-only daemon/parent kernel liveness, independent parent close, daemon death, engine denial: PASS
D45 liveness forward/reverse dry-run/apply/repeat, four crash boundaries, hostile aliases and foreign owner refused without mutation: PASS
D44/D45 actual accounting create/reserve/dispatch/settle/receipt/close via launcher broker, foreign refusal and committed budget stop: PASS (runtime lock creation; inference POST pending)
```
All previous egress/accounting migration and launcher consumers, plus real HTTPS
GET streams, pass before/after restart. Same seven capabilities/compose security
options. No proof of full role migration/deletion or actual old-image rollback.
Targeted Ruff, plugin build/import, strict OpenSpec and whitespace pass. Full
Ruff baseline remains 55 unrelated errors. The initial test run caught a legacy
lease-registration hook rejecting the added keyword; the unsplit call signature
was preserved, and the unchanged regression then passed. No test weakened.

Review notes: the old singleton helper may create informational .pid sidecars
with group write in a setgid directory; the authoritative .lock creation mode
is 0644 before umask and cannot grant group write. D45's broker-readable path
creates no .pid. Full startup orchestration must order reverse liveness with
accounting/egress reversal while roles are stopped; this substep never admits
service. Continue this run with actual inference POST, daily evidence and
refresh, then engine classes, full migration/deletion, old-image and startup.

---
# Current delivery: D44 runtime accounting IPC

D44 routes create/reserve/check/dispatch/settle/receipt/link/close and reference
issuance through a closed authenticated broker operation. The daemon retains
kernel leases; the broker owns the four tables and local claim/retry checkpoints.
Foreign scope, stale fence, outage, source revocation and consumed references
refuse. Mutations never retry ambiguous transport outcomes. Startup inactive.
Cross-family peer-agents: AGREE, APPROVE; no floor/correctness findings. Resolved
the review's path-normalization note with an exact relocated-ledger comparison.

Release-critical files: **0; none**. Runtime broker usage/client/server and
storage/agent_request_usage plus mirrors, test_broker_usage_ipc, launcher oracle,
design/inventory/delivery. No affected heavy-list test file. No full task newly
checked off. No PR, deployment, rebase or force push.

```text
python scripts/linux_oracle.py -- tests/test_broker_usage_ipc.py tests/test_broker_usage_source.py tests/test_request_usage_store.py tests/test_http_inference_lifecycle.py tests/test_parent_turn_request_budget.py tests/test_broker_server.py tests/test_broker_upstream_stream.py -q -rs
182 passed in 64.04s; zero skips
python scripts/linux_oracle.py -- tests/test_broker_usage_ipc.py -q -rs
9 passed in 1.34s; zero skips (final direct-scope refusal assertion)
python scripts/linux_oracle.py --production-image tinyassets-uid-accounting:d44 --build --production-stream
sha256:4299fcbba3a5199d66d02c81da202fda99ff33725c7870796771746af3995db6
D44 actual accounting create/reserve/dispatch/settle/receipt/close via launcher broker, foreign refusal and committed budget stop: PASS
```
D44 passes before/after restart, using explicit fixture lock permissions. Runtime
permission creation/migration and actual inference POST are not claimed here.
All prior launcher consumer and HTTPS GET proofs pass. Egress/accounting forward
and reverse dry-run/apply/repeat, 6+8+3 crash boundaries, hostile input refusal
pass; these remain substeps, not full migration/deletion or actual old-image
rollback. Same seven capabilities and compose security options. Fixture network
and CA are cleaned by the harness.

Targeted Ruff, strict OpenSpec, plugin import and whitespace pass. Full Ruff has
55 pre-existing errors outside these changes. A PowerShell redirected build
reported NativeCommandError for Docker's normal stderr despite passing probes;
reran the completed image directly to get an unambiguous tool exit status.

Next, continuing this run: daemon-owned broker-readable lock creation and offline
mode migration, actual inference POST, daily evidence, refresh; then every engine
class, full migration/two-pass deletion, actual old-image rollback and finally
startup/healthcheck only after all prerequisites pass.

---
# Current delivery: D43 accounting source binding

D42 pushed as 11b4cc3a78; hygiene added 4 tests, removed 0, tampering 0.
D43 routes accounting reference issuance's grant read through authenticated
GRANTED_RESOURCE in selected mode. Installed definition/model checks remain;
connection identity must match exactly. Broker outage/fence/revocation refuses
without local fallback. Unsplit read-only query preserved.
Cross-family peer-agents: AGREE, APPROVE; no floor/correctness findings. Added
direct broker owner/center and resource-revocation probes after the review's
coverage note. No full build task checked off, startup inactive, no PR/deployment.

Release-critical files: **0; none**. Runtime storage/agent_request_usage.py and
its mirror; role_launcher_oracle.py, test_broker_usage_source.py, tasks/inventory/
delivery; D43 design was recorded in the preceding commit. No affected heavy file.

```text
python scripts/linux_oracle.py -- tests/test_broker_usage_source.py tests/test_request_usage_store.py -q -rs
47 passed in 12.34s; zero skips
python scripts/linux_oracle.py --production-image tinyassets-uid-usage-source:d43 --build --production-stream
exit 0; sha256:22a36fd05ffc2f66ad0e335517be1d131d6955693daff45e05bfbb1e4abf36d6
D43 actual accounting source binding via launcher broker: installed definition/model, foreign connection refusal, no daemon ledger: PASS (usage runtime IPC pending)
```
D43 passes before/after restart; D41/D42 and previous consumer proofs, actual
D22/D24 HTTPS streams PASS. Egress/accounting forward/reverse dry-run/apply/
repeat, all 6+8+3 crash boundaries and hostile-input refusals PASS. This is not
full role migration/two-pass deletion or an actual old-image rollback.
Seven entry capabilities and compose security options unchanged; synthetic
network ta-uid-stream-68aa64c6d193-net and public CA cleaned by harness.

Targeted Ruff, plugin build/import, mirror parity (604), strict OpenSpec and
whitespace pass. Full python -m ruff check still reports 55 pre-existing errors
outside touched files. Windows test_request_usage_store: 35 passed, 1 fails
because the existing fd-leak test unconditionally opens /proc/self/fd; the same
test passes on Linux. No skip or failure is reported as acceptance. The existing
path-I/O ratchet failure in broker/supervisor.py::_protect_daemon remains as
recorded under D42. No guard/test was weakened and no history rewritten.

## Next work, in order

1. Accounting runtime create/reserve/receipt/settle IPC, daemon-owned lock
   creation and migration modes for broker read access, and daily evidence.
   UsageStore still opens .tinyassets.db at runtime; do not activate the split.
   Source binding's daemon grant read is now routed, but broker-local installed
   definition access and complete inference POST acceptance remain unproven.
2. Refresh: preserve admission before spending single-use tokens and daemon-only
   vault writes. No broker vault-write privilege may be introduced.
3. Every actual engine class through the launcher and per-class denial/positive
   controls, including daemon planted-link/FIFO/hardlink readers and fd closure.
4. Full role migration/ACLs and capability-free D10 two-pass deletion.
5. Actual old-image rollback, then startup and healthcheck only after every
   preceding proof passes. Existing storage reverse/old-uid proof is insufficient.

---

# Current delivery: D42 read-only kernel liveness

D41 pushed as 4f8636e8cd; hygiene added 5 tests, removed 0, tampering 0.
D42 makes POSIX owner_state use read-only pinned/no-follow proof descriptors
through universe_files. Only actual lock contention means ALIVE; missing,
hostile, replaced or errored proofs are UNKNOWN. Windows adapter unchanged.
Cross-family peer-agents: AGREE, APPROVE; no floor/correctness findings.
Final filesystem opening moved into the existing safe I/O helper after review;
root ancestry now also uses workspace_fs's component-by-component no-follow walk.
No permissions widened, startup inactive, no complete build task checked off.

Release-critical files: **0; none**. Runtime process_liveness.py and
universe_files.py plus mirrors; role_image_oracle.py, new
 test_broker_readonly_liveness.py, design/inventory/delivery. No affected heavy file.
D43 source-binding decision is recorded; its implementation is next/in progress.

```text
python scripts/linux_oracle.py -- tests/test_broker_readonly_liveness.py tests/test_request_usage_store.py tests/test_automation_lease_dead_holder.py tests/test_universe_seats.py tests/test_universe_file_reads_are_bounded.py -q -rs
128 passed in 14.62s; zero skips
python scripts/linux_oracle.py --production-image tinyassets-uid-liveness:d42 --build --production-stream
exit 0; sha256:4ee66e63dbee172886e61fe4f9848516ddba1d239e05c0979d0ce40f561ec62a
D42 broker read-only daemon/parent kernel liveness, independent parent close, daemon death, engine denial: PASS (runtime accounting IPC pending)
```
All prior migration dry-run/apply/repeat/reverse, 6+8+3 crash boundaries,
launcher and D41 consumers, D22/D24 real HTTPS streams PASS. Synthetic
network ta-uid-stream-381619cdce38-net and CA cleaned. No full migration,
two-pass deletion or actual old-image proof is claimed. Production uses the
same seven entry capabilities and compose security options.

Targeted Ruff and plugin build/import pass. One attempted test command named
nonexistent test_universe_files.py; corrected to test_universe_file_reads_are_bounded.
An instrumented os.open before filesystem-module import invalidated its POSIX
feature detection; pre-importing the module corrected the fixture, with all 128
passing afterward. No test/guard weakened. Path-I/O guard: 3 pass, 1 fails on
pre-existing broker/supervisor.py::_protect_daemon's /proc/self/status read;
D42 introduces no remaining ratchet finding. This pre-existing failure remains
an activation prerequisite, not a passed check.

Remaining: accounting runtime IPC and lock-creation/migration modes, source
binding (D43 in progress), daily evidence and refresh; actual engine classes;
full migration/two-pass deletion; actual old-image rollback; startup/healthcheck
only after every prerequisite passes.

---

# Current delivery: D41 injected connection authority

D41 finishes the interrupted injected cloud/effect slice. Canonical immutable
broker authority supplies admitted principal/center; grant and redacted resource
are revalidated together. Selected mode rejects local ledgers and duck types.
Cap policy uses that same snapshot. No startup activation, PR or deployment.
No complete build task newly checked off. Cross-family peer-agents review:
AGREE, APPROVE; no floor/correctness findings.

Release-critical files: **0; none**. Five runtime files (broker/connection_authority,
cloud_automation_continuation, effectors/outbound_boundary,
storage/outbound_connections, user_owned_cloud_automation), their mirrors,
test_broker_injected_authority, role_launcher_oracle and design/inventory/delivery.
No affected heavy-list test file.

```text
python -m pytest tests/test_outbound_effect_boundary.py -q
19 passed in 4.76s
python scripts/linux_oracle.py -- tests/test_broker_injected_authority.py tests/test_user_owned_cloud_automation.py tests/test_cloud_automation_control.py tests/test_outbound_connection_ledger.py -q -rs
108 passed in 9.33s; zero skips
python scripts/linux_oracle.py --production-image tinyassets-uid-injected:d41 --build --production-stream
exit 0; sha256:b11527bfebdfb5d8c7929474e3f8d7f55ffdf3dec22785ba76521dd63da3ced8
D41 actual cloud authority and capped-effect hold via launcher broker: scoped snapshot, foreign refusal, no daemon ledger: PASS
```
D41 passes before/after restart; previous consumer probes and real D22/D24
HTTPS streams PASS. Forward/reverse egress and accounting dry-run/apply/repeat,
6+8+3 crash/recovery boundaries and hostile-input refusals PASS. Full role
migration, two-pass deletion and actual old-image rollback remain unproven.
Same seven entry capabilities and compose security options; synthetic HTTPS
network ta-uid-stream-f80e2b4960ec-net and matching public CA cleaned by harness.
Initial production probe failed because D41 ran before the fixture selected
broker mode; moved it after fixture configuration, then all probes passed.
No product guard relaxed. Targeted Ruff, plugin build/import, mirror parity
(604 canonical files), whitespace pass.

Continue with accounting runtime IPC, kernel liveness, source binding/daily
evidence and refresh; every actual engine class; full migration/two-pass
deletion; actual old-image rollback; startup/healthcheck only after all pass.

---

# Current delivery: D40 intent custody

D39 pushed as e1a413ba99; hygiene added 4 tests, removed 0, tampering 0.
D40 resolves reconciliation custody through broker authority using persisted
root-run owner and center; validates host, git-write scope and push consent.
Missing/foreign/revoked/outage defers without network or local ledger fallback.
No full build task complete, startup inactive. Cross-family review APPROVE;
AGREE, no floor/correctness findings. No PR or deployment.

Release-critical files: **0; none**. Runtime tinyassets/workspace_intents.py and
its mirror; test_broker_workspace_intents.py; role_launcher_oracle.py, design,
inventory and delivery. No affected heavy-list file.

```text
python -m pytest tests/test_workspace_intents.py -q
23 passed in 1.52s
python scripts/linux_oracle.py -- tests/test_broker_workspace_intents.py tests/test_workspace_intents.py -q -rs
31 passed in 3.44s, zero skips
python scripts/linux_oracle.py --production-image tinyassets-uid-intents:d40 --build --production-stream
exit 0; sha256:d5941f67eea12c43e6ed707127cff6a08b5cc61d6d653804cd6bd73b33b6ba51
D40 actual intent custody resolver via launcher broker: persisted run scope, foreign refusal, no daemon ledger: PASS (worker transport not claimed)
```
D40 passes before/after broker restart; all earlier consumer proofs and real
D22/D24 HTTPS streams PASS. Egress/accounting dry-run/apply/repeat/reverse,
6+8+3 crash boundaries and refusal rows PASS. Same seven-capability entry and
compose security options; internal synthetic fixture network
 ta-uid-stream-694c53c8d43f-net and matching public CA, cleaned by harness.
Ruff, plugin build/import, strict OpenSpec and whitespace pass. No schema or
privilege changes. Revoked scope/consent or missing legacy authority deliberately
leaves the intent owed; this does not claim worker/credential transport acceptance.

Continue: injected cloud/effect consumers (D41 in progress), accounting runtime
and refresh, every actual engine class, full migration/two-pass deletion, actual
old-image rollback, then startup/healthcheck only after all probes pass.

---

# Current delivery: D39 workspace authority reads

D39 carries immutable run owner/center through compiler effect dispatch into
workspace admission and push mount revalidation using AUTHORIZED_CONNECTION.
Packets cannot supply principal. Missing/foreign/revoked/outage refuses without
a daemon ledger. No whole build task complete; startup inactive, no PR/deploy.
Cross-family peer-agents review: APPROVE; AGREE, no floor/correctness findings.
Older runs lacking explicit persisted center/owner now refuse workspace effects;
no scope is inferred to keep such rows running.

Release-critical files: **0; none**. Runtime: tinyassets/effectors/__init__.py,
tinyassets/effectors/workspace.py, tinyassets/graph_compiler.py and three mirrors.
Tests: test_broker_workspace_consumers.py (6 cases), workspace_effector,
effects_at_node_time, and affected heavy test_branch_runner. Oracle:
scripts/role_launcher_oracle.py. Design, inventory and delivery updated.

Verification:
```text
python -m pytest tests/test_workspace_effector.py tests/test_effects_at_node_time.py -q
210 passed, 2 skipped in 26.59s
python -m pytest tests/test_branch_runner.py -q
49 passed in 57.04s
python scripts/linux_oracle.py -- tests/test_broker_workspace_consumers.py tests/test_workspace_effector.py tests/test_effects_at_node_time.py tests/test_branch_runner.py -q -rs
265 passed, 2 skipped in 51.91s
python scripts/linux_oracle.py --production-image tinyassets-uid-workspace:d39 --build --production-stream
python scripts/linux_oracle.py --production-image tinyassets-uid-workspace:d39 --production-stream
exit 0; sha256:01e9e68d1620bf713861f386b800b6eeacbe4462017d1984eca4e95a73ba28bb
D39 actual compiler/workspace admission and mount revalidation via launcher broker: trusted owner, foreign refusal, no daemon ledger: PASS
```
D39 passes before/after broker restart. Existing D22/D24 real HTTPS streams,
egress forward/reverse dry-run/apply/repeat, 6 egress crash boundaries, 8
accounting boundaries and 3 reverse recovery boundaries PASS. This is not full
role migration/deletion, actual worker transport, or old-image acceptance.
Seven planned capabilities and compose security options; synthetic internal
HTTPS network ta-uid-stream-655059f531b7-net and public CA, cleaned by harness.
Linux skips are the two Windows-only workspace rejection cases (lines 2689,
2700), not missing Linux probes. Initial no-identity test inherited suite identity;
explicit identity_context(None) fixed fixture; no guard weakened. Initial build
PowerShell stderr redirection reported status 1 despite all probe PASS; repeated
production command without redirection exits 0. Ruff, plugin build/import,
mirror parity (603), strict OpenSpec and whitespace pass.

Continue in order: intent custody/reconciliation and injected cloud automation;
accounting IPC/liveness/source/daily evidence and refresh; every engine class;
full migration/two-pass deletion; actual old-image rollback; startup/healthcheck
only after all acceptance passes. D40 intent work is in progress this turn.

---

# Current delivery: D38 owner metadata consumers

D37 pushed as `3e80b1a91b`; hygiene tests added 7, removed 0, tampering 0.
D38 routes package previews and workspace consent host capture/answer through
owner-only broker metadata. Name pages hold at most 64 rows; redacted views
contain no custody reference. Answer rechecks owner, revocation and host.
No whole build task newly complete. Startup inactive, no PR or deployment.

Release-critical files: **0; none**. Runtime:
`tinyassets/broker/{ledger_queries,owner_metadata}.py`,
`tinyassets/api/{package_requests,pending_requests}.py`, plus four generated
mirrors. Test `tests/test_broker_owner_metadata.py`; oracle
`scripts/role_launcher_oracle.py`; design, delivery, tasks status and inventory.
No affected heavy-list file.

```text
python -m pytest tests/test_workspace_authority.py tests/test_github_is_an_ordinary_connection.py tests/test_command_center_packages.py -q
181 passed, 1 skipped in 54.00s; Windows skip is not acceptance
python scripts/linux_oracle.py -- tests/test_broker_owner_metadata.py tests/test_workspace_authority.py tests/test_github_is_an_ordinary_connection.py tests/test_command_center_packages.py -q -rs
189 passed in 46.06s, zero skips
python scripts/linux_oracle.py -- tests/test_broker_ledger_queries.py -q -rs
12 passed in 0.54s, zero skips
python scripts/linux_oracle.py --production-image tinyassets-uid-metadata:d38 --build --production-stream
exit 0; sha256:5dc1f8096375f9904186705412f0d3bb5c359f37721313dcc88deb71e6adb2bb
D38 actual package connection-name preview via launcher broker: owner pages and foreign metadata absence: PASS
D38 actual workspace consent capture/answer via launcher broker: owner metadata and daemon consent write: PASS
```
D38 passes before/after broker restart. D33-D37 and all earlier consumer proofs,
D22 discovery and D24 effector real HTTPS streams PASS. Egress/accounting
forward/reverse dry-run, apply/repeat, all 6+8+3 crash/recovery boundaries and
hostile input refusals PASS. Same seven-capability entry and compose security
options; internal `ta-uid-stream-6f89ce96ae69-net`, corresponding public-CA volume,
93.184.216.0/29 client .3/server .2. Fixture resources cleaned.
Ruff, mirror parity (603 canonical), plugin build/import, strict OpenSpec,
whitespace and OpenSpec audit pass. Cross-family review APPROVE; **AGREE**, no
floor/correctness findings. No skip is treated as a pass.

D38 preserves owner-only metadata semantics; a live grant remains independently
required by egress authorization. Package names now include all pages instead of
the previous informational 500-row cap. Package outage stays unknown (None),
not an invented empty catalog. Consent outage fails loudly. No new privilege,
security scope change, deployment, or startup admission.

## Remaining, in order

1. D11 workspace effector/intents and injected cloud-automation consumers.
   Workspace `_read_connection` needs the trusted execution principal carried
   through both initial admission and mount revalidation; never derive it from
   an untrusted packet. Workspace intent custody fallback likewise needs admitted
   scope. Replace concrete-ledger coupling with a closed trusted IPC interface.
2. Accounting runtime IPC and refresh. `UsageStore` still opens `.tinyassets.db`;
   D29 only transferred the tables offline. Preserve per-parent kernel liveness:
   `owner_state` currently opens lock files O_RDWR; broker needs a safe read-only
   proof, not broad write access. Source-definition checks and daily evidence
   also need routes. Refresh must retain admission before spending single-use
   tokens and daemon-only vault writes.
3. Every actual engine class through the launcher (identity-only denials do not
   count), then full role migration/ACLs and D10 two-pass deletion.
4. Actual old-image rollback. Current evidence proves reverse storage migration
   and uid-1001 writes at the old location, not an old image boot.
5. Startup/healthcheck integration only after all preceding probes pass.

The turn continued through five verified commits (D33, D34/D35, D36, D37, D38),
not just one decision slice. Build tasks 2.1-2.8 remain unchecked because their
full acceptance conditions have not been satisfied. No PR was opened.

---

# Current delivery: D37 HTTP connect and redeposit

D36 pushed as `688a3e91f1`; hygiene tests added 6, removed 0, tampering 0.
D37 routes connect/redeposit through broker prepare/commit around daemon-only
vault writes. Both connection and grant rows plus the requested policy are
compared in the commit transaction. Fresh, repeated, additive and legacy-scope
upgrade deposits are covered; no secrets travel in this new protocol.
No whole build task newly complete. Startup inactive, no PR or deployment.

Release-critical files: **0; none**. Runtime:
`tinyassets/broker/{http_connect,client,server}.py`,
`tinyassets/api/http_connection.py`, `tinyassets/storage/outbound_connections.py`,
plus five generated mirrors. Test `tests/test_broker_http_connect.py`; oracle
`scripts/role_launcher_oracle.py`. No affected heavy-list file.

```text
python -m pytest tests/test_http_connection_provisioning.py tests/test_outbound_http_connection.py -q
69 passed in 5.25s
python scripts/linux_oracle.py -- tests/test_broker_http_connect.py tests/test_http_connection_provisioning.py tests/test_outbound_http_connection.py -q -rs
78 passed, 1 new test failed: test wrongly assumed exact->full redeposit bypasses existing scope conflict
python scripts/linux_oracle.py -- tests/test_broker_http_connect.py -q -rs
10 passed in 2.47s, zero skips (after correcting that assumption and checking stored mode rather than nonexistent projection field)
python scripts/linux_oracle.py --production-image tinyassets-uid-connect:d37 --build --production-stream
exit 0; final image ID sha256:9ac3ee9edf41fac9c5371b0f0cd084cdfb16166b752f527c55ef3ece0f492281
D37 actual HTTP connect/redeposit via launcher broker: prepare/commit, fresh/repeat/additive, daemon-only vault and no daemon ledger: PASS
```
D37 passes before/after broker restart with actual vault content and daemon private
ledger denial assertions. Previous consumers, D22/D24 HTTPS, egress/accounting
forward/reverse and all 6+8+3 crash/recovery boundaries PASS. Same seven-capability
entry, internal synthetic HTTPS fixture and security options; fixture resources
cleaned. Ruff, mirror parity (602 canonical), plugin import, strict OpenSpec and
whitespace checks pass. An initial command named a nonexistent extension test
file and ran no tests; the corrected targeted commands above ran successfully.

Cross-family review APPROVE; **AGREE** on no floor/correctness findings and on
adding explicit custody/private-path oracle assertions. **DISAGREE_EVIDENCE**
on the suggested missing restart test: `_query_consumers` calls the consumer and
runs both before and after restart (oracle lines 640 and 702 after added assertions);
the output contains both D37 PASS rows. Broker policy errors not caught at the
API door remain credential-blind refusals. As before, vault and ledger are not
one atomic store: a failed commit is reported and needs a fresh gesture.

Remaining in order: owner metadata/injected D11 consumers; accounting runtime
and refresh; every actual engine class through launcher; full role migration
and two-pass deletion; actual old-image rollback; startup/healthcheck only after
all prerequisites pass. Existing rollback probes cover storage reversal and
old-location uid-1001 reads/writes, **not an actual old image**.

---

# Current delivery: D36 HTTP endpoint and access-mode mutation

D34/D35 pushed as `4b5c33e09b`; hygiene: tests added 5, removed 0, tampering 0.
D36 routes extension preview and mutation through broker IPC, checks the live
owner/grant/center and complete policy/incarnation in the mutation transaction,
and enforces additive endpoint/scope changes. Existing ledger validators run
inside that same transaction. Cross-family review APPROVE; **AGREE**, no floor
findings. No whole build task newly complete; startup remains inactive.

Release-critical files: **0; none**. Canonical runtime:
`tinyassets/broker/{http_policy,client,server}.py`,
`tinyassets/api/http_connection.py`, `tinyassets/storage/outbound_connections.py`,
plus five generated mirrors. New test `tests/test_broker_http_policy.py` and
extended oracle `scripts/role_launcher_oracle.py`. No affected heavy-list file.

```text
python -m pytest tests/test_full_channel_access.py tests/test_http_redirect_approval.py tests/test_http_redirect_policy.py tests/test_request_rail_honest_asks.py tests/test_outbound_http_connection.py -q
185 passed in 15.56s
python scripts/linux_oracle.py -- tests/test_broker_http_policy.py tests/test_full_channel_access.py tests/test_http_redirect_approval.py tests/test_http_redirect_policy.py tests/test_request_rail_honest_asks.py -q -rs
162 passed in 32.06s; zero skips
python scripts/linux_oracle.py --production-image tinyassets-uid-policy:d36 --build --production-stream
exit 0; sha256:5ed9d87e271012d2ccf82788d0b09f7e8077867ed520b4e9f5fbc1f8031e9ce7
D36 actual HTTP endpoint and full-access extension via launcher broker: scoped mutation and stale CAS refusal, no daemon ledger: PASS
```
D36 passes before/after broker restart. All D33-D35 consumer rows, D22 discovery
HTTPS and D24 effector HTTPS PASS. Egress/accounting forward/reverse dry-run,
apply/repeat, 6+8 crash boundaries, 3 reverse recovery boundaries and hostile
input refusals PASS. Same seven-capability entry and compose security options;
internal `ta-uid-stream-d82efd666d2d-net`, corresponding public-CA volume,
93.184.216.0/29 client .3/server .2. Fixture resources cleaned.
Ruff, mirror parity (601 canonical files), plugin build/import, strict OpenSpec
and whitespace checks pass. Tests unchanged/strengthened, no skips counted.

Behavioral detail from review: selected mode rejects legacy full-access approvals
with empty incarnation instead of accepting an unbound snapshot. Broker-side
post-preview validation errors remain fixed, credential-blind refusals. No authority
is widened. Full role migration, owner-tree deletion, actual engine classes and
old-image rollback are still unproven. No PR or deployment.

Next: HTTP connect/redeposit, remaining D11 metadata/injected consumers;
accounting/source/liveness/daily evidence and refresh; every actual engine class;
full migration/two-pass deletion; actual old-image rollback; startup/healthcheck
integration only after all prerequisites pass. Continue in this run.

---

# Current delivery: D34 removal readers and D35 rotation

D33 pushed as `9c03ee5ad1`; hygiene: tests added 9, removed 0, tampering 0.
D34 captures removal-request incarnations and reads intentional-disconnect status
through scoped broker facts. D35 uses a single live authority/incarnation snapshot
for rotation while retaining daemon-only vault writes. Cross-family review:
APPROVE, **AGREE**; no floor findings. Added replacement-owner regression coverage
suggested by review. No whole build task newly complete; startup inactive.

Release-critical files: **0; none**. Canonical runtime:
`tinyassets/api/{pending_requests,http_connection}.py`,
`tinyassets/providers/connection_lifecycle.py`, and three generated mirrors.
Extended `tests/test_broker_disconnect.py` and `scripts/role_launcher_oracle.py`.
No affected heavy-list test. Existing tests/assertions preserved.

```text
python -m pytest tests/test_connection_lifecycle.py tests/test_replacing_a_rejected_credential.py tests/test_capability_url_connections.py -q
181 passed in 25.31s
python scripts/linux_oracle.py -- tests/test_broker_disconnect.py tests/test_connection_lifecycle.py tests/test_replacing_a_rejected_credential.py -q -rs
76 passed, 1 new test import error (nonexistent vault reader; corrected to byte comparison)
python scripts/linux_oracle.py -- tests/test_broker_disconnect.py -q -rs
17 passed in 4.16s; zero skips (final, including two replacement cases)
python scripts/linux_oracle.py --production-image tinyassets-uid-lifecycle:d35 --build --production-stream
exit 0; sha256:93691e143a672a9e2bfe5df25ae1fc2bcb71e98eb80a8365816e1ef2e9e77cc4
D35 actual HTTP rotation via launcher broker: live snapshot, owner vault write, ledger policy unchanged: PASS
D34 actual removal request capture and lifecycle status via launcher broker: PASS
```
Both new production rows pass before/after broker restart. D33 removal and D22/D24
HTTPS streams remain PASS. D34 stages the daemon's completed-model flag to test
the status reader; it does not claim full model setup/assignment activation.
D35 proves a real daemon vault write, not use of that rotated fixture by upstream.
Same seven-capability root entry and compose security options as D33; internal
synthetic HTTPS fixture, no real secrets or external requests. All prior egress
and accounting dry-run/repeat/reverse/crash/refusal rows remain PASS; no claim of
full role migration, two-pass deletion or actual old-image rollback.
Ruff, mirror parity, plugin build/import, strict OpenSpec and whitespace pass.

Next: endpoint/access-mode and connect mutations, other D11 injected consumers,
accounting/source/liveness/daily evidence and refresh; every engine class/site;
full migration and two-pass deletion; actual old-image rollback; startup and
healthcheck integration only after all prerequisites pass. No PR or deployment.

---

# Current delivery: D33 broker HTTP disconnect

D33 implements HTTP removal through authenticated broker inspect/fence/erase.
The daemon retains assignment admission and vault cleanup; each broker mutation
checks owner, deterministic center/destination and incarnation in its transaction.
Erasure requires revocation. Lost ACKs fail loudly; retry preserves denial.
Cross-family implementation review: APPROVE, **AGREE**, no floor findings.
No whole 2.1-2.8 task newly checked off; startup inactive, no PR or deployment.

Release-critical files: **0; none**. Canonical runtime files:
`tinyassets/broker/{disconnect,client,server}.py`,
`tinyassets/api/http_connection.py`, `tinyassets/providers/connection_lifecycle.py`,
plus their five generated mirrors. New tests: `tests/test_broker_disconnect.py`.
Oracle: `scripts/role_launcher_oracle.py`. No affected heavy-list file.

Verification:
```text
python -m pytest tests/test_http_connection_removal.py tests/test_connection_lifecycle.py -q
22 passed in 5.95s
python scripts/linux_oracle.py -- tests/test_broker_disconnect.py tests/test_http_connection_removal.py tests/test_connection_lifecycle.py -q -rs
33 passed in 11.21s; zero skips
python scripts/linux_oracle.py --production-image tinyassets-uid-disconnect:d33 --build --production-stream
exit 0; sha256:fcbe4a100e8e0aa1b7238439b9a8fe9cb26a448509f3767ab7abd5fe2d69cf5a
D33 actual HTTP disconnect via launcher broker: fence/erase/repeat, foreign/stale refusal, no daemon ledger: PASS
```
The D33 row passes before and after real broker restart. D22 discovery and D24
effector HTTPS streams still pass. Existing egress/accounting forward/reverse
dry-run/apply/repeat, 6 egress crash boundaries, 8 accounting boundaries and
3 reverse recovery boundaries all PASS; these are not full migration or actual
old-image rollback. Entry uses exactly seven planned capabilities and compose
security options; private internal fixture network `ta-uid-stream-b2eb3abf7c1a-net`,
public CA volume with matching prefix, subnet 93.184.216.0/29, client .3/server .2.
Fixture resources cleaned by oracle. No external request or real credential.

Initial new test repeat hook incorrectly dereferenced the deleted row; corrected
fixture, no product guard changed. Initial production run passed D33 and streams
but failed the unchanged exact FD-count assertion: cyclic SQLite fixture handles
were collected after baseline. Collect setup handles before fork; exact assertion
retained with diagnostic output. Final full run passes. Ruff, mirror parity
(600 files), plugin build/import, strict OpenSpec and whitespace checks pass.

Remaining: removal request capture/lifecycle readers; other D11 mutations/injected
consumers, accounting/source/liveness/daily evidence and refresh; every engine
class/site; full migration/two-pass deletion; actual old-image rollback;
startup/healthcheck only after all prerequisites pass. Continue in this run.

---

# Current delivery: D31 bootstrap readers and D32 graph inventory

D29-D30 are committed/pushed as `dbd7669f10`; hygiene against `0f54d41cfe`:
`tests added 6, removed 0, tampering findings 0, product lines added 489`.
Continued in the same run through D31 and D32. Both received separate cross-family
implementation APPROVE reviews; **AGREE**. No scope/privilege change, startup
activation, PR or deployment. No whole build task newly checked off.

D31 preserves inert pending-confirmation display for revoked own connections
through BOOTSTRAP_RECOVERY, while candidate reads still require live broker
grant/capability authority. D32 routes graph connection inventory and its uses
metadata through catalog pages and reauthorized capability reads. Broker outage
and revocation between page and detail never fall back to a daemon ledger.

Release-critical files for this step: **0; list: none**. Runtime files:
`tinyassets/broker/ledger_queries.py`,
`tinyassets/onboarding/{model_bootstrap,model_bootstrap_candidate}.py`,
`tinyassets/api/{cloud_connections,connection_uses}.py`, and their five generated
mirrors. Oracle: `scripts/role_launcher_oracle.py`. New test files:
`tests/test_broker_bootstrap_ipc.py`, `tests/test_broker_graph_connections.py`.
No affected heavy-list file. Existing test names/assertions remain unchanged.

Verification:

```text
python -m pytest tests/test_model_bootstrap.py tests/test_model_bootstrap_candidate.py tests/test_unify_connection_uses.py -q
47 passed in 31.09s
python scripts/linux_oracle.py -- tests/test_broker_bootstrap_ipc.py tests/test_model_bootstrap.py tests/test_model_bootstrap_candidate.py tests/test_role_accounting_schema.py -q -rs
25 passed, 9 fixture errors (missing expected_grant in new setup; corrected)
python scripts/linux_oracle.py -- tests/test_broker_bootstrap_ipc.py tests/test_broker_graph_connections.py tests/test_unify_connection_uses.py -q -rs
42 passed in 19.13s; zero skips
python scripts/linux_oracle.py --production-image tinyassets-uid-consumers:d32 --production-stream
exit 0
python scripts/linux_oracle.py --production-image tinyassets-uid-consumers:d32
exit 0
```

Final image: `sha256:4adb5e3ea6825e2263bb2b0a46ee71a4197e6bb62a8bddd5df18415c13af9178`.
Built with `--production-stream --build`. Same root entry, seven capabilities,
no-new-privileges and compose security options as D29. Final stream fixture:
`ta-uid-stream-6165a5c544b0-net` (internal), public-CA volume
`ta-uid-stream-6165a5c544b0-ca`, subnet `93.184.216.0/29`, client `.3`, synthetic
`uid-stream.invalid` at `.2`; fixture resources cleaned after the command.

New production output, each before and after broker restart:

```text
D31 actual bootstrap candidate and pending-confirmation consumers through launcher broker: scoped metadata, foreign refusal, no daemon ledger: PASS
D32 actual graph connection inventory via launcher broker: 71 scoped rows with capability metadata, no daemon ledger: PASS
```

D22 discovery HTTPS GET and D24 effector HTTPS stream remain PASS, including vault
bearer, verified TLS and actual body. All D29 accounting forward/reverse dry-run,
apply, repeat, eight accounting crash boundaries, three accounting-to-egress
reverse recovery boundaries, six existing relocation boundaries and hostile
input refusals remain PASS. D31's candidate proof covers the existing-definition
read branch, not a new full bootstrap acquisition/activation. Production engine
classes, inference POST, full role migration, two-pass owner deletion and actual
old-image rollback remain unproven.

Ruff for changed files, mirror parity (599 canonical files), plugin build/import
probe, strict OpenSpec validation and whitespace checks pass. One Linux retry
refused an inconsistent tar snapshot while the next slice was being edited;
reran against a stable tree. A new graph fixture initially expected an outer
not_found; corrected it to explicitly grant a foreign actor center-read access
and assert an empty broker-scoped connection catalog. No product guard changed.
PowerShell `*>` logging marked native stderr as NativeCommandError despite PASS
output; both production commands were rerun directly and exited 0.

Next, keep the prescribed order: remaining D11 mutation/injected-ledger consumers,
runtime accounting with source/liveness checks and daily evidence, refresh;
then every actual engine class/site via launcher; full migration and two-pass
deletion; actual old-image rollback; startup/healthcheck integration only after
every prerequisite passes. The inventory now records D29-D32 receipts and the
concrete UsageStore/liveness dependency trace. Startup remains unactivated.

---

# Previous delivery: D29 accounting transfer and D30 source-budget IPC

Resumed from `0f54d41cfefae44a205d3980ce3ad521683f00ee`; fast-forward pull
was current. Finished inherited accounting WIP, then continued directly into
source-budget IPC and D31 bootstrap readers (D31 still in progress at this receipt).
No startup activation, PR or deployment; no whole task 2.1-2.8 newly checked off.

D29 transfers the four accounting tables offline with committed-copy verification
before source DROP, preserving unrelated daemon tables. Both directions support
nonmutating dry-run, repeat and crash recovery. **AGREE** with cross-family review
findings: reject case-insensitive reserved schema-name/index collisions before
mutation, and permit rollback to resume a reverse egress move after accounting
reverse is stable. Both fixes have new regression probes. D30 reuses existing
scoped GRANTED_RESOURCE IPC for source-budget classification; **AGREE** with its
separate cross-family APPROVE. Broker outage/refusal cannot become unmetered
admission; advisory rendering may report unknown. No new privileges.

Release-critical files for this combined verified step: **1**:
`deploy/role_egress_migration.py`. Other code: `tinyassets/request_budget.py` and
its generated mirror; oracles `scripts/role_image_oracle.py` and
`scripts/role_launcher_oracle.py`; new tests `test_role_accounting_schema.py`
and `test_broker_source_budget.py`. No affected heavy-list file. Existing tests
were not removed, renamed or weakened. D31 design is recorded but its code is
excluded from this verified step.

Commands/results:

```text
python scripts/linux_oracle.py -- tests/test_role_accounting_schema.py -q -rs
4 passed in 0.14s (before review regression additions)
python -m pytest tests/test_role_accounting_schema.py -q
7 passed in 0.33s (after review fixes)
python scripts/linux_oracle.py -- tests/test_broker_source_budget.py tests/test_request_budget.py tests/test_parent_turn_request_budget.py tests/test_turn_request_economy.py -q -rs
126 passed in 62.28s; zero skips
python scripts/linux_oracle.py --production-image tinyassets-uid-accounting:d29 --build
exit 0; sha256:e1152092bc805714e9af2338ff68878646b82064143a58e400bfb84e78125449
```

Production output: D29 forward/reverse dry-run/apply/repeat and broker writes
PASS; eight accounting abrupt-exit boundaries PASS; three accounting-to-egress
reverse recovery boundaries PASS; link/FIFO/conflict/schema/diverged-copy refusal
without mutation PASS. Existing six egress interruption boundaries and denial
probes PASS. D30 actual source-budget consumer succeeds through launcher broker
and rejects missing/foreign authority before and after broker restart, with no
daemon ledger. Same root entry, seven capabilities, no-new-privileges and compose
security options as D28; no network. Ruff for changed files, plugin build/import
probe, OpenSpec strict validation pass. Initial schema parity failure was index
SQL whitespace; matched runtime declaration exactly without loosening assertion.

Remaining in order: D11 remaining reads/mutations, runtime accounting and kernel
liveness/source checks, refresh; every actual engine class/site; full migration
and two-pass deletion; actual old-image rollback; startup/healthcheck only after
all pass. Successful HTTPS GET broker stream is previously proven (D22/D28);
inference POST/accounting remains unproven. Accounting table DROP here is not
the owner-workspace two-pass deletion proof. Startup remains unactivated.

---

# Previous delivery: D28 bounded scoped catalogs

D27 was committed/pushed as `66ec595bca`; its hygiene command against
`90d7186cfe` returned `tests added 10, removed 0, tampering findings 0,
product lines added 590`. Continued directly into D28, without ending the run.
D28 routes the daemon's `ta` capability catalog, account connection GET and
command-center summary through redacted scoped broker pages. Unlimited consumers
iterate; explicit limits 100 and 21 remain. No credential reference is projected.
One cross-family implementation review returned APPROVE; **AGREE**. No startup
activation, PR, deployment, new privilege or security-scope change.

Release-critical files: **0; list: none**. Runtime paths:
`tinyassets/broker/{catalog,client,server}.py`, `tinyassets/ta_capabilities.py`,
`tinyassets/onboarding/connections.py`, `tinyassets/universe_tools.py`, and their
six generated mirrors. Oracle: `scripts/role_launcher_oracle.py`. New tests:
`tests/test_broker_catalog.py`, `tests/test_broker_catalog_ipc.py`.
No existing test names/assertions changed; no affected heavy-list file found.

Windows: `tests/test_ta_capabilities.py tests/test_app_connection_controls.py`
returned **66 passed**; catalog/lifecycle tests returned **18 passed**.
One initial new unit fixture referenced nonexistent `.db_path`; fixed to the
existing ledger path field. No product guard changed for that test correction.
Linux commands:

```text
python scripts/linux_oracle.py -- tests/test_broker_catalog.py tests/test_broker_catalog_ipc.py tests/test_ta_capabilities.py tests/test_ta_capabilities_jail.py tests/test_connection_lifecycle.py tests/test_turn_request_economy.py -q -rs
96 passed in 47.43s
python scripts/linux_oracle.py -- tests/test_broker_catalog_ipc.py -q -rs
10 passed in 1.14s
```

Zero skips. The final IPC run includes actual app GET and summary consumers,
malformed projection, cursor/field bounds, stale/unavailable broker, multi-page
redaction and revocation between pages. Existing jail regression is not new
engine-through-launcher acceptance. Ruff for changed files, mirror parity
(599 canonical files), strict OpenSpec validation and whitespace checks pass.

Production image `sha256:e7318093c24c90c6261000dce9508d9d9abbe4486a52b2529bb5f3a3f991c767`:

```text
python scripts/linux_oracle.py --production-image tinyassets-uid-consumers:d28 --production-stream --build
python scripts/linux_oracle.py --production-image tinyassets-uid-consumers:d28
python scripts/linux_oracle.py --production-image tinyassets-uid-consumers:d28 --production-stream
```

All exit 0, zero skips. Same root entry argv, capability set and security options
as D27 below. Final HTTPS fixture network `ta-uid-stream-61abea346cd7-net`, public
CA volume `ta-uid-stream-61abea346cd7-ca`; same internal subnet/IP/host/env settings
as D27, cleaned after proof. New output before and after actual broker restart:

```text
D28 actual capability catalog via launcher broker: 71 grants over bounded pages, redaction, foreign/fence refusal, no daemon ledger: PASS
```

D27 mutation and prior real HTTPS stream/lifecycle/denial probes also pass.
Egress relocation dry-run/apply/repeat, reverse dry-run/apply/repeat, all six
abrupt-exit recovery boundaries and link/FIFO/conflict refusal remain PASS.
No full migration, two-pass deletion or actual old-image proof is claimed.
Remaining order: D11 reads/mutations/accounting/refresh, every engine class/site
through launcher, full migration/deletion, old-image rollback, startup/healthcheck.
No whole task 2.1-2.8 checked off. Startup remains unactivated.

---

# Current delivery: D27 capability metadata and voice consumers

Resumed at `90d7186cfe8e2587361b9add7e8b363842670bb8`; requested fast-forward
pull was current. D10-D26 retained. D27 adds bounded CAPABILITY read/configure
IPC and CONNECTION_GRANTS lookup, routes connection-use configuration, provider
capability configuration and voice binding/proxy acquisition, and revalidates
every capability kind's live grant inside the actual read/write transaction.
No startup activation, PR or deployment. No whole build task newly completed.

Release-critical files: **0; list: none**. Runtime files are
`tinyassets/broker/{capabilities,client,server,ledger_queries}.py`,
`tinyassets/api/{connection_uses,provider_capability}.py`,
`tinyassets/onboarding/realtime_voice.py`, `tinyassets/storage/outbound_connections.py`
and their eight generated mirrors. Oracle: `scripts/role_launcher_oracle.py`.
New tests: `tests/test_broker_capabilities.py`, `tests/test_broker_capability_ipc.py`.
No existing test names/assertions changed. No affected heavy-list file identified.

Cross-family implementation review via peer-agents: ADAPT, no floor finding.
**AGREE** on typed endpoint/lookup/pricing errors, revocation-race not-found,
distinct voice broker-outage code and explicit remaining-reader inventory.
Implemented fixed wire classes without exposing persisted values. D27 design
record was added while review ran, before commit; no extra design review.
Lost mutation acknowledgements remain unavailable, never automatically replayed.

Verification: Windows consumer/baseline tests **123 passed**; initial Linux
set **130 passed**, zero skips. The expanded Linux command including broker-server
and outbound-ledger regression returned **177 passed, 1 failed** (the new pricing
fixture lacked POST). After fixture correction, final
`python scripts/linux_oracle.py -- tests/test_broker_capability_ipc.py -q -rs`
returned **10 passed in 0.80s**, zero skips. The expanded command was:
`python scripts/linux_oracle.py -- tests/test_broker_capabilities.py tests/test_broker_capability_ipc.py tests/test_broker_server.py tests/test_outbound_connection_ledger.py tests/test_unify_connection_uses.py tests/test_provider_capability_api.py tests/test_model_discovery_capability.py tests/test_realtime_voice.py -q -rs`.
Changed-file Ruff, mirror parity (598 canonical
files), strict OpenSpec validation and whitespace checks pass. Whole-repository
Ruff retains 55 errors in unchanged files. Plugin build initially hit WinError 5
on its atomic staging rename, restored its old tree, then passed on retry.
One Linux command named a nonexistent test file and is not counted. New error
tests first used a protocol-invalid URL and a GET-only fixture; corrected the
fixtures to reach the intended endpoint and pricing checks, without changing guards.

Final runtime image `sha256:8d602371c5cea04d82c2d4be96e800b27d5fc2625cd6be8f2d45204e455e1f93`:

```text
python scripts/linux_oracle.py --production-image tinyassets-uid-consumers:d27 --production-stream --build
python scripts/linux_oracle.py --production-image tinyassets-uid-consumers:d27
```

Both exit 0, zero skips. Entry `/opt/venv/bin/python -I -B /app/scripts/role_image_oracle.py`,
uid 0, cap-drop ALL plus CHOWN/DAC_OVERRIDE/FOWNER/SETUID/SETGID/SETPCAP/KILL,
no-new-privileges and seccomp/AppArmor/systempaths unconfined. Default mode has
network none, no mounts/env overrides. HTTPS fixture uses disposable internal
network `ta-uid-stream-dd71ff7ca632-net`, oracle .3 and fixture .2 in 93.184.216.0/29,
read-only public-CA volume `ta-uid-stream-dd71ff7ca632-ca`, uid-stream.invalid mapping,
TA_ORACLE_HTTPS=1 and the existing outbound HTTP opt-in. Fixture cleanup completed.

New output before/after actual launcher broker restart in both modes:

```text
D27 actual connection-uses capability mutation via launcher broker: configure/read/disable, foreign/fence refusal, no daemon ledger: PASS
```

Existing real HTTPS discovery/effector streams, broker private-ledger writes,
role identity/capabilities, fence/peer/fd checks and launcher lifecycle pass.
Migration substep outputs remain:

```text
forward dry-run, apply, repeat; service remains unadmitted: PASS
reverse dry-run/apply/repeat and uid-1001 old-location writes: PASS
forward/reverse abrupt-exit checkpoint and rename recovery: PASS (6 boundaries)
symlink/hardlink/FIFO/conflicting-copy refusal without mutation: PASS
```

These remain egress-only relocation proofs, not full role migration, two-pass
deletion or actual old-image rollback. Next: remaining catalog/read/mutation
consumers, accounting tables and authoritative liveness, refresh, then every
actual engine class/site, full migration/deletion/rollback and startup/healthcheck.
Startup remains unactivated until all prerequisites are complete and verified.

---

# Current delivery: D26 HTTP compute broker consumers

Implementation committed and pushed as `4cec6e6fc9187e616f344c052bd32e375b2dfd9b`.
Commit hooks passed and the worktree was clean after the push. Final hygiene:
`python scripts/test_hygiene_gate.py --base 2df167974443c8cc3fb0b5e309d6f8b80d0dc0b3 --head 4cec6e6fc9187e616f344c052bd32e375b2dfd9b`
returned `tests added 13, removed 0, tampering findings 0, product lines added 206`.
The new tests execute 20 cases; the hygiene counts above are its own counters.
This receipt-only follow-up changes no runtime and needs no image rebuild.

Resumed from `2df167974443c8cc3fb0b5e309d6f8b80d0dc0b3`; the requested
`git pull --ff-only origin feat/per-role-uid-split` was already current.
D10-D25 retained, including the existing D12 relocation decision. D26 routes
HTTP compute source reads and exact proxy acquisition through scoped broker IPC.
The router clears caller-provided principal data and supplies the independently
admitted serving/work owner. Missing/foreign/revoked authority, malformed replies
and broker outages never construct a daemon ledger. Access mode, usage-reference
forwarding and proxy cleanup are preserved. No new privilege or security scope.

Release-critical files: **zero** (cap 8; list: none). Runtime files:
`tinyassets/providers/api_key_http_provider.py`, `tinyassets/providers/base.py`,
`tinyassets/providers/router.py` and their three generated mirrors. Oracle:
`scripts/role_launcher_oracle.py`. New tests: `tests/test_broker_compute_consumers.py`
and `tests/test_broker_compute_ipc.py`. Existing test files/names/assertions are
unchanged. Explicit staged paths only. No PR or deployment.

One cross-family implementation review via peer-agents returned ADAPT with no
cross-user/authority findings and one pre-dispatch error-classification finding:
**AGREE**. Query/acquisition failures now become ProviderUnavailableError before
any request, allowing unused served reservations to be released. A real-router
regression proves release; request-time transport errors remain conservative and
close the proxy. No second review round. The initial router test assumed no parent
budget; the current router automatically supplies one, so the final test exercises
that actual path instead. No product accounting guard was loosened.

## D26 verification receipt

Final production image:
`sha256:bd774459b143954e8aa793241d6bd7663a7e611f4697259d54cec22b0aab1ed5`.
Built with `python scripts/linux_oracle.py --production-image tinyassets-uid-consumers:d26 --production-stream --build`.
The redirected PowerShell build invocation recorded NativeCommandError for Docker's
stderr and returned shell exit 1 despite completed image/probe output. It is not
counted as a clean acceptance command. Both final unredirected commands returned
exit 0, zero skips, against that exact rebuilt image:

```text
python scripts/linux_oracle.py --production-image tinyassets-uid-consumers:d26 --production-stream
python scripts/linux_oracle.py --production-image tinyassets-uid-consumers:d26
```

Entry: `/opt/venv/bin/python -I -B /app/scripts/role_image_oracle.py`, uid 0,
cap-drop ALL plus CHOWN/DAC_OVERRIDE/FOWNER/SETUID/SETGID/SETPCAP/KILL,
no-new-privileges and seccomp/AppArmor/systempaths unconfined. Network-none run
has no mounts or env overrides. HTTPS run uses internal network
`ta-uid-stream-5475b770744a-net`, oracle .3 and fixture .2 in 93.184.216.0/29,
uid-stream.invalid host mapping, read-only public certificate volume
`ta-uid-stream-5475b770744a-ca`, TA_ORACLE_HTTPS=1 and existing HTTP opt-in=1.
The runner cleaned up its fixture resources. New output before and after restart:

```text
D26 actual HTTP compute source/proxy consumers via launcher broker: scoped reads/acquisition, foreign refusal, no daemon ledger: PASS (inference accounting/POST not claimed)
```

Existing D22/D24 real HTTPS GET streams passed, as did private-ledger broker writes,
daemon/engine-identity denials, chain/capability/fence/peer/fd/non-dumpability and
launcher lifecycle probes. Migration output in both final runs:

```text
forward dry-run, apply, repeat; service remains unadmitted: PASS
reverse dry-run/apply/repeat and uid-1001 old-location writes: PASS
forward/reverse abrupt-exit checkpoint and rename recovery: PASS (6 boundaries)
symlink/hardlink/FIFO/conflicting-copy refusal without mutation: PASS
```

These are egress-relocation proofs only. Full role migration, two-pass deletion,
actual old-image rollback and production inference POST are **not proven**.

Linux regression, uid 1001, Python 3.11.16, bwrap 0.12.0:
`python scripts/linux_oracle.py -- tests/test_broker_compute_consumers.py tests/test_broker_compute_ipc.py tests/test_api_key_http_provider.py tests/test_provider_served_router.py tests/test_provider_invocation_selection.py tests/test_provider_retry.py tests/test_provider_work_authority.py -q -rs`
returned `238 passed, 1 skipped in 25.86s`. The existing true-Codex integration
requires TINYASSETS_REAL_CODEX_TEST_UNIVERSE/SNAPSHOT; it is **not** a pass or
engine-class evidence. The affected heavy-listed retry/work-authority files ran.
Final focused command:
`python scripts/linux_oracle.py -- tests/test_broker_compute_consumers.py tests/test_broker_compute_ipc.py -q -rs`
returned `20 passed in 2.26s`, zero skips. IPC stream tests use a scripted upstream,
not production inference. Windows compute/baseline regression returned 52 passed;
after adding the router release proof, final new-file run returned 17 passed.

Changed-file Ruff, mirror build/import probe, parity (597 canonical files), strict
OpenSpec validation and whitespace checks pass. Whole-repository Ruff still has
55 pre-existing errors outside this diff. Final hygiene receipt is recorded above.

## Remaining and activation gate

D26 completes two more D11 consumer routes; no whole task 2.1-2.8 is checked off.
Still required: every actual engine class/site through the launcher and complete
daemon-reader matrix; remaining D11 mutations, accounting, refresh, deletion and
read consumers; trusted execution context for background graph effectors; full
role migration and D10 two-pass deletion; full role/ACL backup restoration and
actual old-image rollback; real daemon CMD/environment, compose capability parity
and healthchecks. Startup remains unactivated until every required probe passes.
Next HTTP-compute acceptance depends on migrating the existing accounting tables
and authoritative liveness checks into broker custody, not widening broker access
to daemon stores or omitting usage references. No new design stop is identified.

---

# Current delivery: D25 serving context and custody broker consumers

Implementation commits are pushed: `4c394ccaf9` (D24) and `af3c49f434` (D25).
Both passed commit hooks; working tree was clean after the second push.
Final D25 hygiene: `python scripts/test_hygiene_gate.py --base 4c394ccaf9 --head af3c49f434`
returned `tests added 0, removed 0, tampering findings 0, product lines added 87`.
Combined hygiene: `python scripts/test_hygiene_gate.py --base a39ffede4d --head af3c49f434`
returned `tests added 10, removed 0, tampering findings 0, product lines added 370`.
These are the gate's own counters; the new D25 pytest file executes 10 cases.
This receipt-only follow-up adds no runtime change and requires no image rebuild.

D24 is committed/pushed as `4c394ccaf9`. Its final hygiene command
`python scripts/test_hygiene_gate.py --base a39ffede4d --head 4c394ccaf9`
returned `tests added 10, removed 0, tampering findings 0, product lines added 283`.
D25 continues D11: serving-context and initial connection-id reads use existing
GRANTED_RESOURCE IPC with the independently admitted owner supplied at both
production call sites. Live grant/resource scope and subsequent custody-digest
revalidation remain enforced. No local fallback or new broker operation.

Release-critical files: **zero** (cap 8). Runtime files:
`tinyassets/provider_serving_binding.py`, `tinyassets/provider_assignment.py`,
and their two generated mirrors. Oracle: `scripts/role_launcher_oracle.py`.
New tests: `tests/test_broker_serving_consumers.py`. Decisions recorded as D25
in design.md. No existing tests renamed/weakened; explicit staged paths only.
One cross-family implementation review via peer-agents returned APPROVE;
**AGREE**. Review noted the existing provider-store write transaction remains
held during the bounded broker read; the broker reads its separate ledger.
No security/isolation scope or privilege changes, no PR and no deployment.

## D25 verification receipt

`python scripts/linux_oracle.py --production-image tinyassets-uid-consumers:d25 --production-stream --build`
and `python scripts/linux_oracle.py --production-image tinyassets-uid-consumers:d25`
both returned exit 0, zero skips. Image digest:
`sha256:9540845131af9bf2c26555d36a2974c626ed81dd2c8f958c959caad17bbbdb1b`.
Both use `/opt/venv/bin/python -I -B /app/scripts/role_image_oracle.py`, root,
cap-drop ALL plus CHOWN/DAC_OVERRIDE/FOWNER/SETUID/SETGID/SETPCAP/KILL,
no-new-privileges and seccomp/AppArmor/systempaths unconfined. Default run has
network none and no mounts/env overrides. HTTPS run uses internal network
`ta-uid-stream-ae9bb1873033-net`, oracle .3 and fixture .2 in 93.184.216.0/29,
uid-stream.invalid host mapping and read-only public-certificate volume
`ta-uid-stream-ae9bb1873033-ca`, TA_ORACLE_HTTPS=1 and existing HTTP opt-in=1.
Runner cleaned up its fixture resources.

Exact new output before and after broker restart in both modes:
```text
D25 actual serving context/id/custody via launcher broker: scoped reads, foreign refusal, no daemon ledger: PASS
```
D24's effector/bound-preview and D22/D24's real HTTPS probes also passed,
as did the unchanged chain, capabilities, non-dumpability, IPC refusal,
launcher supervision and broker restart probes. Migration output in both modes:
```text
forward dry-run, apply, repeat; service remains unadmitted: PASS
reverse dry-run/apply/repeat and uid-1001 old-location writes: PASS
forward/reverse abrupt-exit checkpoint and rename recovery: PASS (6 boundaries)
symlink/hardlink/FIFO/conflicting-copy refusal without mutation: PASS
```
These are still relocation-only results, not full-role deletion/old-image proof.

Windows regression command:
`python -m pytest tests/test_broker_serving_consumers.py tests/test_open_serving_bind.py tests/test_provider_serving_binding.py -q`
returned `29 passed in 4.35s` before adding the full bind/enable/reserve test.
Final `python -m pytest tests/test_broker_serving_consumers.py -q`: `10 passed in 2.23s`.
Linux uid 1001, Python 3.11.16, bwrap 0.12.0:
`python scripts/linux_oracle.py -- tests/test_broker_serving_consumers.py tests/test_open_serving_bind.py tests/test_provider_serving_binding.py tests/test_provider_assignment_manifest.py tests/test_provider_assignment_admission.py tests/test_served_authority_shared_chain.py tests/test_provider_work_authority.py tests/test_serving_manifest_publication.py -q`
returned `218 passed in 17.49s`, zero skips. After extending the positive
bind/enable test through actual authorize/reserve (fixing a test-only import),
`python scripts/linux_oracle.py -- tests/test_broker_serving_consumers.py -q`
returned `10 passed in 0.72s`, zero skips. No affected file is heavy-listed.
Mirror build: probe-ok; parity: all 597 canonical files matched. Changed-file
Ruff, strict OpenSpec validation and whitespace checks pass. The D24 whole-repo
Ruff run still has 55 pre-existing errors outside this work.

## Remaining and activation gate

D24 and D25 complete five more D11 consumer routes; no whole task 2.1-2.8 is
claimed complete. Still required: every actual engine class/site through the
launcher and complete daemon-reader matrix; remaining D11 ledger mutations,
accounting, refresh, deletion and read consumers; trusted execution context for
background graph effectors; full role migration and D10 two-pass deletion;
full role/ACL backup restoration and actual old-image rollback; real daemon
CMD/environment, compose capability parity and healthchecks. Startup remains
unactivated until every required probe passes. No deletion/actual old-image
result is claimed. Continue under the standing mechanical-decision rule.

---

# Current delivery: D24 effector and bound-preview broker consumers

Resumed from `a39ffede4d`; required pull was already current. Critically reviewed
and completed the seven staged D24 files from the interrupted run. D12-D23 retained.
D24 adds AUTHORIZED_CONNECTION for a single scoped grant/resource/incarnation
snapshot. Effector authority uses trusted execution or ambient identity. Bound
preview hashes that same snapshot; proxy acquisition rechecks authority and
preserves the resource access mode (fixed during this review). Selected mode
never opens a local ledger. No security scope or retained privilege change.

Release-critical files: **zero** (cap 8). Runtime files:
`tinyassets/bound_requests.py`, `tinyassets/broker/ledger_queries.py`,
`tinyassets/effectors/authenticated_external_call.py`, and their three generated
mirrors. Probe files: `scripts/role_launcher_oracle.py`,
`scripts/role_stream_oracle.py`. New tests: `tests/test_broker_effector_consumers.py`.
No existing tests renamed or weakened; explicit paths only. No PR or deployment.

One cross-family code review via peer-agents returned APPROVE; **AGREE**.
Its follow-up check is satisfied: broker server uses authorize_exact on the live
resource; CredentialBlindBroker enforces resource.access_mode in its scope check.
Its background graph observation remains an explicit D11 obligation:
`effectors/__init__.py::_authenticated_call_adapter` must thread trusted execution
context for identity-free scheduled runs. D24 refuses those calls rather than
inferring an owner from an untrusted packet or opening a daemon ledger.

## D24 verification receipt

Production, exit 0 and zero skips in both modes:
`python scripts/linux_oracle.py --production-image tinyassets-uid-consumers:d24 --production-stream --build`
and `python scripts/linux_oracle.py --production-image tinyassets-uid-consumers:d24`.
Image digest: `sha256:82e4233bec4d61ac39d90d6f4fe9b4e4289e82517850f3667f155b8a1f5fb743`.
Entry: `/opt/venv/bin/python -I -B /app/scripts/role_image_oracle.py`, root,
cap-drop ALL plus CHOWN/DAC_OVERRIDE/FOWNER/SETUID/SETGID/SETPCAP/KILL,
no-new-privileges, seccomp/AppArmor/systempaths unconfined as in the D22 receipt.
Default mode: network none, no mounts or environment overrides. HTTPS mode:
internal `ta-uid-stream-6b6bdad961f1-net`, oracle IP 93.184.216.3, fixture .2,
uid-stream.invalid host mapping, read-only public-certificate volume
`ta-uid-stream-6b6bdad961f1-ca`, TA_ORACLE_HTTPS=1 and existing HTTP opt-in=1.
Fixture resources cleaned up by the runner.

Exact new output, both before and after broker restart:
```text
D24 actual effector/bound-preview consumers via launcher broker: scoped snapshot, foreign refusal, no daemon ledger: PASS
D24 actual effector proxy HTTPS through launcher broker: vault bearer, verified TLS, real body, no daemon ledger: PASS
```
The second line is HTTPS mode only. Existing broker lifecycle, capabilities,
IPC refusal and D22 real HTTPS probes also pass. Migration remains relocation-only:
```text
forward dry-run, apply, repeat; service remains unadmitted: PASS
reverse dry-run/apply/repeat and uid-1001 old-location writes: PASS
forward/reverse abrupt-exit checkpoint and rename recovery: PASS (6 boundaries)
symlink/hardlink/FIFO/conflicting-copy refusal without mutation: PASS
```

Windows: `python -m pytest tests/test_authenticated_external_call_effector.py tests/test_broker_effector_consumers.py tests/test_broker_resource_consumers.py tests/test_broker_ledger_queries.py -q`
returned `99 passed in 17.04s` before the additional access-mode test.
Final `python -m pytest tests/test_broker_effector_consumers.py -q`: `22 passed`.
Linux uid 1001, Python 3.11.16, bwrap 0.12.0:
`python scripts/linux_oracle.py -- tests/test_broker_effector_consumers.py tests/test_authenticated_external_call_effector.py tests/test_inline_approvals.py tests/test_inline_request_storage.py tests/test_broker_ledger_queries.py tests/test_broker_resource_consumers.py tests/test_role_launcher.py tests/test_linux_oracle.py -q`
returned `181 passed in 45.89s`, zero skips, before that additional test.
Final `python scripts/linux_oracle.py -- tests/test_broker_effector_consumers.py -q`
returned `22 passed in 1.05s`, zero skips. No affected file is heavy-listed.
Mirror build: probe-ok; parity: all 597 canonical files matched. Changed-file
Ruff, strict OpenSpec validation and whitespace checks pass. Repository-wide Ruff
still reports the same 55 pre-existing errors outside these files.

## Remaining and activation gate

D24 completes three more D11 consumer routes, not an entire task 2.1-2.8.
Remaining: every actual engine class/site through launcher and daemon-reader
matrix; D11 ledger mutations/accounting/refresh/deletion and other read consumers;
trusted graph effector execution context; full role migration and D10 two-pass
deletion; full role/ACL backup restoration and actual old-image rollback;
real daemon CMD/environment, compose capability parity and healthchecks.
Startup remains unactivated until every required probe passes. No full-role
deletion or actual old-image result is claimed. Continue these items autonomously
under the standing mechanical-decision rule.

---

# Current delivery: D23 three more scoped broker consumers

Implementation commits: `69aafb4ae7` (D22) and `2218bdcb68` (D23).
Both used explicit staged paths and passed all commit hooks. Final hygiene:
`python scripts/test_hygiene_gate.py --base b78457fc58 --head HEAD` returned
`tests added 10, removed 0, tampering findings 0, product lines added 332`.
This receipt is a documentation-only follow-up; all three commits are pushed
together to origin/feat/per-role-uid-split. No PR or deployment.

Continues from D22 implementation `69aafb4ae7`. D23 routes compute-grant
validation, model-access custody-incarnation capture and source display names
through GRANTED_RESOURCE, using one live principal/center/grant/connection
snapshot. Malformed projections fail with a fixed transport error. Broker
unavailability never creates a local ledger. Revoked sources now refuse in
unsplit mode too. Display-only failures retain the existing empty-label result.

Release-critical files: **zero** (cap 8). Runtime files:
`tinyassets/broker/ledger_queries.py`, `tinyassets/api/compute_connection.py`,
`tinyassets/api/model_access_requests.py`, `tinyassets/providers/source_display.py`
and their four generated mirrors. Oracle: `scripts/role_launcher_oracle.py`.
New tests: `tests/test_broker_resource_consumers.py`. Existing tests are unchanged.

One cross-family implementation review through peer-agents returned APPROVE;
**AGREE**. Also adopted its optional test-strength suggestion: an Alice-owned
definition naming Bob's grant reaches the broker and is refused for incarnation
capture and display, rather than testing only the earlier definition-owner check.
No second review round. No new operation, privilege or security-scope change.

## D23 verification receipt

Final production build and both modes passed (exit 0, zero skips), including
all final oracle edits and the additional foreign-definition probe:
`python scripts/linux_oracle.py --production-image tinyassets-uid-consumers:d23 --production-stream --build`
and `python scripts/linux_oracle.py --production-image tinyassets-uid-consumers:d23`.
Final digest: `sha256:0cafbc6cb445cee4273ec297fea56820d7ac600d52e04b5f4372d64135d3c8d2`.
The stream invocation used the exact D22 launch options with network
`ta-uid-stream-ea91615167a5-net` and certificate volume
`ta-uid-stream-ea91615167a5-ca`; default mode uses --network none, no mounts,
no environment overrides. Both use /opt/venv/bin/python -I -B
/app/scripts/role_image_oracle.py from that digest, root entry, cap-drop ALL,
only CHOWN/DAC_OVERRIDE/FOWNER/SETUID/SETGID/SETPCAP/KILL and the recorded compose
security options. Fixture resources were cleaned up after the stream run.

Exact new output, before and after broker restart:
```text
D23 actual compute-grant/incarnation/display consumers via launcher broker: scoped reads, foreign refusal, no daemon ledger: PASS
D22 actual launcher broker HTTPS stream: scoped discovery GET, vault bearer, verified TLS, real network/body, no daemon ledger: PASS
```
The network-none run correctly does not claim D22 HTTPS. Both modes also passed:
```text
forward dry-run, apply, repeat; service remains unadmitted: PASS
reverse dry-run/apply/repeat and uid-1001 old-location writes: PASS
forward/reverse abrupt-exit checkpoint and rename recovery: PASS (6 boundaries)
symlink/hardlink/FIFO/conflicting-copy refusal without mutation: PASS
launcher migration-capability retirement/readback and pre-bind refusal: PASS
launcher broker crash/restart preserves in-memory owner fence: PASS
broker caps=all-zero nnp=1 non-dumpable; no received-fd leak; cross-uid shutdown: PASS
```
The earlier combined image also passed (before the additional foreign-definition
probe): `sha256:0f3d44f3b02415513109e2e782122e77568745c5cbf6a532eb77a182ff3e45ba`.
Command: `python scripts/linux_oracle.py --production-image tinyassets-uid-consumers:d23 --production-stream --build`.
D22 HTTPS and D23 actual consumers passed both before and after broker restart.

Windows:
`python -m pytest tests/test_broker_resource_consumers.py tests/test_compute_connection.py tests/test_model_access_requests.py tests/test_learning_never_locks_out.py tests/test_broker_ledger_queries.py -q`
returned `92 passed in 24.11s` (before adding the foreign-definition test).
`python -m pytest tests/test_engine_mcp_server.py tests/test_llm_policy_pin.py tests/test_broker_resource_consumers.py -q`
returned `93 passed, 3 skipped in 6.35s`. The three existing symlink tests cannot
create symlinks on this Windows host; they are NOT counted as passes.

Linux, uid 1001, Python 3.11.16, bwrap 0.12.0, exit 0 and zero skips:
`python scripts/linux_oracle.py -- tests/test_broker_resource_consumers.py tests/test_compute_connection.py tests/test_model_access_requests.py tests/test_learning_never_locks_out.py tests/test_broker_ledger_queries.py tests/test_broker_discovery_http.py tests/test_role_launcher.py tests/test_linux_oracle.py -q`
returned `137 passed in 42.32s` (before the additional foreign-definition test).
`python scripts/linux_oracle.py -- tests/test_engine_mcp_server.py tests/test_llm_policy_pin.py tests/test_broker_resource_consumers.py -q`
returned `96 passed in 12.06s`, including that new test and all three Windows
symlink skips. No touched/affected test file is in the heavy-test list.

Mirror regeneration returned `probe-ok`; parity reports all 597 canonical files
matched. Changed-file Ruff, strict OpenSpec validation and whitespace checks pass.
Repository-wide Ruff still reports the same 55 pre-existing errors outside this
diff, as recorded in D20/D21. No test was renamed or weakened.

## Remaining and activation gate

D22's real HTTPS streaming and these three D11 read consumers are proven substeps,
not completion of any full task 2.1-2.8. Remaining: actual launcher integration
for every engine class/site and the complete daemon-reader matrix; remaining
D11 ledger/mutation/accounting/refresh/deletion consumers; full role migration,
D10 two-pass deletion, full role/ACL backup restoration and actual old-image
rollback; real daemon CMD/environment, capability parity and healthchecks.
Startup stays unactivated until every required probe passes. No PR or deployment.
Relocation dry-run/apply/repeat/recovery results below are not full-role rollback
or deletion evidence. No new deletion or actual old-image result is claimed.

---

# Current delivery: D22 real launcher-backed HTTPS streaming

Started at `b78457fc58`; required fast-forward pull was already current.
D12-D21 retained. D22 preserves the existing HTTP deployment opt-in in the
launcher's static broker environment as canonical 0/1. It remains disabled when
absent, cannot be supplied over IPC, and enables no test transport or credential
hook. The production-image oracle now optionally exercises real HTTPS with a
synthetic bearer vault and an isolated internal Docker network. Its public CA
is installed only in the disposable oracle container, not the built image/host.

Release-critical files in this step: **two** (cap 8): `Dockerfile` and
`deploy/role_launcher.py`. Other implementation files: `scripts/linux_oracle.py`,
`scripts/role_launcher_oracle.py`, new `scripts/role_stream_oracle.py` and two
focused test files. No tinyassets runtime edits belong to this step; D23 work
will be a separate explicit-path commit.

One cross-family implementation review through peer-agents returned ADAPT for
an unsorted import only; **AGREE**, fixed and reran changed-file Ruff. No floor
or authority finding. No second review round. The first HTTPS probe exposed
the missing HTTP flag. The next reached a successful response but its assertion
incorrectly expected no .tinyassets.db even though discovery fixtures already
create that store; changed the probe to assert its bytes/metadata remain equal.
Neither failed attempt is counted as a pass.

## D22 verification receipt

`python scripts/linux_oracle.py --production-image tinyassets-uid-stream:d22 --production-stream --build`
returned exit 0, zero skips. Image:
`sha256:ec56d9b0ae0abf438c7d4eba1a9dee851db656c1c9680bad86e721d95d898be3`.
The final host cleanup implementation was verified against that digest with:
`python scripts/linux_oracle.py --production-image tinyassets-uid-stream:d22 --production-stream`
(exit 0). Import ordering and the summary wording were subsequently corrected;
the final combined image will be rebuilt for D23.

The runner uses the existing exact entry capabilities CHOWN, DAC_OVERRIDE,
FOWNER, SETUID, SETGID, SETPCAP, KILL with cap-drop ALL, no-new-privileges,
seccomp/AppArmor/systempaths unconfined. The stream variant replaces network
none with a newly created/read-back internal-only network, assigns .2 to the
zero-capability fixture and .3 to the oracle in 93.184.216.0/29, adds the exact
uid-stream.invalid host entry and a read-only Docker volume containing only the
public fixture certificate. No ports are published and no host directory is
mounted. The runner removes its container, certificate volume and network in
finally, with cleanup errors reported.

Relevant exact output (both before and after broker crash/restart):
```text
D22 actual launcher broker HTTPS stream: scoped discovery GET, vault bearer, verified TLS, real network/body, no daemon ledger: PASS
launcher broker crash/restart preserves in-memory owner fence: PASS
broker caps=all-zero nnp=1 non-dumpable; no received-fd leak; cross-uid shutdown: PASS
```
The existing chain, identities, broker-private access, IPC refusal, setgid and
relocation probes also passed. Migration output:
```text
forward dry-run, apply, repeat; service remains unadmitted: PASS
reverse dry-run/apply/repeat and uid-1001 old-location writes: PASS
forward/reverse abrupt-exit checkpoint and rename recovery: PASS (6 boundaries)
symlink/hardlink/FIFO/conflicting-copy refusal without mutation: PASS
```
These remain relocation-only evidence, not full-role or actual old-image rollback.

Windows: `python -m pytest tests/test_role_launcher.py tests/test_linux_oracle.py -q`
returned `37 passed in 0.72s`. Linux:
`python scripts/linux_oracle.py -- tests/test_role_launcher.py tests/test_linux_oracle.py -q`
returned `37 passed in 0.74s` (uid 1001, Python 3.11.16, bwrap 0.12.0; no skips).
Changed-file Ruff and `openspec validate per-role-uid-split --strict` passed.

## Remaining and activation gate

Successful launcher-backed HTTP streaming is now proven for the real discovery
consumer, including restart. No complete task 2.1-2.8 is newly checked off.
Still required: every engine class/site and daemon-reader matrix, remaining D11
ledger/mutation/accounting/refresh/deletion routes, full role migration and
D10 deletion, full role/ACL backup restoration and actual old-image rollback,
real daemon CMD/environment/capability parity/healthchecks. Startup remains
unactivated. No PR or deployment.

---

# Current delivery: D20 discovery HTTP and D21 relocated-ledger backup

Started at `415e976897ee32ef40da8594a16799f27cf20ec0`; the required
`git pull --ff-only origin feat/per-role-uid-split` was already current.
D12-D19 are retained. D20 routes discovery HTTP's scoped grant/resource read
and existing broker stream without a daemon ledger. D21 includes the relocated
ledger in the strict host backup tier, preserving committed WAL rows, relative
path and uid/gid/modes. Both mechanical decisions are recorded in design.md.

Release-critical files: **one**, `deploy/backup.sh` (cap 8). Runtime changes:
`tinyassets/broker/ledger_queries.py`, `tinyassets/providers/discovery_http.py`,
plus their generated mirrors. Oracle: `scripts/role_launcher_oracle.py`.
New tests: `tests/test_broker_discovery_http.py`, `tests/test_broker_backup.py`.
Existing discovery test names and assertions are unchanged; malformed-projection
tests are added. The runbook and D11 inventory are updated.

Cross-family reviews used peer-agents, one round for each distinct slice.
D20: **AGREE** with the ADAPT finding about malformed projection errors;
added LookupError/TypeError mapping and an explicit projection-shape guard.
The new null-projection fixture initially exposed AttributeError; the shape
guard fixes it, and the affected Windows suite subsequently passed 149 tests.
D21: **AGREE** with ADAPT findings about post-copy identity verification and
the stale runbook. File and broker-parent identities are rechecked after copy;
this does not claim ABA race-proofness against a malicious trusted broker.
Also fixed the review's older brain-repair root-mode concern: omit the staging
root's tar header, retaining staging privacy and live-volume root metadata.
No second review round. Reviews found no security-scope change or new privilege.

## D20 verification receipt

Final production image command (exit 0, zero skips):
`python scripts/linux_oracle.py --production-image tinyassets-uid-discovery:d20 --build`

```text
[oracle] production image sha256:55cbd9abb5a74ae039043358fd01ecc6c214a909ec83d34603307d6ec84303c9
[oracle] docker run --rm --network none --user 0:0 --cap-drop ALL --cap-add CHOWN --cap-add DAC_OVERRIDE --cap-add FOWNER --cap-add SETUID --cap-add SETGID --cap-add SETPCAP --cap-add KILL --security-opt no-new-privileges=true --security-opt seccomp=unconfined --security-opt apparmor=unconfined --security-opt systempaths=unconfined --entrypoint /opt/venv/bin/python sha256:55cbd9abb5a74ae039043358fd01ecc6c214a909ec83d34603307d6ec84303c9 -I -B /app/scripts/role_image_oracle.py
privileged chain: PASS (root owners, protected ancestors and link targets)
non-root/writable descendant module chain refusal: PASS
identity uid=1001 groups=[] caps=all-zero nnp=1
image accounts, immutable paths, writable HOME, unprivileged bwrap: PASS
overlapping consent migration refused without mutation: PASS
broker private directory ownership/setgid readbacks without FSETID: PASS
forward dry-run, apply, repeat; service remains unadmitted: PASS
identity uid=1002 groups=[1102] caps=all-zero nnp=1
broker actual ConnectionLedger existing/fresh writes and proxy mkdir: PASS
identity uid=1001 groups=[1100, 1101, 1102] caps=all-zero nnp=1
identity uid=1003 groups=[1100] caps=all-zero nnp=1
direct daemon/engine-identity private path denials: PASS (not class acceptance)
identity uid=1001 groups=[] caps=all-zero nnp=1
reverse dry-run/apply/repeat and uid-1001 old-location writes: PASS
forward/reverse abrupt-exit checkpoint and rename recovery: PASS (6 boundaries)
symlink/hardlink/FIFO/conflicting-copy refusal without mutation: PASS
launcher migration-capability retirement/readback and pre-bind refusal: PASS
D20 actual discovery HTTP via launcher broker: scoped resource/endpoint refusals, malformed-profile independence, no daemon ledger: PASS (successful HTTP stream not claimed)
D11 actual discovery/priced-source consumers via launcher broker; foreign scope, fence, SQL/path/method refusal; no local ledger: PASS
daemon non-dumpable procfs; same-uid fake broker gets no proof: PASS
launcher exact-pid, malformed/oversized/SCM_RIGHTS/static-operation refusals: PASS
launcher broker uid=1002; socket=1002:1101/0660; daemon fences without disk token: PASS
D20 actual discovery HTTP via launcher broker: scoped resource/endpoint refusals, malformed-profile independence, no daemon ledger: PASS (successful HTTP stream not claimed)
D11 actual discovery/priced-source consumers via launcher broker; foreign scope, fence, SQL/path/method refusal; no local ledger: PASS
daemon supervisor acquisition, private-memory channel after restart, stop without signal: PASS
launcher broker crash/restart preserves in-memory owner fence: PASS
privileged chain: PASS (root owners, protected ancestors and link targets)
broker caps=all-zero nnp=1 non-dumpable; no received-fd leak; cross-uid shutdown: PASS
launcher wrong-uid filesystem refusal; actual broker uses private ledger: PASS
LAUNCHER/BROKER SUBSTEP ONLY: real daemon CMD, streams/accounting, engine classes pending
FOUNDATION/EGRESS SUBSTEP ONLY: launcher, IPC, real engine classes, full rollback pending
```

Windows (exit 0):
`python -m pytest tests/test_discovery_http.py tests/test_broker_ledger_queries.py tests/test_discovery_snapshot.py tests/test_model_discovery_capability.py -q`
returned `149 passed in 11.56s`.

Linux (exit 0, zero skips):
`python scripts/linux_oracle.py -- tests/test_broker_discovery_http.py tests/test_discovery_http.py tests/test_broker_ledger_queries.py tests/test_discovery_snapshot.py tests/test_model_discovery_capability.py tests/test_broker_server.py -q`
returned:
```text
[oracle] python 3.11.16 | git 2.47.3 | bwrap 0.12.0 | uid 1001
178 passed in 17.87s
```
An earlier attempt failed during source snapshot with `tar: ./tests: file changed
as we read it` while the next test file was created; no tests ran in that attempt.
The final invocation above used the settled files. The successful IPC stream in
these regression tests uses a scripted upstream, not production HTTP acceptance.

## D21 verification receipt

Actual host-maintenance identity, with synthetic `1002:1101` ledger and parent,
WAL committed rows, private modes, strict archive, and the actual full-volume
restore script (Docker/rclone endpoints replaced by local fixture commands):
`python scripts/linux_oracle.py --as-root --no-bwrap -- tests/test_broker_backup.py -q`
returned (exit 0, zero skips):
```text
[oracle] python 3.11.16 | git 2.47.3 | bwrap 0.12.0 | uid 0
7 passed in 0.84s
```
Root is the existing host-backup role here, not an engine substitute. These tests
also prove legacy root-ledger backup/restore and refusal of parent/file symlinks,
hardlinks, FIFOs and corrupt ledgers before any upload, with outside bytes and
metadata unchanged. This does not prove full role/ACL or old-image restore.

Shellcheck ran separately against both backup scripts in a disposable pinned
Debian/Python container, since the ordinary oracle image lacks shellcheck:
```text
docker run --rm --network bridge -e DEBIAN_FRONTEND=noninteractive -v C:/Users/Jonathan/Projects/wf-uid/deploy:/src:ro python:3.11-slim@sha256:a3ab0b966bc4e91546a033e22093cb840908979487a9fc0e6e38295747e49ac0 sh -c 'apt-get update -qq && apt-get install -y -qq shellcheck > /dev/null && shellcheck --severity=warning /src/backup.sh /src/backup-restore.sh'
exit 0, no shellcheck findings
```

Final ordinary Linux backup regression:
`python scripts/linux_oracle.py -- tests/test_broker_backup.py tests/test_backup_script.py tests/test_backup_ship_gh.py -q`
returned `71 passed, 2 skipped in 3.26s` (exit 0). The two skipped shellcheck
tests are NOT counted as passes; the separate shellcheck command above completed
both checks. The seven D21 acceptance cases passed in both this uid-1001 run
and the explicit root host-maintenance run without skips.

Changed-file Ruff, strict OpenSpec validation, diff whitespace and mirror parity
passed (all 597 canonical files matched; regeneration import probe `probe-ok`).
Repository-wide Ruff still reports the same 55 pre-existing errors outside this
diff. No affected test file is in `.github/heavy-test-files.txt`.

Windows backup regression command:
`python -m pytest tests/test_broker_backup.py tests/test_backup_script.py tests/test_backup_ship_gh.py -q`
returned `9 failed, 54 passed, 10 skipped in 6.38s`. The seven new host-backup
cases skip Windows; nine existing restore cases fail because Git Bash lacks
`flock` and rejects Windows `C:/...` paths as POSIX absolute BACKUP_FILE paths.
Baseline verification loaded `deploy/backup.sh` and `scripts/backup_prune.py`
verbatim from `git show 415e976897:<path>` into an external temporary directory,
pointed the unchanged test module's BACKUP_SH there, and ran the two existing
test modules. It reproduced the identical nine failed names, `54 passed,
3 skipped in 6.33s`. No existing test was renamed or weakened. The Linux runs
above prove the actual host behavior; these Windows failures/skips are not passes.

## Remaining and activation gate

Implementation commit: `5440f8e7cd32ab1ccecd33134dc4a34714cc53de`.
Staged explicit paths only; all commit hooks passed. Final hygiene command
`python scripts/test_hygiene_gate.py --base 415e976897 --head HEAD` returned
`tests added 9, removed 0, tampering findings 0, product lines added 202`.
The implementation worktree was clean. This receipt is a documentation-only
follow-up; both commits are destined for origin/feat/per-role-uid-split.

No task 2.1-2.8 is newly complete. Startup remains unactivated; no PR or deployment.
Remaining: actual engine-class launcher integration for every class and daemon
reader matrix; remaining D11 ledger/mutation/accounting/refresh/deletion consumers;
full role migration and D10 two-pass deletion; successful production broker
streaming; full role/ACL backup restoration and actual old-image rollback;
real daemon CMD/environment, capability parity and healthchecks. Activate only
after every required probe passes. Current migration evidence is relocation-only:
dry-run/apply/repeat both directions and six crash boundaries, not full rollback.

---

# Prior delivery: D19 named broker ledger reads

Started from `e5f48c5ee9`; required fast-forward pull was already current.
D12-D18 remain intact. D19 is a mechanical D11 continuation: the discovery
snapshot and priced-source guard now use two named authenticated broker reads.
No raw SQL, path or arbitrary method dispatch crosses IPC. The broker checks
scope in one transaction; selected-mode failures never open a local ledger.
Connect asks preserve the predeposit case only when both proposed IDs are
proven absent. Revoked discovery preserves its typed source_revoked response.

Release-critical paths under SENSITIVE_RE: zero (cap 8). Security-sensitive
runtime files: `tinyassets/broker/ledger_queries.py`, `broker/client.py`,
`broker/server.py`, `providers/discovery_snapshot.py`, `api/connection_uses.py`
(the last four also under tinyassets). Generated mirrors accompany them.
Oracle: `scripts/role_launcher_oracle.py`; new tests:
`tests/test_broker_ledger_queries.py`. No existing test name or assertion changed.

One cross-family review through peer-agents returned AGREE / APPROVE with no
blocking findings. The review noted the intentionally tighter orphan-row refusal,
consistent with the existing connect_http refusal. No second review round.

No task 2.1-2.8 is newly checked complete. Startup is
still unactivated. Remaining: every actual engine class and reader matrix;
remaining D11 ledger/mutation/discovery HTTP/accounting/refresh/deletion/backup
consumers; full migration and D10 deletion; successful streams; actual old-image
rollback; daemon CMD/environment, capability parity and healthchecks. No PR or
deployment. Only these two D11 read consumers are claimed implemented.

## D19 verification receipt

Production Dockerfile build: `python scripts/linux_oracle.py --production-image tinyassets-uid-queries:d19 --build`.
Final direct verification command (exit 0, zero skips):
`python scripts/linux_oracle.py --production-image tinyassets-uid-queries:d19`.
The final image includes the additional malformed-pricing guard fixture. The
redirected PowerShell build wrapper reported NativeCommandError for Docker's
stderr progress despite completing the build/probes; the direct command above
independently returned exit 0. Exact image/launch/probe output:

```text
[oracle] production image sha256:8edbf229667a6ee555b69e813fded7b095961aba20f06e0e4fa07dbddba5c2c9
[oracle] docker run --rm --network none --user 0:0 --cap-drop ALL --cap-add CHOWN --cap-add DAC_OVERRIDE --cap-add FOWNER --cap-add SETUID --cap-add SETGID --cap-add SETPCAP --cap-add KILL --security-opt no-new-privileges=true --security-opt seccomp=unconfined --security-opt apparmor=unconfined --security-opt systempaths=unconfined --entrypoint /opt/venv/bin/python sha256:8edbf229667a6ee555b69e813fded7b095961aba20f06e0e4fa07dbddba5c2c9 -I -B /app/scripts/role_image_oracle.py
privileged chain: PASS (root owners, protected ancestors and link targets)
non-root/writable descendant module chain refusal: PASS
identity uid=1001 groups=[] caps=all-zero nnp=1
image accounts, immutable paths, writable HOME, unprivileged bwrap: PASS
overlapping consent migration refused without mutation: PASS
broker private directory ownership/setgid readbacks without FSETID: PASS
forward dry-run, apply, repeat; service remains unadmitted: PASS
identity uid=1002 groups=[1102] caps=all-zero nnp=1
broker actual ConnectionLedger existing/fresh writes and proxy mkdir: PASS
identity uid=1001 groups=[1100, 1101, 1102] caps=all-zero nnp=1
identity uid=1003 groups=[1100] caps=all-zero nnp=1
direct daemon/engine-identity private path denials: PASS (not class acceptance)
identity uid=1001 groups=[] caps=all-zero nnp=1
reverse dry-run/apply/repeat and uid-1001 old-location writes: PASS
forward/reverse abrupt-exit checkpoint and rename recovery: PASS (6 boundaries)
symlink/hardlink/FIFO/conflicting-copy refusal without mutation: PASS
launcher migration-capability retirement/readback and pre-bind refusal: PASS
D11 actual discovery/priced-source consumers via launcher broker; foreign scope, fence, SQL/path/method refusal; no local ledger: PASS
daemon non-dumpable procfs; same-uid fake broker gets no proof: PASS
launcher exact-pid, malformed/oversized/SCM_RIGHTS/static-operation refusals: PASS
launcher broker uid=1002; socket=1002:1101/0660; daemon fences without disk token: PASS
D11 actual discovery/priced-source consumers via launcher broker; foreign scope, fence, SQL/path/method refusal; no local ledger: PASS
daemon supervisor acquisition, private-memory channel after restart, stop without signal: PASS
launcher broker crash/restart preserves in-memory owner fence: PASS
privileged chain: PASS (root owners, protected ancestors and link targets)
broker caps=all-zero nnp=1 non-dumpable; no received-fd leak; cross-uid shutdown: PASS
launcher wrong-uid filesystem refusal; actual broker uses private ledger: PASS
LAUNCHER/BROKER SUBSTEP ONLY: real daemon CMD, streams/accounting, engine classes pending
FOUNDATION/EGRESS SUBSTEP ONLY: launcher, IPC, real engine classes, full rollback pending
```

Completed Windows commands:
```text
python -m pytest tests/test_broker_ledger_queries.py tests/test_discovery_snapshot.py tests/test_unify_connection_uses.py -q
63 passed
python -m pytest tests/test_broker_supervisor.py tests/test_broker_relocated_paths.py tests/test_model_discovery_capability.py tests/test_discovery_http.py -q
120 passed
```

The first regression run found six failures from the initial query conversion:
revocation mapping and predeposit asks. Both were fixed without changing any
existing tests, then the complete affected suites above passed. Added probes
cover cross-owner/mismatched/revoked scope, malformed pricing, absent pairs,
concurrent SQLite snapshot consistency, fence-before-open and no local fallback.

Changed-file Ruff passed; repository-wide Ruff remains at the same 55 pre-existing
errors outside this diff. No affected test file is in the heavy-test list.
Plugin regeneration: import probe `probe-ok`; mirror parity: all 597 canonical
files matched. `openspec validate per-role-uid-split --strict` passed and
`git diff --check` passed. Linux regression command (exit 0, zero skips):

```text
python scripts/linux_oracle.py -- tests/test_broker_ledger_queries.py tests/test_discovery_snapshot.py tests/test_unify_connection_uses.py tests/test_broker_server.py tests/test_broker_process.py tests/test_broker_fence.py tests/test_model_discovery_capability.py tests/test_discovery_http.py -q
[oracle] python 3.11.16 | git 2.47.3 | bwrap 0.12.0 | uid 1001
214 passed in 29.55s
```

Implementation commit: `eb5d9d6c34bfd5e59c6e32578daa72cd4361e160`.
Staged explicit paths only. Commit hooks passed: mirror parity, mojibake,
import graph, path resolver, cross-provider drift and skills validation.
`python scripts/test_hygiene_gate.py --base e5f48c5ee9 --head HEAD` returned:
`tests added 7, removed 0, tampering findings 0, product lines added 589`.
The implementation worktree was clean after commit. This receipt update is a
documentation-only follow-up; both commits are for origin/feat/per-role-uid-split.
No PR or deployment.

Migration results here are relocation-substep results: dry-run/apply/repeat in
both directions and six abrupt-exit boundaries passed. Full role migration,
two-pass deletion and actual old-image rollback are NOT proven. No startup
activation or complete engine-class/streaming/accounting acceptance is claimed.

---

# Current delivery: D18 daemon broker acquisition

Started from `be34773f5f`; required fast-forward pull was already current.
D12-D17 remain intact. D18 is a mechanical continuation of D6/D7: daemon
supervisor acquires the launcher-owned broker, authenticates launcher parent
and broker peers before sending proof/token, holds its fence in a process-bound
registry and stops without signals or socket removal. The legacy owner file
reader/writer and process generation argument are removed. Broker-selected
legacy workers refuse before allocation. Production CMD remains unactivated.

Release-critical paths under SENSITIVE_RE: zero in this step (cap 8).
Security-sensitive runtime files: `tinyassets/broker/supervisor.py`,
`tinyassets/broker/client.py`, `tinyassets/broker/process.py`,
`tinyassets/storage/outbound_connections.py`; generated mirrors accompany them.
Acceptance harness: `scripts/role_launcher_oracle.py`. Existing process test
names remain, with assertions amended for the required in-memory contract.

One cross-family code review through peer-agents returned ADAPT. AGREE with
both blocking findings: removed the accidental UTF-8 BOM and stale timer-loop
inventory. A second stale `_spawn` entry was found by the inventory suite;
per AGENTS loop 7, handed that bounded reconciliation to another agent, which
removed it and proved the inventory suite (5 passed) plus Ruff. No second review
round. Also accepted the nonblocking suggestion to return ProxyRequestError
when a stopped supervisor's existing client is used. Reviewer agreed with
launcher/broker peer checks, retirement, non-dumpability, generation allocation,
registry process binding and the distinction between test and kernel evidence.

No additional
2.1-2.8 task is checked complete. This step does not yet implement D11 consumers,
engine cells, full migration/deletion, successful streams or actual old-image
rollback. Startup activation still requires every mandatory probe to pass.


## D18 verification receipt (2026-10-04 local / 2026-10-05 UTC)

Final production-image command exited 0, with no skips:
`python scripts/linux_oracle.py --production-image tinyassets-uid-supervisor:d18 --build`.
The image includes the reviewed BOM/inventory/error-type fixes. Exact launch and
acceptance output:
```
[oracle] production image sha256:4529835547a444f537ede81fd02c83f641fb2f12f22e00cb7039e250722dedd8
[oracle] docker run --rm --network none --user 0:0 --cap-drop ALL --cap-add CHOWN --cap-add DAC_OVERRIDE --cap-add FOWNER --cap-add SETUID --cap-add SETGID --cap-add SETPCAP --cap-add KILL --security-opt no-new-privileges=true --security-opt seccomp=unconfined --security-opt apparmor=unconfined --security-opt systempaths=unconfined --entrypoint /opt/venv/bin/python sha256:4529835547a444f537ede81fd02c83f641fb2f12f22e00cb7039e250722dedd8 -I -B /app/scripts/role_image_oracle.py
privileged chain: PASS (root owners, protected ancestors and link targets)
non-root/writable descendant module chain refusal: PASS
identity uid=1001 groups=[] caps=all-zero nnp=1
image accounts, immutable paths, writable HOME, unprivileged bwrap: PASS
overlapping consent migration refused without mutation: PASS
broker private directory ownership/setgid readbacks without FSETID: PASS
forward dry-run, apply, repeat; service remains unadmitted: PASS
identity uid=1002 groups=[1102] caps=all-zero nnp=1
broker actual ConnectionLedger existing/fresh writes and proxy mkdir: PASS
identity uid=1001 groups=[1100, 1101, 1102] caps=all-zero nnp=1
identity uid=1003 groups=[1100] caps=all-zero nnp=1
direct daemon/engine-identity private path denials: PASS (not class acceptance)
identity uid=1001 groups=[] caps=all-zero nnp=1
reverse dry-run/apply/repeat and uid-1001 old-location writes: PASS
forward/reverse abrupt-exit checkpoint and rename recovery: PASS (6 boundaries)
symlink/hardlink/FIFO/conflicting-copy refusal without mutation: PASS
launcher migration-capability retirement/readback and pre-bind refusal: PASS
daemon non-dumpable procfs; same-uid fake broker gets no proof: PASS
launcher exact-pid, malformed/oversized/SCM_RIGHTS/static-operation refusals: PASS
launcher broker uid=1002; socket=1002:1101/0660; daemon fences without disk token: PASS
daemon supervisor acquisition, private-memory channel after restart, stop without signal: PASS
launcher broker crash/restart preserves in-memory owner fence: PASS
privileged chain: PASS (root owners, protected ancestors and link targets)
broker caps=all-zero nnp=1 non-dumpable; no received-fd leak; cross-uid shutdown: PASS
launcher wrong-uid filesystem refusal; actual broker creates private ledger: PASS
LAUNCHER/BROKER SUBSTEP ONLY: real daemon CMD, streams/accounting, engine classes pending
FOUNDATION/EGRESS SUBSTEP ONLY: launcher, IPC, real engine classes, full rollback pending
```

Exact completed commands/results:
```
python -m pytest tests/test_broker_supervisor.py tests/test_broker_process.py tests/test_broker_fence.py tests/test_broker_server.py tests/test_broker_relocated_paths.py -q
20 passed, 27 skipped (Windows; skips are not kernel acceptance)
python scripts/linux_oracle.py -- tests/test_broker_supervisor.py tests/test_broker_process.py tests/test_broker_fence.py -q
24 passed, zero skips
python scripts/linux_oracle.py -- tests/test_broker_supervisor.py tests/test_broker_process.py tests/test_broker_fence.py tests/test_broker_server.py tests/test_broker_relocated_paths.py tests/test_outbound_connection_ledger.py tests/test_outbound_http_connection.py tests/test_outbound_effect_boundary.py tests/test_outbound_proxy_startup_diagnosis.py tests/test_request_budget_broker.py -q
177 passed, zero skips
python scripts/linux_oracle.py -- tests/test_broker_supervisor.py tests/test_broker_process.py tests/test_broker_fence.py tests/test_broker_server.py tests/test_broker_relocated_paths.py tests/test_role_launcher.py tests/test_broker_upstream_stream.py tests/test_broker_scan.py tests/test_platform_secret_scope.py -q
181 passed, 17 skipped (skips are not acceptance)
python -m pytest tests/test_control_plane_inventory.py -q
5 passed (handoff and lead validation)
python scripts/linux_oracle.py -- tests/test_control_plane_inventory.py -q
5 passed, zero skips
python -m ruff check tinyassets/broker/supervisor.py tinyassets/broker/client.py tinyassets/broker/process.py tinyassets/storage/outbound_connections.py tests/test_broker_supervisor.py tests/test_broker_process.py tests/control_plane_timer_inventory.py scripts/role_launcher_oracle.py
All checks passed!
python -m ruff check --output-format concise
55 pre-existing errors outside changed files
python packaging/claude-plugin/build_plugin.py
Import probe: probe-ok
python scripts/check_mirror_parity.py
mirror-parity: all 596 canonical file(s) mirror-matched
openspec validate per-role-uid-split --strict
Change 'per-role-uid-split' is valid
git diff --check
exit 0
```

Broader Windows command (recorded as a failure, not a pass):
```
python -m pytest tests/test_outbound_connection_ledger.py tests/test_outbound_http_connection.py tests/test_outbound_effect_boundary.py tests/test_outbound_proxy_startup_diagnosis.py tests/test_request_budget_broker.py tests/test_platform_secret_scope.py tests/test_role_launcher.py -q
2 failed, 144 passed, 32 skipped, 9 errors
```
All failures/errors are in unchanged test_request_budget_broker.py: nine Unix
socket fixture setups use unavailable os.getuid, and two symlink probes hit
WinError 1314. That entire file passed in the Linux 177-test run above. No test
was removed, skipped anew or weakened to make Windows green. Initial D18 Windows
fake-socket unit coverage also needed a test-only AF_UNIX constant; it now passes.
No affected test file matches .github/heavy-test-files.txt.

Implementation `a34998fd573c46fee8ccc87ab8926a432acac8b7` was committed using
explicit paths and pushed to origin/feat/per-role-uid-split. Pre-commit mirror,
mojibake, import-graph, path-resolver, cross-provider-drift and skill checks passed.
`python scripts/test_hygiene_gate.py --base be34773f5f --head HEAD` returned:
`tests added 4, removed 0, tampering findings 0, product lines added 347`.
Worktree was clean after that push. This receipt is a documentation-only follow-up.

Remaining acceptance is unchanged beyond D18: real daemon launcher/CMD and
allowlisted environment; every actual engine class/site and daemon reader matrix;
D11 ledger/accounting/refresh/deletion/backup IPC consumers; complete role migration
and runtime mode declarations; D10 two-pass deletion and actual old-image rollback;
successful broker streaming; compose/ta-op capability parity and healthcheck;
startup activation only after all probes pass. The relocation-only reverse probe
is not full role rollback. No deployment, PR, added privilege or isolation-scope
change is authorized or performed by this step.

---

# Current delivery: D16 staged launcher and broker lifecycle

Started from `d43935b600`; the required fast-forward pull was already current.
OpenSpec admission is ALLOWED. D12-D15 and the relocation substep remain intact.
D16 records mechanical IPC framing, socket group assignment after capability
retirement, durable generation allocation and explicit logical data-root routing.

Implemented but not startup-activated: stdlib launcher kernel with exact daemon
uid/pid authentication, migration capability retirement/readbacks, protected
chain verification, fixed broker argv/environment, descriptor closure, readiness,
crash restart and ordered cross-uid shutdown. The new role-split broker mints
its fence generation, checks identity/capabilities, becomes non-dumpable after
exec and uses the relocated ledger with an explicit logical command-center root.
No engine kind is admitted without a cell. Normal script startup refuses.

Release-critical paths under pr-scope-guard SENSITIVE_RE (3, cap 8): `Dockerfile`,
`deploy/role_launcher.py`, `deploy/role_egress_migration.py`. Additionally reviewed
security-sensitive files: `scripts/check_privileged_chain.py`, the two role oracle
scripts, `tinyassets/broker/fence.py`, `tinyassets/broker/process.py`, and
`tinyassets/storage/outbound_connections.py`. Generated plugin copies accompany
the three canonical runtime files.

D17 follows an actual production-image failure: chmod without target-group
membership silently cleared the setgid bit. The migration now uses its existing
SETGID authority temporarily around fchmod and asserts uid/gid/mode readbacks;
the oracle uses that helper for IPC setup and asserts private parent mode 2700.
No CAP_FSETID or new retained privilege is added.

No task 2.1-2.8 is newly checked complete. Remaining: real daemon spawn/environment
and CMD integration; every actual engine class/cell and daemon reader matrix;
all D11 authenticated ledger/accounting/refresh/deletion/backup consumers;
in-memory supervisor replacement and removal of the legacy token path;
full role migration and D10 deletion/reverse migration; actual broker streams,
old-image rollback, ta-op/compose capability parity and healthcheck acceptance.
Startup activation awaits all required proofs. No PR or deployment.

One cross-family review via peer-agents returned ADAPT. **AGREE**: a deeply
nested JSON packet can raise RecursionError on Python 3.11 and escape the
malformed-request refusal. Catch it explicitly and add the 2200-byte nesting
fixture to the production oracle. Reviewer agreed with capability retirement,
exact-pid peer binding, descriptor refusal, socket modes, lifecycle, durable
generation, logical-root separation and continued startup refusal. Readiness
was subsequently strengthened from fresh socket metadata to an actual connection
whose SO_PEERCRED must match the spawned broker pid/uid/gid. No second round.

Oracle diagnostic correction: rejecting a wrong-pid peer before reading its
request may produce AF_UNIX connection reset instead of a delivered REFUSED
packet. The probe accepts only that reset/broken-pipe or explicit REFUSED as
denial; timeout and successful replies still fail. This does not change the peer
gate, read any unauthenticated request or weaken an existing test.

## D16-D17 verification receipt (2026-10-04 local / 2026-10-05 UTC)

Production Dockerfile image:
`sha256:badccc57b43762c61b1750a7fc8be7b98d24902a4c3d5257457f584c04a7950d`.
Final command `python scripts/linux_oracle.py --production-image tinyassets-uid-launcher:d16 --build`
exited 0. Earlier failing iterations found the setgid mode, wrong-pid denial
transport interpretation, procfs directory inference and readiness/reaper race
described here; none is counted as a pass.

Executed container command (no host mounts/network; the planned seven entry
capabilities and compose security options):
```
docker run --rm --network none --user 0:0 --cap-drop ALL --cap-add CHOWN --cap-add DAC_OVERRIDE --cap-add FOWNER --cap-add SETUID --cap-add SETGID --cap-add SETPCAP --cap-add KILL --security-opt no-new-privileges=true --security-opt seccomp=unconfined --security-opt apparmor=unconfined --security-opt systempaths=unconfined --entrypoint /opt/venv/bin/python sha256:badccc57b43762c61b1750a7fc8be7b98d24902a4c3d5257457f584c04a7950d -I -B /app/scripts/role_image_oracle.py
```
Output (the full acceptance statements; no skips):
```
privileged chain: PASS (root owners, protected ancestors and link targets)
non-root/writable descendant module chain refusal: PASS
identity uid=1001 groups=[] caps=all-zero nnp=1
image accounts, immutable paths, writable HOME, unprivileged bwrap: PASS
overlapping consent migration refused without mutation: PASS
broker private directory ownership/setgid readbacks without FSETID: PASS
forward dry-run, apply, repeat; service remains unadmitted: PASS
identity uid=1002 groups=[1102] caps=all-zero nnp=1
broker actual ConnectionLedger existing/fresh writes and proxy mkdir: PASS
identity uid=1001 groups=[1100, 1101, 1102] caps=all-zero nnp=1
identity uid=1003 groups=[1100] caps=all-zero nnp=1
direct daemon/engine-identity private path denials: PASS (not class acceptance)
identity uid=1001 groups=[] caps=all-zero nnp=1
reverse dry-run/apply/repeat and uid-1001 old-location writes: PASS
forward/reverse abrupt-exit checkpoint and rename recovery: PASS (6 boundaries)
symlink/hardlink/FIFO/conflicting-copy refusal without mutation: PASS
launcher migration-capability retirement/readback and pre-bind refusal: PASS
launcher exact-pid, malformed/oversized/SCM_RIGHTS/static-operation refusals: PASS
launcher broker uid=1002; socket=1002:1101/0660; daemon fences without disk token: PASS
launcher broker crash/restart preserves in-memory owner fence: PASS
privileged chain: PASS (root owners, protected ancestors and link targets)
broker caps=all-zero nnp=1 non-dumpable; no received-fd leak; cross-uid shutdown: PASS
launcher wrong-uid filesystem refusal; actual broker creates private ledger: PASS
LAUNCHER/BROKER SUBSTEP ONLY: real daemon CMD, streams/accounting, engine classes pending
FOUNDATION/EGRESS SUBSTEP ONLY: launcher, IPC, real engine classes, full rollback pending
```

The relocation dry-runs preserve content/metadata; forward/reverse repeat is a
no-op and six abrupt-exit boundaries resume. This remains **egress rollback**,
not full role rollback or actual old-image startup. D10 deletion is not yet
implemented or proven. The trusted oracle daemon fixture is explicit; it does
not replace the actual engine-class or real daemon acceptance requirements.

Other exact verification commands/results:
```
python -m pytest tests/test_role_launcher.py tests/test_broker_fence.py tests/test_broker_relocated_paths.py -q
17 passed
python -m pytest tests/test_role_launcher.py tests/test_broker_fence.py tests/test_broker_relocated_paths.py tests/test_broker_server.py tests/test_broker_process.py tests/test_dockerfile_shape.py tests/test_linux_oracle.py -q
75 passed, 27 skipped (Windows; not kernel acceptance)
python -m pytest tests/test_outbound_connection_ledger.py tests/test_broker_supervisor.py tests/test_platform_secret_scope.py -q
48 passed, 32 skipped (Windows)
python scripts/linux_oracle.py -- tests/test_role_launcher.py tests/test_broker_fence.py tests/test_broker_relocated_paths.py tests/test_broker_server.py tests/test_broker_process.py tests/test_broker_supervisor.py tests/test_outbound_connection_ledger.py tests/test_privileged_chain.py -q
75 passed, zero skips
python scripts/linux_oracle.py -- tests/test_outbound_http_connection.py tests/test_outbound_effect_boundary.py tests/test_outbound_proxy_startup_diagnosis.py tests/test_broker_scan.py tests/test_broker_upstream_stream.py tests/test_request_budget_broker.py tests/test_platform_secret_scope.py tests/test_dockerfile_shape.py tests/test_linux_oracle.py -q
295 passed, 17 skipped (skips are not acceptance)
python -m ruff check deploy/role_egress_migration.py deploy/role_launcher.py scripts/role_launcher_oracle.py scripts/role_image_oracle.py scripts/check_privileged_chain.py tinyassets/broker/fence.py tinyassets/broker/process.py tinyassets/storage/outbound_connections.py tests/test_role_launcher.py tests/test_broker_fence.py tests/test_broker_relocated_paths.py
All checks passed!
python -m ruff check --output-format concise
55 pre-existing errors, all outside changed files
python packaging/claude-plugin/build_plugin.py
Import probe: probe-ok
python scripts/check_mirror_parity.py
mirror-parity: all 596 canonical file(s) mirror-matched
openspec validate per-role-uid-split --strict
Change 'per-role-uid-split' is valid
git diff --check
exit 0
```
No affected test file matches .github/heavy-test-files.txt. Existing test names
and assertions are retained.

Implementation `6fe67a30a31a9168d77219dc1052cc7dcae9594b` was committed with explicit
paths and pushed to origin/feat/per-role-uid-split. Pre-commit mirror, mojibake,
import-graph, path-resolver, cross-provider-drift and skill checks passed.
`python scripts/test_hygiene_gate.py --base d43935b600 --head HEAD` printed:
`tests added 7, removed 0, tampering findings 0, product lines added 742`.
Worktree was clean after the implementation push; this receipt is a docs-only
follow-up. No PR, deployment, startup activation or additional completed task
is claimed. Continue from the remaining list at the top of this document.

The crash probe exposed a readiness/reaper race: a START_BROKER arriving during
broker exit could reap the child inside readiness, hiding the restart event
from the lifecycle loop. Readiness now uses waitid(WNOWAIT); only poll reaps and
counts a restart. The same crash/restart assertion remains in the oracle.
The non-dumpability probe checks the protected environ inode and attempts a
same-uid read; /proc/pid directory ownership alone was an incorrect inference.

# Previous delivery: D12 relocation and implementation

Starting HEAD `5ecf0ec8fb`; fast-forward pull was already current; clean worktree.
OpenSpec admission: ALLOWED. D12 accepts the lead relocation decision. D13 uses
2700 private directories because daemon group 1101 must not grant private access.
D14 stages the immutable image foundation without activating an unfinished root
launcher. Mechanical choices will be recorded and implemented autonomously.

Foundation release-critical paths: `Dockerfile`, `deploy/compose.yml`,
`deploy/broker_main.py`, `deploy/role_egress_migration.py`,
`.github/workflows/docker-build.yml` (5 under the gate;
`scripts/check_privileged_chain.py` is additionally security-sensitive).
No deployment or PR. Full task acceptance remains pending.

## D15 implementation and review disposition

Relocation is implemented as `deploy/role_egress_migration.py`, installed immutable
as `/usr/local/libexec/ta-egress-migration.py`, but not activated at startup.
It requires an initialized layout-2 marker with the consent migration done,
uses the existing exclusive layout lock, checkpoints crash-left WAL before
renames, fsyncs files and directories, and uses renameat2(RENAME_NOREPLACE).
It refuses symlinks, hardlinks, FIFOs, mount crossings and conflicting copies.
Forward/reverse progress is durable and resumable; dry-run never opens SQLite.
The top-level layout deliberately remains migrating after this SUBSTEP; only
the complete role migration may admit service. This is D15, recorded in design.md.

One cross-family implementation review via peer-agents returned ADAPT.
- **AGREE**: overlapping consent migration could clear the top-level fence.
  Fixed with pre-mutation layout/consent validation; the production probe now
  refuses both absent and interrupted consent state without mutation, and
  calls actual storage_layout.check after relocation to prove admission refuses.
- **AGREE**: the chain gate skipped broker site-packages under -S and only
  checked directories. Fixed by explicitly enumerating the venv site paths
  without evaluating .pth code, recursively checking modules and symlink
  targets, and probing a non-root-owned and a writable descendant module.
No second review round. Reviewer confirmed no-overwrite, link refusal,
checkpoint ordering, crash-resume and exclusive-lock mechanisms.

No tinyassets/ source changed, so plugin mirror regeneration is not applicable.
Tasks 2.1 and 2.3 have foundation work, 2.4 has the egress substep, and 2.8 has
production-image harness support and substep probes. None of 2.1-2.8 is checked
complete: launcher/CMD, full role migration, D10 deletion, vault mode consumers,
all D11 IPC/path/backup/accounting/refresh consumers, capability parity activation,
actual engine classes, broker stream and old-image rollback remain outstanding.
This branch must not be deployed or treated as the completed role split.

## Foundation verification (2026-10-04)

Production image built from the edited Dockerfile:
`sha256:1ca1b9e6dfa4890396bb1e98bee628b9f1193ac49a4a6b98cc4aead58441900c`.

Commands:
```
python scripts/linux_oracle.py --production-image tinyassets-uid-foundation:d14 --build
python scripts/linux_oracle.py --production-image tinyassets-uid-foundation:d14
```
The direct second command exited 0. It prints the complete docker argv:
```
docker run --rm --network none --user 0:0 --cap-drop ALL --cap-add CHOWN --cap-add DAC_OVERRIDE --cap-add FOWNER --cap-add SETUID --cap-add SETGID --cap-add SETPCAP --cap-add KILL --security-opt no-new-privileges=true --security-opt seccomp=unconfined --security-opt apparmor=unconfined --security-opt systempaths=unconfined --entrypoint /opt/venv/bin/python sha256:1ca1b9e6dfa4890396bb1e98bee628b9f1193ac49a4a6b98cc4aead58441900c -I -B /app/scripts/role_image_oracle.py
```
Output:
```
privileged chain: PASS (root owners, protected ancestors and link targets)
non-root/writable descendant module chain refusal: PASS
identity uid=1001 groups=[] caps=all-zero nnp=1
image accounts, immutable paths, writable HOME, unprivileged bwrap: PASS
overlapping consent migration refused without mutation: PASS
forward dry-run, apply, repeat; service remains unadmitted: PASS
identity uid=1002 groups=[1102] caps=all-zero nnp=1
broker actual ConnectionLedger existing/fresh writes and proxy mkdir: PASS
identity uid=1001 groups=[1100, 1101, 1102] caps=all-zero nnp=1
identity uid=1003 groups=[1100] caps=all-zero nnp=1
direct daemon/engine-identity private path denials: PASS (not class acceptance)
identity uid=1001 groups=[] caps=all-zero nnp=1
reverse dry-run/apply/repeat and uid-1001 old-location writes: PASS
forward/reverse abrupt-exit checkpoint and rename recovery: PASS (6 boundaries)
symlink/hardlink/FIFO/conflicting-copy refusal without mutation: PASS
FOUNDATION/EGRESS SUBSTEP ONLY: launcher, IPC, real engine classes, full rollback pending
```

`python -m pytest tests/test_dockerfile_shape.py tests/test_docker_entrypoint.py tests/test_linux_oracle.py tests/test_no_platform_llm_credentials.py tests/test_no_platform_github_push_credential.py -q`: 177 passed before the two added runner regressions; `python -m pytest tests/test_linux_oracle.py -q`: 23 passed after them.
`python scripts/linux_oracle.py -- tests/test_privileged_chain.py tests/test_dockerfile_shape.py tests/test_docker_entrypoint.py tests/test_linux_oracle.py -q`: 65 passed.
After review fixes, `python scripts/linux_oracle.py -- tests/test_privileged_chain.py tests/test_linux_oracle.py -q`: 28 passed, zero skips.
Changed-file ruff, strict OpenSpec validation and git diff --check pass.
Full `python -m ruff check` still reports the same 55 pre-existing errors.
No affected file is on the heavy-test list.

The simulated-admission Docker fixture starts Uvicorn successfully with immutable
/app. Baseline and edited images both return exit 78 for unadmitted startup
(checked via subprocess.returncode; PowerShell's tool result normalized it to 1).
The synthetic fixture lacks a release receipt, so ta-op pulse correctly refuses
its absent git_sha; this is not claimed as healthcheck acceptance. No real-user
app pass, full deletion, actual old-image rollback or launcher stream is proven.

## Commit receipt

Implementation pushed: `02d5542a78` on `origin/feat/per-role-uid-split`.
`python scripts/test_hygiene_gate.py --base 5ecf0ec8fb --head HEAD`:
`tests added 7, removed 0, tampering findings 0, product lines added 718`.
Explicit paths staged; worktree clean after push. No PR or deployment.

## Historical delivery records (blockers below superseded by D12)

# Current delivery: D11 broker ownership and ledger-parent clarification

Starting HEAD `21096788fb04a3587b900ed1883ed5183d92be20`; the requested first
command `git pull --ff-only origin feat/per-role-uid-split` returned `Already
up to date.` Worktree was clean. OpenSpec admission returned `ALLOWED`.

Recorded the lead decision in design.md D11 and reconciled proposal, role spec,
tasks and rollback requirements. Broker uid 1002 owns outbound ledger/proxy
persistence with group ta-brk; daemon ledger/accounting/refresh access must use
authenticated broker IPC. D10 is unchanged. The ownership decision is accepted;
the old question about giving the broker daemon-store access is superseded.

The new [broker-access-inventory.md](broker-access-inventory.md) enumerates 41
ledger constructor sites and 25 methods opening SQL connections, plus raw SQL,
injected clients, proxy writes, account deletion and backup access. Each has an
explicit implementation disposition. **No route has yet been implemented.**
The trace also establishes that current inference accounting lives in
`.tinyassets.db`, not outbound.db, and local OAuth refresh takes vault-write
admission before spending. Those dependencies cannot be overlooked when routing.

## Pending decision: physical ledger parent

The ownership-only interpretation of the instruction cannot satisfy the broker
write/create probe while retaining D4's exact `/data = 1001:1001/0755` row.
SQLite needs directory write authority for its journal lifecycle. Even a
1002:1101/0600 outbound.db that the broker opens O_RDWR fails an actual
`ConnectionLedger.create_connection`; a fresh ledger cannot be created at all.
Startup precreation of the database is insufficient. The proxy subtree works
when startup creates it as broker-owned 2700.

A user clarification is pending: **may startup relocate the ledger to
`/data/.broker/state/outbound.db`, with reverse migration restoring the original
path?** That is the recommended resolution, preserving private-parent authority.
The alternative needs explicit parent-directory authority. Relocation must also
amend backup's top-level glob, generic account deletion and code that derives the
command-center/accounting root from the ledger parent. No relocation, root ACL,
journal-mode weakening, symlink workaround or retained capability has been applied.

The retained brief explicitly requires: **"If you hit a genuine design ambiguity,
record it in delivery.md and stop rather than guess."** The OpenSpec apply skill
also says **"Pause and ask (don't guess) on unclear tasks, design issues revealed
mid-implementation, or blockers."** Source: [.agents/skills/openspec/SKILL.md](../../../.agents/skills/openspec/SKILL.md).
This is the remaining path-layout decision, not another design review or a
reopening of D10. Runtime work is paused pending that answer.

## Production-image diagnostic (not task 2.8 acceptance)

Used the previously built production Dockerfile image, confirmed by:

```powershell
docker image inspect tinyassets-uid-baseline:664a4361e7 --format '{{.Id}}'
git diff 664a4361e7 HEAD -- Dockerfile tinyassets deploy
```

Image ID `sha256:7d30057f0d2f6a6259b44ee7164831d2c1919697c2d9cae55512051909e585d4`;
runtime/image/deploy diff is empty. This reuses an unchanged production image;
it is not a newly built launcher image. Synthetic files only, disposable
network-disabled container, no host mounts, compose security options and the
specified seven entry capabilities. Children read back all five capability
sets as zero. No refresh/network request was made.

Exact command (script is reproduced below for durable replay):

```powershell
Get-Content -Raw C:/Users/Jonathan/AppData/Local/Temp/uid-broker-parent-probe.py | docker run --rm -i --network none --user 0:0 --cap-drop ALL --cap-add CHOWN --cap-add DAC_OVERRIDE --cap-add FOWNER --cap-add SETUID --cap-add SETGID --cap-add SETPCAP --cap-add KILL --security-opt no-new-privileges=true --security-opt seccomp=unconfined --security-opt apparmor=unconfined --security-opt systempaths=unconfined --entrypoint /opt/venv/bin/python tinyassets-uid-baseline:664a4361e7 -
```

Exit 0: the diagnostic asserts the failures below, not that production works.
The private-parent control uses `.broker/outbound.db` in a synthetic fixture;
it demonstrates directory authority, not the proposed final state path/migration.

```text
uid=1002 groups=[1102] caps=all-zero nnp=1
broker existing database file open=PASS
broker actual ConnectionLedger write=FAIL: attempt to write a readonly database
broker fresh root database create=FAIL: unable to open database file
broker precreated proxy directory child create=PASS
diagnostic private-parent control actual ledger create/write=PASS
uid=1001 groups=[1100, 1101, 1102] caps=all-zero nnp=1
direct access denied uid=1001 path=outbound.db=PASS
direct access denied uid=1001 path=.outbound-proxy=PASS
direct access denied uid=1001 path=.broker/outbound.db=PASS
uid=1003 groups=[1100] caps=all-zero nnp=1
direct access denied uid=1003 path=outbound.db=PASS
direct access denied uid=1003 path=.outbound-proxy=PASS
direct access denied uid=1003 path=.broker/outbound.db=PASS
DIAGNOSTIC COMPLETE; not launcher, IPC, engine-class or migration acceptance
```

Diagnostic source:

```python
"""Diagnostic only: D4 parent permissions after broker ownership transfer."""
import ctypes
import os
import sqlite3
import tempfile
import traceback
from pathlib import Path

from tinyassets.storage.outbound_connections import ConnectionLedger

libc = ctypes.CDLL(None, use_errno=True)
CAP_FIELDS = ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb')


class Header(ctypes.Structure):
    _fields_ = [('version', ctypes.c_uint32), ('pid', ctypes.c_int)]


class Data(ctypes.Structure):
    _fields_ = [('effective', ctypes.c_uint32), ('permitted', ctypes.c_uint32),
                ('inheritable', ctypes.c_uint32)]


def retire(uid, groups):
    assert libc.prctl(38, 1, 0, 0, 0) == 0
    for cap in range(int(Path('/proc/sys/kernel/cap_last_cap').read_text()) + 1):
        assert libc.prctl(24, cap, 0, 0, 0) == 0
    assert libc.prctl(47, 4, 0, 0, 0) == 0
    os.setgroups(groups)
    os.setresgid(uid, uid, uid)
    os.setresuid(uid, uid, uid)
    header, data = Header(0x20080522, 0), (Data * 2)()
    assert libc.capset(ctypes.byref(header), ctypes.byref(data)) == 0
    fields = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines())
    assert all(int(fields[k].strip(), 16) == 0 for k in CAP_FIELDS)
    assert os.getresuid() == (uid, uid, uid)
    assert os.getresgid() == (uid, uid, uid)
    assert os.getgroups() == groups
    print(f'uid={uid} groups={groups} caps=all-zero nnp=1', flush=True)


def child(uid, groups, fn):
    pid = os.fork()
    if pid == 0:
        try:
            retire(uid, groups)
            fn()
        except BaseException:
            traceback.print_exc()
            os._exit(1)
        os._exit(0)
    assert os.waitpid(pid, 0)[1] == 0


root = Path(tempfile.mkdtemp(prefix='broker-parent-'))
os.chown(root, 1001, 1001)
root.chmod(0o755)
existing = root / 'outbound.db'
ConnectionLedger(existing)
os.chown(existing, 1002, 1101)
existing.chmod(0o600)
proxy = root / '.outbound-proxy'
proxy.mkdir()
os.chown(proxy, 1002, 1101)
proxy.chmod(0o2700)
private = root / '.broker'
private.mkdir()
os.chown(private, 1002, 1101)
private.chmod(0o2700)


def insert(path):
    ledger = ConnectionLedger(path)
    ledger.create_connection(connection_id='synthetic', owner_user_id='alice',
                             connection_class='http', connection_type='http',
                             auth_scheme='bearer', scopes=('POST',), provider='http',
                             destination='compute:synthetic', credential_ref='vault://http/synthetic',
                             allowed_endpoints=[{'host': 'models.example.com',
                                                 'path_template': '/v1/chat', 'methods': ['POST']}])


def broker():
    with existing.open('r+b'):
        print('broker existing database file open=PASS', flush=True)
    try:
        insert(existing)
    except sqlite3.OperationalError as exc:
        assert 'readonly' in str(exc), str(exc)
        print('broker actual ConnectionLedger write=FAIL: ' + str(exc), flush=True)
    else:
        raise AssertionError('unexpected write through unwritable journal parent')
    try:
        ConnectionLedger(root / 'new-outbound.db')
    except sqlite3.OperationalError as exc:
        assert 'unable to open database file' in str(exc), str(exc)
        print('broker fresh root database create=FAIL: ' + str(exc), flush=True)
    else:
        raise AssertionError('unexpected create in daemon-owned parent')
    (proxy / 'synthetic-grant').mkdir()
    print('broker precreated proxy directory child create=PASS', flush=True)
    insert(private / 'outbound.db')
    print('diagnostic private-parent control actual ledger create/write=PASS', flush=True)


child(1002, [1102], broker)


def denied():
    for path in (existing, proxy, private / 'outbound.db'):
        try:
            fd = os.open(path, os.O_RDONLY)
        except PermissionError:
            print(f'direct access denied uid={os.getuid()} path={path.relative_to(root)}=PASS', flush=True)
        else:
            os.close(fd)
            raise AssertionError(f'unexpected access: {path}')


child(1001, [1100, 1101, 1102], denied)
child(1003, [1100], denied)
print('DIAGNOSTIC COMPLETE; not launcher, IPC, engine-class or migration acceptance', flush=True)
```

## Verification and remaining work

- `openspec validate per-role-uid-split --strict`: exit 0, change is valid.
- `git diff --check`: exit 0.
- `python -m pytest tests/test_ta_op_modes.py -q --basetemp=C:/Users/Jonathan/AppData/Local/Temp/uid-broker-parent-pytest`: exit 0, `10 passed in 0.33s`.
- `python -m ruff check --output-format concise`: exit 1, `Found 55 errors.`
  All are in unchanged files; no unrelated lint changes made.
- `python scripts/linux_oracle.py -- tests/test_ta_op_modes.py -q`: exit 0,
  `[oracle] python 3.11.16 | git 2.47.3 | bwrap 0.12.0 | uid 1001`,
  `10 passed in 0.13s`. This is the existing capability baseline, not task 2.8
  production-launcher acceptance.
- `python scripts/test_hygiene_gate.py --base 21096788fb --head HEAD`: exit 0,
  `tests added 0, removed 0, tampering findings 0, product lines added 0`.

Release-critical files in this documentation step: **0; list: none**. Changed
paths are seven Markdown artifacts under this change: design.md, proposal.md,
tasks.md, specs/runtime-process-roles/spec.md, rollback.md, delivery.md and the
new broker-access-inventory.md. No runtime, gate or test files changed. No plugin
mirror regeneration applies. No fourth design review, PR or deployment.

Tasks completed: decision/inventory documentation only; **no new task checkbox**.
Tasks 2.1-2.8 remain incomplete. Actual per-class oracle probes, broker IPC
accounting, launcher stream and healthcheck, migration dry-run/apply/repeat/
interrupted-resume, rollback and deletion are **NOT RUN / NOT IMPLEMENTED**.
Do not substitute this uid-only diagnostic for any required class probe.
Continue the ordered build once the physical ledger-parent decision is resolved.

---

# Current delivery: least-privilege D10 amendment

Starting HEAD `664a4361e7`; `git pull --ff-only origin feat/per-role-uid-split`
returned `Already up to date.` Worktree was clean. Read both briefs and the full
round-3 refute, all change artifacts and PLAN operating principles. Admission
`python scripts/openspec_flow.py check-change per-role-uid-split --provider codex`
returned `ALLOWED`.

The lead resolves the prior capability ambiguity: no runtime root maintenance,
no retained DAC_OVERRIDE/FOWNER/CHOWN and no separate privileged helper. Deletion
uses capability-free engine 1003 inside the admitted owner's cell through normal
launcher spawn, then daemon 1001 for daemon entries/empty structure. Both passes
fail loudly with the path. Account deletion and scoped_reset are covered.
Reverse migration is explicit opt-in at container startup in the forward
migration code path before capability drop, dry-run capable, idempotent and
never deletes data. Group-preserving creation modes remain required.

D10, proposal, runtime-role delta, tasks and rollback runbook now agree. No probe
has been weakened; no build task is checked off. Release-critical paths changed
in this documentation step: **0; list: none**. All six changed paths are under
this change: design.md, proposal.md, tasks.md, specs/runtime-process-roles/spec.md,
rollback.md and delivery.md. No tinyassets/ edits, so mirror regeneration is not
applicable. No PR, deployment, test-name change or assertion weakening.

## Build continuation and new stop: broker filesystem authority

Task 2.1 prerequisite inspection covered the Dockerfile, daemon entry/health
paths, provider jail, node sandbox, runtime path resolution and compose image
consumers. No runtime edit was made before the blocker below was established.
The audit is not complete and task 2.1 remains unchecked. Observations to retain:

- Source-relative uses inspected in mcp_server, discovery and storage.rotation
  read packaged data; mcp_server writes default universe state under data_dir().
- Provider homes are per-launch; moving daemon HOME removes the wrapper's /app
  default from the daemon path. Catalog's implicit repo_root is cwd, requiring
  the remaining audit to check explicit sqlite_cached configurations.
- The optional slack-agent service uses the same image but overrides entrypoint.
  A root image USER would otherwise make that service root too. Preserve its
  uid-1001 execution explicitly when implementing the image/compose changes;
  this is a required compatibility adaptation, not a deployed change.

**Separate authority conflict, reproduced using the production Dockerfile.**
D1 gives the broker uid/gid 1002 and supplementary group **1102 only**, with no
capabilities. D4 assigns shared root stores **1001:1001**, strips other access,
and leaves remaining platform state with the daemon. These rows exclude the
broker from outbound.db; the design supplies no broker ACL or mediated ledger
channel. Yet the mandatory working-stream path requires these operations:

| Actual code | Required access under current implementation | Evidence |
|---|---|---|
| broker/process.py `_Dispatchers.ledger_for` (59-65), server.py `_open` (361-366) | open shared outbound.db to authorize every stream | production-image diagnostic below: unable to open database file |
| storage/outbound_connections.py `ConnectionLedger.__init__` (5148-5206) | schema initialization/upgrade and incarnation backfill in addition to reads | code inspection: executescript, ALTER TABLE and conditional UPDATE; a read ACL alone is not a complete contract |
| storage/outbound_connections.py `_build_credential_broker_dispatch` (4928-4955), `broker_dispatch_config` (6071-6089) | mkdir and write under /data/.outbound-proxy/<grant-hash>, plus open the ledger | production-image diagnostic: EACCES at runtime mkdir |
| storage/agent_request_usage.py `resolve_inference_usage`, `UsageStore`, `claim_reference` and usage dispatch (141-197, 401-425, 465+) | write accounting transactions in shared .tinyassets.db; inspect parent/owner liveness locks | source inspection only; not claimed as a runtime probe |
| process_liveness.py `owner_state` (88-109) | open .consumer_liveness/<token>.lock O_RDWR for kernel liveness check | source inspection only; inaccessible means UNKNOWN, not successful inference admission |
| connection_oauth/tokens.py `ConnectionTokens.current` (274-337) | refresh may enter local refresh_credential and its vault-write admission | source inspection only: remote service path requires provider_id plus supplied/inherited config; D3 broker allowlist supplies neither general refresh IPC nor write authority |

The last path also conflicts with the explicit owner-only vault-writer contract:
broker process `_Dispatchers.dispatch_for` does not supply oauth_service in its
config, and non-directory OAuth bundles take the local refresh path regardless.
No refresh network request was attempted. The code's lock-before-spend protection
must remain intact; granting vault write to make refresh work would contradict D4.

**Decision needed:** define the broker's data/operation authority as a whole:
which ledger/accounting operations use authenticated daemon IPC versus explicit
broker-specific storage rights, where broker audit/runtime writes live, and how
refresh remains daemon-owned. A narrowly read-only ledger consumer would also
need to stop schema/backfill writes in broker opens. Giving the broker daemon
group 1001, restoring other-read, widening ta-work, granting vault write, or
retaining capabilities would not be a faithful implementation of the stated
role/mode tables. No one of these choices is inferred.

This is a concrete build compatibility/authority issue, not a fourth design
review and not a reopening of D10. The retained brief says: **"If you hit a
genuine design ambiguity, record it in delivery.md and stop rather than guess."**
Accordingly stopped before runtime implementation. D10 remains resolved.

## Production-image diagnostic: exact command and output

Built the unchanged production Dockerfile from starting runtime HEAD 664a4361e7
(the working-tree changes were Markdown only):

```powershell
docker build -f Dockerfile -t tinyassets-uid-baseline:664a4361e7 .
docker image inspect tinyassets-uid-baseline:664a4361e7 --format '{{.Id}}'
```

Build exit 0. Image ID:
`sha256:7d30057f0d2f6a6259b44ee7164831d2c1919697c2d9cae55512051909e585d4`.
Platform manifest:
`sha256:8ae8092e8558708ebc32bb86eac86e4c91d4f67141e88ab78049c26b287922e3`.

The following script was saved outside the repo at
`C:/Users/Jonathan/AppData/Local/Temp/uid-broker-d4-probe.py`. It creates only
synthetic data inside a disposable network-disabled container with no host
mounts, applies D1/D4 identities/modes, and calls the actual production broker
methods. It is a diagnostic, **not** task 2.8 launcher/stream acceptance: no new
launcher or migration exists yet. All five child capability sets are read back
zero; all compose security options and the proposed seven entry caps are used.

```python
import ctypes
import os
import sqlite3
import tempfile
import traceback
from pathlib import Path

from tinyassets.broker.process import _Dispatchers
from tinyassets.storage.outbound_connections import ConnectionLedger, _build_credential_broker_dispatch

CAP_FIELDS = ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb')
libc = ctypes.CDLL(None, use_errno=True)

class Header(ctypes.Structure):
    _fields_ = [('version', ctypes.c_uint32), ('pid', ctypes.c_int)]

class Data(ctypes.Structure):
    _fields_ = [('effective', ctypes.c_uint32), ('permitted', ctypes.c_uint32), ('inheritable', ctypes.c_uint32)]

def retire(uid, groups):
    assert libc.prctl(38, 1, 0, 0, 0) == 0
    for cap in range(int(Path('/proc/sys/kernel/cap_last_cap').read_text()) + 1):
        assert libc.prctl(24, cap, 0, 0, 0) == 0
    assert libc.prctl(47, 4, 0, 0, 0) == 0
    os.setgroups(groups)
    os.setresgid(uid, uid, uid)
    os.setresuid(uid, uid, uid)
    header, data = Header(0x20080522, 0), (Data * 2)()
    assert libc.capset(ctypes.byref(header), ctypes.byref(data)) == 0
    fields = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines())
    assert all(int(fields[k].strip(), 16) == 0 for k in CAP_FIELDS)
    assert os.getresuid() == (uid, uid, uid)
    assert os.getresgid() == (uid, uid, uid)
    assert os.getgroups() == groups
    print(f'identity uid={uid} gid={uid} groups={groups} caps=all-zero nnp=1', flush=True)

def child(uid, groups, action):
    pid = os.fork()
    if pid == 0:
        try:
            retire(uid, groups)
            action()
        except BaseException:
            traceback.print_exc()
            os._exit(1)
        os._exit(0)
    assert os.waitpid(pid, 0)[1] == 0

root = Path(tempfile.mkdtemp(prefix='uid-broker-d4-'))
os.chown(root, 1001, 1001)
root.chmod(0o755)

def seed():
    ledger = ConnectionLedger(root / 'outbound.db', verify_authenticated_principal=lambda: 'alice')
    ledger.create_connection(connection_id='conn-a', owner_user_id='alice', connection_class='http',
        connection_type='http', auth_scheme='bearer', scopes=('POST',), provider='http',
        destination='compute:conn-a', credential_ref='vault://http/synthetic',
        allowed_endpoints=[{'host':'models.example.com', 'path_template':'/v1/chat', 'methods':['POST']}])
    ledger.grant_connection(grant_id='grant-a', connection_id='conn-a', owner_user_id='alice', universe_id='cc-alice')
    (root / 'outbound.db').chmod(0o640)
    (root / '.outbound-proxy').mkdir(mode=0o700)
    print('seed outbound.db=1001:1001:0640 .outbound-proxy=1001:1001:0700', flush=True)

child(1001, [1100, 1101, 1102], seed)

def control():
    ledger = _Dispatchers(root, allow_test_fixtures=False).ledger_for('alice')
    grant, resource = ledger.authorize_exact(universe_id='cc-alice', grant_id='grant-a', connection_id='conn-a')
    assert grant.connection_id == resource.connection_id == 'conn-a'
    print('daemon.actual_ledger_authorize=PASS', flush=True)

child(1001, [1100, 1101, 1102], control)

def broker():
    try:
        _Dispatchers(root, allow_test_fixtures=False).ledger_for('alice')
    except sqlite3.OperationalError as exc:
        assert str(exc) == 'unable to open database file', str(exc)
        print('broker.actual_ledger_for=OperationalError: ' + str(exc), flush=True)
    else:
        raise AssertionError('D4 unexpectedly admitted broker to the ledger')
    try:
        _build_credential_broker_dispatch({'runtime_root':str(root / '.outbound-proxy' / 'synthetic-grant')})
    except PermissionError as exc:
        assert '.outbound-proxy' in str(exc.filename)
        print('broker.actual_dispatch_build=EACCES: .outbound-proxy/synthetic-grant', flush=True)
    else:
        raise AssertionError('D4 unexpectedly admitted broker writes to owner runtime')

child(1002, [1102], broker)
print('DIAGNOSTIC PASS: D1/D4 permissions block actual broker ledger and runtime setup; NOT stream acceptance', flush=True)

```

```powershell
Get-Content -Raw C:/Users/Jonathan/AppData/Local/Temp/uid-broker-d4-probe.py | docker run --rm -i --network none --user 0:0 --cap-drop ALL --cap-add CHOWN --cap-add DAC_OVERRIDE --cap-add FOWNER --cap-add SETUID --cap-add SETGID --cap-add SETPCAP --cap-add KILL --security-opt no-new-privileges=true --security-opt seccomp=unconfined --security-opt apparmor=unconfined --security-opt systempaths=unconfined --entrypoint /opt/venv/bin/python tinyassets-uid-baseline:664a4361e7 -
```

Exit 0, diagnostic assertions passed:

```text
identity uid=1001 gid=1001 groups=[1100, 1101, 1102] caps=all-zero nnp=1
seed outbound.db=1001:1001:0640 .outbound-proxy=1001:1001:0700
identity uid=1001 gid=1001 groups=[1100, 1101, 1102] caps=all-zero nnp=1
daemon.actual_ledger_authorize=PASS
identity uid=1002 gid=1002 groups=[1102] caps=all-zero nnp=1
broker.actual_ledger_for=OperationalError: unable to open database file
broker.actual_dispatch_build=EACCES: .outbound-proxy/synthetic-grant
DIAGNOSTIC PASS: D1/D4 permissions block actual broker ledger and runtime setup; NOT stream acceptance
```

## Verification

```text
python scripts/linux_oracle.py -- tests/test_ta_op_modes.py -q
[oracle] python 3.11.16 | git 2.47.3 | bwrap 0.12.0 | uid 1001
..........                                                               [100%]
10 passed in 0.26s
```

Exit 0; this is the existing test-oracle baseline capability/healthcheck contract,
not proof of the unimplemented launcher's capability retirement.

- Windows: `python -m pytest tests/test_ta_op_modes.py -q --basetemp=C:/Users/Jonathan/AppData/Local/Temp/uid-d10-least-privilege-pytest`:
  exit 0, `10 passed in 0.52s`.
- `openspec validate per-role-uid-split --strict`: exit 0,
  `Change 'per-role-uid-split' is valid`.
- `python -m ruff check --output-format concise`: exit 1, `Found 55 errors.`
  All in unchanged files (same baseline count as the preceding checkpoint).
  No Python or heavy-test file was edited; no unrelated fixes.
- `git diff --check`: exit 0.
- `python scripts/test_hygiene_gate.py --base 664a4361e7 --head HEAD`:
  exit 0, `tests added 0, removed 0, tampering findings 0, product lines added 0`.
- Commit hooks: mirror parity N/A, mojibake clean (6 text files),
  cross-provider drift clean and skill validation passed. Explicit paths staged;
  no commit -a. Worktree clean after the checkpoint. Push target remains
  origin/feat/per-role-uid-split; no PR.

**Remaining:** all build tasks 2.1-2.8; all actual-class production-image
F1-F7/C1-C6 probes; capability-free two-pass deletion/reset and failure-path
proofs; startup migration dry-run/copy/idempotence/crash-resume; startup reverse
migration followed by old-image read/write/delete; broker launcher/stream and
new-image healthcheck. All are **NOT RUN/NOT PROVEN**, distinct from the diagnostic
above. Tasks 2.9 and 2.10 remain unchecked. No deployment was authorized.
Deviation: the brief's stop condition was applied at the broker authority
conflict. No fourth design review or build-code review occurred: no runtime
implementation exists in this checkpoint.

The entries below are historical evidence only; their unresolved-decision
language and runtime root-maintenance proposal are superseded by amended D10.

---

# Current delivery: lead technical decision D10

Started with `git pull --ff-only origin feat/per-role-uid-split`:
`Already up to date.` Starting HEAD: `39f99b0155819fad4dcb5ebd5672276941810ef0`.
The worktree was clean. Read both build briefs, the full round-3 refute including
confirmed items, proposal/design/tasks and both spec deltas. OpenSpec apply is
ready; delivery admission for codex is `ALLOWED`.

## Access-preservation decision recorded

D10 records the lead's technical decision: ACLs alone are insufficient; all
owner-tree daemon deletion/reset/cleanup uses audited launcher-mediated root
maintenance with the fixed `delete-tree`, `reset-tree`, `chown-back` allowlist,
exact daemon uid/pid verification and no-follow openat traversal confined to the
requesting owner's tree. Rollback requires explicit, idempotent, dry-run-capable
reverse migration before an old image starts. Known owner-work 0700/chmod sites
must use group-preserving modes as defense in depth. No widening outside the
owner's work tree, no general root exec and no free-rollback claim.

Updated design, proposal, runtime-role delta and tasks, and added `rollback.md`.
The runbook is a specified sequence, clearly marked not yet executable/tested;
there is no maintenance CLI to document as working. Added the required
production acceptance rows for actual daemon deletion/reset of engine-created
0700 trees, other-owner and symlink-escape refusal, and reverse migration followed
by actual old-image uid-1001 read/write/delete. No build task was checked off.

## Build stop: maintenance authority versus mandatory retirement

The access-preservation choice is resolved. A separate explicit conflict remains:

- D2 and D6 step 4 require CHOWN/FOWNER/DAC_OVERRIDE to be removed from **all
  five** launcher capability sets before service.
- Task 2.2 requires that drop/readback; task 2.8 requires the launcher to refuse
  service while it holds FOWNER or DAC_OVERRIDE. D9/F6 and the runtime-role spec
  preserve this control. The new instruction says all brief probes still apply.
- The new root operation must run after service starts and traverse/re-mode
  engine-owned 0700 content and chown it back to uid 1001. These need the retired
  authority. Root uid alone does not supply it. Fork/exec from the retired
  launcher does not regain it under the bounding set and no-new-privileges.

The isolated Linux diagnostic below demonstrates the conflict, including
successful retained-capability controls. This is not a fourth design review,
not production-image acceptance and not a claim that maintenance was built.

**Decision needed:** either amend retirement to allow the existing launcher to
retain these three capabilities for the fixed scoped operations (and replace
that conflicting oracle row), or preserve launcher retirement and authorize a
separate maintenance helper created before retirement to retain them (amending
the one-long-lived-privileged-process goal). The request authorizes maintenance
but does not state which still-required security constraint changes. No choice,
helper, probe weakening or privilege-regain workaround has been implemented.
The clarification was sent to the lead during this turn.

The build brief explicitly says: "If you hit a genuine design ambiguity, record
it in delivery.md and stop rather than guess." Accordingly stopped before 2.1;
no runtime, test or gate edits. This supersedes the historical F5 stop below.

## Capability diagnostic: exact command and output

Synthetic data only in a disposable network-disabled container, no host volume
mounted, using the existing **test** oracle image. It is **not** the production
image and proves only the capability conflict. Image:
`tinyassets-linux-oracle:724828e06295`, digest
`sha256:c36872bb77443c134a36f690b86d68470f05c9353bd6be0874971874cd1dd6e5`.
The run uses all three compose confinement options, no-new-privileges and the
proposed seven-capability entry set, without SYS_ADMIN.

Write the following script to a temporary file outside the repository, e.g.
`C:/Users/Jonathan/AppData/Local/Temp/uid-maintenance-capability-probe.py`:

```python
import ctypes
import errno
import os
import subprocess
import sys
import tempfile
from pathlib import Path

MIGRATION = (1 << 0) | (1 << 1) | (1 << 3)
FIELDS = ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb')
libc = ctypes.CDLL(None, use_errno=True)
class Header(ctypes.Structure):
    _fields_ = [('version', ctypes.c_uint32), ('pid', ctypes.c_int)]
class Data(ctypes.Structure):
    _fields_ = [('effective', ctypes.c_uint32), ('permitted', ctypes.c_uint32), ('inheritable', ctypes.c_uint32)]

def caps():
    lines = Path('/proc/self/status').read_text().splitlines()
    return {k: int(v.strip(), 16) for k, v in (s.split(':', 1) for s in lines) if k in FIELDS}

def setcaps(mask):
    header = Header(0x20080522, 0)
    data = (Data * 2)(Data(mask, mask, 0), Data(0, 0, 0))
    return libc.capset(ctypes.byref(header), ctypes.byref(data))

def check(label, action, expected):
    try:
        action()
        result = 'OK'
    except OSError as exc:
        result = errno.errorcode[exc.errno]
    print(f'{label}={result}', flush=True)
    assert result == expected, (label, result, expected)

def open_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    os.close(fd)

root = Path(tempfile.mkdtemp(prefix='maintenance-caps-'))
root.chmod(0o755)
for label in ('retained', 'retired'):
    tree = root / label
    tree.mkdir()
    (tree / 'data').write_text('synthetic owner A only')
    os.chown(tree / 'data', 1003, 1100)
    os.chown(tree, 1003, 1100)
    tree.chmod(0o700)
assert caps()['CapEff'] == 0x1eb, caps()
tree = root / 'retained'
check('retained.root.open_0700', lambda: open_dir(tree), 'OK')
check('retained.root.chmod', lambda: tree.chmod(0o770), 'OK')
check('retained.root.chown_back', lambda: os.chown(tree, 1001, 1001), 'OK')
assert libc.prctl(38, 1, 0, 0, 0) == 0
for cap in (0, 1, 3):
    assert libc.prctl(24, cap, 0, 0, 0) == 0
assert setcaps(0x1e0) == 0
assert all(not (value & MIGRATION) for value in caps().values())
print('retired.caps=' + ','.join(f'{k}:{v:08x}' for k, v in caps().items()), flush=True)
tree = root / 'retired'
check('retired.root.open_0700', lambda: open_dir(tree), 'EACCES')
check('retired.root.chmod', lambda: tree.chmod(0o770), 'EPERM')
check('retired.root.chown_back', lambda: os.chown(tree, 1001, 1001), 'EPERM')
assert setcaps(0x1eb) == -1 and ctypes.get_errno() == errno.EPERM
print('retired.capset_regain=EPERM', flush=True)
subprocess.run([sys.executable, '-I', '-S', '-c', '''
import os
from pathlib import Path
fields = ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb')
values = {k: int(v.strip(), 16) for k, v in (s.split(':', 1) for s in Path('/proc/self/status').read_text().splitlines()) if k in fields}
assert os.geteuid() == 0
assert all(not (v & 0xb) for v in values.values())
print('retired.fork_exec_regain=DENIED', flush=True)
'''], check=True)
print('DIAGNOSTIC PASS: root uid alone cannot perform required maintenance after retirement', flush=True)

```

Exact command executed (PowerShell):

```powershell
Get-Content -Raw 'C:/Users/Jonathan/AppData/Local/Temp/uid-maintenance-capability-probe.py' | docker run --rm -i --network none --cap-drop ALL --cap-add CHOWN --cap-add DAC_OVERRIDE --cap-add FOWNER --cap-add SETUID --cap-add SETGID --cap-add SETPCAP --cap-add KILL --security-opt no-new-privileges=true --security-opt seccomp=unconfined --security-opt apparmor=unconfined --security-opt systempaths=unconfined --entrypoint python tinyassets-linux-oracle:724828e06295 -
```

Output (exit 0, diagnostic assertions passed):

```text
retained.root.open_0700=OK
retained.root.chmod=OK
retained.root.chown_back=OK
retired.caps=CapInh:00000000,CapPrm:000001e0,CapEff:000001e0,CapBnd:000001e0,CapAmb:00000000
retired.root.open_0700=EACCES
retired.root.chmod=EPERM
retired.root.chown_back=EPERM
retired.capset_regain=EPERM
retired.fork_exec_regain=DENIED
DIAGNOSTIC PASS: root uid alone cannot perform required maintenance after retirement
```

## Verification and release-critical scope

- `python scripts/linux_oracle.py -- tests/test_ta_op_modes.py -q`: exit 0.
  Baseline capability/healthcheck-contract regression only, not role-split
  production acceptance. Output:

  ```text
  [oracle] python 3.11.16 | git 2.47.3 | bwrap 0.12.0 | uid 1001
  ..........                                                               [100%]
  10 passed in 0.15s
  ```

- `openspec validate per-role-uid-split --strict`: exit 0,
  `Change 'per-role-uid-split' is valid`.
- `python -m ruff check`: exit 1, `Found 55 errors.` All are in unchanged
  Python files; this checkpoint changes only Markdown. No unrelated fixes.
- `git diff --check`: exit 0, no whitespace errors.
- `python scripts/test_hygiene_gate.py --base 39f99b0155819fad4dcb5ebd5672276941810ef0 --head HEAD`:
  exit 0, `tests added 0, removed 0, tampering findings 0, product lines added 0`.
  An initial attempt against the staged tree object was rejected because this
  gate requires commits for merge-base; rerunning on the checkpoint commit
  succeeded. No gate code was changed.
- `python -m pytest tests/test_ta_op_modes.py -q --basetemp=C:/Users/Jonathan/AppData/Local/Temp/uid-d10-pytest`:
  exit 0, `10 passed in 0.37s` on Windows. Baseline regression only.
- Commit hooks: mirror parity N/A, mojibake clean (6 text files), cross-provider
  drift clean and skill validation passed.
- No affected runtime implementation or heavy file changed. No test name or
  assertion changed; no plugin mirror regeneration needed (no tinyassets edit).
- No fourth design review. No implementation code exists for the normal
  cross-family build review yet. No PR and no deployment.

**Release-critical files in this step: 0; list: none.** Six documentation files:
`design.md`, `proposal.md`, `tasks.md`, `specs/runtime-process-roles/spec.md`,
`rollback.md` and `delivery.md`, all under this change directory. Checked against
`.github/workflows/pr-scope-guard.yml` SENSITIVE_RE, hard cap 8. Future build
steps still require their own exact release-critical inventory before edits.

**Remaining work:** all tasks 2.1-2.8, including the maintenance implementation,
all-class F1-F7/C1-C6 production-image oracle matrix, migration dry-run/copy/
crash-resume/idempotence, reverse migration/old-image rollback, actual deletion
and reset, broker launch/stream and healthcheck. All are **NOT RUN/NOT PROVEN**.
Tasks 2.9 deployment and 2.10 sync/archive remain unchecked. No build task is
complete. Deviation: applied the brief's stop condition at the demonstrated
capability-lifetime conflict; the access-preservation decision itself is recorded.

---

The following is historical delivery evidence, superseded by D10 and the current
status above where it describes F5 as undecided.

# Current delivery: founder decision 2026-10-05

**founder decision 2026-10-05: fold + build with probes.** Started with
`git pull --ff-only origin feat/per-role-uid-split`: `Already up to date.`
Starting HEAD: `0852b897cf`. Read the full round-3 refute (including confirmed
items), earlier brief, proposal/design/tasks, both deltas and prior delivery.
D9 folds F1-F7 into the design, spec deltas and tasks. No fourth design review.
No runtime code changed, no new task checked off, no PR and no deployment.

## Build stop: F5 ACL-mask ambiguity

The prescribed access/default ACLs and umask do not guarantee daemon deletion
or rollback access after engine writes. `workspace_provision_execution.py:69`
explicitly creates `.venv` with mode 0700. Linux masks the inherited named-user
ACL to the requested group mode; an engine can also chmod an initially
accessible directory to 0700. Both uid 1001 with group 1100 and uid 1001 without
that group then receive EACCES reading or deleting its child. The existing
`workspace_fs.RealPoolFilesystem.remove_tree_no_follow` ultimately needs to open
that directory; a uid-1001 daemon cannot chmod an inode owned by uid 1003 to
repair it. Merely changing the .venv creation mode misses arbitrary engine code.

**Decision needed before implementation:** choose a mechanism that preserves
current daemon deletion and old-image rollback for restrictive engine-owned
paths. Options require different authority/compatibility contracts: control all
permission-reducing operations, or provide scoped repair/deletion plus an explicit
rollback preparation protocol. Neither additional authority nor removal of the
rollback/deletion requirement is inferred. No mechanism has been selected.
This is an implementation-design ambiguity, not a fourth refute round.

The founder explicitly instructed: "If you hit a genuine design ambiguity,
record it in delivery.md and stop rather than guess." Build stopped before 2.1.
D9 preserves all required outcomes and labels F5 unresolved; the documentation
fold is not a claim that the design is ready to implement unchanged.

## Linux counterexample command and output

Synthetic data only in a disposable network-disabled Linux container; no host
volume mounted. Existing **test** oracle image, NOT the production image and NOT
acceptance for tasks 2.1-2.8. Compose's security options are supplied explicitly.
Image ID from `docker image inspect tinyassets-linux-oracle:724828e06295 --format
'{{.Id}}'`: `sha256:c36872bb77443c134a36f690b86d68470f05c9353bd6be0874971874cd1dd6e5`.

Exact reproducible PowerShell command (the same script was run from a temporary
file outside the repository):

```powershell
@'
import ctypes
import os
import tempfile

acl = ctypes.CDLL('libacl.so.1', use_errno=True)
acl.acl_from_text.argtypes = [ctypes.c_char_p]
acl.acl_from_text.restype = ctypes.c_void_p
acl.acl_set_file.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_void_p]
acl.acl_free.argtypes = [ctypes.c_void_p]

def setacl(path, kind, text):
    entry = acl.acl_from_text(text.encode())
    assert entry
    assert acl.acl_set_file(os.fsencode(path), kind, entry) == 0, ctypes.get_errno()
    acl.acl_free(entry)

root = tempfile.mkdtemp(prefix='uid-acl-')
os.chown(root, 1001, 1100)
os.chmod(root, 0o2770)
entry = 'u::rwx,u:1001:rwx,g::rwx,m::rwx,o::---'
setacl(root, 0x8000, entry)
setacl(root, 0x4000, entry)
pid = os.fork()
if pid == 0:
    os.setgroups([1100])
    os.setgid(1003)
    os.setuid(1003)
    os.umask(0o007)
    for label in ('daemon', 'older-image'):
        for name, mode in (('normal', 0o777), ('venv', 0o700), ('chmod', 0o777)):
            path = root + '/' + label + '-' + name
            os.mkdir(path, mode)
            with open(path + '/data', 'w') as handle:
                handle.write('synthetic owner A data')
            if name == 'chmod':
                os.chmod(path, 0o700)
    os._exit(0)
assert os.waitpid(pid, 0)[1] == 0
for groups, label in (([1100], 'daemon'), ([], 'older-image')):
    pid = os.fork()
    if pid == 0:
        os.setgroups(groups)
        os.setgid(1001)
        os.setuid(1001)
        for name in ('normal', 'venv', 'chmod'):
            path = root + '/' + label + '-' + name
            try:
                with open(path + '/data') as handle:
                    handle.read()
                read = 'PASS'
            except PermissionError:
                read = 'EACCES'
            try:
                os.unlink(path + '/data')
                delete = 'PASS'
            except PermissionError:
                delete = 'EACCES'
            print(label, name, 'read=' + read, 'delete=' + delete, flush=True)
            expected = 'PASS' if name == 'normal' else 'EACCES'
            assert read == expected and delete == expected
        os._exit(0)
    assert os.waitpid(pid, 0)[1] == 0
'@ | docker run --rm -i --network none --security-opt seccomp=unconfined --security-opt apparmor=unconfined --security-opt systempaths=unconfined --entrypoint python tinyassets-linux-oracle:724828e06295 -
```

Output, exit 0 (asserts reproduction of the failure, not acceptance success):

```text
daemon normal read=PASS delete=PASS
daemon venv read=EACCES delete=EACCES
daemon chmod read=EACCES delete=EACCES
older-image normal read=PASS delete=PASS
older-image venv read=EACCES delete=EACCES
older-image chmod read=EACCES delete=EACCES
```

An initial diagnostic reused the daemon-deleted positive-control file for the
old-image case and exited 1 with FileNotFoundError. The command above corrects
that harness issue by creating independent fixtures; the restrictive-path
EACCES results reproduced. Neither run used real user data.

## Checkpoint verification

- `openspec validate per-role-uid-split --strict`: exit 0,
  `Change 'per-role-uid-split' is valid`.
- `git diff --check 0852b897cf HEAD`: exit 0, no output.
- `python scripts/test_hygiene_gate.py --base 0852b897cf --head HEAD`: exit 0,
  `tests added 0, removed 0, tampering findings 0, product lines added 0`.
- Task checkbox counts: design 2, build 10; no new checkmarks.
- `python -m ruff check`: exit 1, `Found 55 errors.` All occur in unchanged
  Python files; this diff contains only Markdown. No unrelated fixes were made.
- Commit hooks: mirror parity N/A, mojibake clean (6 text files),
  cross-provider drift clean, skill validation passed.

## Scope and remaining work

This step changes six documentation files under this change directory:
proposal.md, design.md, tasks.md, delivery.md and both spec deltas.
**Release-critical files in this step: 0; list: none.** Checked against the scope
guard SENSITIVE_RE (workflows, deploy/, Dockerfile, .dockerignore and listed
CI gate files). No runtime/test/gate edit, so no plugin mirror regeneration.
No implementation commit was started; there are no ordered build commits to
claim. When the F5 mechanism is resolved, inventory exact release-critical
paths before each ordered PR-sized build step and keep each step at most 8;
commits alone do not shrink a later combined PR's scope-guard diff.

Tasks 2.1-2.8 remain incomplete, 2.9 deployment is outside authorization, and
2.10 sync/archive remains pending. All F1-F7/C1-C6 production acceptance probes,
production-image build, actual-class operations, broker launch/stream, healthcheck,
migration dry-run/crash-resume/idempotence/rollback and actual deletion APIs
remain **NOT RUN**. Only the isolated ACL counterexample above ran. No affected
runtime tests exist for this docs-only diff; no test name/assertion was changed.
Normal cross-family code review remains required for the eventual build; no
code exists in this checkpoint to review. No fourth design review was requested.

Deviation: stopped before build on the demonstrated F5 ambiguity, as instructed.
There is no change to the requested isolation, rollback or deletion standard.

---

The following delivery history predates the founder's 2026-10-05 decision and
is retained as historical evidence; current status and D9 supersede it.

# Delivery status

Implementation is pending; the security-scope decision is resolved by the lead amendment
recorded in design.md D8 (2026-10-04). No build task is
checked off, no runtime file has changed, and nothing has been deployed.

Base: `22560b9d0f7f79582cfea482b3c4efb2cda27e5a` (`origin/main`, fetched during
this session). The existing clean worktree is already on
`feat/per-role-uid-split`. Proposal, design, tasks and both spec deltas were read.
OpenSpec apply reports ready; delivery admission reports `ALLOWED`.

## Acceptance scope resolved: every engine class

The lead, applying founder principles, decided that cross-user isolation is the platform's
ONLY invariant and is non-negotiable, using the supplied Muse per-user runtime-cell
architecture as the reference. "Deny only for jailed providers" is REJECTED.

D8 now requires every owner-scoped engine child and descendant to enter the owner's
bubblewrap namespace through the launcher. Shared uid 1003 and `ta-work` remain for role
separation and rollback compatibility, but their access is confined to the owner view.
D8 records every known spawn site, including engine MCP, native discovery, preview,
image decoding and additional utility/box/auth-probe paths. No class gets an unjailed
fallback; a fresh implementation inventory must close any further sites before acceptance.

Acceptance is now the production-image Linux oracle matrix: owner A's actual process of
EVERY engine class is denied B's data, owner.json, vault and owner token, including
procfs/fd/IPC routes, with a working owner-A operation for each class. Synthetic uid probes
and a single provider jail cannot substitute. Existing two-round refute fixes remain.
The counterexample below is retained as evidence against the rejected shared-group-only
mechanism; it is not acceptance evidence for the amended design.

## Linux counterexample (not production-image acceptance)

This command ran successfully in a disposable, network-disabled Linux container,
using synthetic files only. It implements D4's directory and workspace permission
inventory and D1's engine identity. It does not mount or access user data.

PowerShell command:

```powershell
@'
import os
from pathlib import Path
root = Path('/tmp/role-boundary')
root.mkdir(mode=0o755)
for owner in ('owner-a', 'owner-b'):
    cc = root / owner
    cc.mkdir()
    os.chown(cc, 1001, 1100)
    os.chmod(cc, 0o2711)
    workspace = cc / 'workspace'
    workspace.mkdir()
    os.chown(workspace, 1001, 1100)
    os.chmod(workspace, 0o2770)
    data = workspace / 'private.txt'
    data.write_text(owner + ' private data')
    os.chown(data, 1001, 1100)
    os.chmod(data, 0o660)
os.setgroups([1100])
os.setresgid(1003, 1003, 1003)
os.setresuid(1003, 1003, 1003)
print(f'uid={os.getuid()} gid={os.getgid()} groups={os.getgroups()}')
for owner in ('owner-a', 'owner-b'):
    print(f'{owner}: READ ALLOWED: {(root / owner / "workspace" / "private.txt").read_text()}')
'@ | docker run --rm -i --network none python:3.11-slim python -
```

Exact output, exit 0:

```text
uid=1003 gid=1003 groups=[1100]
owner-a: READ ALLOWED: owner-a private data
owner-b: READ ALLOWED: owner-b private data
```

Adding D4's broker traverse ACL does not remove the engine's work-group access.
This is a counterexample to general denial, not a failure of the intended vault
permission boundary and not a substitute for the required production-image proof.

## Current-code differences to account for during implementation

- Both dependencies are now present: `tinyassets/broker/supervisor.py` and
  `tinyassets/platform_secrets.py`. The design's unmerged-dependency statements
  are historical.
- The jail module is `tinyassets/providers/provider_jail.py`. Current native discovery
  already calls `aspawn_owned` with `metadata_view` and `require_confinement=True`;
  preserve this narrower view when adding launcher identity enforcement.
- `engine_mcp_http._EngineServer.start` now starts from `child_env(os.environ)`,
  supplies OAuth service configuration, and propagates the execution-owner tree
  environment in addition to the six engine configuration names. Its allowlist
  requires a current consumer audit; blindly using the historical list could
  break OAuth and lease tracking.
- `platform_secrets.child_env` now excludes the `TINYASSETS_OAUTH_` namespace and
  `TINYASSETS_CONNECTION_OAUTH_SERVICE`, in addition to `CHILD_FORBIDDEN_ENV`.
- Storage layout is now version 2, with an existing consent migration. Role
  migration must preserve that document and its recovery semantics.
- Additional direct subprocess sites exist in `ui_preview._supervised` and
  `tool_images._decode_in_child`. The former uses PID containment without
  filesystem isolation. D8 now assigns both to owner-scoped engine cells.

These are audit findings, not implemented deviations.

## Task 2.1 preliminary write audit

The audit is not complete and does not prove that `/app` can yet be made read-only.
Confirmed so far:

- API data helpers, MCP universe paths and the OAuth database resolve through
  `storage.data_dir`; old `/app/output` references describe corrected bugs.
- Provider `default_view` creates `.agent-workspace` in the command center and
  binds the current credential snapshot read-write for CLI lock/session files.
- Node sandbox scratch comes from `tempfile.mkdtemp`; its jail uses `/tmp` as
  HOME. Production proofs must still establish access after the UID change.
- `codex_provider._codex_workdir` defaults to the source root. Its actual served
  call paths must be audited before deciding whether the fallback in D2 is needed.
- Preview and image decoder children use a source-tree cwd; cwd alone does not
  prove a source-tree write.
- The healthcheck canary reads its source-adjacent helper and performs HTTP I/O.

## Release-critical scope

Actual release-critical files changed: **0** (only this delivery record changed).
The scope guard's sensitive-path expression and hard cap of 8 were inspected.
Expected existing release-critical files for the specified implementation are
`Dockerfile`, `deploy/docker-entrypoint.sh`, `deploy/compose.yml`,
`deploy/native/ta_op.c`, and `.github/workflows/docker-build.yml`.
The deploy validator and any additional privileged artifacts must be inventoried
before claiming the final count. No workflow behavior has changed.

## Verification and remaining work

Docker Desktop's Linux engine is available (`docker version`: client/server
29.5.2). No production Dockerfile build, Linux oracle acceptance run, migration
dry-run, migration-copy proof, stream proof, healthcheck proof, or affected
Windows test run has occurred. No plugin mirror regeneration is needed for this
documentation-only record. Security implementation review has not been dispatched.

Documentation checks: `git diff --check` passed. The hygiene gate
(`python scripts/test_hygiene_gate.py --base origin/main --head HEAD`) reported
`tests added 0, removed 0, tampering findings 0, product lines added 0`.
`python -m ruff check` exited 1 with `Found 55 errors.` in unchanged repository
files; no Python file was edited, and no unrelated lint fixes were made.

Tasks 2.1-2.8 all remain incomplete. Tasks 2.9-2.10 remain unchecked; production
deployment is explicitly outside this request. The acceptance scope is resolved.
Finish the write/spawn inventory, then implement and prove 2.1-2.8 in order.


## Design-amendment verification (2026-10-04)

Started from `aae57d3034` with `git pull --ff-only origin feat/per-role-uid-split`
(already up to date). This amendment changes only the proposal, design, tasks, runtime
process-role delta and this delivery record. No product code or gate file changed.
`npx --yes @fission-ai/openspec validate per-role-uid-split --strict` and
`git diff --check` pass. Task sections contain 2 and 10 checkboxes (12 total).
`python -m ruff check` still reports 55 errors in unchanged files. The production-image
Linux oracle matrix is specified, not executed: its implementation and production-image
harness support belong to the still-unchecked build tasks. No deployment or PR is part
of this amendment.


Cross-family amendment review via `peer-agents` returned `DISAGREE_EVIDENCE`;
**AGREE** with all four findings, corrected in this amendment: D4 now declares relay
parent/directory/socket permissions and runtime mode-map updates; D8/spec/tasks explicitly
require private networking and cross-owner port/socket denial; root migration owns legacy
owner.json cleanup before chown; D7's owner-work exception and absent historical gh site
are narrowed explicitly. The reviewer confirmed inventory coverage and preservation of
both prior refute rounds. No second review round was dispatched.

D47 hygiene correction: the per-commit gate rejected adding a Windows skip to the already-committed D44 IPC tests (1 tampering finding). Restored those tests without the skip in an additive follow-up; no exception, test-removal approval, or history rewrite. D47's newly introduced evidence tests retain their own Unix prerequisite. All acceptance receipts above are zero-skip Linux runs.
