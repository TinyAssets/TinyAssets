# Uptime and Alarms

> As-built baseline (2026-07-22, change `backfill-uptime-and-alarms`): describes the shipped monitoring, incident paging, bounded recovery, deploy rollback, backup, and disaster-recovery contracts. Known gaps remain non-normative limitations in the archived change design.

## Purpose

Keep the operational uptime control paths explicit and testable without absorbing the MCP protocol, daemon scheduling, or community patch-loop contracts owned by neighboring capabilities.
## Requirements
### Requirement: Platform Liveness Follows The Current Executor Lifecycle

The platform SHALL project daemon-owned coordinator liveness from actual
started instances in the serving process, their thread lifecycle and elapsed
time since their last successful poll. Historical shared or named per-universe
heartbeat files SHALL NOT establish the expected platform executor inventory.
The public projection SHALL expose only present, alive, beat_age_s, phase and
consec_crashes; it SHALL reveal no tenant, worker, task, runtime identity or count.
An enabled missing executor SHALL report unhealthy. A dead, stopping or stalled
expected coordinator SHALL NOT be concealed by a healthy peer or fresh activity.
A stopped instance SHALL retire only after its thread exits. The stall bound
SHALL be the greater of 300 seconds and the poll interval plus 120 seconds.

Per-universe heartbeat publication, queue-descriptor classification and the
existing shell-watchdog heartbeat contract SHALL remain unchanged. An engine
MCP child that cannot observe its parent coordinator SHALL report unknown
liveness with phase external_coordinator, not infer that the parent is stopped.
The activity canary SHALL fail on unknown expected-coordinator liveness even
when its last-activity timestamp is recent. Coordinator liveness SHALL NOT be
presented as proof that a particular user's work completed.

#### Scenario: Retired artifacts cannot override current execution
- **WHEN** an expected daemon coordinator is alive and making successful polls while old shared or named heartbeat files remain
- **THEN** platform status reports the current coordinator without treating those files as expected running processes

#### Scenario: A dead current coordinator remains visible
- **WHEN** the enabled current coordinator is missing, dead, stopping or beyond its poll-progress bound
- **THEN** status reports unhealthy and fresh activity or another healthy coordinator does not hide it

#### Scenario: A child process cannot attest its parent
- **WHEN** get_status executes in a per-universe engine MCP child
- **THEN** coordinator liveness is explicitly unknown with no private identity disclosed


### Requirement: Host-Independent Public Canary And Incident Lifecycle

The platform SHALL request a five-minute Layer-1 public uptime schedule on
GitHub Actions, support manual dispatch, and react to every completed `Deploy
prod` workflow (`.github/workflows/uptime-canary.yml`). The requested schedule
SHALL NOT be represented as a guaranteed observation interval. For deploy
completion events, the probe job SHALL run only after a successful deploy,
while the alarm sink SHALL distinguish
the probe result as literal red, literal green, or unknown. The bundle SHALL
probe the canonical MCP handshake, a real tool call, daemon last activity,
sustained revert-loop state, and wiki persistence plus authorization policy.
The wiki sub-probe SHALL require `TINYASSETS_WIKI_CANARY_TOKEN` before network
access, assert that a separate tokenless `initialize` receives the OAuth 401
challenge, then use the named `canary` service principal for the MCP session,
an exact reserved `write_page` call, and its matching `read_page`. It SHALL
write a fresh per-run marker, require a successful response for
`drafts/notes/uptime-probe.md`, and verify the same marker and path through the
authenticated read. A missing or rejected credential, an accepted write to a
non-exact path, a read mismatch, an invalid tokenless challenge, or another
HTTP/network failure SHALL remain red with the existing step-specific
diagnostics. There is no unsigned fallback. The
`live-mcp-connector-surface` capability owns the underlying pre-dispatch
challenge and canary confinement; this requirement owns its uptime evidence
and workflow diagnostic propagation.

The bundle SHALL combine sub-probes as follows: any measured failure is red;
otherwise absent required observations are unknown; only positive green
observations for every required sub-probe establish green. Missing private
legacy REVERT evidence SHALL be unknown and SHALL NOT authorize access to user
logs or become fabricated empty evidence. Transport failures SHALL remain red
even when they share an exit code with missing evidence. Diagnostics SHALL
distinguish those causes explicitly, not by parsing human-readable text.

The sink SHALL open a `p0-outage` issue after two consecutive measured Layer-1
red observations, append evidence while red, and comment recovery then close
the issue only on literal green. Prior red SHALL be established by the
immediately preceding eligible production workflow run's exact-attempt
measured-red step receipt, not its overall workflow conclusion. Missing,
unknown, skipped, cancelled, wrong-source or ambiguous prior receipts SHALL
NOT establish prior red; the sink SHALL NOT skip unknown observations to join
nonconsecutive reds.

An unavailable, empty, or unrecognized current result, including a skipped
probe after a failed deploy, SHALL be unknown: the sink SHALL make no label or
issue mutation, SHALL not page, and SHALL complete successfully so unknown
cannot become red threshold evidence. Unknown SHALL remain visible with its
missing observation and prerequisite in the scheduled summary; successful
execution of classification SHALL NOT be reported as green uptime acceptance.
MCP protocol and handle correctness remain owned by
`live-mcp-connector-surface`; this requirement owns probe orchestration and
incident state.

#### Scenario: Second consecutive red opens a durable incident

- **WHEN** the combined Layer-1 bundle is red and the immediately prior eligible run has an exact-attempt measured Layer-1 red receipt
- **THEN** the alarm sink opens one GitHub issue labeled `p0-outage` with the probe exit and output
- **AND** subsequent red ticks append evidence to that open issue instead of creating a parallel incident

#### Scenario: Unrelated workflow failure does not cross the outage threshold

- **WHEN** current Layer-1 is red but the prior run failed only in Layer-2 or has no measured Layer-1 red receipt
- **THEN** the prior workflow conclusion does not count as a previous outage and no threshold-crossing page is fabricated

#### Scenario: Green closes the incident

- **WHEN** the combined Layer-1 bundle is literally green while a `p0-outage` issue is open
- **THEN** the alarm sink appends a `GREEN — RECOVERED` record and closes the issue as completed

#### Scenario: Unknown result preserves incident state

- **WHEN** the probe result is unavailable, empty, or unrecognized, including when a failed `Deploy prod` completion skips the probe job
- **THEN** the alarm sink records an Actions warning and summary without creating or querying labels or issues, without paging, and without failing the canary workflow
- **AND** an open `p0-outage` issue remains open until a literal green result is observed

#### Scenario: Missing legacy evidence leaves coverage explicitly incomplete

- **WHEN** the current named canary projection lacks the legacy private activity tail while the other probes pass
- **THEN** the result is unknown with the current-engine execution-quality observation named as an open prerequisite, not green or a platform outage

#### Scenario: A real failure outranks a separate unavailable observation

- **WHEN** a current endpoint, tool, expected coordinator, or wiki probe measures failure while legacy revert evidence is unavailable
- **THEN** Layer-1 remains red with the real failure diagnostic and the unavailable coverage remains visible

#### Scenario: Downstream sub-probes respect upstream health

- **WHEN** the MCP handshake or real-tool probe fails
- **THEN** dependent activity, revert-loop, and wiki probes are skipped where they cannot produce meaningful evidence
- **AND** the upstream failure keeps the combined result red

#### Scenario: Canary wiki write is read back

- **WHEN** the scoped service token is present and the reserved `write_page`
  call reports `drafts/notes/uptime-probe.md`
- **THEN** the wiki sub-probe reads that page as the same `canary` principal and requires the
  written canary content to match
- **AND** any rejected write, different path, or read mismatch is red

#### Scenario: Missing credential fails before network access

- **WHEN** the scoped service token is absent or empty in the canary environment
- **THEN** the wiki sub-probe exits 2 naming
  `TINYASSETS_WIKI_CANARY_TOKEN` before making a network request
- **AND** it does not attempt an anonymous read or write

### Requirement: Community Watch Preserves Typed Canary Observation

The community watch SHALL consume explicit measured Layer-1 receipts from the
exact current Actions attempt, bound to the production workflow source, head,
run and freshness. Whole-workflow success alone SHALL NOT establish green.
Missing, malformed, ambiguous or superseded receipts SHALL remain unknown.
Existing measured-red receipts and other red watch stages SHALL remain red;
unknown SHALL NOT override them. Missing cadence remains a distinct monitoring
failure, not a fabricated measurement of endpoint health. This internal
consumer SHALL reuse Actions metadata without new runtime storage or authority.

The workflow SHALL validate its watcher's structured result against the actual
process exit before passing health to the incident sink. Missing, malformed or
inconsistent output SHALL report unknown health and fail a distinct monitor-
unavailable job gate, without incident or dispatch mutation. A valid unknown
classification SHALL remain distinct from a watcher that failed to classify.

#### Scenario: Successful classification recorded unavailable coverage

- **WHEN** the Uptime workflow succeeds without a positive measured-green receipt
- **THEN** community watch reports the Layer-1 observation as unknown rather than green
- **AND** its alarm sink makes no incident or dispatch mutation for unknown or yellow overall status

#### Scenario: Exact measured result controls incident state

- **WHEN** an exact, current, unambiguous measured-red receipt is read
- **THEN** existing red alarm actions remain available and unknown elsewhere cannot hide the red
- **AND** recovery remains restricted to literal green, including positive measured-green evidence for the observation stage

#### Scenario: Watcher crashes or produces incomplete output

- **WHEN** the watcher or its parser fails, or its output lacks a valid current result matching its process exit
- **THEN** an always-run gate reports monitor unavailability and fails the watch job
- **AND** unknown health cannot create, update or recover an incident, while valid measured red and green retain their existing semantics

### Requirement: Absent Scheduled Rendered Acceptance Is Explicit

The scheduled monitor SHALL report Layer-2 unknown when its runner lacks an
authorized rendered-user acceptance session. It SHALL do so before invoking
the browser/LLM harness and SHALL NOT provision a platform LLM actor, reuse
an owner's browser or subscriptions, or label the absent capability as a
provider outage. Layer-1 results and Layer-2 coverage SHALL remain separately
visible. Full hostless acceptance SHALL remain incomplete while required
rendered evidence is unavailable, regardless of the classifier process exit.

#### Scenario: Hosted runner lacks the rendered session

- **WHEN** the scheduled runner has no authorized rendered-user session
- **THEN** the summary identifies that prerequisite and records Layer-2 unknown without calling `claude_chat.py`
- **AND** Layer-1 measurements are preserved, no provider outage is inferred, and rendered acceptance remains open

### Requirement: Durable Acknowledgement-Aware Emergency Paging

The alarm sink SHALL use the open outage issue and its comments as durable Pushover escalation state (`scripts/pushover_page.py`). With Pushover credentials configured, threshold crossing SHALL send a priority-2 emergency page with a 60-second retry interval and 3600-second expiry and record a successful `[PAGED ...]` marker. An unacknowledged open incident SHALL be eligible for fresh pages at the 1-hour, 4-hour, and 24-hour ladder rungs, then no more often than every 24 hours. A non-bot issue comment after the newest marker SHALL count as human acknowledgement and suppress the next page.

#### Scenario: Threshold crossing pages immediately

- **WHEN** the second consecutive red opens the outage issue and Pushover credentials are present
- **THEN** the workflow sends a priority-2 `vibrate` page with retry and expiry values
- **AND** a successful send appends a machine-readable PAGED marker to the issue

#### Scenario: Human acknowledgement suppresses escalation

- **WHEN** a non-bot comment was created after the newest PAGED marker
- **THEN** the next alarm tick returns `host_acknowledged` and does not send another page

#### Scenario: Missing paging state fails visibly

- **WHEN** an incident is page-eligible but credentials are absent or the Pushover POST fails
- **THEN** the paging command returns a non-zero failure and does not emit a false PAGED marker

### Requirement: Layered Bounded Host Recovery

The production host SHALL combine service restart policy with two installed watchdog paths. `tinyassets-daemon.service` SHALL run compose in the foreground with `Restart=always`, a ten-second restart delay, and a five-restart-per-five-minute start limit. `tinyassets-watchdog.timer` SHALL probe the local MCP endpoint every 30 seconds, restart `tinyassets-daemon.service` after three consecutive reds, and suppress repeat restarts for ten minutes. `daemon-watchdog.timer` SHALL run every two minutes under a non-blocking process lock and restart the service when the systemd unit is inactive, the daemon container is stopped, or the freshest worker-supervisor heartbeat is older than 900 seconds. A successful watchdog restart SHALL be followed by a later probe/heartbeat observation rather than treated as proof of recovery by the restart command alone.

#### Scenario: Hung MCP endpoint crosses the probe threshold

- **WHEN** the bootstrap-installed MCP watchdog records three consecutive red local probes and no restart occurred in the prior ten minutes
- **THEN** it issues the narrowly allowed restart of `tinyassets-daemon.service`, resets the red streak optimistically, and records the restart
- **AND** the next timer tick probes again to determine actual recovery

#### Scenario: Dead unit or stale fleet heartbeat restarts once per shell-watchdog run

- **WHEN** the two-minute daemon watchdog finds an inactive unit, a stopped daemon container, or a freshest supervisor heartbeat older than 900 seconds
- **THEN** it restarts `tinyassets-daemon.service` and exits that run
- **AND** overlapping invocations on that watchdog's lock exit without a second restart

#### Scenario: Systemd restart storms are bounded

- **WHEN** the foreground compose service repeatedly exits
- **THEN** systemd waits ten seconds between restarts and stops automatic attempts after five starts inside five minutes until the limit is reset

### Requirement: Class-Specific P0 Triage And Re-Probe

When a `p0-outage` issue is opened, the P0 triage workflow SHALL collect a pre-restart diagnostic bundle, classify it with the priority-ordered `env_unreadable`, `tunnel_token`, `provider_exhaustion`, `disk_full`, `oom`, `image_pull_failure`, `watchdog_hotloop`, or `unknown` class, execute the class-specific bounded response, wait for startup where applicable, and re-probe the canonical MCP URL. A bounded class-specific repair, generic restart, or provider-exhaustion page failure SHALL remain visible but SHALL NOT prevent the canonical re-probe. Green SHALL close the outage only when that re-probe is green; persistent red SHALL add `needs-human` with diagnostics and fail the triage run visibly. Tunnel-token repair SHALL remain manual, and provider-exhaustion worker pause SHALL remain gated by `TINYASSETS_REVERT_AUTO_REPAIR` while the existing `scripts/pushover_page.py` CLI pages regardless of that gate. The workflow SHALL preserve issue-scoped concurrency with in-progress runs not cancelled.

#### Scenario: Environment permission regression is repaired before restart

- **WHEN** diagnostics contain the canonical `ENV-UNREADABLE` marker
- **THEN** triage restores `/etc/tinyassets/env` to `root:tinyassets` mode `0640`, verifies the daemon user can read it, performs the generic compose recreate, and re-probes

#### Scenario: Image pull failure uses only the recorded immutable rollback target

- **WHEN** diagnostics classify as `image_pull_failure`
- **THEN** triage reads `rollback_target` from `/data/release-state.json`, requires an `@sha256:` reference, atomically installs it as `TINYASSETS_IMAGE`, pulls it, and restarts the service
- **AND** absence of a digest-pinned target fails the repair instead of falling back to a mutable image tag

#### Scenario: Manual and gated classes remain honest

- **WHEN** diagnostics classify as `tunnel_token`
- **THEN** automation opens distinct rotation work and pages rather than claiming an automatic token repair
- **WHEN** diagnostics classify as `provider_exhaustion` while its auto-repair variable is not enabled
- **THEN** automation pages in warn-only mode and does not stop the worker or create pause sentinels

#### Scenario: Failed bounded repair still reaches the canonical decision

- **WHEN** a class-specific repair or generic restart exits non-zero
- **THEN** its failure remains visible and the workflow continues to canonical re-probe
- **AND** only a green re-probe closes the issue while a red re-probe adds `needs-human` and fails the run visibly

#### Scenario: Provider page failure stays visible but does not replace probe truth

- **WHEN** the provider-exhaustion page command exits non-zero
- **THEN** the page step is visibly failed while canonical re-probe still runs
- **AND** only that re-probe determines auto-recovery or persistent-red escalation

### Requirement: Digest-Pinned Deploy Admission Rollback And Receipt

The production deploy workflow SHALL resolve the requested image to an
immutable digest before mutating production, capture a previous rollback image
only from agreeing configured and actual-running daemon identities, and capture
the bounded prior active-release receipt before mutation. It SHALL verify
`/etc/tinyassets/env` remains readable by the daemon user after restart and
require the canonical MCP canary and advertised-handle assertion to pass.

An immutable image identity MUST match exactly a canonical
`repository@sha256:<64 lowercase hex>` value under
`^[a-z0-9]+(?:[._-][a-z0-9]+)*(?::[0-9]+)?(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)+@sha256:[0-9a-f]{64}$`.
The workflow SHALL independently observe the canonical configured ref from
`TINYASSETS_IMAGE` and the canonical RepoDigest derived from the actual daemon
container's Docker image ID. It SHALL record `active_identity_status` as exactly
`agreed`, `mismatch`, `configured_unknown`, `running_unknown`, or
`both_unknown`. Only two valid, byte-for-byte equal observations are `agreed`;
no tag, bare image ID, digest prefix, or ambiguous RepoDigest set is agreement.

The captured prior receipt SHALL use strict base64 and SHALL be rejected in its
entirety when its decoded payload exceeds exactly 65,536 bytes, is invalid
base64 or UTF-8, is not a JSON object, contains unsupported types in a consumed
field, or contains inconsistent/invalid active identity fields. A structurally
valid version-1 receipt MAY only corroborate identity after configured and
actual-running observations already agree with its canonical `image_ref`; no
version-1 field may seed Git/build provenance, `image_tag`, `deployed_at`, or a
future `rollback_target`.

Prior provenance or ancestry MUST be reused only from a version-2
**terminal-proof** receipt whose `release_state_version=2`,
`outcome=deployed|rolled_back`, `active_identity_status=agreed`,
configured/running/active/legacy canonical refs all agree,
`canary_bundle_status=passed`, and active ref exactly matches the newly observed
agreed ref. Each reused field MUST pass its own type/format validation. A reused
`rollback_target` MUST additionally be canonical and differ from the failed
attempted ref. The bounded prior-match enum SHALL be
`absent|invalid|mismatch|v1_identity_match|v2_terminal_proof_match`.

Image source SHA SHALL come only from a full 40-lowercase-hex
`org.opencontainers.image.revision` label inspected from the exact immutable
digest, or from a matching version-2 terminal-proof receipt.
For a `workflow_run`, current build-run provenance SHALL be populated only when
that digest-bound revision equals the triggering run's full `head_sha`. A
manual dispatch MUST NOT derive source SHA from `github.sha`, tag text, or a
SHA-shaped tag; when digest-bound provenance is unavailable or inconsistent,
the source SHA and build provenance fields SHALL be empty. An active image
matching only a version-1 receipt SHALL derive provenance afresh from its
digest-bound revision label or remain unknown.

The workflow SHALL emit `production_mutation_started=true` immediately before
its first state-changing command directed at the production host and
`image_mutation_started=true` immediately before its first
`TINYASSETS_IMAGE` write. In the current workflow the first marker is
immediately before the `Scrub stale cloud env overrides` SSH command. The
checkout action and the `Resolve image tag`, `Verify secrets present`, `Install
SSH key`, and expanded read-only `Capture previous image tag (for rollback)`
steps are exactly the pre-host-write paths: both markers remain false, no host
mutation or terminal host publication occurs, terminal result is
`not_applicable`, and the prior receipt is unchanged.

Once the production marker is true, terminal publication SHALL be attempted
even if the immediately following remote command fails before a write can be
proven. Image rollback MUST be attempted only when the image marker is true.
Rollback handling SHALL run after the forward stages with `if: always()`, emit
safe outputs before fallible commands, and emit final outputs before its exact
exit. Its bounded outputs are `rollback_attempted=true|false`,
`rollback_result=succeeded|failed|not_attempted`,
`rollback_canary_status=passed|failed|not_run`, and
`rollback_reason=attempted|not_needed|pre_host_write_failure|image_mutation_not_started|no_valid_target`.

Rollback step exit SHALL follow this complete table:

| State and final tuple | Required exit |
|---|---|
| both markers false; `false/not_attempted/not_run/pre_host_write_failure` | zero |
| production marker true and image marker false; `false/not_attempted/not_run/image_mutation_not_started` | zero |
| fully green forward path; `false/not_attempted/not_run/not_needed` | zero |
| image marker true, failed forward path, no valid previous ref; `false/not_attempted/not_run/no_valid_target` | nonzero |
| image marker true; `true/succeeded/passed/attempted`; configured/running refs agree with previous ref | zero |
| image marker true and rollback command, canary, or required identity proof fails or is unavailable | nonzero |
| any missing, invalid, or contradictory marker/output tuple | nonzero |

`not_needed` is valid only for a fully green forward path. A passed rollback
canary with missing/mismatched image identity is an unproven required rollback
and MUST exit nonzero.

Every path whose production marker is true SHALL reach a post-rollback terminal
step that attempts to invoke a small pure executable receipt-classifier/builder. Its
classification core
MUST accept explicit JSON observations and return deterministic JSON without
reading environment variables, Docker, SSH, GitHub, clocks, or the filesystem.
It SHALL validate and atomically publish version-2
`/data/release-state.json`, while a pre-host-write path SHALL leave the prior
receipt unchanged. The terminal step SHALL use `if: always()` and emit
`terminal_receipt_result=failed` before any fallible post-production-mutation work,
overwrite it with `published` only after atomic installation, or emit
`not_applicable` without host contact when production mutation did not start.
The output enum is exactly `published|failed|not_applicable`; writer failure
SHALL remain red and MUST NOT destroy the last atomic receipt. Before attempting
atomic installation, the terminal step SHALL expose the classifier's bounded
`terminal_outcome`, `terminal_active_identity_status`, and
`terminal_canary_status` outputs for failure-issue wording.
`terminal_canary_status` SHALL be forward canary status for `deployed`,
rollback canary status for `rolled_back` or an attempted rollback, and forward
canary status for a non-rollback failure.

The version-2 receipt SHALL use exactly these bounded classifications:

- `release_state_version`: integer `2`;
- `outcome`:
  `deployed|rolled_back|rollback_failed|failed_without_rollback`;
- `forward_deploy_status`: `succeeded|failed`;
- `forward_canary_status`: `passed|failed|not_run`;
- `production_mutation_started` and `image_mutation_started`: booleans;
- `prior_receipt_match_status` as defined above;
- `attempted_source_provenance`: `digest_revision_label|unknown`;
- `active_source_provenance`:
  `attempted_digest|digest_revision_label|v2_terminal_proof|unknown`;
- `active_identity_status` as defined above;
- rollback fields and enums as defined above.

`deployed` SHALL require both markers true, all forward admission checks and
forward canary green, rollback reason `not_needed`, and configured/running
agreement with the attempted ref. `rolled_back` SHALL require both markers
true, a failed forward path, the successful rollback tuple, and
configured/running agreement with the captured previous ref. Any image-marked
path lacking required rollback command, canary, or terminal identity proof
SHALL be `rollback_failed`. Any production-marked path that is not `deployed`
and did not attempt image rollback SHALL be `failed_without_rollback`,
including failure before image mutation and unavailable rollback target.
Receipt publication failure SHALL NOT change this observed-host outcome; it
separately keeps the workflow red through `terminal_receipt_result=failed`.

All version-2 receipts SHALL contain
`attempted_git_sha`, `attempted_image_tag`, `attempted_image_ref`,
`attempted_image_digest`, `configured_image_ref`, `running_image_ref`,
`active_git_sha`, `active_image_ref`, `active_image_digest`, `terminal_at`, and
all legacy keys. Attempted/active `image_digest` fields SHALL preserve the
legacy representation as full canonical RepoDigests. Active/legacy identity
fields SHALL be empty unless configured and running observations agree.

Every legacy key SHALL have exactly this version-2 meaning:

| Legacy field | Required meaning |
|---|---|
| `git_sha` | proven SHA of the agreed active image from fresh digest-label provenance or a matching version-2 terminal-proof receipt; never copied from version 1; otherwise empty |
| `image_tag` | mutable display ref proven to have resolved to the agreed active digest in the current run or a matching version-2 terminal-proof receipt; never copied from version 1; otherwise empty |
| `image_ref` | agreed canonical active RepoDigest; otherwise empty |
| `image_digest` | same agreed canonical active RepoDigest; otherwise empty |
| `build_run_id`, `build_run_url` | build run proven to have produced the active digest; matching current trigger or version-2 terminal-proof prior values; never copied from version 1; otherwise empty |
| `deploy_run_id`, `deploy_run_url` | current terminal deploy workflow run |
| `config_hash` | terminal hash of readable live env as `sha256:<64 lowercase hex>`; otherwise empty |
| `config_version` | `tinyassets-env-v1` when `config_hash` is present; otherwise empty |
| `schema_migration_rev` | `not_applicable` until a real observed revision exists |
| `canary_bundle_status` | `passed`, `failed`, or `not_run` for the canary applicable to the agreed active identity; never `passed` on identity mismatch |
| `deployed_at` | `terminal_at` for `deployed`/`rolled_back`; validated version-2 terminal-proof prior value for a failed outcome still matching prior active; never copied from version 1; otherwise empty |
| `rollback_target` | canonical future repair target selected only by the matrix below; otherwise empty |
| `actor` | actor of the current terminal deploy workflow |
| `repository` | repository of the current terminal deploy workflow |
| `workflow_event` | event name of the current terminal deploy workflow |

`rollback_target` SHALL follow the complete matrix:

| Outcome | Agreed active identity | Required value |
|---|---|---|
| `deployed` | attempted ref | independently agreed canonical pre-image-mutation previous ref; else empty |
| `rolled_back` | previous ref | canonical prior target only from a version-2 terminal-proof receipt matching restored ref; else empty |
| `rollback_failed` | attempted ref | captured canonical previous ref; else empty |
| `rollback_failed` | previous ref | canonical target from matching version-2 terminal-proof prior receipt; else empty |
| `rollback_failed` | another ref or no agreement | empty |
| `failed_without_rollback` | attempted ref | captured canonical previous ref; else empty |
| `failed_without_rollback` | previous ref | canonical target from matching version-2 terminal-proof prior receipt; else empty |
| `failed_without_rollback` | another ref or no agreement | empty |

A failed attempted image MUST NOT become a future `rollback_target`.

Deploy failure SHALL remain red and open a distinct `deploy-failed` issue with
rollback wording selected exactly as follows:

| Condition | Required wording |
|---|---|
| production marker false; `pre_host_write_failure` | "Production host write did not start; image rollback was not attempted." |
| production marker true; image marker false; `image_mutation_not_started` | "Production mutation started, but image mutation did not; image rollback was not required." |
| `not_needed` plus terminal outcome `deployed`, terminal active identity `agreed`, and terminal applicable canary `passed` | "Rollback was not needed because terminal outcome is deployed, active image identity agrees, and the applicable canary passed." |
| `not_needed` without that complete terminal tuple | "Rollback was not attempted; forward production health is unproven." |
| image marker true; `no_valid_target` | "Rollback was not attempted because no validated immutable previous image was available." |
| successful rollback tuple plus terminal outcome `rolled_back` and terminal agreement with previous ref | "Rollback succeeded and the rollback canary passed." |
| attempted; canary failed | "Rollback failed; the rollback canary failed." |
| attempted; canary not run | "Rollback failed before the rollback canary ran." |
| attempted; canary passed; terminal identity not agreed with previous ref | "Rollback was not proven: the canary passed but configured and running image identity did not agree with the rollback target." |
| missing, invalid, or contradictory rollback outputs | "Rollback status is unavailable; rollback success was not proven." |

The issue SHALL append exactly one terminal receipt sentence:

| `terminal_receipt_result` | Required wording |
|---|---|
| `published` | "Terminal release-state receipt published." |
| `failed` | "Terminal release-state publication failed; durable active-release truth is not proven and the prior receipt may be stale." |
| `not_applicable` with production marker false | "Terminal release-state publication was not applicable; the prior receipt was left unchanged." |
| `not_applicable` after production marker, empty, or other | "Terminal release-state status is unavailable or inconsistent; durable active-release truth is not proven." |

The issue SHALL show the canonical active ref only for `agreed`, show both refs
and state that they disagree for `mismatch`, identify the unavailable
observation for unknown identity states, and MUST NOT convert empty outputs into
a success claim. It MUST NOT describe a `not_needed` path as proven healthy
without terminal `deployed` outcome, agreed active identity, and applicable
passed canary.

#### Scenario: Mutable or missing target is rejected before production host write

- **WHEN** the requested build tag cannot be resolved to a canonical immutable ref
- **THEN** the deploy fails before `production_mutation_started` and before any production-host write
- **AND** it leaves the active release receipt unchanged
- **AND** terminal receipt result is `not_applicable`

#### Scenario: Successful deploy publishes agreed active and attempted identity

- **WHEN** the immutable target is configured and running, env readability passes, and the canonical canary plus handle assertion pass
- **THEN** the workflow atomically writes an owned, readable version-2 receipt with `outcome=deployed`
- **AND** both production and image mutation markers are true
- **AND** configured, running, attempted, active, and legacy image identity agree with the target
- **AND** terminal receipt result is `published`

#### Scenario: Manual old tag does not inherit workflow source

- **WHEN** `workflow_dispatch` selects an arbitrary old tag whose immutable image has no valid digest-bound revision
- **THEN** the receipt leaves attempted and active Git SHA and build-run provenance empty
- **AND** it does not use `github.sha`, tag text, or a SHA-shaped tag as source provenance

#### Scenario: Failed image-mutating deploy rolls back and proves recovery

- **WHEN** image mutation began, the forward path fails, a canonical agreed previous image was captured, rollback commands succeed, and the rollback canary passes
- **THEN** the workflow re-observes the configured and actual running daemon images
- **AND** it publishes `outcome=rolled_back` only when both equal the previous ref
- **AND** legacy active fields describe the restored release
- **AND** the failed attempted image is not the next rollback target

#### Scenario: Canary green without rollback identity agreement is not recovery

- **WHEN** the rollback canary passes but configured and running identities disagree or do not equal the previous ref
- **THEN** the workflow remains red and classifies `outcome=rollback_failed`
- **AND** the issue states that rollback was not proven

#### Scenario: Failed required rollback remains red and explicit

- **WHEN** image mutation began and rollback command, rollback canary, or required terminal identity proof fails
- **THEN** the workflow emits final rollback outputs before returning nonzero
- **AND** it attempts to publish `outcome=rollback_failed`
- **AND** the issue says rollback failed rather than saying production was rolled back

#### Scenario: Image mutation without rollback target is not reported as recovery

- **WHEN** image mutation began, the forward path fails, and no agreed canonical previous image is available
- **THEN** the workflow publishes `outcome=failed_without_rollback` with `rollback_result=not_attempted` and `rollback_reason=no_valid_target`
- **AND** the issue says rollback was not attempted

#### Scenario: Malformed oversized or mismatched prior receipt is untrusted

- **WHEN** the prior receipt is malformed, decodes beyond 65,536 bytes, or does not match the agreed previous ref
- **THEN** the classifier does not reuse any of its source provenance or rollback ancestry
- **AND** independently observed terminal identities and outcome remain reportable

#### Scenario: Version one receipt corroborates identity only

- **WHEN** a structurally valid version-1 prior receipt matches dual observed current identity
- **THEN** the classifier records `prior_receipt_match_status=v1_identity_match`
- **AND** it does not copy version-1 Git SHA, image tag, build provenance, deployed time, or rollback target
- **AND** it derives source afresh from the active immutable digest label or leaves it unknown

#### Scenario: Version two terminal proof may seed ancestry

- **WHEN** a version-2 prior receipt satisfies every terminal-proof invariant and matches dual observed current identity
- **THEN** the classifier records `prior_receipt_match_status=v2_terminal_proof_match`
- **AND** only individually validated provenance and rollback ancestry fields may be reused

#### Scenario: Configured and running image mismatch remains explicit

- **WHEN** terminal configured and actual-running image refs are valid but unequal
- **THEN** active and legacy identity/provenance fields remain empty
- **AND** the receipt records `active_identity_status=mismatch` and both observations
- **AND** neither `deployed` nor `rolled_back` is allowed

#### Scenario: Production mutation before image mutation still publishes terminal truth

- **WHEN** production mutation began but a failure occurred before `image_mutation_started`
- **THEN** image rollback exits zero with `rollback_reason=image_mutation_not_started` and does not write `TINYASSETS_IMAGE`
- **AND** the terminal step attempts to publish `outcome=failed_without_rollback`

#### Scenario: Terminal writer failure remains visible and red

- **WHEN** production mutation began but host observation, classification, transfer, or atomic receipt installation fails
- **THEN** `terminal_receipt_result=failed` is visible to later steps
- **AND** the workflow remains failed, the last atomic receipt remains intact, and the issue says durable terminal truth is not proven

#### Scenario: Pre-host-write failure leaves receipt untouched

- **WHEN** any enumerated runner/read-only step fails before the production mutation marker
- **THEN** always-running rollback and terminal steps emit `pre_host_write_failure` and `not_applicable`
- **AND** neither step mutates production or the prior receipt

#### Scenario: Validly unnecessary rollback exits zero

- **WHEN** the path is pre-host-write, failed before image mutation, or fully forward-green with its corresponding valid non-required tuple
- **THEN** the rollback step exits zero after emitting final outputs

#### Scenario: Required unproven rollback exits nonzero

- **WHEN** image mutation began and rollback is unavailable, fails, has a red canary, or lacks required terminal identity proof
- **THEN** the rollback step emits its truthful final tuple and exits nonzero

#### Scenario: Not needed alone cannot prove forward health in the issue

- **WHEN** rollback reason is `not_needed` but terminal outcome is not `deployed`, terminal active identity is not `agreed`, or the applicable terminal canary is not `passed`
- **THEN** the deploy-failed issue says forward production health is unproven

#### Scenario: Pure classifier matrix is directly executable

- **WHEN** the receipt classifier is invoked with fixture observations for forward success, rollback variants, identity mismatch, prior-receipt rejection, or manual provenance
- **THEN** it returns deterministic outcomes, legacy projections, and safe rollback targets without host or workflow side effects

### Requirement: Nightly Two-Tier Backup And Manual Fresh-Host Data-Restore Drill
The installed backup timer SHALL run nightly at 03:00 UTC and catch up after a missed schedule. `deploy/backup.sh` SHALL create a strict brain archive using SQLite's backup API for database files and a best-effort live full-volume archive that tolerates only GNU tar's file-changed exit 1; it SHALL upload both tiers to the configured rclone `BACKUP_DEST` and apply per-tier daily/weekly/monthly retention, with optional best-effort GitHub release shipping when `GH_TOKEN` is set. Before provisioning, the manually dispatched DR workflow SHALL validate that its selected primary-host artifact is an absolute, readable, non-symlink `tinyassets-data-*.tar.gz` regular file confined to `/var/backups/tinyassets`, contains only the safe `_data` archive shape, and has at least one regular member; it SHALL record the archive SHA-256 plus one representative member path and SHA-256, and path-like GitHub outputs SHALL use protocol-safe encoding. Before provisioning, it SHALL also read only the configured production `TINYASSETS_IMAGE` assignment, normalize at most one matching pair of surrounding single or double quotes, require the resulting exact canonical `ghcr.io/tinyassets/tinyassets-daemon@sha256:<64 lowercase hex>` form, publish that validated nonsecret value as one workflow output, and SHALL NOT copy the primary host's environment or secrets. Before any mutating DigitalOcean request, the workflow SHALL query current distribution-image inventory through the bounded API helper, treat absent pagination navigation as a valid terminal page, follow only valid non-repeating `links.pages.next` continuations on the exact images endpoint within a 10-page budget, aggregate all fetched pages, and select the highest numeric item satisfying `public is true`, `status == "available"`, `distribution == "Debian"`, full slug match `debian-<major>-x64`, and configured-region membership. Catalog/continuation failure, present-but-malformed or cyclic pagination, page-budget exhaustion, malformed inventory, or no eligible image SHALL remain red before resource creation and SHALL NOT fall back to a retired static slug. The resolved provider image slug and daemon runtime image SHALL appear as distinct fields in terminal PASS/failure evidence. DigitalOcean API failure SHALL remain red and SHALL NOT be reinterpreted as absent state; diagnostics SHALL name HTTP status or transport class, read at most 4096 failure-body bytes, emit at most 300 normalized/redacted characters, exclude bearer credentials and the raw body, and never enter a successful-response output. The workflow SHALL provision a fresh DigitalOcean Droplet, bootstrap it, transfer the selected archive with pipeline failure propagation, require the destination SHA-256 to match the preflight digest, restore that exact local file without implicitly starting services, verify the representative member at Docker's inspected restored-volume mountpoint, require exactly one `TINYASSETS_IMAGE=` assignment in the fresh template environment, replace only that assignment with the validated nonsecret digest, start only the daemon separately from that template, probe MCP through an SSH port forward, and attempt destruction of the successful drill host before publishing an unqualified artifact/run/restored-state PASS record. A failed destruction SHALL make the job red and create or update a `dr-failed` escalation containing the Droplet ID, run URL, and bounded diagnostic; it SHALL NOT leave only a PASS record. A red probe SHALL open `dr-failed`, leave the host available by default, and make the workflow conclusion red after evidence handling. A mid-job failure SHALL retain run evidence and run cleanup. A cleanup-only manual dispatch SHALL accept one explicit positive-decimal retained Droplet ID, skip all provisioning, backup, and SSH work, confirm through a bounded lookup that the exact ID has the `tinyassets-dr-drill` name and both `dr-drill` and `tinyassets` tags, and only then make exactly one bounded deletion request with the repository's configured DigitalOcean credential. Before it stops volume consumers or changes the resolved live volume, full-volume restore SHALL validate that a selected gzip archive is readable and contains only regular files and directories rooted at `_data`; it SHALL reject traversal, absolute, mixed-root, non-directory root, symbolic-link, hardlink, and special-file members. It SHALL extract with the `_data` root stripped to a unique staging sibling so hidden files are restored exactly, preserve the resolved live volume root's ownership and mode on that sibling, serialize restores per resolved volume, stop every running container mounting that volume before swapping, and use a same-parent rename swap that automatically restores the prior directory if the replacement rename fails. A successful swap SHALL retain the old sibling for caller-controlled post-canary rollback. A local absolute, readable, non-symlink regular-file `BACKUP_FILE` SHALL be accepted for a previously downloaded full archive, bypass rclone, and remain caller-owned.

#### Scenario: Nightly backup preserves strict brain state and a recoverable full volume
- **WHEN** the persistent 03:00 UTC timer fires
- **THEN** the backup copies top-level SQLite databases transactionally into the brain tier, creates the full live-volume tier, uploads both to `BACKUP_DEST`, and prunes retention
- **AND** a full-tier tar exit of 1 is retained as a hot-volume warning while exit 2 or greater fails the backup

#### Scenario: Invalid drill archive stops before provisioning
- **WHEN** the selected primary-host path is outside the canonical backup root, missing, unreadable, a symlink, not a regular `tinyassets-data-*.tar.gz`, unsafe, corrupt, or has no representative regular member
- **THEN** the workflow exits red before any DigitalOcean Droplet request

#### Scenario: Production runtime image is pinned without secret transfer
- **WHEN** the primary host's final `TINYASSETS_IMAGE` assignment, after removing at most one matching pair of surrounding quotes, is missing, mutable, outside the canonical repository, or not a lowercase SHA-256 digest reference
- **THEN** the workflow exits red before any DigitalOcean mutation
- **WHEN** that assignment is valid and the fresh template contains exactly one `TINYASSETS_IMAGE=` assignment
- **THEN** the drill replaces only that assignment with the validated public digest before Compose starts
- **AND** it does not copy or persist the primary environment or any secret
- **AND** terminal evidence distinguishes the runtime digest from the Debian provider image
- **WHEN** the fresh template contains zero or multiple `TINYASSETS_IMAGE=` assignments
- **THEN** the drill exits red before Compose starts

#### Scenario: Current Debian image is resolved before provisioning
- **WHEN** the bounded aggregate contains multiple image items
- **THEN** the workflow considers only items with `public is true`, `status == "available"`, `distribution == "Debian"`, a full `debian-<major>-x64` slug match, and configured-region membership
- **AND** selects the highest numeric major across all fetched pages
- **AND** it passes that exact current slug into the Droplet creation request

#### Scenario: Complete catalog omits pagination navigation
- **WHEN** a valid image response contains the complete `images` array and no `links` or `pages` navigation
- **THEN** the workflow treats that response as the terminal page and selects from its images

#### Scenario: No eligible Debian image is available
- **WHEN** catalog or continuation lookup fails, inventory/pagination is malformed or cyclic, the 10-page budget is exhausted, or no exact eligible image serves the configured region
- **THEN** the workflow exits red before SSH-key creation or any other mutating request
- **AND** it does not retry with a static retired image slug

#### Scenario: DigitalOcean failure is not absent state
- **WHEN** image catalog lookup, key lookup, key creation, Droplet creation, lookup, or deletion receives a non-success HTTP response
- **THEN** the workflow reports only the bounded, normalized, credential-redacted diagnostic with its HTTP status
- **AND** a failed lookup is not treated as permission to create a replacement resource

#### Scenario: DigitalOcean response is adversarially large or secret-bearing
- **WHEN** a failed response exceeds 4096 bytes or contains the exact token, bearer-like text, control characters, or unstructured content
- **THEN** at most 300 sanitized diagnostic characters reach logs or failure evidence
- **AND** the raw body and bearer material do not reach stdout, GitHub outputs, or issues

#### Scenario: Transfer is bound to the selected archive
- **WHEN** either side of the primary-to-drill stream fails or the drill-host SHA-256 differs from preflight
- **THEN** the workflow exits red before restore
- **AND** restore receives the exact transferred absolute file through `BACKUP_FILE` only after the digests match

#### Scenario: Restore and start remain separate operations
- **WHEN** the DR workflow restores the selected archive into a fresh host's `tinyassets-data` volume
- **THEN** the restore script exits after extraction without starting the daemon
- **AND** the workflow verifies one preflight-selected member's path and SHA-256 at Docker's inspected restored-volume mountpoint
- **AND** the workflow writes only the validated public runtime digest into the fresh template
- **AND** the workflow starts only the daemon service in a separate step after that restored-state proof

#### Scenario: Representative restored state does not match
- **WHEN** the selected member is missing, is not a regular non-symlink file under the inspected volume root, or its SHA-256 differs
- **THEN** compose and the MCP probe do not run and the drill remains red

#### Scenario: Fresh-host drill records success or preserves failure evidence
- **WHEN** restored-state proof and the SSH-forwarded MCP probe are green
- **THEN** the workflow first confirms destruction of the drill Droplet
- **AND** only then appends the archive checksum, encoded representative member path, representative member checksum, cleanup confirmation, and run evidence to `docs/ops/dr-drill-log.md`
- **WHEN** the probe is red
- **THEN** it opens a `dr-failed` issue and leaves the Droplet running unless `destroy_on_failure` was explicitly selected
- **AND** the workflow conclusion is red
- **WHEN** a pre-probe step fails after a Droplet exists
- **THEN** the workflow retains run/artifact identifiers and runs mid-job cleanup

#### Scenario: Operator deletes one retained drill Droplet
- **WHEN** an operator manually dispatches cleanup-only with one positive decimal Droplet ID
- **THEN** the workflow skips backup validation, SSH setup, image selection, and provisioning
- **AND** it requires the looked-up resource to have that exact ID, the `tinyassets-dr-drill` name, and both drill tags
- **AND** it makes one bounded DELETE request for exactly that ID
- **WHEN** the cleanup ID is malformed, the identity check fails, or the deletion fails
- **THEN** the cleanup job exits red without provisioning another resource

#### Scenario: Droplet destruction fails
- **WHEN** success cleanup, requested red cleanup, cleanup-only deletion, or mid-job cleanup cannot delete a known Droplet
- **THEN** the job is red and a `dr-failed` escalation records the Droplet ID, run URL, and bounded diagnostic
- **AND** no durable evidence represents that run as an unqualified PASS

#### Scenario: Invalid archive leaves the live volume intact
- **WHEN** a selected archive is corrupt, truncated, unsafe, or cannot be extracted into staging
- **THEN** restore exits without stopping containers or changing the live volume

#### Scenario: Replacement rename fails
- **WHEN** the original volume has been renamed aside and the staged directory cannot be renamed into its place
- **THEN** restore automatically renames the retained original back to the resolved live path and exits with failure

#### Scenario: Concurrent restores use isolated volume locks
- **WHEN** restores target two different resolved volume directories at the same time
- **THEN** each uses a unique sibling stage and completes independently; a second restore of one resolved volume is refused while its lock is held

#### Scenario: Operator restores a downloaded GitHub Release archive
- **WHEN** `BACKUP_FILE` names an absolute readable non-symlink regular file containing a local full archive and no list or timestamp mode is requested
- **THEN** restore uses that archive without rclone and does not delete it

### Requirement: Fresh-host backup configuration matches the runtime contract

The fresh-host environment template SHALL expose the canonical `BACKUP_DEST`
consumed by the root-run backup service and SHALL NOT present unused
`STORAGEBOX_*` fields as sufficient backup configuration. Active operator
guidance SHALL direct operators to configure the named rclone remote as root,
store its configuration at `/root/.config/rclone/rclone.conf` with root
ownership and mode `0600`, and keep destination credentials out of the shared
daemon environment file.

#### Scenario: Operator follows fresh-host backup setup

- **WHEN** an operator configures backup shipping from the fresh-host template and active runbook
- **THEN** the service receives `BACKUP_DEST`
- **AND** root's rclone lookup resolves the documented credential file
- **AND** no unused `STORAGEBOX_*` value is presented as runtime configuration

### Requirement: DNS resolution canary reports probe state through a prior-conclusion alarm sink
The system SHALL declare the DNS canary on GitHub-hosted infrastructure with a `*/15 * * * *` schedule, manual dispatch, and a `dns-canary` concurrency group whose `cancel-in-progress` value is false. That setting SHALL preserve an already running job, while GitHub's concurrency controller MAY replace an older pending same-group run when another run queues; neither the declared schedule nor the group SHALL promise actual dispatch latency or one execution per schedule tick. The probe SHALL call `socket.gethostbyname` once for `tinyassets.io` and once for `mcp.tinyassets.io`, report green only when both calls return without error, and report red otherwise. It SHALL NOT claim that the returned address is public, current across all resolvers, or reachable. The probe job SHALL publish its overall result and diagnostics from a tolerated probe step, then a final non-tolerated step SHALL fail if and only if the published overall result is red. The alarm sink, when the workflow executes, SHALL run regardless of probe-job success, consume the published current-run outputs, create the `dns-red` label if absent, open an issue only when the immediately preceding completed workflow run also failed, append later red evidence to an open issue, and comment recovery before closing an open issue on green.

#### Scenario: Both names resolve
- **WHEN** both single-address resolver calls return without error
- **THEN** the probe reports green even though it does not classify or connect to either returned address, and the final propagation step succeeds

#### Scenario: First red does not page
- **WHEN** the probe is red, there is no open `dns-red` issue, and the immediately prior completed workflow run did not fail
- **THEN** the alarm sink records first-red output without opening an issue and the probe job concludes failure after publishing that output

#### Scenario: Consecutive red opens or updates the incident
- **WHEN** the probe is red and either the immediately prior completed run failed or a `dns-red` issue is already open
- **THEN** the sink opens the threshold-crossing issue or appends the new resolver evidence to the existing issue

#### Scenario: Red conclusion becomes threshold evidence
- **WHEN** the tolerated probe step publishes red
- **THEN** the final probe-job step exits non-zero, the current alarm sink still receives the published red output, and the completed workflow exposes failure to the next run

#### Scenario: Green closes an open DNS incident
- **WHEN** the probe is green and a `dns-red` issue is open
- **THEN** the sink comments `GREEN — RECOVERED` evidence and closes the issue as completed

### Requirement: Scheduled release reconciliation uses deploy-run ancestry as its production proxy
The system SHALL declare release reconciliation with a `*/15 * * * *` schedule, manual dispatch, and a completed `workflow_run` trigger for `Docker build smoke` on `main`. The reconciliation job SHALL accept schedule and manual reconcile events, but it SHALL accept a `workflow_run` event only when the triggering run concluded successfully, its `head_branch` is `main`, its head repository is the current repository, and its upstream event is `push` or `workflow_dispatch`. The workflow SHALL retain a `release-reconcile` concurrency group whose `cancel-in-progress` value is false. That setting SHALL preserve an already running job and, under GitHub's default single-pending policy, SHALL coalesce a burst into at most one pending same-group run whose newer arrival can replace an older pending run; neither the declared triggers nor the group SHALL promise actual dispatch latency or one execution per event or schedule tick.

When executed, reconciliation SHALL check out current `main`, derive the newest release-relevant commit on `main` from the push-path list in `build-image.yml`, and fall back to current `HEAD` when that list cannot be read. Because that list is a coarse superset, reconciliation SHALL then refine the commit with `scripts/runtime_paths.py newest-runtime-commit` to the newest first-parent commit that changed a runtime input (a Dockerfile `COPY`/`ADD` source, a host-service input, a deploy-chain or host-writing workflow, or a served `PLAN.md` change-loop excerpt), so a merge that `build-image.yml` skipped as runtime-equivalent is not reported as drift; a missing or failing classifier SHALL keep the coarse commit. A command failure while reading release history, or an empty release-path history, SHALL produce an explicit deferred result rather than being treated as empty history or in-sync evidence. Before dispatching, reconciliation SHALL enumerate queued or running main-branch `build-image.yml` and `deploy-prod.yml` runs. If any active run's `head_sha` contains the release-relevant commit by Git ancestry, it SHALL report that release work is already converging; an older active SHA that does not contain the relevant commit SHALL NOT suppress recovery. A failure to read active-run, deploy-retry, or successful-deploy state SHALL make no corrective dispatch and SHALL defer to a later wake-up. Deferred results SHALL render as no release decision and SHALL NOT render as production being current with `main`.

Reconciliation SHALL enumerate successful `Deploy prod` workflow runs filtered to `main`; when any returned run `head_sha` contains the release-relevant commit by Git ancestry it SHALL report in sync. Otherwise it SHALL dispatch `build-image.yml` on `main`, identify and wait for the resulting same-SHA workflow-dispatch run, and explicitly dispatch `deploy-prod.yml` with the built commit's 12-character immutable image tag only if current repository `main` still equals the built SHA and no active or successful same-SHA deploy already exists. A failed same-SHA deploy triggered by the normal `workflow_run` chain SHALL permit one explicit reconciliation retry. A completed non-success same-SHA `workflow_dispatch` deploy SHALL defer later wake-ups before another image build or deploy, capping automated reconciliation retry at one per SHA while preserving the deploy failure alarm. A reconcile-initiated manual image build SHALL NOT cancel an active push image build; a newer push MAY still supersede older image work. Main advancement before run discovery or after build completion, and cancellation caused by newer main work, SHALL defer without stale deployment; other image-build failures SHALL remain visible as errors. The executable workflow contract SHALL run in CI under Python 3.12, matching the production Ubuntu runner's system interpreter. Docker-smoke success SHALL be only a wake-up signal and SHALL NOT replace the deploy-ancestry decision. This current proxy SHALL NOT claim to read the live release receipt or prove that production still serves the returned deploy-run SHA.

#### Scenario: Trusted main Docker smoke wakes reconciliation
- **WHEN** own-repository `Docker build smoke` completes successfully for a `push` or `workflow_dispatch` event with `head_branch` equal to `main`
- **THEN** the reconciliation job is eligible and evaluates current `main`

#### Scenario: Pull-request, fork, failed, or non-main smoke cannot run the privileged job
- **WHEN** `Docker build smoke` completes for a pull request, a different head repository, a branch other than `main`, or a conclusion other than success
- **THEN** the reconciliation job does not execute

#### Scenario: Schedule remains a best-effort event-independent backstop
- **WHEN** no qualifying Docker-smoke completion wakes reconciliation
- **THEN** the `*/15 * * * *` schedule and manual dispatch remain declared
- **AND** the declaration makes no guarantee that GitHub starts a run within 15 minutes

#### Scenario: Current active release work suppresses duplicate dispatch
- **WHEN** a queued or running main image-build or deploy run's `head_sha` contains the newest release-relevant commit
- **THEN** reconciliation reports that release work is already converging and does not dispatch another build

#### Scenario: Stale active release work does not suppress recovery
- **WHEN** queued or running release work has a `head_sha` that does not contain the newest release-relevant commit
- **THEN** reconciliation continues its deploy-ancestry decision and may dispatch recovery for current `main`

#### Scenario: Unknown GitHub run state fails closed
- **WHEN** the active-run query or successful-deploy query fails
- **THEN** reconciliation records that no decision was made and performs no corrective dispatch
- **AND** the operator summary does not claim production is current

#### Scenario: Release-history command failure is not empty history
- **WHEN** `git rev-parse` or `git log` fails while deriving the release-relevant commit
- **THEN** reconciliation emits a deferred no-decision result with a warning
- **AND** does not report empty release-path history or production in sync

#### Scenario: Failed explicit retry caps same-SHA production mutation
- **WHEN** the normal chained deploy failed and one same-SHA workflow-dispatch deploy has also completed without success
- **THEN** later reconciliation wake-ups defer before another image build or deploy
- **AND** the existing deploy failure alarm remains the durable operator signal until newer main work arrives

#### Scenario: Reconcile-initiated build reaches explicit deploy
- **WHEN** no active or successful release run contains the relevant commit, the requested main image build succeeds, and repository `main` still equals the built SHA
- **THEN** reconciliation explicitly dispatches `deploy-prod.yml` with the built SHA's 12-character image tag

#### Scenario: Existing same-SHA deploy suppresses duplicate dispatch
- **WHEN** an active or successful deploy already exists for the built main SHA
- **THEN** reconciliation performs no duplicate explicit deploy dispatch

#### Scenario: Main advancement suppresses stale image deployment
- **WHEN** repository `main` advances while a reconcile-initiated image build runs
- **THEN** reconciliation does not dispatch deployment of the older image and leaves the newer main for a later release evaluation

#### Scenario: Newer push supersedes recovery build without false alarm
- **WHEN** a newer push cancels the reconcile-initiated image build
- **THEN** reconciliation records benign supersession and performs no stale deployment
- **AND** a failed, timed-out, or otherwise non-successful build that was not cancelled remains a visible error

#### Scenario: Manual recovery build preserves active push build
- **WHEN** a reconcile-initiated manual image build enters the same concurrency group as an active push image build
- **THEN** the manual run does not cancel the active push run

#### Scenario: Smoke stampede coalesces without duplicate corrective dispatch
- **WHEN** one reconciliation is running and 999 same-group wake-ups arrive while its current-main image build becomes active
- **THEN** the running reconciliation remains active, the default concurrency policy retains at most the latest pending reconciliation, and the coalesced pending run observes the active build and performs no second corrective dispatch

#### Scenario: Later successful deploy contains the relevant commit
- **WHEN** a successful main-branch deploy run's `head_sha` is a descendant of the newest release-relevant commit
- **THEN** reconciliation reports no action even when later docs-only commits exist on `main`

#### Scenario: Empty release-path history is a no-op
- **WHEN** path extraction succeeds but no commit touching a release path is found
- **THEN** reconciliation reports no release-relevant history and does not dispatch

#### Scenario: Deploy-run metadata can be a false-green proxy
- **WHEN** a successful deploy run's `head_sha` contains the relevant commit but its published live receipt or current production state differs
- **THEN** this reconciler can still report in sync because it does not read either live source

### Requirement: Disk-pressure timer preserves ordered alert, rotation, and disposable-host remediation
The system SHALL provide a persistent systemd timer definition with five-minute post-boot, one-hour-since-active, and minute-27 calendar triggers plus a 180-second oneshot service sourcing `/etc/tinyassets/env`; the repository artifact alone SHALL NOT claim that a particular host installed or enabled it. The service SHALL declare three sequential commands in this order: disk alerting, run-transcript rotation, and bounded daemon-image retention. Disk alerting SHALL return 1 at or above `DISK_WARN_PCT` (default 80) whether it opens an issue, lacks a token, or runs dry, and the service SHALL accept status 1 so that the ordered commands still execute. Unexpected process statuses other than 0 or 1 SHALL still fail the unit.

Alarm and retention SHALL measure pressure as `100 * (1 - available_bytes / total_bytes)`, with zero available and positive total measured as100%. Retention SHALL remove only individually registry-verified immutable TinyAssets daemon cache images under deployment serialization. Its keep set SHALL be all container references (running or stopped), the current daemon image, every daemon image at least as new as the current one, the two newest older daemon images, and the configured and receipt rollback references. In the default `count` mode (`TINYASSETS_DAEMON_IMAGE_RETENTION_MODE`), every pass SHALL remove images outside the keep set regardless of disk pressure; pressure at or above the default85% trigger SHALL grade the pass as relieved (at or below the75% low watermark) or unmet (exit1). The `threshold` mode SHALL keep the original behaviour: no removal below the trigger, and removal stops at the low watermark. Any other mode value SHALL refuse before work. A candidate whose registry recovery cannot be proved SHALL be preserved and reported while the pass continues with the remaining candidates, within at most eight verification attempts; such a pass SHALL NOT report success. A pass with nothing to remove SHALL NOT take the host locks. Post-deploy, daily and hourly cleanup SHALL use this same narrow policy, never broad prune, journal vacuum, volume, container or user-data deletion. Each pass SHALL stop at four removals or its120-second work budget.

Registry verification SHALL occur before acquiring the fence-then-mutation locks; the locked phase SHALL have a maximum60-second budget. Any fence-state file SHALL refuse deletion. The current configured image and authoritative volume-root release receipt SHALL be reread before every removal. Unknown image-store filesystem mapping SHALL refuse even dry-run planning. Image removal SHALL require both an explicit apply argument and an exact retention-specific operator opt-in. Absent/0 opt-in SHALL keep retention read-only; malformed values SHALL refuse before work. Installing/enabling timers alone SHALL NOT authorize image removal. This flag SHALL NOT alter alarm or transcript rotation behavior; installed dry-run proof SHALL invoke the helper directly.

#### Scenario: Below warning threshold reaches all three commands
- **WHEN** the watched path is below the warning threshold and earlier commands otherwise succeed
- **THEN** disk alerting returns 0 and systemd proceeds to transcript rotation and the auto-prune threshold check

#### Scenario: Pressure alert preserves the cleanup chain
- **WHEN** disk usage is at or above the warning threshold
- **THEN** `disk_watch.py` returns 1 after its issue or warning path
- **AND** systemd accepts status 1 and proceeds to transcript rotation and auto-prune in their declared order

#### Scenario: Unexpected failure still fails the unit
- **WHEN** a service command returns a status other than 0 or 1
- **THEN** systemd treats the oneshot as failed

#### Scenario: Excess daemon cache is removed by count, not by pressure
- **WHEN** count mode runs below the pressure trigger, guards permit mutation and exact registry recovery is verified
- **THEN** only unreferenced immutable daemon image refs outside the keep set are removed non-force, oldest first, with fresh reference checks under the lock
- **AND** the running image, every container reference, the configured and receipt rollback refs, newer images and the two newest older images remain

#### Scenario: Recoverable old daemon cache relieves pressure
- **WHEN** pressure reaches85%, guards permit mutation and exact registry recovery is verified
- **THEN** the same keep set applies and the pass reports pressure relieved or unmet
- **AND** in threshold mode the low watermark and work bounds stop further removals

#### Scenario: One unrecoverable image does not block retention
- **WHEN** registry recovery cannot be proved for one candidate
- **THEN** that image is preserved and reported, and other verified candidates are still removed

#### Scenario: Unknown state does not authorize deletion
- **WHEN** lock/fence, current image, protected refs, measurement or remote recovery cannot be established
- **THEN** retention emits a sanitized refusal or unknown result and performs no unsafe deletion
- **AND** unknown alarm mapping reports unknown/status1, permitting the ordered rotation step rather than asserting healthy pressure

#### Scenario: Containerd repeats an immutable digest in its tag field
- **WHEN** an image's only RepoTag exactly equals its sole fixed-repository immutable RepoDigest
- **THEN** that duplicate digest alias does not by itself disqualify the image
- **AND** mutable, foreign, additional or mismatched aliases remain excluded, and all protection, registry and locked recheck gates still apply

#### Scenario: Dry-run, post-deploy and daily scheduling remain narrow
- **WHEN** retention runs dry, through the daily cleanup timer, or from the deploy workflow after the public canary and protected receipt are green
- **THEN** dry-run never deletes and every entrypoint obeys the identical keep set, recovery and preservation gates
- **AND** a post-deploy retention failure is a warning that does not fail the successful deploy

### Requirement: No uptime probe expects the platform to hold a model

The platform has no LLM (AGENTS.md Hard Rule 15), so no scheduled probe, deploy gate or keepalive SHALL require the daemon to report a bound model or SHALL exercise a host model login. The former LLM-binding canary, its post-deploy binding gate and the weekly Codex and Claude keepalives are retired. The production deploy SHALL instead run `deploy/retire_platform_llm_logins.sh` only after the public canary is green; it removes retired credential names from the host env file, deletes both platform login directories in full (founder decision 2026-09-24) behind exact-path, mount and symlink guards, and logs names and counts only. The same step retires the platform GitHub push credential: it scrubs the push-capability maps from the env file and removes the GitHub App token refresher's units, script, env file and documented private key, naming (not deleting) a key configured anywhere else. `tests/test_no_platform_llm_credentials.py` and `tests/test_no_platform_github_push_credential.py` fail if compose, the entrypoint, any workflow, the env template, the drop-first helper or the code reintroduces either.

#### Scenario: A green deploy retires what the host still holds

- **WHEN** a deploy's public canary is green
- **THEN** the retirement step runs, refuses if the running daemon still defines `CODEX_HOME` or `CLAUDE_CONFIG_DIR`, and otherwise removes the retired names and credential files

#### Scenario: Only the exact platform login directories are ever deleted

- **WHEN** a login directory is a symlink, resolves anywhere but `<volume>/.codex` or `<volume>/.claude`, is referenced by a universe symlink or config, or the volume is not the daemon's `/data` mount
- **THEN** the step deletes nothing there, fails loudly, and leaves the universes (`u-*`) untouched

### Requirement: Executable Uptime Alarm Concurrency Proof

The uptime control path SHALL preserve the global `uptime-canary` concurrency group with `cancel-in-progress: false` and SHALL have an executable proof that runs the exact alarm-sink GitHub-script against shared incident state. The proof SHALL model one running plus one replaceable pending run, execute serialized and coalesced schedules, and prove a single incident across red, unknown, later red, and green observations. It SHALL prove unknown makes no incident mutation, later red appends to the same incident, green closes it, and the actual paging decision sees the shared PAGED marker and produces no duplicate immediate page. The proof artifact SHALL state the command, environment, date, scheduler-model limitation, and result.

#### Scenario: Coalesced uptime ticks preserve one incident and one immediate page

- **WHEN** a red observation opens an incident and overlapping ticks are serialized or coalesced under the global concurrency group
- **THEN** unknown performs no mutation, a later red appends to that same incident, and green closes it
- **AND** the real paging decision treats the shared immediate-page marker as ineligible for another immediate page

### Requirement: Convergent Host Uptime Service Installation
The production host uptime installer SHALL synchronize the five service/timer pairs for `tinyassets-watchdog`, `daemon-watchdog`, `tinyassets-backup`, `tinyassets-prune`, and `tinyassets-disk-watch` plus the exact runtime closure `deploy/{daemon-watchdog.sh,backup.sh}`, `scripts/{__init__,watchdog,mcp_public_canary,disk_watch,disk_autoprune,rotate_run_transcripts,backup_ship_gh,backup_prune}.py`, `tinyassets/__init__.py`, and `tinyassets/storage/{__init__,rotation}.py`. It SHALL install runtime assets into a content-addressed release, make unit execution resolve through one atomic `current` pointer, and give disk-watch the installed working directory required for its module import. After acquiring a bounded-wait lock derived from canonical safe target roots, it SHALL pause timers without killing active services, treat active, activating, reloading, and deactivating services as non-quiescent, fail closed on unreadable or unknown systemd state, wait a bounded interval for oneshots to finish, install the complete manifest, reload systemd, atomically activate the release, enable and start every timer on every invocation, and fail unless every timer is both enabled and active. After a successful install it SHALL remove release directories beyond the live release plus the four newest others, touching only directories with the release-id name shape; a pruning failure SHALL be logged and SHALL NOT fail the install. Fresh-host bootstrap, source-SHA-pinned post-deploy reconciliation, and the restart workflow's install option SHALL invoke this same installer and obtain its exact manifest from that installer. Bootstrap Git operations and source-SHA resolution SHALL converge across the canonical checkout's root-to-service-account ownership transition while every Git process runs as the checkout's current owner; bootstrap SHALL NOT add any safe-directory exception, and SHALL validate a full lowercase commit SHA before invoking the shared installer. Automatic reconciliation SHALL use the triggering workflow's full source SHA; manual and restart dispatches SHALL use and record immutable `github.sha`. Each workflow SHALL verify a checksum in a unique remote staging directory. Bootstrap SHALL provide `sudo`, `visudo`, and `flock`; the installer SHALL validate a private scoped-watchdog sudoers candidate before atomic installation. Same-target installs SHALL wait boundedly and each acquired caller SHALL verify convergence; installs against distinct target roots SHALL remain parallel. Both watchdog timers SHALL remain enabled and active on a fresh host while their services skip recovery until `TINYASSETS_IMAGE` is configured.

#### Scenario: Fresh host receives every uptime layer
- **WHEN** bootstrap runs against a host with none of the uptime units installed
- **THEN** all five timer/unit pairs and the exact runtime closure are installed before systemd reload
- **AND** disk-watch can import transcript rotation from its installed working directory
- **AND** every timer is enabled, active, and explicitly verified
- **AND** watchdog services do not start an intentionally unconfigured daemon

#### Scenario: Repeat install repairs disabled current timers
- **WHEN** every installed file is byte-current but one or more required timers are disabled or inactive
- **THEN** rerunning either installer caller enables and starts every timer instead of skipping activation

#### Scenario: Bootstrap resolves the checkout under its owning identity
- **WHEN** fresh bootstrap reads the root-owned checkout or repeat bootstrap updates and reads the service-account-owned checkout
- **THEN** every Git process runs as the checkout's current owner
- **AND** bootstrap stores a validated full lowercase `HEAD` SHA for the shared installer
- **AND** no safe-directory exception is created

#### Scenario: Installation failure stays visible
- **WHEN** a prerequisite, repository/SHA validation, manifest/checksum validation, sudoers validation, copy, daemon reload, timer activation, enabled check, or active check fails
- **THEN** bootstrap or the installer exits non-zero and does not report convergence

#### Scenario: Active oneshot is not killed or mixed with new files
- **WHEN** a timer service remains active after timers are paused
- **THEN** the installer waits without stopping that service
- **AND** on bounded timeout it restores timer activation and exits before changing installed files

#### Scenario: Workflow installs the triggering source
- **WHEN** host reconciliation follows a successful deploy workflow run
- **THEN** its checkout and checksummed private remote bundle use that triggering run's full source SHA
- **WHEN** reconciliation is manually dispatched
- **THEN** it records, bundles, and installs the dispatch's immutable `github.sha`
- **AND** the restart workflow's install option delegates its `github.sha` bundle to the same installer

#### Scenario: Concurrent targets remain isolated
- **WHEN** at least 64 callers install into distinct resolved target roots concurrently
- **THEN** every target receives only its own manifest and converges independently
- **WHEN** at least 32 callers target the same resolved root within the lock-wait bound
- **THEN** their mutations do not interleave and every caller subsequently verifies convergence

### Requirement: Production deploy preserves and converges off-host backup authority

Application deployment and exact-source host-service installation SHALL
preserve a verified host-owned `BACKUP_DEST`, keep Spaces credentials confined
to root's rclone configuration, and provision the documented bucket-scoped
destination before enabling the backup timer only when both destination and
credential configuration are absent.

#### Scenario: Application deploy preserves canonical backup destination

- **WHEN** production already has a `BACKUP_DEST` assignment and an application deploy scrubs stale runtime overrides
- **THEN** the deploy leaves `BACKUP_DEST` unchanged
- **AND** it continues deleting the retired rename-era overrides

#### Scenario: Exact-source installer sees working backup configuration

- **WHEN** `BACKUP_DEST` and root's rclone configuration are both present and the configured destination probe succeeds
- **THEN** host-service installation reuses them without creating or rotating a Spaces key
- **AND** the backup timer remains enabled

#### Scenario: Exact-source installer sees completely absent backup configuration

- **WHEN** both `BACKUP_DEST` and root's rclone configuration are absent
- **THEN** the installer creates one bucket-scoped read/write key through the provider API
- **AND** targets the existing immutable external destination `spaces:workflow-backups-jonnyton-sfo3/workflow-backups`
- **AND** installs its credentials only at `/root/.config/rclone/rclone.conf` with root ownership and mode `0600`
- **AND** sets the nonsecret destination through the atomic TinyAssets environment installer
- **AND** retries the non-mutating destination probe within a bounded propagation window
- **AND** verifies the destination before enabling the backup timer

#### Scenario: Backup configuration is partial or invalid

- **WHEN** only one configuration half is present or the configured destination probe fails
- **THEN** installation exits red without overwriting or rotating the existing credentials

#### Scenario: New credential installation fails

- **WHEN** a newly created key cannot be installed and verified
- **THEN** failure cleanup removes any newly written destination/configuration and requests deletion of that exact new key
- **AND** no API token, Spaces secret, or provider response body reaches logs, outputs, artifacts, or the daemon environment
- **AND** provider failures expose only the HTTP or transport class plus an allowlisted error identifier/category derived without printing provider message text

#### Scenario: New credential has not propagated to the data plane

- **WHEN** the provider creates the scoped key but the destination probe initially returns access denied
- **THEN** the installer retries that same credential with bounded backoff
- **AND** does not create another key, broaden the grant, or enable the backup timer
- **AND** rolls back the key and host configuration if the propagation window expires

#### Scenario: Product rename does not rename external backup infrastructure

- **WHEN** product-facing source and documentation use the TinyAssets name
- **THEN** the installer and runbook retain the provider identity of the existing pre-rename Spaces bucket
- **AND** they do not silently substitute a similarly named bucket that has not been created and verified

#### Scenario: Operator explicitly exercises a production backup

- **WHEN** an exact-source manual host-service dispatch sets `run_backup` true
- **THEN** the workflow starts the installed backup service after convergence
- **AND** requires a successful service result and fresh brain/full archives at the primary destination
- **AND** scopes journal evidence to the new service invocation's exact systemd invocation ID
- **AND** requires two successful GitHub release asset uploads and the terminal backup completion marker from that invocation
- **AND** rejects any backup or retention warning/error from that invocation
- **AND** emits no environment-file contents, rclone configuration, or credential values

#### Scenario: Host-service installation does not request backup exercise

- **WHEN** a deploy-triggered run or manual dispatch leaves `run_backup` false
- **THEN** host services converge without starting the backup service

#### Scenario: GitHub release listing lags a successful upload

- **WHEN** a backup release and asset have been created successfully but the release-list endpoint has not yet returned that new release
- **THEN** retention boundedly waits for a list view that includes the newly created release before evaluating the prunable set
- **AND** each API request has an explicit transport timeout and one shared wall-clock budget across listing, deletion, tag cleanup, and sleeps caps reconciliation at two minutes
- **AND** an already-deleted victim returned by a stale list triggers bounded reconciliation rather than being counted as a successful deletion
- **AND** it deletes only the oldest recognized backup releases needed to leave at most `BACKUP_GH_RETAIN` recognized releases
- **AND** it never deletes an unrecognized parked or audit release

### Requirement: Terminal Receipt Separates Cleanup From Forward And Rollback Truth

Terminal publication SHALL preserve the original forward and rollback
observations while separately recording whether deploy cleanup restored the
boot posture or safely re-fenced the fleet. It SHALL derive running image
identity only from a container whose inspected running state is true.

#### Scenario: Post-forward cleanup safely fences the fleet

- **WHEN** the forward deploy and its canary succeed, rollback is truthfully
  `not_needed`, and cleanup cannot prove restored boot posture but does prove
  the fleet authoritatively restart-fenced
- **THEN** the terminal receipt preserves the successful forward and
  not-needed rollback tuple
- **AND** records cleanup mutation, `cleanup_restored=false`, and
  `cleanup_safely_fenced=true`
- **AND** classifies the terminal outcome as `failed_without_rollback` with a
  failed applicable canary instead of rejecting a contradictory synthesized
  rollback tuple or reporting the stopped container as running

#### Scenario: Cleanup proof is contradictory or incomplete

- **WHEN** cleanup is simultaneously recorded restored and safely fenced, a
  safe fence lacks a cleanup mutation marker, or a mutated production deploy
  has neither exact restoration nor safe-fence proof
- **THEN** terminal receipt construction fails closed and the workflow remains
  red without replacing the prior atomic receipt

### Requirement: A Deploy Waits For In-Flight Work Before It Swaps The Daemon
The production deploy SHALL NOT recreate the daemon while work is in flight. It MAY proceed regardless in four cases only:
- its cap is reached (45 min, including the image prefetch);
- the daemon is not serving;
- the in-flight check cannot answer three times running;
- a host-mutating recovery workflow (p0-outage-triage, restart-daemon, install-host-services, apply-daemon-env) is queued behind it.

Work is in flight when either holds:
- an account seat is held by a live holder process, whether or not its lease has expired;
- a queued, running or resumed graph run is owned by a live process. A run row with no owner token counts if the run started after the daemon container did.

The check runs before the swap and outside the host-mutation lock, from deploy-prod's "Wait for in-flight turns" step. That step runs `deploy/wait_for_turns.sh`, which runs `scripts/turns_in_flight.py`. The check SHALL run in a throwaway sibling container that uses the daemon's image and uid, mounts the data volume, has no network, and never execs into the daemon. The probe file SHALL be checksum-verified before each poll. Every remote call SHALL be bounded. While waiting, the deploy SHALL refresh an expiring `/data/.deploy-pending.json` marker, which `get_status.deploy_pending` reports. Live proof (2026-10-02): deploy run 36979226551 held the swap while the founder's turn `c5264d0a253a4d07b6ff7466a087b6cf` ran past 5 minutes; that turn ended `completed`.

#### Scenario: A long turn finishes before the swap
- **WHEN** a deploy starts while a chat turn holds an interactive seat
- **THEN** the deploy polls until the seat is released, swaps only then, and the turn completes on the old daemon

#### Scenario: The cap still bounds the wait
- **WHEN** work stays in flight for the whole cap
- **THEN** the deploy proceeds with outcome `cap_reached`, and the cut turn is settled and reported by the startup reconcile

#### Scenario: Recovery is never queued behind a polite wait
- **WHEN** a host-mutating recovery workflow is queued behind the deploy's concurrency group
- **THEN** the wait ends with outcome `yield_to_host_mutation` and the deploy proceeds

#### Scenario: An unverifiable probe is never read as idle
- **WHEN** the probe upload is missing, partial or stale, or the sibling container cannot run
- **THEN** the poll is unknown, and only three consecutive unknowns let the deploy proceed (`check_unavailable`)
