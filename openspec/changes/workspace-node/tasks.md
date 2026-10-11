## 1. Slice A — checkout, run, push (scratch and permanent)

- [x] 1.1 Scratch pool: lease table + `workspace_outbox` (actions `wipe_scratch` / `discard_permanent_generation` / `release_lock_only`) in the workspace-owning runs database; one `BEGIN IMMEDIATE` admission (storage reservation, pool total, job lock, byte-ledger maximum, `ACTIVE`); same-database terminal status + outbox atomicity, plus immediate universe outbox enqueue and root-status sweep repair when the canonical run row is stored separately; at-least-once processor with claim tokens, deterministic quarantine names and reconcile-every-combination retries; `AVAILABLE`/`LOST` + both locks released in the final transaction; startup sweeper as an admission barrier; permanent workspaces as immutable generations switched atomically; no `pin`, no `reuse`
- [x] 1.2 Credential broker in the outbound worker (in-memory, exact `(protocol, host, path)`, repeated `get`, `store`/`erase` ignored, torn down per operation); empty-environment git launcher with the forced options; address pinning via `http.curloptResolve` from the HTTP driver's per-address classification; stderr scrub to fixed classes; `git_read`/`git_write` grant scopes
- [x] 1.3 `workspace` sink `checkout`: full clone into staging → prerequisite-free bundle → delete staging → populate a fresh unpublished generation from the bundle → publish; intent journal; resolved-SHA receipt; typed consents `workspace_checkout`/`workspace_push`/`workspace_provision`; evidence without token or host path
- [x] 1.4 Code node `workspace:` binding: chain-only capability, exact-path bind rule, `ws.run/read/write/glob/bundle` with relative beneath-only paths, bounded drains, cumulative caps, workspace rlimit profile, RSS watchdog, parent-side outer-bwrap kill on timeout with verified exit
- [x] 1.5 `workspace` sink `push`: bundle copied through the held lease dirfd (beneath/no-symlink, bounded regular file) → credential-free staging `bundle verify` + fsck-checked `index-pack` + strict `fsck` → branch policy → credentialed fast-forward push from staging → `ls-remote` reconciliation; `discard`
- [x] 1.6 Admissions kind `workspace` (jobs/hour, bytes/hour), the MODIFIED checkout-as-read exception, the workspace bytes excluded from HTTP budgets, the seven failure classes with suggested actions
- [x] 1.7 Tests: gitfile/alternates/replace-ref workspace cannot make staging read another repository; hook/config in the checkout never runs credentialed; token absent from cmdline/environ/files/evidence; 401 retry succeeds; two concurrent admissions cannot oversubscribe; crash between terminal status and release repaired at startup before admission; `setsid` descendant dies with the namespace; default-branch push refused; checkout settles as read
- [ ] 1.8 Live proof on the founder universe through the app: checkout TinyAssets, run `compileall` in a workspace node, push a one-line change on a `tiny/…` branch, open the PR; deployed sha asserted

## 2. Slice B — provisioning

- [x] 2.1 Manifest extraction through the held lease dirfd (beneath/no-symlink, bounded regular files, digests); Python requirement grammar (`name[extras]==version ; marker --hash=…` only) and npm lockfile validator (`https://registry.npmjs.org/` tarballs only). Runtime checkout now checks resolved connection consent before extracting all manifests together; refusal precedes any resolver network. Local integration and Linux extraction tests pass; release acceptance remains in 2.3/3.1.
- [ ] 2.2 Resolver jail (no checkout, admitted manifests + empty cache, own network namespace, egress allowlist with per-address validation); `pip download --only-binary=:all: --require-hashes`; `npm ci --ignore-scripts` fetch; offline install in the workspace jail bound to the digests; `workspace_provision` consent; `workspace_provision_refused`
- [ ] 2.3 Tests: URL/path/VCS/include/option lines refused before network; sdist-only package refused; git-URL npm dependency refused; resolver cannot reach loopback/private/neighbours; offline install runs with no network; live proof: provision the checked-in hash-locked fixture `tests/fixtures/workspace/requirements-locked.txt` and run `pytest -q tests/test_docview.py` in a workspace node

## 2b. Drop-first operational exec migration — slice 1 landed, slice 2 in this tree

Design + approved amendment + delta scenarios:
`drop-first-operational-exec-amendment.md` (staging preface at its head);
coordinator disposition and opposite-family compatibility review (both
2026-09-20) in `2026-09-20-drop-first-ops-shape-disposition.md (archived in git)`;
root native proof on local fixtures 2026-09-21 in
`2026-09-21-drop-first-native-local-proof.md (archived in git)`. Shape APPROVED; no
further shape review needed. The reviewed source is preserved at
`84c116ae45992a974f6ca9625131b42509b15ae5` (PR #3894, draft); its 27-file
tree carried 13 release-critical paths against the scope guard's hard cap of 8,
so it is landing in two slices with runtime source byte-identical to that commit.

**Slice 1 — installed helper (LANDED: PR #3895, `f60e6656`, merged and
deployed 2026-09-21; root verified in production `/usr/local/libexec/ta-op`
as `-r-xr-xr-x 1 0 0` and `ta-op version` as uid/gid 1001 through the
protected deployed-sha and authenticated public-canary gates; 5
release-critical paths):**
`Dockerfile` (static build in the existing builder stage, root-owned `0555`
install at `/usr/local/libexec/ta-op` outside `/app` and `/data`, exit-78
smoke in both stages), `deploy/native/ta_op.c`, `deploy/native/ta_op_modes.tsv`,
`deploy/native/ta_op_native_check.sh`, `deploy/native/NATIVE-TEST-PLAN.md`;
`tests/test_ta_op_modes.py` (source/mode parity, helper safety, anchored
readback, compile-time-only status path, fixed-argv `claude-login`) with an
in-test mirror of the gate's TSV parser; one Dockerfile-shape test; the
installed-helper requirement synced into
`openspec/specs/daemon-runtime-and-dispatch/spec.md`. No workflow, compose,
env-apply, gate registration, caller, permission or root-start change in that
slice.

**Slice 2 — caller/healthcheck/env-apply/gate migration (this tree, exactly
8 release-critical paths, the scope guard's hard cap):** `scripts/check_drop_first_exec.py` +
`scripts/invariants/drop_first_exec.py` + `scripts/invariants_run.py`
registration; migrated callers `scripts/droplet.py`,
`deploy/apply-daemon-env-remote.sh` (version preflight above the fail-open
read and every mutation), `deploy/compose.yml` (`ta-op pulse` healthcheck),
`deploy/tinyassets-env.template`, `.github/workflows/apply-daemon-env.yml`,
both keepalive workflows (argv only), `scripts/workspace_bwrap_oracle.py`;
runbooks `deploy/DEPLOY.md`, `deploy/README.md` (rollback-pairing
correction); `tests/test_drop_first_exec_gate.py`,
`tests/test_drop_first_operational_migration.py`, the keepalive assertions in
`tests/test_dockerfile_shape.py`, and `test_ta_op_modes.py` switching to import
`load_modes` from the gate; the three delta scenarios (TTY is not an
exemption; env-apply refuses pre-mutation when the wrapper is absent; the
healthcheck runs the pulse route with bundle-before-image rollback) synced
into the main daemon spec 2026-09-21. The caller runtime and gate paths retain
the exact reviewed content of `84c116ae`; `Dockerfile` and `deploy/native/` are untouched by
this slice. No root-start, capability-set or readiness change.

Outstanding native, CI, deploy and live gates are carried by 3.1 below — they
are the release gate for this work, not separate delivery work.

## 3. Land

- [ ] 3.1 Release gate and land: native proof per `deploy/native/NATIVE-TEST-PLAN.md` (root ran rows 1–16 and 18 on local fixtures 2026-09-21, row 17 on a local fixture install only — `2026-09-21-drop-first-native-local-proof.md (archived in git)`; installed production ownership/mode and legacy-identity version verified, managed-bootstrap proof remains local), CI image build of slice 1 and live `/usr/local/libexec/ta-op version` on the deployed image (done 2026-09-21, PR #3895), then slice 2 (live `ta-op pulse` healthcheck green, public canary, rendered `ui-test`); installed-helper delta synced 2026-09-20 (slice 1) and the three slice-2 deltas synced 2026-09-21; still to do: sync the remaining workspace deltas into `openspec/specs/`, archive the change, PLAN.md pointer, plugin mirror parity, `deployed_sha.py --assert-contains`
