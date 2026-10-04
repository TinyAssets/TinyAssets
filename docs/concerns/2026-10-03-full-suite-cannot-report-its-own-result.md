---
severity: P1
title: The full suite exhausts file descriptors and cannot report its own result
filed: '2026-10-03'
summary: 'A whole-suite run in the Linux oracle reaches the end of the tests and then dies in pytest''s session teardown with OSError [Errno 24] Too many open files. Descriptors are gone before any pytest_sessionfinish hook can open a file, so there is no summary line and --junitxml cannot be written either. The run also reports thousands of failures that pass in isolation, so a wide run''s F count is not a failure set. Net effect: "the full suite is green" is not an available gate for any PR, and a set-compare across two wide runs compares two artifacts.'
---

# The full suite exhausts file descriptors and cannot report its own result

**Filed:** 2026-10-03, while producing the set-compare for PR #4291.
**Verified:** 2026-10-03, two independent runs of `python scripts/linux_oracle.py`
against the working tree at `e28620ebe` (python 3.11.16, bwrap 0.12.0, uid 1001,
25932 tests collected). Reproduced both times at the same point.
**Severity:** P1. Not a product defect — a **verification** defect. It removes the
whole-suite gate for every PR, and AGENTS.md § Testing requires set-comparing against
base before calling anything a regression.

## What happens

The run executes all 25932 tests, then dies in pytest's own `pytest_sessionfinish`.

Run 1, plain `-q`, killed in the tmpdir plugin's cleanup:

    File "/usr/local/lib/python3.11/site-packages/_pytest/tmpdir.py", line 337, in pytest_sessionfinish
        cleanup_dead_symlinks(basetemp)
      File "/usr/local/lib/python3.11/site-packages/_pytest/pathlib.py", line 354, in cleanup_dead_symlinks
        for left_dir in root.iterdir():
      File "/usr/local/lib/python3.11/pathlib.py", line 931, in iterdir
        for name in os.listdir(self):
    OSError: [Errno 24] Too many open files: '/tmp/b'

Run 2, with `--junitxml`, killed in the junitxml plugin writing its report:

    File "/usr/local/lib/python3.11/site-packages/_pytest/junitxml.py", line 652, in pytest_sessionfinish
        with open(self.logfile, "w", encoding="utf-8") as logfile:
    OSError: [Errno 24] Too many open files: '/work/.../head.xml'

Both are the same cause at a different victim: by the end of a wide run the process
holds no spare descriptor, so **every** `sessionfinish` hook that wants to open
something fails. That takes out the summary line *and* the structured report, which is
the normal escape hatch when a run ends badly.

## Why this is worse than a lost summary

The failures the progress output does show are mostly not real. Measured at
`e28620ebe`: 3944 of 25932 slots reported `F`/`E`, spread across 258 test files. Running
the heaviest ones alone in the same oracle:

| File | Wide run | Alone |
|---|---|---|
| `tests/test_workspace_effector.py` | 152 F | **passes** |
| `tests/test_universe_nodes.py` | 89 F | **passes** |
| `tests/test_desktop.py` | 96 F | 96 F (reproduces) |

So roughly 3850 of those 3944 are an artifact of running wide, and exactly one cluster
is real — and that one turned out to be pre-existing (identical failing ids at the
branch's merge base `f7ca7af4d`, 96/96, so nothing attributable to #4291).

The consequence for process: a set-compare built on two whole-suite runs compares two
artifacts, not two codebases. Nobody should read a wide-run failure count as evidence
until each cluster survives isolation.

## Probable cause

Unclosed SQLite connections. `with sqlite3.connect(...)` does **not** close the
connection — the context manager wraps a *transaction*, not the handle — and that
pattern is widespread: on `9c0fd4c6`, outside the plugin mirror, 12 direct
`with sqlite3.connect(...)` sites and 104 `with <store>.connection() as ...` sites,
against only 23 uses of `closing(` anywhere. Each connection left open holds descriptors
for the database and its `-wal`/`-shm` sidecars, so a run that opens thousands of
per-test databases climbs steadily toward the limit. Consistent with the observed
distribution: no failures before test index 8382, dense from roughly 19700 onward.

Not yet proven: nobody has counted open descriptors across the run. That measurement is
step 1 below, and it is cheap.

## Minimal fix

1. **Measure it.** Add a conftest hook, or run under a wrapper, that samples
   `len(os.listdir('/proc/self/fd'))` every N tests and prints it. That turns "probably
   the SQLite leak" into a curve, and names the test file where the climb starts.
2. **Close what is opened.** Audit the 12 `with sqlite3.connect(...)` sites and the 104
   `with <store>.connection() as ...` sites for handles that are never closed, and give
   them `contextlib.closing` or a `try/finally`. This is the actual fix; the rest is
   mitigation. Fixing the shared `connection()` helpers is likely to cover most of the
   104 in one change.
3. **Make the oracle survivable meanwhile.** Add `--ulimit nofile=…` to the `docker run`
   argv in `scripts/linux_oracle.py` (built at `scripts/linux_oracle.py:266`, which
   passes no `--ulimit` today) so a wide run can at least finish and report. Mitigation
   only: it moves the ceiling, it does not stop the leak.
4. **Record the usable route** in the testing section: for attribution, one wide run to
   locate clusters, then run each candidate file **alone**, then set-compare only what
   reproduces, diffing sorted `FAILED` ids rather than counts. That is what produced the
   #4291 answer in two 7-second runs after four failed attempts at the wide route.

## How to resolve

Show a whole-suite oracle run that prints its own summary line, and a descriptor count
that is flat rather than climbing across the run. Delete this file then.

## Related

- `AGENTS.md` § Testing — "A local Windows run is not an oracle on its own. Pin the tree
  and set-compare against the same suite at base before calling anything a regression."
  That instruction currently has no working implementation for the whole suite.
- `.github/heavy-test-files.txt` — heavy tests already skip on `pull_request`, so some
  files get no PR-time Linux evidence at all; this concern removes the fallback of
  running everything locally instead.
