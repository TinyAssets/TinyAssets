## Why

Codex does not work for the founder, and the cause is the shape, not a missing
patch:

- **A pinned step ran on the wrong model.** The founder's app agent pinned a
  review step to Codex/GPT. It ran on Claude with no error. Nothing in the
  path records that the caller asked for a native agent, so nothing can refuse
  the substitution.
- **The catalogue never fills.** The owner's Codex connection shows
  "catalogue refresh pending" with no models. Production logs show
  `owner cell ended: revoked kind=provider-discovery exit=-9 ... Refusing to
  create helper binaries under temporary dir "/tmp" (codex_home ...)`. The
  discovery cell gives the CLI `HOME=/tmp` and `CODEX_HOME=/tmp/provider-auth`
  (`tinyassets/role_provider_cell.py` `FIXED_ENV`, `AUTH_DIR`), and the CLI
  refuses to run from a temporary directory.
- **Each vendor is code.** `claude_provider.py` (1,335 lines),
  `codex_provider.py` (607), `codex_app_server.py` (492) and
  `codex_launch_contract.py` (72) each implement a launch, an auth layout, a
  stream codec and a catalogue read. `_CLI_REFS`, `_CLI_PROTOCOLS`,
  `_CLI_PROVIDER_CLASSES` and `_cli_provider` close the set to two names.
  Per-vendor auth layout lives in `providers/base.py` and
  `credential_vault.py`. Discovery runs in its own cell kind
  (`role_provider_discovery.py`). Registry rows S3 and S4 are drifted
  because of these files.

The harness audit (2026-10-11, rule b) already decided the shape: a native
CLI harness cannot truthfully be one model response, so each CLI becomes an
explicitly named external-agent package over the generic job API. The
founder brought that slice of convergence item C9 forward on 2026-10-11:
"we dont want to band-aid patch but we know the shape we are building now so
if we can get the correct shape in there that solves this sooner then good."
A patch to the per-path provider cells was dropped on purpose.

## What Changes

- **External-agent package contract.** A v2 extension revision gains one
  contribution kind, `external_agents`. Each row is data: the CLI to install,
  how to launch it, where its auth material lives in the agent home, how it
  lists its models, and how a job is started, streamed, cancelled and
  receipted. Codex and Claude Code become two shipped packages under
  `tinyassets/external_agents/packages/`, pinned by digest. No vendor name remains in harness code.
- **One generic runner.** `tinyassets/external_agents/runner.py` runs any
  package through two transport families named by the package
  (`jsonrpc`, `control_stream`). It has two operations, `catalogue` and
  `job`, and both return receipts.
- **Selection through the one model call (S1).** A caller selects "use Codex
  for this step" with a model reference on an external-agent source
  (`codex/<model>`), or by naming the agent (`agent: codex`). The call
  records `execution: native_agent`, the requested agent and model, and
  dispatches to the runner. An unavailable agent fails loudly and names the
  agent and the reason. Nothing substitutes another agent or model unless the
  caller declared a fallback.
- **Pre-box slice.** Until C6 P3 lands, the runner launches in the existing
  owner provider cell, admitted once per job, with an owner-private
  persistent agent home mounted at `/agent-home`, never under `/tmp`. The
  runner talks to a `Launcher` seam. C6 P3 replaces the cell launcher with
  the box launcher, and the package, runner and receipts move unchanged.
- **Catalogue as a package operation.** Model discovery is the package's
  `catalogue` operation, run through the same runner and cell. Every refresh
  leaves a receipt. A source reports `pending` only while a refresh is
  actually in flight; a failed refresh reports its receipt's reason.
- **Deletions in the same release:** the three vendor provider modules and
  the launch contract, `_CLI_REFS`, `_CLI_PROTOCOLS`,
  `_CLI_PROVIDER_CLASSES`, `_cli_provider`, the per-vendor launch, auth-layout
  and auth-probe code in `providers/base.py` and `credential_vault.py`, the
  separate provider-discovery cell kind, the Codex bubblewrap mode, the
  vendor install lines in the `Dockerfile`, and (gated on live parity)
  `onboarding/openai_device.py`. Estimated net is about **-3.4k executable
  lines** (design § Deletions). A slice whose measured net is not negative
  does not merge.

**BREAKING** (internal only): `ProviderDefinition` protocols `cli:codex` and
`cli:claude-code` become `agent:<package>`; one migration rewrites stored
definitions and connection rows. Public MCP handles are unchanged.

## Capabilities

### New Capabilities

- `external-agent-packages`: the package contract, the generic runner, the
  catalogue and job operations, the pre-box launch, selection and loud
  failure, and receipts.

### Modified Capabilities

- `provider-routing`: subscription CLIs are no longer executors chosen by
  vendor; the Codex bubblewrap probe and sandbox-failure classification are
  removed.
- `agent-model-selection`: native metadata discovery becomes the package's
  `catalogue` operation in the agent home, with receipts.

## Impact

- **Floor class (ADR-015).** Credentials (auth material moving into and out
  of the agent home, token write-back to custody) and authority (agent
  selection through the connected set). Those tasks get one cross-family
  `peer-agents` round.
- **Storage.** No new database. Catalogue receipts reuse the shortlist store.
  The agent home is a new per-owner directory outside every command-center
  tree. One migration rewrites `cli:*` protocols to `agent:*`.
- **Dependencies.** C3 `one-model-call-path`: tasks 2.1-2.3 (#4629, in the
  merge queue) for `model_call.call` and the receipt, 2.4 (connected-set
  authority) and 2.5 (explicit choice, declared fallbacks only) before
  selection ships. C6 P3 later swaps the launcher. Shape-convergence C9 keeps
  the rest: the thin loop as the only loop, `TINYASSETS_AGENT_LOOP`, Ollama,
  and CLIs out of the image into the box.
- **Supersedes** the dropped per-path provider-cell patch and the Codex-home
  part of #4614.
