# Isolation landing audits: R1 (/app write audit), R2 (nlink), R3 (read-only liveness)

Date: 2026-10-06. Gates slices L4 (R1) and L1 (R2, R3) of the per-role-uid-split
landing plan. U1 = `feat/per-role-uid-split` at `c8c5042654`. Main and production =
`7e68ef26cb` (`deployed_sha.py`: production serves `7e68ef26cb4e`). Image under test:
`ghcr.io/tinyassets/tinyassets-daemon:7e68ef26cb4e`, digest
`sha256:e56a12959eb8c65ba4ccc520f6f183fa7f0d06bfa14556c5629f28393d6f7421`.

## Verdicts

| Risk | Gate | Verdict |
|---|---|---|
| R1: `/app` read-only, HOME moved | L4 | **No required runtime write under `/app`.** Nothing needs D2's duplicate-copy fallback. Two image fixes belong in L4: the stale root-owned `/tmp/codex.lock`, and the wrapper's `/app` default. |
| R2: unconditional `st_nlink != 1` read refusal | L1 | **Production measurement still needed (the lead runs it).** The earlier scan implies 1–5 multi-link inodes inside owner trees. The read-only census command is below. Decision rule in § R2. |
| R3: read-only `owner_state` flock | L1 | **No DEAD→UNKNOWN in any state production can reach.** Four states production does not create (all now refused) move DEAD→UNKNOWN. One intended change: an unwritable proof now reads DEAD instead of UNKNOWN. |

## R1: runtime writes under `/app` and HOME

### Live run

The production image (above) ran with compose's daemon posture:
- `cap_drop: ALL`, `no-new-privileges`, unconfined seccomp, apparmor and systempaths, 4g memory;
- compose's daemon environment, with `HOME=/home/tinyassets` (a uid-1001 `0700` tmpfs);
- `/data` on a fresh volume;
- `/app` replaced by a **root-owned copy mounted read-only**. That is stricter than U1's `chmod -R a-w` and gives the same answer for uid 1001 with no `DAC_OVERRIDE`.

Admission came from CI's `tests/fixtures/docker_admitted_process.py` (simulated admission), started through the real tini entrypoint.

| Exercise | Result |
|---|---|
| Boot (entrypoint, lifespan, uvicorn) | Up; no EROFS or EACCES in the logs |
| `ta-op pulse` (the compose healthcheck) | rc 0. Without `/data/release-state.json` it is rc 1 with "carries no git_sha", which is fixture state, not a write. |
| CI protocol probe (`docker_protocol_probe.py`, the canary with `--assert-handles`) | OK |
| Main's tests for served turns, run in the container against `/app` code. Covers the fake-codex served turn through the full OS sandbox, provider jail, node sandbox, workspace, tool jail and `ta` jail, ui_preview Chromium, engine-MCP server, git_bridge, workspace_git, turn coordinator, converse and orphan reconcile. | **1,283 passed, 0 EROFS.** Every failure is a repo file the image does not ship (`app_ui.js`, `deploy/compose.yml`, `scripts/claude_chat.py`, `scripts/check_drop_first_exec.py`) or pytest's AF_UNIX path length (passes with `--basetemp=/tmp/b`). |
| Real `codex exec -C /app` with a per-universe `CODEX_HOME` (the production shape) | All state went to `CODEX_HOME`: sqlite stores, sessions, skills, `.lock`. Nothing in HOME or `/app`. Reached the network (401 with no auth). |
| Real `codex` with no `CODEX_HOME` and no `~/.codex` | **Fails: `flock: cannot open lock file /tmp/codex.lock: Permission denied`.** Same result with `HOME=/app`. A pre-existing image defect; see F1. |
| Real `claude -p` in the daemon environment | Writes `~/.claude.json` and `~/.claude/{projects/-app,sessions,backups}`. With `HOME=/app` read-only it fails to persist silently. Production never runs claude in the daemon environment: each launch gets its own HOME under the universe's `provider-child` tmpfs (`providers/base.py:757-795`). |
| `docker diff` after everything | Root-filesystem writes went only to `/tmp`. HOME received only the Chromium fontconfig cache (`~/.cache/fontconfig`) and the manual claude run above. |

### Code inventory

Every row below writes to `/app` or HOME. Production paths not listed resolve to `TINYASSETS_DATA_DIR` or `/tmp`.

| # | Location | Write | Reached in production? | Fix |
|---|---|---|---|---|
| F1 | `Dockerfile:267-269` runs `/usr/local/bin/codex --version` as root at build. U1's Dockerfile does the same. | Leaves a `/tmp/codex.lock` in the layer that is root:root `0600` | Every codex call with no lock directory fails for uid 1001. Served and universe launches always set `CODEX_HOME`, so this is latent today. | L4: in the same `RUN`, `rm -f /tmp/codex.lock`, or run the check with `CODEX_HOME="$(mktemp -d)"` |
| F2 | `deploy/codex-flock-wrapper.sh:27` `${HOME:-/app}`, falling back to a shared `/tmp/codex.lock` | lock sentinel | Only with no `CODEX_HOME` | L4: default to `${HOME:-/tmp}`; make the fallback per-uid (`/tmp/codex-$(id -u).lock`) so an earlier writer's file cannot block another uid |
| F3 | `ui_preview.py:384,552`: Chromium inherits HOME | `~/.cache/fontconfig` (seen live); possibly `~/.pki/nssdb` | Yes, every `app_ui_preview` render | None needed: U1 creates `/home/tinyassets` 1001 `0700`. Keep HOME writable. A read-only HOME would cost a font-cache rebuild per render, not a failure. |
| F4 | `providers/base.py:1174-1205,1369`: live auth probe in the daemon environment | `~/.codex/*` | No: returns before running unless `~/.codex/auth.json` exists, and production has no platform login | None |
| F5 | `codex_provider._codex_workdir` (`:337`, used at `:1063`) | Nothing: only `-C <source root>` for a call with no universe | No: the router refuses a call with no universe, and the jail refuses a scope with no universe. Live, `-C /app` wrote nothing to `/app`. | None. The source-root default exists for host dev calls. |
| F6 | `storage.data_dir()` default `~/.tinyassets`; `idempotency.py:269` falls back to it on error | all state | No: compose and the Dockerfile set `TINYASSETS_DATA_DIR`. Compose's `slack-agent` service sets `HOME: /app` without it, but its module does not exist. | L4 already moves both compose `HOME`s. Delete `slack-agent` separately. |
| F7 | `preferences.py:39,106` | `~/.tinyassets/preferences.json` | Dead code: nothing imports it | Delete it in a later cleanup |
| F8 | `credential_vault.py:174` expands `~` in `codex_home` | vault files | No: containment (`base.py:922-932`) rejects it first | None |
| F9 | `fantasy_daemon/__main__.py:3386,3667` default `output/...` relative to cwd | universe state | Dev CLI only, not the image CMD | None |
| F10 | stdlib `tempfile` falls back to cwd (`/app`) when `/tmp` is unwritable | scratch | Only if `/tmp` is read-only | Keep `/tmp` writable in L4 (it is) |

These write nothing to `/app` or HOME, by code reading and the live run:
- `docker-entrypoint.sh` (the codex auth-bundle install was removed 2026-09-24; Dockerfile:372's comment is stale);
- the `ta-op pulse` / canary chain (stdlib HTTP only);
- node, workspace, tool and `ta` jails: `--clearenv`, `HOME=/tmp` tmpfs, and no bind of `/app`;
- engine-MCP and broker children (they inherit `HOME` and cwd but write neither);
- bytecode (`PYTHONDONTWRITEBYTECODE=1`; `-B` and `-I` children).

### L4 proof additions

1. Add F1 and F2 to L4.
2. Fix the stale comments: Dockerfile:256-263 ("sentinel in /app/.codex") and :372 (auth-bundle install).
3. Repeat this run on the L4 image. It reuses `docker_admitted_process.py`, so it needs no new tooling.
4. Run this read-only check on production before L4:
   ```powershell
   python scripts/droplet.py ssh -- 'docker diff tinyassets-daemon | grep -E "^[ACD] /(app|home|root)" | head -300'
   ```
   It lists what the live daemon has written to its own `/app` (today HOME) since the last deploy. Any entry outside `/app/.cache/fontconfig` and `/app/.pki` is a write this audit missed. Not run here: that is production access.

## R2: multi-link files the L1 reader would refuse

On U1 the shared descriptor reader `workspace_fs._open_regular_beneath` (`:636-693`) refuses `st_nlink != 1` with `UnsafePoolPath("... has N links ...")`:
- **unconditionally**: no `role_modes` or `broker_selected` gate;
- on POSIX only;
- before the identity check, which runs only on labelled roots.

`read_regular_file_beneath` and `copy_regular_file_beneath` share it. Confirmed on U1 in the Linux oracle: an `identity.md` with two links raises, and reads once the alias is removed.

### What the earlier scan implies

The 2026-10-05 alias scan counted 52,167 regular names against 52,162 sole-owner inodes, with 0 cross-owner and 0 unseen names. A "sole-owner inode" has `nlink ==` the names seen, all within one tree. So the 5 extra names are same-tree aliases: **between 1 and 5 inodes with nlink > 1, inside owner trees**. That scan covered owner trees only, so `/data/wiki` and other platform files are unmeasured. It also cannot say which reader those files reach.

### Which code paths refuse

From U1's caller map. Fail-closed paths turn a multi-link file into a user-visible error:

| Class (census name) | Readers on U1 | On refusal |
|---|---|---|
| `brain-file` (root `soul.md`, `identity.md`, `MEMORY.md`, `AGENTS.md`, …) | `universe_soul`, `soul_edit:151,369`, `onboarding/soul.py:15`, `memory_items.py:17`, `universe_intelligence`, `universe_self_model`, `agent_review` | **soul_edit raises** (and refuses the write, `soul_edit:357`); onboarding `handle_soul` and `MEMORY.md` items raise; the rest read as empty |
| `agent-workspace` and `other-universe-file` | owner `/u` view `api/universe_file_reads._read:172` → `read_file`, `list_files`, `app_ui` | "not found" or `AgentNotFoundError` |
| `wiki`, `platform` (`/data/wiki/**`, platform JSON) | `api/helpers._read_text/_read_json/_read_platform_text` → `api/wiki.py`, `api/universe.py`, `onboarding/session_store.py`, `work_targets`, `effectors/wiki_write_back`, `wiki/okf_export` | **raises**; the API action or turn fails |
| `canon` | `ingestion/canon_io:219`, `ingestion/core:192`, `api/universe._canon_json` | **raises**; ingestion fails |
| `fixed-file` | `config.py:158` (config.yaml), `notes.py:78`, `activity.log` (`_tail_file_lines` raises; status swallows), `dispatcher_config.yaml` → `config_corrupt` | mixed |
| `agents-settings` | `harness_settings:188` | `SettingsError` |
| `output` | `api/universe:2094` | error row |
| `skills`, `soul-versions` | `universe_tools:1214`, `universe_soul` | skipped |
| `package-cells` | `role_packages`, `role_package_cell` | the cell launch fails |
| `liveness-proof` | `universe_files.readonly_lock_file` (R3) | `owner_state` returns UNKNOWN |
| `sqlite`, `git-internals` | not a `workspace_fs` reader | none |

Code that creates multi-link files today:
- `universe_tools._promote_brain_files:307-329`: link, then unlink. An interruption leaves the root brain file **and** its `.agent-workspace` twin at nlink 2. That is exactly the brain-file class above. It is the likeliest source of the 5.
- `boxes/local.py:559` (dev only), `execution_authority/blob_stream.py:206-221`, `provider_authority.py:132`: the same link-then-unlink shape. Interruptions are rare.
- The agent's own `ln` or `git clone <local path>` inside its read-write workspace: git hardlinks objects by default on a local clone. These are legitimate same-owner aliases.

### The command for the lead (read-only)

`scripts/data_nlink_census.py` (this PR):
- stdlib only;
- metadata only: no payload is opened;
- O_NOATIME directories, no link followed, stays on one device;
- names are redacted by default.

It uses the same transport as the earlier alias scan:

```powershell
$env:TINYASSETS_DROPLET_KEY = "$env:USERPROFILE/.ssh/workflow_deploy_ed25519"
Get-Content -Raw scripts/data_nlink_census.py | python scripts/droplet.py ssh -- 'python3 -I -B - --root /var/lib/docker/volumes/tinyassets-data/_data'
```

- Exit codes: 0 = no multi-link file; 3 = some (see `refused_names_by_class` and `inodes`); 2 = a walk error.
- `--paths` prints real names. Use it only if a class needs a closer look.
- Dry-runs:
  - in the production image as uid 1001 through this exact stdin path: exit 3 on a synthetic interrupted promotion, classified `brain-file` plus `agent-workspace`;
  - `tests/test_data_nlink_census.py` in the Linux oracle: 4 passed, and it checks the tree is unchanged.

### Decision rule for L1

- **0 multi-link names in a fail-closed class:** land L1 as written.
- **Only `brain-file` or `agent-workspace` names from interrupted promotion:**
  - land L1 with a same-inode repair in `_promote_brain_files`: when target and source are `samestat`, unlink the workspace twin. It is the same inode in the same tree, so no data is lost and nothing crosses owners;
  - run the repair once over the found inodes before deploying.
- **Anything else** (agent `ln` or git local clones, wiki, platform): **gate the nlink refusal** to labelled dedicated-owner roots or `broker_selected()`.
  - Reason: on today's single-tree jail, a cross-owner alias cannot be planted (lead's clarification in `docs/concerns/2026-10-05-role-reader-hardlink-alias.md`). So with the switch OFF, the ungated check refuses only legitimate same-owner aliases and buys nothing.

## R3: the read-only `owner_state` probe

U1 replaces main's `owner_state` on POSIX with `_readonly_owner_state`:
- it pins the root with `open_dir_nofollow` (every component, no symlinks, absolute only);
- it opens `.consumer_liveness/<token>.lock` with `O_RDONLY|O_NONBLOCK|O_NOFOLLOW`;
- it refuses a non-regular file or `nlink != 1`;
- it takes `flock(LOCK_EX|LOCK_NB)`: success is DEAD, EWOULDBLOCK is ALIVE, any other errno is UNKNOWN;
- on exit it checks that the name and the parent still match, else UNKNOWN.

Main's version opened `O_RDWR`, followed links, and treated **every** flock error as ALIVE.

`docs/design-notes/2026-10-06-isolation-landing-audits-r3-parity-test.py` runs main's implementation verbatim against U1's for each on-disk state, through `storage/agent_turn_runner` (`orphan` is `== DEAD`; `alive` is `== ALIVE`). Ran on U1 in the Linux oracle as uid 1001, together with `test_orphan_ready_turn.py` and `test_orphaned_turn_reconcile.py`: **45 passed**.

| State | main | U1 | Production-reachable? |
|---|---|---|---|
| Live runner in another process | ALIVE | ALIVE | yes |
| Runner killed | DEAD | DEAD (and `reconcile_orphaned_turns` settles it as `abandoned`) | yes |
| Claim released in process | DEAD | DEAD | yes |
| No proof file / FIFO | UNKNOWN | UNKNOWN (never blocks) | yes |
| Proof not writable by the prober | UNKNOWN | **DEAD** | the intended change: the broker reads proofs it cannot write |
| Proof hardlinked | DEAD | **UNKNOWN** (the turn stays stuck) | No: the daemon creates proofs `O_CREAT` in its own directory. The census reports any as `liveness-proof`. |
| Proof or its directory is a symlink | DEAD | **UNKNOWN** | no |
| Base path through a symlink | DEAD | **UNKNOWN** | No: `storage.data_dir()` always returns a resolved path |
| Relative base path | DEAD | **UNKNOWN** | No, same reason |
| Non-EWOULDBLOCK flock error (ENOLCK) | ALIVE | UNKNOWN | No on ext4 or overlay, and `hold_liveness` could not lock either |
| Cleanup holds the lock and unlinks during the probe | ALIVE | UNKNOWN (the exit check fails) | Briefly; both are "not DEAD", and only `alive()` changes, toward correct |

The same semantics reach every other `owner_state` caller (automations, runs, assigned-queue consumer, agent_request_usage, universe_seats, workspace_staging). Each passes a base path derived from `data_dir()`. **L1 should carry the parity test** as `tests/test_owner_state_readonly_parity.py`; main cannot host it before L1, because `_readonly_owner_state` does not exist there.

## Not done here

- **The production census.** This lane does not touch production data. The lead runs the commands above.
- **The L4 image itself** is not built here. F1 and F2 are release-critical (`Dockerfile`, `deploy/`), so they belong to L4 and this PR changes no release-critical file.
- **A real chat turn against a live model.** The provider path was proved with the fake-codex served turn through the full OS sandbox, and the real codex binary up to the API's 401.
