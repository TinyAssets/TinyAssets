# `ta-op` native test plan and driver

**Status 2026-09-21.** The lane that authored this plan ran none of it. The
root coordinator has since run rows 1–16 and 18 locally, in disposable WSL
Docker containers on this exact source, and row 17 against a LOCAL fixture
install only; the verbatim artifact is
`docs/reviews/2026-09-21-drop-first-native-local-proof.md` and the row-by-row
map is in the amendment's "Native evidence" section. Those are local fixture
results: the production image, the installed-image row 17, the live
healthcheck and every deployed gate remain open. No static assertion
anywhere in this change is claimed as native runtime proof:
`tests/test_ta_op_modes.py` proves the *table* and the *source predicates* are
coherent, `tests/test_drop_first_exec_gate.py` proves the *gate* fires, and
neither says anything about what the binary does on a running kernel. Only the
rows below can do that, and all of them need Linux.

The driver is `deploy/native/ta_op_native_check.sh`. It runs only the rows the
*current* privilege context can actually reach and reports every other row as
`SKIP` or `NOT_PROVEN`, by name and with the reason. **A `SKIP` is never a
pass, and the driver never infers a boundary from an exit status that would be
the same either way.**

## Build (unprivileged, ordinary compiler; safe anywhere with gcc)

```
gcc -static -O2 -Wall -Wextra -Werror -o /tmp/ta-op deploy/native/ta_op.c
ldd /tmp/ta-op   # must say: not a dynamic executable
```

`-Werror` is part of the contract: a warning in a binary that performs a
privilege retirement is a defect.

## Which rows the entry identity can reach

`main()` validates the **mode name** and the **argument count** before it
looks at the identity, then runs the uid branch (`drop_from_root()` at uid 0,
`verify_legacy_rootless()` at uid 1001, refuse otherwise), then closes
descriptors, and only then runs a builtin or validates the `printenv` NAME.

That ordering decides what each row can prove:

- **Genuinely uid-independent:** unknown mode, wrong arity, no mode at all.
  They refuse before the identity branch, so any uid reaches them.
- **Post-identity, NOT uid-independent:** malformed `printenv` NAME, and
  `env-summary`. At a uid that is neither 0 nor 1001 the wrapper answers
  `TA_OP_REFUSED:unexpected-entry-uid`; at uid 0 without exactly the four bootstrap caps
  it answers `TA_OP_REFUSED:exact-bootstrap-caps`; at uid 1001 in a posture that
  fails the verification it answers one of the `legacy-entry-*` tags. All of
  those are exit 78 too. **An identity refusal must never be recorded as a
  NAME result** — in either direction. The driver probes with `version` first
  and skips these rows when the guard refuses.

## Rows

| # | Row | Context needed | Expected |
|---|---|---|---|
| 1 | unknown mode | any uid | exit 78, `TA_OP_REFUSED:unknown-mode`, no exec |
| 2 | wrong arity (`pulse extra`, bare `printenv`) | any uid | exit 78, `TA_OP_REFUSED:arity` |
| 3 | malformed NAME (`printenv 'a b'`, `printenv lower`, `printenv 9X`, `printenv ''`) | **post-identity** — uid 1001 in production posture, or uid 0 with exactly the four bootstrap caps | exit 78, `TA_OP_REFUSED:env-name`. An identity tag here is NOT_PROVEN for this row, not a failure of it |
| 4 | no mode at all | any uid | exit 78, `TA_OP_REFUSED:no-mode` |
| 5 | unexpected entry uid | Linux, run as a uid that is neither 0 nor 1001 | exit 78, `TA_OP_REFUSED:unexpected-entry-uid` |
| 6 | **rootless exact groups** — uid/gid 1001, `Groups: 1001` | Linux container matching production posture (`--user 1001:1001`, `--cap-drop ALL`, `--security-opt no-new-privileges`) | `version` exits 0 and prints `ta-op 1 modes=6` |
| 7 | rootless with a foreign supplementary group | same, plus `--group-add 65534` | exit 78, `TA_OP_REFUSED:legacy-entry-unexpected-group` |
| 8 | **mutation control** — rootless entry into a container with caps | `--user 1001:1001 --cap-add SYS_ADMIN` | exit 78, `TA_OP_REFUSED:legacy-entry-caps-not-empty`. *Without this row the legacy branch is decorative.* |
| 9 | rootless without NNP | `--user 1001:1001 --cap-drop ALL` and no `no-new-privileges` | exit 78 at `nnp-readback` |
| 10 | **root + exactly the bootstrap caps** | `--user 0 --cap-drop ALL --cap-add KILL --cap-add SETGID --cap-add SETUID --cap-add SETPCAP` | full drop runs; the child reports uid/gid 1001, `Groups:` empty, all five cap sets 0, `NoNewPrivs: 1` |
| 11 | root with a fifth cap | row 10 plus `--cap-add NET_ADMIN` | exit 78, `TA_OP_REFUSED:exact-bootstrap-caps` |
| 12 | root with three caps | row 10 minus `--cap-add KILL` | exit 78, `TA_OP_REFUSED:exact-bootstrap-caps` |
| 13 | **post-drop target identity** | row 10 with a mode whose target prints `/proc/self/status` | the *target*, not the wrapper, reads uid/gid 1001 and all-zero caps |
| 14 | **descriptor boundary** | Linux + `strace` (see below) | `close(9)` appears in the trace **before** the `execve`, and fd 9 is absent from the target's `/proc/<pid>/fd` |
| 15 | **loader / env boundary** | any Linux + `strace` | `strace -f -e trace=openat` shows no `ld.so`, no `/etc/nsswitch.conf`, no `/etc/passwd`, no `/etc/group`, no `/app` or `/data` path opened before the `execve` |
| 16 | `env-summary` filtering | **post-identity** (same contexts as row 3) | with `FOO=1 TINYASSETS_GOAL_POOL=off SECRET_TOKEN=ollama-token` set, prints exactly `TINYASSETS_GOAL_POOL=off` — the value containing `ollama` is NOT printed |
| 17 | wrapper is not setuid/setgid | any | `stat -c %a` is `555` and `%U` is `root` on the installed path |
| 18 | **identity readback is a whole-line match** | separate build, uid 1001 | see below — the crafted-status regression |

### Row 14 — why an exit status proves nothing here

An earlier driver ran `exec 9< /etc/hostname; ta-op printenv PATH` and recorded
a pass because `printenv` exited 0. `printenv` exits 0 whether or not fd 9 was
closed; it never looks. That is not evidence, and the row is now reported
`NOT_PROVEN` by the driver. The observable check is the trace:

```
exec 9< /etc/hostname
strace -f -e trace=close,execve -o /tmp/ta-op-fd9.trace /tmp/ta-op printenv PATH
# close(9) must appear, and it must appear BEFORE the execve line:
grep -n 'close(9)\|execve' /tmp/ta-op-fd9.trace | head
```

`strace` needs `CAP_SYS_PTRACE` or a same-uid target with unrestricted
`ptrace_scope`; run it in the disposable container, not on the droplet.

### Row 18 — the crafted-status regression

`identity_retired()` reads the four UID and four GID positions back out of
`/proc/self/status`. The predicate used to be a plain `strstr()`, which is a
**prefix** test: `Uid:\t1001\t1001\t1001\t1001` is a substring of
`Uid:\t1001\t1001\t1001\t10010`, so an fsuid belonging to a different user
satisfied the readback. `status_line_is()` now requires a line boundary on
both sides.

No container flag can set an fsuid independently of the uid (`setfsuid(2)` is
callable only by the process itself), so the boundary is exercised by pointing
the readback at a crafted file with the compile-time-only `TA_STATUS_PATH`
override. The production Dockerfile never defines it, and
`tests/test_ta_op_modes.py` asserts that.

```
printf 'Name:\tta-op\nUid:\t1001\t1001\t1001\t10010\nGid:\t1001\t1001\t1001\t1001\nNoNewPrivs:\t1\nCapInh:\t0000000000000000\nCapPrm:\t0000000000000000\nCapEff:\t0000000000000000\nCapBnd:\t0000000000000000\nCapAmb:\t0000000000000000\n' > /tmp/fake-status
gcc -static -O2 -Wall -Wextra -Werror -DTA_STATUS_PATH='"/tmp/fake-status"' \
    -o /tmp/ta-op-fakestatus deploy/native/ta_op.c
# as uid 1001, in the rootless posture container:
/tmp/ta-op-fakestatus version    # must exit 78 with TA_OP_REFUSED:fs-uid-readback
```

The same binary built from the **pre-correction** source exits 0 here. That is
the discriminator: this row is only worth running if it is run against both.

Rows 1, 2 and 4 run unprivileged on any Linux box with no container at all.
Rows 3, 6–13, 16 and 18 need the identity posture, so they need a disposable
container. (Run 2026-09-21 by root in that posture; see the status paragraph
at the top. Rows 14 and 15 were supplied by a local fd-launcher fixture plus an
`strace` fixture image, both offline at execution time.)

**No mode executes a provider.** The `claude-keepalive`, `codex-keepalive`
and `claude-login` modes were removed 2026-09-24 with the host logins they
served: the platform has no LLM (AGENTS.md Hard Rule 15).

## Remaining proof this plan does NOT supply

- The production image actually building with the new builder stage (CI).
- `python scripts/deployed_sha.py --assert-contains <sha>` after deploy.
- The live public canary and a rendered `ui-test` conversation.
- The `ta-op pulse` healthcheck going green on the real daemon.
