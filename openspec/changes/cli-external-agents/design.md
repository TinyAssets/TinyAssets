## Context

This is the external-agent slice of convergence item C9, brought forward by
the founder on 2026-10-11 (proposal § Why). It builds on three decided
shapes:

- **S1 one model call** (`one-model-call-path`, C3). `call(model_ref,
  context, parent)` is the only way to reach a model. Its receipt keeps the
  requested, resolved and reported model apart, and `execution` is
  `model_response` or `native_agent`.
- **S8 one box per owner** (`one-box-per-owner`, ADR-019). Native CLIs run
  inside the owner's box as external-agent packages (P3). P3 depends on C8,
  so it is not near.
- **Audit rule (b).** A native CLI harness cannot truthfully be one model
  response. It is an explicitly named external-agent package over the
  generic job API, with its own native-session evidence.

What exists and is reused:

- `NativeJsonRpcProtocol` and `NativeControlProtocol`
  (`providers/native_jsonrpc_discovery.py`) already describe catalogue reads
  as data: method names and row keys. They are the seed of the package
  `catalogue` section.
- `role_provider_execution.py` already refuses to name a provider: "the image
  layout alone decides what a cell may exec" (`INSTALL_TREE`).
- `providers/owned_process.py` already spawns and ends a CLI as one process
  family.
- `agent_sessions.py` already keeps native session handles without naming a
  vendor.
- `credential_vault.snapshot_llm_subscription_credential` already produces an
  owner-exact, revalidated credential snapshot.
- `shortlist_refresh.py` already stores catalogue snapshots and reasons.
- The v2 extension manifest (`extension_manifest.py`, S6) is the one
  extension unit. This change adds a contribution kind to it, not a second
  unit.

What is wrong: each vendor's launch, auth layout, stream codec and catalogue
call is hand-written Python; the cell gives every CLI `HOME=/tmp`; discovery
is its own cell kind; and the call path cannot record that a caller asked
for a native agent, so a substitution is invisible.

## Goals / Non-Goals

**Goals:**

- One package contract and one runner. Vendors are data.
- A caller can select a native agent for a step, the receipt proves which
  agent and model ran, and an unavailable agent fails loudly.
- Codex works again: catalogue filled, jobs run, from an owner-private home.
- A pre-box slice that moves into the box without changing the package,
  runner or receipts.
- A net-negative diff in every PR.

**Non-goals:**

- The thin loop as the only loop, deleting `TINYASSETS_AGENT_LOOP`, and the
  Ollama provider (rest of C9).
- Running CLIs in the box (C6 P3), and the broker's in-box model endpoint.
- Streaming settlement (`broker-streaming-contract`). Settlement uses the
  job's final usage event until then.
- Owner-authored external-agent packages executing before the box (D7).

## Decisions

### D1. The package contract

An external agent is a row of a new `external_agents` kind in a v2 extension
manifest. `extension_manifest.KINDS` gains `external_agents`; nothing else in
the unit changes. A row is pure data:

```jsonc
{
  "name": "codex",                         // the agent's public name
  "description": "OpenAI Codex CLI",
  "family": "openai",                      // receipt family, from data
  "install": {                             // how the CLI arrives
    "npm": "@openai/codex", "version": "0.160.0", "bin": "codex"
  },
  "auth": {
    "service": "codex",                    // custody service name in the vault
    "home_env": "CODEX_HOME",              // env var that points at the home
    "home_dir": "codex",                   // subdirectory of /agent-home
    "files": ["auth.json"],                // materialized for the job only
    "writeback": ["auth.json"],            // refreshed material returned to custody
    "env_from_file": {}                    // e.g. {"CLAUDE_CODE_OAUTH_TOKEN": "token"}
  },
  "catalogue": {
    "transport": "jsonrpc",                // jsonrpc | control_stream
    "argv": ["app-server"],
    "initialize": {"method": "initialize",
                   "params": {"clientInfo": {"name": "tinyassets", "version": "1"}},
                   "notification": "initialized"},
    "list": {"method": "model/list", "params": {"limit": 100, "includeHidden": true},
             "items": "/data", "cursor": "/nextCursor", "cursor_param": "cursor"},
    "row": {"model": "/model", "default": "/isDefault", "hidden": "/hidden",
            "modalities": "/inputModalities",
            "effort_levels": "/supportedReasoningEfforts", "effort_level": "/reasoningEffort"}
  },
  "job": {
    "transport": "jsonrpc",
    "argv": ["app-server"],
    "start":  {"method": "thread/start",
               "params": {"model": "{model}", "cwd": "{workdir}", "ephemeral": false}},
    "resume": {"method": "thread/resume", "params": {"threadId": "{session}"}},
    "input":  {"method": "turn/start",
               "params": {"threadId": "{session}", "input": "{input}",
                          "effort": "{effort}"}},
    "session_handle": "/thread/id",
    "tools": {"mode": "dynamic_rpc", "define_at": "/dynamicTools",
              "call_method": "item/tool/call"},
    "events": {
      "text_delta":  {"method": "item/agentMessage/delta", "text": "/delta"},
      "item_done":   {"method": "item/completed"},
      "usage":       {"method": "thread/tokenUsage/updated",
                      "input": "/tokenUsage/total/inputTokens",
                      "output": "/tokenUsage/total/outputTokens"},
      "model":       {"method": "turn/started", "value": "/turn/model"},
      "done":        {"method": "turn/completed", "status": "/turn/status"}
    },
    "cancel": {"method": "turn/interrupt", "params": {"threadId": "{session}"},
               "grace_s": 10}
  }
}
```

The JSON pointers above illustrate the shape. Task 2 takes the exact values
from the current `codex_app_server.py` and `claude_provider.py` and from a
recorded transcript of each pinned CLI version; it does not invent them.

Rules the parser enforces:

- Placeholders are a closed set: `{model}`, `{effort}`, `{workdir}`,
  `{session}`, `{input}`, `{system}`, `{mcp_config}`. A placeholder fills one
  whole argv element or one whole JSON value; no string interpolation, no
  shell.
- `transport` is `jsonrpc` (newline-delimited JSON-RPC, integer ids, server
  requests allowed) or `control_stream` (typed `control_request` /
  `control_response` envelopes with string ids, plus typed stream events, as
  `NativeControlProtocol` already reads). These are transport families, not
  vendors. A CLI that needs a third family is a reviewed platform change.
- `tools.mode` is `dynamic_rpc` (the CLI calls back with a server request
  naming a tool; the runner answers it) or `mcp_config` (the runner writes an
  MCP config file into the job directory that points at the owner's engine
  route, and passes its path as `{mcp_config}`). Both expose exactly the
  served tool set of the calling turn; the package cannot widen it.
- `auth.files` and `auth.writeback` are plain file names inside `home_dir`.
  No path separators, no links.
- `install` names a package manager, a package and an exact version. Before
  the box the image builds one install tree per shipped package from this
  field (D9).

Claude Code is the same contract with `transport: control_stream`,
`tools.mode: mcp_config`, `auth.home_env: CLAUDE_CONFIG_DIR` and
`env_from_file` for a raw OAuth token, which is today's
`subprocess_env_for_provider` branch expressed as data.

### D2. Shipped packages, pinned

The two packages live at
`tinyassets/external_agents/packages/<name>/extension.json`. They are parsed
by `extension_manifest.parse_manifest` at import and pinned by digest in
`tinyassets/external_agents/registry.py`, which holds no vendor name: it
lists the directory. The digest goes into every receipt as
`agent_package_digest`.

### D3. One runner, two operations

`tinyassets/external_agents/runner.py`:

```python
@dataclass(frozen=True)
class AgentJob:
    agent: str                 # package name
    model: str | None          # native model id from the catalogue; None = package default
    effort: str | None         # must be in the model's catalogue effort levels
    input: AgentInput          # system, new messages, attachments as files in workdir
    tools: ServedTools         # the calling turn's tool set and engine route
    session: AgentSessionRef | None   # resume key (agent_sessions)
    deadline: float

async def run_job(job: AgentJob, launch: Launcher, custody: Custody) -> AsyncIterator[AgentEvent]
async def run_catalogue(agent: str, launch: Launcher, custody: Custody) -> CatalogueResult
```

Each operation:

1. Resolves the pinned package and checks the install tree exists.
2. Revalidates custody (owner, command center, service, digest) through the
   broker, as `refresh_native_catalogue` does today.
3. Asks the launcher for a process with the agent home, the job directory
   and the owner's egress, and materializes `auth.files` into the home with
   mode 0600 (D5).
4. Drives the declared transport: initialize, start or resume, input; maps
   declared events to `AgentEvent`s (`text_delta`, `tool_call`,
   `tool_result`, `usage`, `model`, `done`, `error`); answers tool callbacks
   through `ServedTools` only.
5. On deadline, caller cancel or parent stop: sends the declared `cancel`,
   waits `grace_s`, then ends the process family (`owned_process`).
6. Always: returns `auth.writeback` files to custody (D5), deletes
   `auth.files` from the home, removes the job directory, and emits one final
   receipt event.

A malformed event, an unknown server request, a non-zero exit before `done`,
or a `done` with a failure status ends the job with a typed error carrying a
bounded, redacted excerpt (the existing `_redacted_stderr_excerpt` rules,
moved here). Nothing is retried inside the runner; retries are the call's.

### D4. The launcher seam, and where it runs now

```python
class Launcher(Protocol):
    async def spawn(self, argv: list[str], env: dict[str, str], *,
                    home: str, workdir: str, egress: str) -> OwnedProcess: ...
```

The runner never names a cell, a box or a path outside the three it is
given. Two implementations exist over time, never at once for the same
owner:

- **Now: `CellLauncher`** (`role_provider_execution.py`, renamed
  `role_external_agent.py`). The existing owner provider cell, admitted once
  per job through the existing owner launcher. Its fixed environment
  changes:
  - `HOME=/agent-home`, `<auth.home_env>=/agent-home/<auth.home_dir>`. The
    agent home is a persistent, owner-private directory at
    `<data_root>/.platform/owners/<owner>/agent-home/`, uid and gid of the
    owner identity, mode 0700, created no-follow. It is outside every
    command-center tree, so no tool, node or package cell of that owner
    mounts it. It is bind-mounted read-write into the external-agent cell
    only.
  - `TMPDIR=/tmp` stays a per-job tmpfs. Nothing the CLI keeps lives there,
    which is what Codex's check requires.
  - The workdir is `/agent-home/jobs/<job id>`, created per job and removed
    after it.
  - The sealed snapshot mount, `AUTH_DIR=/tmp/provider-auth`, and the copy
    step are deleted; the runner materializes auth files instead (D5).
  - Discovery and execution become one cell kind, `external-agent`, with
    the operation in the bounded config line. `role_provider_discovery.py`
    is deleted.
- **After C6 P3: `BoxLauncher`.** `BoxProvider.exec` in the owner's box,
  with the agent home at the box's own home directory. P3 deletes
  `CellLauncher` in the same PR. The package files, the runner, the call
  integration and the receipts do not change. P3 may switch a package's
  `auth` to the broker's in-box endpoint (D5); that is a data change.

### D5. Credentials (floor)

- Auth material comes only from the broker's custody snapshot for
  `(owner, command center, auth.service)`, revalidated before materializing
  and again before write-back.
- The runner writes `auth.files` into `/agent-home/<home_dir>/` at job start
  and deletes them at job end, including on cancel and crash recovery (the
  next job start deletes stale auth files before writing new ones). Between
  jobs the home holds only non-secret state: config, caches and native
  session files.
- `auth.writeback`: after the process ends, the runner reads each declared
  file (no-follow, regular, size-bounded) and asks the broker to replace the
  custody record with compare-and-swap on the snapshot digest it started
  from. A digest mismatch (a newer deposit arrived) discards the job's copy
  and logs it. This fixes the read-only mount that today makes a CLI token
  refresh fail.
- `env_from_file` moves a declared file's contents into a declared env var
  of the CLI process, for CLIs that read a token from the environment.
- No value from the daemon environment crosses into the process except the
  fixed safe set (`safe_environment` keeps its role).
- After the box, a package may declare `auth.mode: broker_endpoint` (base
  URL env var plus model API family) so the CLI holds no credential and the
  broker injects it. Whether each subscription allows that is the audit's
  open question 2; it is not needed for this slice.

### D6. Selection through the one model call (S1)

A caller selects a native agent with the ordinary model reference. No new
caller API:

- **By source and model.** `model: codex/gpt-5.5-codex` in an agent's
  `settings.yaml`, a node's `llm_policy.preferred`, or a `ta call model`
  request. `codex` resolves to the owner's connection whose definition
  protocol is `agent:codex`. Resolution order is C3 D3 unchanged.
- **By agent only.** `agent: codex` (settings, node or `ta`) with no model
  resolves to the package's catalogue default.

The connected set (C3 D2) includes a connection with protocol `agent:<name>`
when its custody is adopted and its last catalogue receipt succeeded or is
younger than the usable age. In `model_call.call`:

1. The resolved source's protocol prefix decides the executor:
   `agent:<name>` goes to `external_agents.run_job`; everything else stays on
   `ProviderRouter.call`. This is the only branch, and it is on a protocol
   kind, not a vendor.
2. The receipt records `execution: native_agent`, `agent: <name>`,
   `agent_package_digest`, `requested_model` (as written, for example
   `codex/gpt-5.5-codex` or `agent:codex`), `resolved_model` (connection and
   native model id), `reported_model` (from the package's declared `model`
   event, else empty and `model_status: unknown`), `native_session` (the
   handle), usage with provenance `cli_reported` or `unknown`, and the
   settled outcome.
3. A caller may also assert `execution: native_agent` on a request. If the
   resolved source is not an external agent, the call fails before sending.

**Failing loudly.** When the requested agent is not connected, has no
adopted custody, has no usable catalogue, lacks the requested model, is not
installed, or its job fails, the call raises `ExternalAgentUnavailable` with
the agent name, the model and one reason from a fixed set (`not_connected`,
`signed_out`, `catalogue_failed: <reason>`, `model_not_listed`,
`not_installed`, `job_failed: <class>`). The step fails with that message,
for example: "This step asked for Codex (gpt-5.5-codex), but Codex is
unavailable: catalogue_failed: the CLI refused its home directory. Reconnect
Codex or remove the pin." No other agent or model runs unless the caller
declared it in `fallbacks`. Automatic (no explicit choice) may still resolve
to an external-agent source, as the default chat does today on a
subscription; its receipt still says `native_agent`.

### D7. Which package revision runs

Before the box, only the shipped, digest-pinned revisions run. An owner's
command center may hold a copy (it is a file like any other), but the runner
does not execute it: a prompt-injected edit to argv templates could otherwise
steer a credentialed process. After the box, the owner's box already runs
owner code with whatever the box holds, and N5 and the broker endpoint
protect credentials; owner-authored external-agent packages are then
admissible. That widening is a P3 decision, recorded here and not built.

### D8. Catalogue as a package operation, with receipts

- `run_catalogue` is the only model discovery path for external agents.
  `native_discovery.refresh_native_catalogue` and `read_native_catalogue`
  become a thin call to it, and the protocol classes become the parser of
  the package's `catalogue` section.
- Each refresh writes a catalogue receipt beside the shortlist snapshot:
  `agent`, `agent_package_digest`, custody reference digest, `started_at`,
  `completed_at`, `outcome` (`succeeded`, `failed`, `timed_out`,
  `cancelled`), model count, and for a failure a reason class plus a
  redacted excerpt of at most 240 characters.
- Status a client sees is derived from receipts, never guessed:
  - `fresh`, `stale`: the last succeeded receipt's age;
  - `refreshing`: a receipt with `started_at` and no `completed_at`, younger
    than the catalogue deadline (30 s);
  - `failed: <reason>`: the last receipt failed and nothing is in flight;
  - `never_run`: no receipt.
  `catalogue_refresh_pending` is deleted. A refresh that the scheduler
  admitted but never started becomes `failed: not_started` at the deadline,
  so no state can stay pending forever.
- A refresh runs when custody is adopted (connect), when the owner asks, and
  when the shortlist reaches its refresh age, as today.

### D9. The image

Before the box, the `Dockerfile` installs one tree per shipped package,
`/opt/<name>-install`, by reading each package's `install` field with a small
build script (`scripts/install_external_agents.py`). The two vendor-specific
`RUN npm install` stanzas, the vendor version ARGs and
`codex_cli_smoke.py`'s launch-contract copy are deleted. The smoke check
becomes the runner's `catalogue` operation against a fixture home in the
image build, so an image with a broken CLI fails to build. C6 P3 moves
installation into the box rootfs.

### D10. Sign-in

`onboarding/openai_device.py` (543 lines) reimplements a vendor's device
login and writes its `auth.json` shape. A package may declare
`login: {"argv": [...], "transport": "device_code", "prompt": "/verification_uri"}`;
the runner runs the CLI's own login inside the agent home, relays the
verification URL to the owner, and deposits the resulting `auth.files` into
custody. `openai_device.py` and its onboarding routes are deleted only after
a live sign-in through the package path succeeds for a fresh account
(task 7). Until then they remain and write the same custody record.

## Deletions (same release) and estimated net

| Delete | Where | Approx. lines |
|---|---|---|
| Codex executor, app-server codec, launch contract | `providers/codex_provider.py`, `codex_app_server.py`, `codex_launch_contract.py` | -1,171 |
| Claude Code executor | `providers/claude_provider.py` | -1,335 |
| `_CLI_PROVIDER_CLASSES`, `_cli_provider`; `_CLI_PROTOCOLS`; `_CLI_REFS` | `providers/provider_resolver.py`, `providers/definition.py`, `api/compute_connection.py` | -55 |
| Per-vendor child env and auth overlay (`_PROVIDER_AUTH_OVERLAY_ENV_VARS`, `_provider_child_runtime_env`, `_valid_provider_auth_overlay`, the vendor branches of `subprocess_env_for_provider`) | `providers/base.py:598-990` | -300 |
| Codex auth probes and refresh viability; bubblewrap probe and failure classification | `providers/base.py:990-1500` | -500 |
| Codex and Claude home and token helpers (`_codex_home_from_record` ... `apply_provider_auth_env`), `_decode_codex_auth_json` | `credential_vault.py:2283-2600`, `:192` | -350 |
| The provider-discovery cell kind; the snapshot rewrite in the execution cell | `role_provider_discovery.py`; `role_provider_cell.py` | -220 |
| Vendor-specific discovery glue, `catalogue_refresh_pending` | `native_discovery.py`, `native_jsonrpc_discovery.py`, `shortlist_refresh.py`, `model_options.py` | -150 |
| Device login reimplementation and its routes (task 7, after live parity) | `onboarding/openai_device.py`, `onboarding/__init__.py` | -640 |
| `codex_home` status probe | `api/status.py:470-490` | -20 |
| **Deleted** | | **about -4,740** |
| Package parser kind and registry | `extension_manifest.py`, `external_agents/registry.py` | +80 |
| Runner: two transports, two tool modes, events, cancel | `external_agents/runner.py`, `transports.py` | +650 |
| Agent home, auth materialize and write-back | `external_agents/home.py`, broker custody CAS | +160 |
| Call integration, `ExternalAgentUnavailable`, receipt fields | `providers/model_call.py`, `execution_receipt.py` | +90 |
| Catalogue receipts and derived status | `shortlist_refresh.py` | +80 |
| Package login | `external_agents/login.py` | +100 |
| `cli:*` to `agent:*` migration | `deploy/` | +40 |
| Image install script | `scripts/install_external_agents.py` (not core) | — |
| Package manifests | `external_agents/packages/*/extension.json` (data) | — |
| **Estimated net** | | **about -3,540** |

Without task 7 the net is about -2,900. Figures are function spans on
`a27a311acc3`; task 1 measures them. The gate per PR is
`git diff --numstat origin/main -- tinyassets deploy ':!**/tests/**'` with
added minus deleted below zero.

## Migration

M1 (writers stopped, after a full backup, in one short window): rewrite every
stored `ProviderDefinition.protocol` `cli:codex` to `agent:codex` and
`cli:claude-code` to `agent:claude-code`, and every connection row whose
`subscription_cli` ref names them. Record counts in the deploy log. Custody
records keep their shape; `auth.service` names the existing service values
(`codex`, `claude`). Native session handles in `agent_sessions` keep working
because the agent home's session directory is seeded once, per owner, from
`.runtime/agent-sessions/native/<adapter>/` and the old directory is removed
after a verified copy.

## Risks / Trade-offs

- **Parity before removal (audit § 8).** Claude Code serves the founder's
  chat today. `claude_provider.py` is deleted only after the runner passes
  the served-turn parity check on a production copy: tools through
  `mcp_config`, resume, effort, streaming, a tool-wait, and cancel (task 5).
  Codex is broken today, so it cuts over first with nothing to regress.
- **Two transport families in the runner.** They are generic protocols, not
  vendors, and the vendor guard (`vendor-names`) proves no vendor string
  remains in `external_agents/` outside `packages/`. A third family is a
  reviewed change.
- **Auth files on disk during a job.** The same exposure as today's
  snapshot copy, now in an owner-private home outside the command center and
  removed after the job. The box and the broker endpoint (D5) reduce it
  further.
- **Usage is CLI-reported.** Provenance says so; unknown never reads as
  zero (C3 D5).
- **Interim launcher.** `CellLauncher` lives until P3. It is the existing
  provider cell with a corrected home, not a new cell, and P3 deletes it.
