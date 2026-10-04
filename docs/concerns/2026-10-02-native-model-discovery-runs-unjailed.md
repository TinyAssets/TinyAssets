---
severity: P2
title: Native metadata confinement deployment and live acceptance remain unverified
filed: '2026-10-02'
summary: 'PR #4404 repairs the unjailed metadata spawn; hosted synthetic isolation proof passed on 2026-10-04. Keep this concern until automatic deployment and real vendor enumeration under confinement are verified.'
---

# Native model discovery launches the provider binary outside the jail

**Filed:** 2026-10-02, from the cross-family review of the per-role uid-split design
(PR #4331), which had to enumerate every process running at the daemon's uid.
**Verified:** 2026-10-02 against `origin/main` `342ab4101`, by reading the code — not
observed in production.
**Severity:** P2. A defence-in-depth and consistency gap, not a demonstrated read.
See *Why P2 and not P0* below; it becomes P1 the moment any user-influenced value
reaches the discovery argv or protocol.

## Source fix and remaining verification (2026-10-04)

PR #4404 routes metadata through the confined owned-process launcher and binds only the owner's exact snapshot. The original code observations below describe the pre-fix main tree `342ab4101`, not the repaired source. On reviewed source `06e2ebb56bb5bd6ba0578daf3c461ad38c00c9d1`, hosted Linux jail run 37191691078 passed the synthetic metadata isolation case with the no-skip gate; run 37191691029 passed all six affected shards. Local Linux oracle proof also passed 23 tests with zero skips. Runtime security review is recorded in PR comment 5977935795, with subsequent main-merge/skip-budget integration independently approved by Claude.

This concern remains open until the merged SHA is verified deployed using the command below. Synthetic Python proof establishes the OS boundary but does not establish that the installed vendor CLI can enumerate real models under the strict jail; that live acceptance is still owed. No live credential/provider calls or real-user clean-use evidence are claimed.

## Original finding (pre-fix main)

The vendor-neutral jail landed at **one** spawn point. `providers/owned_process.py:635-652`
imports `confine_launch`, builds a bubblewrap `jailed` launch and execs that; this is the
path every provider CLI takes for inference.

Native model discovery is a **second** spawn point and goes nowhere near it:

- `providers/base.py:1396-1421` (`BaseProvider.enumerate_models`) builds an environment
  with `subprocess_env_for_provider` and calls `read_native_catalogue(...)` with
  `cwd=str(credential_snapshot_dir)` (1418-1420).
- `providers/native_jsonrpc_discovery.py:126-134` runs
  `asyncio.create_subprocess_exec(*argv, env=env, cwd=cwd, ...)`. The only confinement
  it applies is `start_new_session=True` (128), which is process-group hygiene, not
  isolation. Neither file mentions `bwrap`, `jail` or `confine`.
- The binary is a full agent executable: `providers/codex_provider.py:766-768` is the
  only provider that declares native discovery, and its argv is
  `_resolve_codex_cmd()` plus `("app-server",)`.

So that process has:

- **the daemon's filesystem view**, including `/data/<every other command center>/` and
  their `.credential-vault.json` files. The jailed path denies exactly this: the jail
  refuses a bind resolving outside the owning command center
  (`providers/provider_jail.py:169,386`) and masks every hidden platform entry, the vault
  and `.credentials/` included (316-346);
- **a live subscription credential**, as `CODEX_HOME` and as its working directory. The
  snapshot itself is handled carefully — `providers/native_discovery.py:85-96` re-derives
  custody and compares it to the passed reference before copying, and `117` cleans the
  snapshot up in a `finally` — so the custody side of this is sound. The reach is the gap,
  not the credential handling;
- **no capability bound** beyond the daemon's own, where the jailed path runs under
  bubblewrap with `--unshare-all` (`provider_jail.py:512`).

The environment is *not* part of this finding: `subprocess_env_for_provider` builds it
from scratch through `_provider_child_runtime_env` when a snapshot is supplied
(`providers/base.py:754-792`), rather than copying `os.environ`.

## When it runs

Not only on a founder action. `refresh_native_catalogue` is reached from the serving and
work paths:

- `providers/served_model_plan.py:439-442`
- `providers/work_model_selection.py:51-53`
- `providers/model_selection.py:109-111` and `150-152`

so an ordinary served turn that needs a model list can start it.

## Why P2 and not the P0 it descends from

`docs/concerns/2026-09-24-provider-subprocesses-can-read-every-universe.md` is P0 because
the behaviour was **observed**: CLI session records showed model CLIs running `find`/`grep`
over `/app/tinyassets` and listing `/data`, driven by user workflow prompts. Here:

- the argv is fixed (`codex app-server`) and carries no user text;
- the channel is a JSON-RPC handshake defined by `NativeJsonRpcProtocol`
  (`codex_provider.py:771-775`), not a prompt;
- nothing has been observed reading another command center.

What remains is a trusted-binary assumption: a compromised or trojaned `codex` package, or
a later change that threads a user-influenced argument or protocol value into this path,
would run with both a live credential and cross-command-center reach — the one combination
the jail exists to prevent.

The reason to record it rather than wait: that P0's acceptance criterion is *"a jail test
proves that a provider subprocess for universe A cannot read universe B or `/app`"*. A test
written against `owned_process` passes while this spawn point stays open, so the P0 can be
closed without this being fixed.

## Minimal fix

Route discovery through the same confinement as inference, and make "the jail is at one
spawn point" true by test rather than by convention:

1. `read_native_catalogue` takes its launch from `owned_process`' jailed path instead of
   calling `create_subprocess_exec` itself — `confine_launch` with the owning command
   center as the view, binding only the credential snapshot the protocol needs. The jail
   already supports a credential home bound at a fixed path inside it
   (`provider_jail.py:171`), which is the shape this needs.
2. A test asserts that **no module under `tinyassets/providers/` reaches a process-spawn
   call without going through the jailed spawn point**, so the next adapter that adds a
   second spawn site fails rather than quietly inheriting the daemon's reach.
3. The jail proof covers discovery, not only inference: a discovery launch for command
   center A cannot read command center B's directory or `/app`, run in `linux-jail-proof`.

Step 2 is the part that stops this recurring; steps 1 and 3 close this instance.

## How to resolve

Land 1-3, show the jail proof covering a discovery launch, and verify the deploy with
`python scripts/deployed_sha.py --assert-contains <sha>`. Delete this file then.

## Related

- `2026-09-24-provider-subprocesses-can-read-every-universe.md` — the P0 this is an
  uncovered instance of.
- `2026-10-01-provider-jail-has-unfiltered-host-network.md` — a gap *inside* the jail;
  this one is a path that never enters it.
- `openspec/changes/per-role-uid-split/` — moves engine and provider children to their own
  uid, which bounds this independently of the jail. It is not a substitute: all such
  children share one uid, so the uid does not separate command centers from each other.
