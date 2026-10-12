## ADDED Requirements

### Requirement: A native CLI is an external-agent package of data
A native agent CLI SHALL be described only by an `external_agents` row of a
v2 extension manifest that declares its install, auth material, catalogue
operation and job operation (start, resume, input, tools, events, cancel).
Harness code SHALL NOT name a vendor; vendor strings SHALL appear only in
package manifests. Templates SHALL use the closed placeholder set, each
filling a whole argv element or JSON value, with no shell and no string
interpolation.

#### Scenario: a package adds no harness code
- **WHEN** the Codex and Claude Code packages are both installed
- **THEN** no module under `tinyassets/` outside
  `tinyassets/external_agents/packages/` contains either vendor name
- **AND** both run through the same runner

#### Scenario: an invalid template is refused
- **WHEN** a package row uses an unknown placeholder, an auth file name with
  a path separator, or an undeclared transport
- **THEN** the manifest parser refuses the revision and nothing is launched

### Requirement: One runner serves every external agent
`tinyassets/external_agents/runner.py` SHALL run every package's `catalogue`
and `job` operations through the transport family the package names
(`jsonrpc` or `control_stream`) and the tool mode it names (`dynamic_rpc` or
`mcp_config`). Tools exposed to a job SHALL be exactly the calling turn's
served tools. A job SHALL end by the declared cancel, then by ending the
process family after the declared grace, on deadline, caller cancel or
parent stop. A malformed event, an unknown server request or an early exit
SHALL end the job with a typed error and a bounded, redacted excerpt.

#### Scenario: a parent stop ends the job
- **WHEN** the parent turn of a running external-agent job is stopped
- **THEN** the runner sends the package's cancel, ends the process family
  after the grace, and settles the attempt

#### Scenario: a job cannot widen its tools
- **WHEN** the CLI calls back for a tool outside the calling turn's served set
- **THEN** the runner refuses that call and the job continues or ends with
  the refusal recorded

### Requirement: The agent mount is owner-private and job auth is ephemeral
Before the owner box exists, the runner SHALL launch in the owner's existing
provider cell, admitted once per job, with `HOME` and the package's home
variable inside a persistent owner-private agent home mounted at
`/agent-home`. The agent home SHALL live outside every command-center tree,
be owned by the owner identity with mode 0700, and be mounted into no other
cell. Its `/agent-home/jobs` subtree SHALL be a private mode-0700 tmpfs so a
killed cell cannot retain auth bytes on disk. No CLI home SHALL be under
`/tmp`. The runner SHALL
reach the process only through the `Launcher` seam, so that the box launcher
replaces the cell launcher without changing packages, runner or receipts.

#### Scenario: Codex lists models from its home
- **WHEN** the owner's Codex catalogue is refreshed on the production image
- **THEN** the CLI runs with its home under `/agent-home/jobs` on the private
  tmpfs, not `/tmp`
- **AND** the refresh succeeds and lists models

#### Scenario: another owner's home is invisible
- **WHEN** a job for owner A lists or reads paths in its cell
- **THEN** owner B's agent home is not present

### Requirement: Auth material exists only for the job and returns to custody
The runner SHALL materialize a package's declared auth files from the
broker's owner-exact custody snapshot, revalidated before use, into the
agent home with mode 0600 at job start, and SHALL delete them at job end,
including after cancel or crash. Declared write-back files SHALL be returned
to custody by compare-and-swap on the starting snapshot digest; a mismatch
SHALL discard the job's copy. No daemon environment value SHALL cross into
the process except the fixed safe set.

#### Scenario: a refreshed token is kept
- **WHEN** the CLI refreshes its token during a job
- **THEN** the refreshed file replaces the custody record after the job

#### Scenario: a newer deposit wins
- **WHEN** the owner deposits new credentials while a job runs
- **THEN** the job's write-back is discarded and the new deposit stays

#### Scenario: no auth file outlives the job
- **WHEN** a job ends by success, failure or cancel
- **THEN** no declared auth file remains in the agent home

### Requirement: Only pinned shipped packages run before the box
Before the owner box exists, the runner SHALL execute only the shipped
external-agent package revisions pinned by digest in the registry, and only
when the package's install tree exists in the image. A copy of a package in
an owner's command center SHALL NOT be executed.

#### Scenario: an edited owner copy is ignored
- **WHEN** a command center holds an edited copy of the Codex package
- **THEN** jobs still run the pinned shipped revision, whose digest appears
  in the receipt

### Requirement: A caller selects a native agent through the one model call
A caller SHALL select an external agent with an ordinary model reference
(`<agent>/<model>`) or `agent: <name>`, from a call, agent settings or a
node's policy, through `model_call.call`. The call SHALL dispatch an
`agent:<name>` source to the runner, branching on the protocol kind only. A
request asserting `execution: native_agent` SHALL fail before sending when
the resolved source is not an external agent.

#### Scenario: a pinned review step runs on Codex
- **GIVEN** an owner whose default source is Claude and who connected Codex
- **WHEN** a workflow review step pins `codex/<model>`
- **THEN** the step runs on Codex through the runner
- **AND** no Claude attempt is reserved

### Requirement: An unavailable external agent fails loudly
The call SHALL raise `ExternalAgentUnavailable`, naming the agent, the model
and one reason from a fixed set, when a requested external agent is not
connected, signed out, has no usable catalogue, does not list the requested
model, is not installed, or its job fails. No other agent or model SHALL run
unless the caller declared it as a fallback.

#### Scenario: Codex signed out
- **WHEN** a step pinned to Codex runs while the owner's Codex custody is
  signed out
- **THEN** the step fails with a message naming Codex, the model and
  `signed_out`
- **AND** nothing else runs

### Requirement: Every external-agent job leaves a native-agent receipt
Each job SHALL settle exactly one attempt and return a receipt with
`execution: native_agent`, `agent`, `agent_package_digest`,
`requested_model`, `resolved_model`, `reported_model` (empty with
`model_status: unknown` when the CLI did not report one), `native_session`,
usage with provenance `cli_reported` or `unknown`, and the outcome. The
reported model SHALL come only from the package's declared model event.

#### Scenario: the receipt proves what ran
- **WHEN** a Codex job completes
- **THEN** its receipt shows `agent: codex`, the requested and resolved
  models, and the model the CLI reported

### Requirement: Catalogue status is derived from refresh receipts
Each catalogue refresh SHALL write a receipt with the agent, package digest,
custody reference digest, start and completion times, outcome, model count,
and for a failure a reason class and a redacted excerpt. The status a client
sees SHALL be `fresh`, `stale`, `refreshing` (in flight and younger than the
catalogue deadline), `failed: <reason>` or `never_run`. A refresh admitted
but not started by the deadline SHALL become `failed: not_started`.

#### Scenario: a failed refresh is not pending
- **WHEN** a Codex catalogue refresh fails
- **THEN** the connection shows `failed:` with the reason
- **AND** it never shows a pending state without a refresh in flight
