# L11 implementation receipt

Draft PR #4513 targets `feat/per-role-uid-split`. The lane-specific target
overrides the generic instruction to target main. Both origin/main and the
parent were merged without rebase or force push; the resumed worktree was clean.

Implemented broker-only bearer/basic/OAuth2 injection for exact smart-HTTP git
host/repository scopes. A launch gets ephemeral URL rewrites on its command
center's existing egress proxy. Neither the real credential, broker socket,
vault nor a credential helper enters the box. Upload and download use bounded
binary IPC, current grant/policy checks and generation fencing. Redirects and
secret-bearing responses refuse. Unknown push outcomes are never replayed.
D72 per-owner git_bridge is preserved and its tests pass.

Linux oracle commands (each with `MSYS_NO_PATHCONV=1`):

```
python scripts/linux_oracle.py -- -q tests/test_agent_git_credentials.py tests/test_universe_tools.py tests/test_universe_tools_jail.py tests/test_broker_server.py tests/test_universe_egress.py tests/test_outbound_ssrf_driver.py tests/test_outbound_connection_ledger.py tests/test_outbound_http_connection.py --basetemp /tmp/b
```

**322 passed, zero skips.** Includes a 2 MB binary push, a second nonempty clone,
fetch of a new remote commit, byte comparison, filesystem/environment/output/
argv checks, another owner's refusal, route lifetime and wrong-proxy refusal,
six upload/download credit-starvation revoke/scope/fence checks, automatic
owner-catalog selection, and local bash continuing when git setup is refused.

```
python scripts/linux_oracle.py -- -q tests/test_agent_git_credentials.py tests/test_broker_server.py tests/test_universe_egress.py tests/test_broker_scan.py tests/test_broker_upstream_stream.py tests/test_broker_disconnect.py tests/test_broker_fence.py tests/test_git_bridge.py tests/test_role_git.py tests/test_converse_turn_cost.py --basetemp /tmp/b
```

**224 passed, 1 failed, zero skips.** The failure is the parent's
`test_pending_remove_captures_broker_incarnation_and_rejects_replacement`:
browser consent refuses the fixture's ordinary identity before its incarnation
check. Recorded separately; no gate or assertion weakened. Static prompt budgets
pass unchanged on Linux.

```
python scripts/linux_oracle.py -- -q tests/test_scoped_identity_reset.py tests/test_universe_server_isolation.py tests/test_mcp_instruction_surfaces.py --basetemp /tmp/b
```

**146 passed, zero skips.** These heavy-file checks cover scoped identity,
universe isolation and MCP instruction surfaces.

Ruff passes for all lane Python files. Plugin mirror rebuild and import probe
pass. OpenSpec strict validation passes. Cross-family Claude verdict **ADAPT**;
all findings addressed, with disposition in review.md. A mirror rebuild raced
one oracle source copy (tar refused a changing directory); that run is not
counted as verification and was rerun after the mirror stabilized.

Full PR hygiene against `origin/feat/per-role-uid-split`: **262 added, 0 removed,
0 tampering** (includes merged-main work). Comparison from the pre-implementation
commit: **12 added, 0 removed, 0 tampering**. No new skip/xfail was introduced.

The original Docker-blocked concern is resolved. This is synthetic Linux proof
through the served `/u` jail, not a production `/cc` rollout. Task 2.4 remains
open: no deploy SHA assertion, live real-user app pass, or production provider
integration is claimed. No real repository was used as a capability probe.

## Continuation: protected consent fixture (2026-10-05)

Merged the latest parent owner-cell lifetime changes and origin/main without
rebasing. The disconnect fixture now answers through tests.owner_answer, which
uses the actual cookie/origin-checked approval handler. Its incarnation-refusal
and retained-connection assertions are unchanged; no consent bypass was added.
The resolved concern was removed after verifying its committed history.

Linux oracle: 126 passed, zero skips across test_broker_disconnect,
test_consent_owner_answers and test_owner_launcher_client. After merging main,
the first two files passed again: 121 passed, zero skips. Commands use
MSYS_NO_PATHCONV=1 python scripts/linux_oracle.py -- -q <the named tests under
tests/, with .py suffix> --basetemp /tmp/b. Ruff passed for the fixture and
merged launcher client.
