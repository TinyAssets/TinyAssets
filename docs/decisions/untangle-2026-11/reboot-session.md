# Rebooted runner session selection as built (2026-11-18)

The CLI can report a session ID after launch, or replace the one it started with. The runner
saves that ID with the run. On main `f934871ad` and combined `5db0a6738`, reboot recovery
read the saved state and then built its command from the original launch document. A first
turn with a saved ID could refuse to recover. A later turn could resume an older session.
Warm renewal already used the latest ID.

Both restarts now build the continuation from the current run state, with the previous
fallback to the launch ID when no later ID is known. Ordinary launch still uses the initial
document. Kimi still selects continuation from the conversation working directory. The
runner's disk and RPC formats stay at their existing versions.

An independent offline comparison exercised the real invocation builder and the current five
served records in 80 combinations of original/saved ID, working directory and launch/recovery.
The runner and probes bundled successfully. The Worker dispatch test passed. Two new privileged
cases use the public recover command and check which session reaches the fake CLI. Running
those cases requires the repository's real-root sandbox test host.

## Open dependencies

- The command only reaches session selection after finding an unfinished job whose recorded
  PID is absent. An unrelated live process at that PID makes recovery skip the job. The
  ordinary PID check also serves other job consumers. Review a durable job identity against
  the pinned on-disk format, upgrades and rollback before changing it.
- For P-hooks, direct admission and queued dispatch independently enforce earlier waiting
  inputs. An answered approval is appended to the queue. Dispatch selects the engine again.
  Trace both admission paths, session/engine identity and deferred call approval before
  adding promptless input. Mail already uses an empty input while waiting to be reread.
  Keep ordinary reboot/renewal continuations distinct from an engine's deferred tool call.
