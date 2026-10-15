> **Staged landing.** This amendment is the full approved design and is
> preserved as the source of truth. It is landing in two slices so each PR stays
> within the scope guard's hard cap of 8 release-critical files; the runtime
> source in both slices is byte-identical to the independently reviewed
> `84c116ae45992a974f6ca9625131b42509b15ae5` (PR #3894, draft, preserved).
> **Slice 1 (landed: PR #3895, `f60e6656`, merged and deployed 2026-09-21):**
> the installed helper only — `Dockerfile`, `deploy/native/ta_op.c`,
> `deploy/native/ta_op_modes.tsv`, `deploy/native/ta_op_native_check.sh`,
> `deploy/native/NATIVE-TEST-PLAN.md`, `tests/test_ta_op_modes.py`; root
> verified the deployed binary root-owned `0555` and its `version` mode as
> uid/gid 1001 before this slice opened. **Slice 2 (this tree, exactly 8
> release-critical paths):** the caller migration —
> `scripts/check_drop_first_exec.py` registered as the `drop-first-exec`
> invariant (`scripts/invariants/drop_first_exec.py`,
> `scripts/invariants_run.py`), `scripts/droplet.py`,
> `deploy/apply-daemon-env-remote.sh`, `deploy/compose.yml`,
> `deploy/tinyassets-env.template`, `deploy/DEPLOY.md`, `deploy/README.md`,
> both keepalive workflows, `.github/workflows/apply-daemon-env.yml`,
> `scripts/workspace_bwrap_oracle.py`, `tests/test_drop_first_exec_gate.py`,
> `tests/test_drop_first_operational_migration.py`, the keepalive assertions
> in `tests/test_dockerfile_shape.py`, `tests/test_ta_op_modes.py` importing
> `load_modes` from the gate, and the three delta scenarios
> (TTY-is-not-an-exemption, env-apply preflight, healthcheck pulse route)
> synced into `openspec/specs/daemon-runtime-and-dispatch/spec.md`. No
> root-start, capability-set or managed-runtime readiness change in either
> slice.

# Drop-first operational exec amendment

Approved shape. `2026-09-20-drop-first-ops-shape-disposition.md (archived in git)`
carries the coordinator disposition, the `DISAGREE_EVIDENCE` correction to the
legacy-entry predicate, and the opposite-family compatibility review's
`APPROVE` with two binding conditions. Both conditions are implemented here.
That file is a verbatim copy of the coordinator's artifact, taken from the
root checkout on 2026-09-20 so the citation resolves inside this branch; it was
authored by the coordinator, not by this lane. Shape review is closed; this
file records what was built and why, so the delta scenarios below can be synced
when the change lands.

**Scope.** This is the *supported drop-first exec/healthcheck migration plus
tested gate*, authorized as D1 and D3 by that disposition. It is an operational
prerequisite, not an inventory-only ratchet: it supplies the runtime the
callsites must call, and migrates every one of them. It does not start the
container as root, change `Config.User`, `cap_add` or `no-new-privileges`, flip
a readiness or control-margin value, or perform any provider operation.

*(An earlier revision of this file numbered this "prerequisite four of the
chain at `design.md:416-418`". `design.md` in this change is 399 lines and has
no such chain — that citation pointed at nothing and has been removed rather
than re-anchored to a section written after it. The scope above is taken from
the disposition, which is the artifact that actually authorizes this work.)*

## What changed

| Artifact | Role |
|---|---|
| `deploy/native/ta_op.c` | the wrapper: guard, retirement, readback, fixed exec |
| `deploy/native/ta_op_modes.tsv` | the closed mode table, single source |
| `Dockerfile` (builder + runtime) | static compile in the existing builder stage; install root-owned `0555` at `/usr/local/libexec/ta-op` |
| `scripts/check_drop_first_exec.py` + `scripts/invariants/drop_first_exec.py` | the repo gate, registered in the invariant framework (so `invariants.yml` runs it) |
| `scripts/droplet.py`, `deploy/apply-daemon-env-remote.sh`, `deploy/compose.yml`, both keepalive workflows, `scripts/workspace_bwrap_oracle.py` | the migrated real callers |
| `deploy/DEPLOY.md`, `deploy/README.md`, `deploy/tinyassets-env.template`, `.github/workflows/apply-daemon-env.yml` | migrated runbooks + the rollback-pairing correction |

Untouched on purpose: `Config.User`, `cap_add`, `no-new-privileges`, root-start,
any readiness flag, any control-margin value, every keepalive **schedule** and
**enabled state** (argv migration only), and every managed resource family.

## The two entry identities

`getuid()==0` — managed-bootstrap entry, for after the root-start flip.
Requires *exactly* the five caps (`0x2001c1`) permitted/effective/bounding with
inheritable and ambient empty; then NNP, keepcaps clear, ambient clear, whole
bounding set dropped to `cap_last_cap`, `setgroups(0)`,
`setresgid`/`setresuid(1001)`, explicit `capset` zero — and reads every one of
them back, plus a `setuid(0)` that must fail `EPERM`, before any runtime target
exists.

`getuid()==1001` — **legacy-rootless verification**, which is production today.
Asserts all four UID and all four GID positions are 1001, all five cap sets are
0 and `NoNewPrivs` is 1, and permits supplementary groups only when the list is
empty **or is exactly the same primary gid 1001**.

The `Groups: 1001` predicate is not a guess: the coordinator's read-only
production command at ~10:09 UTC on 2026-09-20 —
`python scripts/droplet.py ssh -- 'docker exec tinyassets-daemon cat /proc/self/status'`
— returned `Groups: 1001`. Requiring an empty group list would refuse a healthy
production daemon. A supplementary entry equal to the primary gid conveys no
authority the in-force `setresgid(1001)` does not; `/app` and `/data` are
group-owned by that primary gid. Any other gid is refused.

**This branch is labelled what it is.** Nothing is dropped, because nothing is
held. It is *not* a managed-bootstrap drop receipt and must never be cited as
one. Its value is that a bare `docker exec` asserted none of this.

Anything other than uid 0 or 1001 is refused (exit 78).

## Closed mode table

Nine fixed modes, each preserving the argv a real caller runs today:
`version`, `env-summary` (in-wrapper builtins), `pulse`, `canary`, `printenv`,
`claude-keepalive`, `codex-keepalive`, `bwrap-oracle`, `claude-login`. No mode accepts an
executable path, an interpreter switch or a shell string. `printenv` is the
only mode with an operand: one `^[A-Z_][A-Z0-9_]*$` NAME, validated after the
drop. Unknown mode, no mode and wrong arity fail closed **before** the identity
guard runs; a malformed NAME is refused **after** the identity branch and
descriptor closure, still before any target exec. The `main()` order in
`deploy/native/ta_op.c` is: mode name, arity, identity branch,
`close_extra_fds()`, builtin or NAME validation, `execv`. Every refusal
therefore precedes the target, but only the first three are uid-independent.

*(An earlier revision of this paragraph said all three refusals ran before the
guard. The code and the native plan's "Which rows the entry identity can
reach" section say otherwise, and the driver implements the plan's ordering —
it reports an identity refusal on the NAME row as `NOT_PROVEN`, never as a
NAME result. Corrected 2026-09-21; no code changed.)*

`env-summary` satisfies the reviewer's binding condition (b): it is an
**in-wrapper post-drop print**, never an exec of `printenv | grep | sort`. The
three flag families (`AUTO_SHIP`, `OLLAMA`, `PIN_WRITER`) are
compiled in and matched against the **name** only. This is strictly less
exposure than today's callsite, which pipes the whole environment across ssh.

## Env-apply preflight — binding condition (a)

`deploy/apply-daemon-env-remote.sh:88` was
`docker exec "$DAEMON_CONTAINER" printenv "$1" 2>/dev/null || true`: a missing
wrapper returns the empty string, the caller reads that as "key unset", and
mutates plus restarts on a false premise. The version preflight therefore sits
**above** that read and above every mutation, checks the `ta-op 1 ` banner (not
merely the exit status), and refuses with `exit 1` — no restart, no bare
`printenv` fallback. This is what makes a merge-before-image-arrival safe.

## Rollback pairing

The compose healthcheck now runs `/usr/local/libexec/ta-op pulse`, a binary
that exists only in images built from this commit. That is safe because
`deploy_fail_safe.sh` restores the runtime bundle (which carries `compose.yml`)
**before** `set_image` converges the previous image, on both the internal
failure path and the `--restore-bundle` public-canary path.

The carve-out is stated, not hidden: the bundle is restored only when this run
installed one, so a **manual or image-only downgrade** leaves the new
`compose.yml` against an old image and the probe fails on a missing binary.
`deploy/DEPLOY.md` says so and says to downgrade the compose bundle in the same
step. Rollback semantics were not loosened to mask it.

## Gate scope, stated honestly

The gate governs repo-authored invocations. Two residuals are named rather than
papered over:

1. `scripts/droplet.py ssh -- <cmd>` forwards an arbitrary remote command, and
   any admin holding `~/.ssh/tinyassets_deploy_ed25519` already has arbitrary
   root SSH on the droplet. **Ad-hoc admin SSH is outside the repo gate by
   construction.** After a future root-start flip those ad-hoc execs would run
   as root; that residual belongs to the root-start decision, not here.
2. ~~A `-t`/`-it` exec is reported as a note.~~ **Removed.** The first revision
   of the gate exempted TTY-allocating execs on the reasoning that no workflow,
   timer or CI runner can allocate one. The coordinator's review rejected that:
   a TTY is a property of the invocation, not proof that automation cannot
   reach the command, and the exemption silently permitted arbitrary
   repo-authored argv. The gate now fails every bare exec, TTY or not
   (`tests/test_drop_first_exec_gate.py::test_a_tty_is_not_an_exemption`), and
   `scan_text` no longer has a second, softer return channel at all.

   The one real shape the exemption covered — the operator subscription login
   on a fresh `/data` volume — is the ninth mode, `claude-login`: fixed argv
   `/usr/local/bin/claude auth login --claudeai`, no operand, no path and no
   flag from the callsite, reached through the same verified retirement as
   every other mode. `/usr/local/bin/claude` is the image's own symlink to
   `/opt/claude-code-install/node_modules/.bin/claude`, so the program is the
   one the runbooks already documented. Both documented callsites
   (`deploy/DEPLOY.md`, `deploy/tinyassets-env.template`) are migrated.

   **This preserves an already-supported operator action after a verified
   drop. It is not authority to log in or to call a provider, and nothing in
   this change runs it.** No new broad exemption replaces the old one, and the
   concern that recorded the carve-out
   (`docs/concerns/2026-09-20-ta-op-interactive-tty-carveout.md`) is deleted,
   which is how a concern is resolved.

## Identity readback is a whole-line match

`identity_retired()` reads all four UID and all four GID positions back out of
`/proc/self/status`. The predicate was a plain `strstr()`, which is a **prefix**
test: `Uid:\t1001\t1001\t1001\t1001` is a substring of
`Uid:\t1001\t1001\t1001\t10010`, so an fsuid of 10010 — a different user —
satisfied a check whose whole purpose is to prove the identity is retired.
`status_line_is()` now requires a line boundary on both sides.

The regression is the `test_identity_readback_is_an_anchored_whole_line_match`
case in `tests/test_ta_op_modes.py` (red against the pre-correction source, which
has three `status_has(` occurrences — the definition at `ta_op.c:142` and the
two readback call sites at `:161` and `:162` — and no anchored predicate) plus
row 18 of the native plan,
which builds a second binary with the compile-time-only `TA_STATUS_PATH`
override against a crafted status file. Production never defines that macro and
a test asserts the Dockerfile does not. No authority, capability set, group
rule or entry branch changed.

## One citation to avoid in the D2 regression

`output/claude-handoff-reaping-result.md` (Fable's lane, `714e9683`) records
`app-pulse` manual exit 1. **The fixture was not token-less** — an earlier
addendum said so and the disposition corrects it: the raw run-5 receipt
contains a synthetic canary token. The failure is a missing `git_sha` in a
`network none` fixture container, not missing authorization. It is **not**
evidence about the production `--pulse-only` healthcheck that `ta-op pulse`
now wraps, in either direction. The production healthcheck is unchanged by
this correction.

---

## Native evidence (root, 2026-09-21) — local fixtures, not production

`2026-09-21-drop-first-native-local-proof.md (archived in git)` is the root
coordinator's verbatim artifact, copied into this branch. Source candidate is
this exact head. In one sentence each, what it establishes and what it does not:

- **Rows 1, 2, 3, 4, 6, 16** — driver `ta_op_native_check.sh` in a disposable
  rootless container (`--user 1001:1001 --cap-drop ALL`, NNP, `--network none`):
  11 pass, 0 fail, 1 skip (row 17: no installed path in that container),
  1 NOT_PROVEN (row 14, by design of the driver). Row 3 and row 16 therefore
  ran past the identity guard, as the plan requires for those rows.
- **Rows 5, 7, 8, 9, 11, 12** — entry-refusal matrix, one container per row:
  every expected `TA_OP_REFUSED:*` tag and exit 78 observed, including the row 8
  mutation control (`legacy-entry-caps-not-empty`).
- **Rows 10 + 13** — root plus exactly the five caps, target substituted by a
  LOCAL assertion probe in an ignored fixture image: the child (pid 1) observed
  all four Uid/Gid 1001, empty `Groups`, all five cap sets 0, `NoNewPrivs 1`.
  This is post-drop identity for a substituted target, not the production
  `printenv` binary.
- **Rows 14 + 15** — a fixed launcher opened fd 9 without `CLOEXEC` and exec'd
  the exact helper: the target reported `fd9: closed` on both the root+5-cap and
  rootless paths; `strace -f -e trace=openat,close,execve` under default seccomp
  on the rootless path showed the helper opening only `/proc/self/status` and
  `/proc/self/fd` before `close(9)=0` then `execve(/usr/bin/printenv)=0`, with
  no loader, NSS, `/etc/passwd`, `/etc/group`, `/app` or `/data` open before it.
- **Row 18** — crafted fourth-UID `10010` status: the fixed source exits 78
  `fs-uid-readback`; the pre-correction `7302c91a` prefix implementation exits 0.
  Both built static as uid 1001 with warnings fatal. The mutant control held.
- **Row 17** — LOCAL fixture install reads mode 555, uid 0, gid 0. The
  production installed image has **not** been checked.
- **Pytest cohorts** (root-run, independent of this lane): Windows venv
  87 passed / 0 skipped on the four `ta_op`/gate/migration/Dockerfile files;
  canonical Linux oracle 74313: 102 passed / 1 skip (the gate's git-history
  base-object control, absent from the oracle's source archive) on those four
  plus `test_invariants_framework.py`; Windows rerun 15125: 103 passed / 0
  skipped; operational rollback/flag Linux cohort 33064, now terminal:
  canonical oracle `--no-bwrap -q tests/test_apply_daemon_env_voice_flags.py
  tests/test_deploy_bundle_validator.py tests/test_deploy_bundle_transaction.py`
  → **86 passed, 0 skipped, 56.24s**.

**Still open, and not claimed here:** the production image building and
installing the wrapper (CI), the installed-image row 17, `deployed_sha.py
--assert-contains`, the live `ta-op pulse` healthcheck, the public canary and a
rendered `ui-test`. Root-start is not authorized by any of this. The provider
modes (`claude-keepalive`, `codex-keepalive`, `claude-login`) were never run.

## Delta scenarios — synced 2026-09-21

Synced into `openspec/specs/daemon-runtime-and-dispatch/spec.md` from source
head `20573b0f` in the metadata commit that follows it. The main spec had no
prior healthcheck requirement, so the third block below is ADDED, not
MODIFIED, in the main spec; the heading here is corrected to match. The
change is **not** archived: 3.1 in `tasks.md` still carries the production
image, deploy and live gates.

### ADDED Requirement: Repo-authored operational execs into the daemon reach their target only after a verified identity retirement

The production image SHALL carry a statically linked, root-owned `0555`
wrapper at `/usr/local/libexec/ta-op`, outside every directory chowned to the
runtime user, and every repo-authored `docker exec` into the daemon container
SHALL invoke it with a mode declared in `deploy/native/ta_op_modes.tsv`. The
wrapper SHALL validate the mode name and argument count, then run the
entry-identity branch, then close descriptors above standard error, and only
then run a builtin, validate the single `printenv` operand, or exec the fixed
target. Every refusal SHALL occur before any runtime target is executed.

#### Scenario: Managed-bootstrap entry retires every set before the target exists
- **WHEN** the wrapper starts with `getuid()==0`
- **THEN** it SHALL refuse unless exactly `CAP_CHOWN`, `CAP_SETGID`,
  `CAP_SETUID`, `CAP_SETPCAP` and `CAP_SYS_ADMIN` are permitted, effective and
  bounding with inheritable and ambient empty
- **AND** it SHALL set no-new-privileges, clear keepcaps and ambient, drop the
  whole bounding set, clear supplementary groups, set all three GIDs then all
  three UIDs to 1001, zero every capability set, and read all of that back —
  including a `setuid(0)` that fails `EPERM` — before it execs anything.

#### Scenario: Legacy-rootless entry verifies and is not labelled a drop
- **WHEN** the wrapper starts with `getuid()==1001`
- **THEN** it SHALL require all four UID and all four GID positions to equal
  1001, all five capability sets to be zero, `NoNewPrivs` to be 1, and the
  supplementary group list to be empty or to contain only gid 1001
- **AND** any other supplementary gid SHALL be refused
- **AND** the result SHALL be recorded as legacy-rootless verification, never
  as a managed-bootstrap drop receipt.

#### Scenario: Any other entry identity is refused
- **WHEN** the wrapper starts with a uid other than 0 or 1001
- **THEN** it SHALL exit 78 without execing.

#### Scenario: The mode table is closed
- **WHEN** the wrapper is invoked with no mode, an undeclared mode, or the wrong
  argument count for a declared mode
- **THEN** it SHALL exit 78 before the entry-identity branch runs and without
  execing
- **AND** no mode SHALL accept an executable path, an interpreter switch or a
  shell string.

#### Scenario: A malformed printenv operand is refused after the identity branch and before the target
- **WHEN** `printenv` is invoked with an operand that does not match
  `^[A-Z_][A-Z0-9_]*$`
- **THEN** the wrapper SHALL first run the entry-identity branch and close
  descriptors, then exit 78 with `TA_OP_REFUSED:env-name` without execing
- **AND** an identity refusal reached on that path SHALL be reported as an
  identity result, never as a NAME result.

#### Scenario: The filtered environment summary never leaves the wrapper
- **WHEN** `env-summary` runs
- **THEN** the wrapper SHALL print, after the identity retirement, only those
  environment entries whose NAME matches the compiled-in flag families, sorted
  in-process
- **AND** it SHALL NOT exec a shell, a pipeline, or a full `printenv`.

#### Scenario: Descriptors do not survive into the target
- **WHEN** the wrapper execs a runtime target
- **THEN** every descriptor above standard error SHALL have been closed first.

#### Scenario: Allocating a terminal is not an exemption
- **WHEN** a repo-authored `docker exec` into the daemon container allocates a
  terminal (`-t`, `-it`, `-ti`, `--tty`) and does not invoke the wrapper
- **THEN** the gate SHALL report it as a violation, not as a note
- **AND** the gate SHALL expose no second, non-failing finding channel
- **AND** the operator subscription login SHALL be reached as the fixed
  `claude-login` mode, whose argv is compiled in and takes nothing from the
  callsite.

#### Scenario: The identity readback matches a whole line
- **WHEN** the wrapper reads the UID and GID lines back out of the kernel
  status file
- **THEN** it SHALL require the match to begin and end at a line boundary, so
  that a longer field value — an fsuid of 10010 against an expected 1001 —
  SHALL NOT satisfy the readback.

### ADDED Requirement: Environment application refuses before mutating when the wrapper is absent

The remote env-apply helper SHALL verify the wrapper's fixed version route in
the running daemon before its first read of the running process and before any
environment or service mutation.

#### Scenario: Absent or unacceptable wrapper aborts pre-mutation
- **WHEN** the version route is missing, errors, or returns an unexpected banner
- **THEN** the helper SHALL abort with a non-zero status before writing the env
  file and before restarting the daemon
- **AND** it SHALL NOT fall back to an unwrapped `printenv` read.

### ADDED Requirement: The daemon healthcheck runs the drop-first pulse route

*(Was headed MODIFIED. `openspec/specs/` had no healthcheck requirement to
modify — the only prior mention is the Purpose line recording the 2026-08-29
deletion of the worker healthcheck — so this is new requirement text.)*

The daemon healthcheck SHALL invoke `/usr/local/libexec/ta-op pulse` in exec
form, with no shell fallback.

#### Scenario: Rollback restores the compose bundle before the image
- **WHEN** a deploy rolls back, on either the internal-failure path or the
  public-canary `--restore-bundle` path, and that run installed a bundle
- **THEN** the runtime bundle SHALL be restored before the previous image is
  recorded and converged
- **AND** documentation SHALL state that a manual or image-only downgrade does
  not preserve the pair and must downgrade the compose bundle in the same step.
