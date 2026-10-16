# development-coordination-runtime Specification

## Purpose
Define the repository's cross-provider orientation, claim, worktree, context, drift, and Agent Village coordination behavior.
## Requirements
### Requirement: Session Orientation Reports Repository Freshness Without Mutating Work

The session sync gate SHALL fetch the configured remote with pruning unless invoked with `--no-fetch`, identify the primary checkout, and report whether that checkout is on `main` and how many commits it trails the configured base reference. Its default mode MUST be advisory and MUST NOT switch branches, pull, reset, clean, or otherwise modify tracked work. In strict mode it SHALL return non-zero when the primary checkout is off `main` or behind the base reference.

#### Scenario: Dirty checkout is behind main

- **WHEN** the primary checkout is behind `origin/main`
- **THEN** the gate prints the behind count and a synchronization instruction
- **AND** it leaves the checkout and its dirty files unchanged

#### Scenario: Strict mode exposes stale orientation to automation

- **WHEN** `--strict` is supplied and the primary checkout is off `main` or behind the configured base
- **THEN** the gate exits non-zero after reporting the condition

### Requirement: Worktree Inspection Preserves Lane Intent

The worktree status tool SHALL combine Git worktree state, branch/upstream state, pull-request/merge evidence, and local `_PURPOSE.md` metadata into a per-worktree classification. Dirty worktrees MUST take precedence over cleanup classifications. A clean lane whose branch is fully merged or whose upstream is gone MAY be marked ready to remove, while an unmerged clean local branch with purpose metadata but no pull-request route SHALL be reported as needing promotion. The tool SHALL expose both a human table and machine-readable JSON, and any printed cleanup commands MUST remain suggestions rather than executing removal.

#### Scenario: Dirty merged worktree is not declared removable

- **WHEN** a worktree has local changes even though its branch is merged
- **THEN** the tool classifies it as dirty and does not mark it ready to remove

#### Scenario: Purpose exists but integration route is missing

- **WHEN** a clean local branch has `_PURPOSE.md` but no upstream or pull request
- **THEN** the tool reports that the lane needs PR promotion

### Requirement: Provider Context Is Recovered At Lifecycle Checkpoints

The provider context feed SHALL scan shared ideas, activity, provider memories, brain notes, execution artifacts, and live worktree purpose files, then rank and cap candidates so one noisy file or source type cannot suppress all other sources. It SHALL accept the lifecycle phases `claim`, `plan`, `build`, `review`, `foldback`, and `memory-write`, and SHALL include cross-provider memory during claim orientation while filtering it from narrower phases unless otherwise shared. Purpose files belonging to merged or dead worktree branches SHALL be excluded when branch evidence is available. The feed MUST remain contextual evidence, not authority to add, claim, or implement work.

#### Scenario: Claim phase surfaces cross-provider history

- **WHEN** a Codex provider requests the claim-phase feed and relevant Claude memory exists
- **THEN** that memory is eligible to appear alongside shared ideas and current lane context

#### Scenario: Merged lane metadata does not masquerade as active context

- **WHEN** a discovered `_PURPOSE.md` belongs to a worktree branch known to be merged
- **THEN** the feed omits that purpose record from active candidates

### Requirement: Cross-Provider Rule Drift Is Detectable

The cross-provider drift checker SHALL inspect substantive rules in provider-specific configuration surfaces and compare them with the project-wide `AGENTS.md` contract. It SHALL report unmirrored cross-provider conventions with a fix prescription and a non-zero collision result, while allowing genuinely harness-specific sections that carry an explicit provider-only marker. The checker MUST be diagnostic and MUST NOT rewrite either canonical or provider-specific instructions.

#### Scenario: Shared convention exists only in a provider file

- **WHEN** a substantive convention in a watched provider-specific file has no equivalent in `AGENTS.md` and no harness-specific marker
- **THEN** the checker reports cross-provider drift and instructs the caller to move or mirror the rule into `AGENTS.md`

#### Scenario: Harness-specific section is explicitly marked

- **WHEN** a provider-specific section is labeled as applying only to that harness
- **THEN** the checker does not treat that section alone as cross-provider drift

### Requirement: Cross-provider drift checks cover required artifacts and skill mirrors
The cross-provider drift checker SHALL report a `missing-artifact` issue when a watched provider file references a configured guard artifact that does not exist, and SHALL report `skill-mirror-missing-source`, `skill-mirror-missing`, or `skill-mirror-drift` when `.agents/skills/<name>/SKILL.md` and its `.claude/skills/<name>/SKILL.md` mirror are absent or differ. Each issue SHALL carry a path, message, and concrete prescription; detected drift SHALL exit 2, while a clean tree SHALL exit 0.

#### Scenario: Referenced guard artifact is absent
- **WHEN** a watched provider file names a required guard artifact that is missing
- **THEN** the checker emits `missing-artifact` with the missing path and a create-or-retarget prescription

#### Scenario: Canonical skill and Claude mirror differ
- **WHEN** matching project skill files exist under `.agents/skills/` and `.claude/skills/` but their text differs
- **THEN** the checker emits `skill-mirror-drift` and directs the operator to run the skill sync script

#### Scenario: JSON output preserves issue fields
- **WHEN** the checker is invoked with `--format json`
- **THEN** it emits a JSON array whose issue objects preserve `code`, `path`, `message`, and `prescription`

### Requirement: Coordination inspectors expose automation-facing JSON modes
`worktree_status.py --json` SHALL emit its worktree records as a JSON array, and `provider_context_feed.py --json` SHALL emit its ranked context candidates as a JSON array. JSON mode SHALL inspect and report state without claiming work or mutating a worktree.


#### Scenario: Worktree inventory is machine-readable
- **WHEN** `worktree_status.py --json` completes
- **THEN** it emits a parseable array of the same per-worktree status records used by the human table

#### Scenario: Provider context candidates are machine-readable
- **WHEN** `provider_context_feed.py --json` is invoked at a lifecycle phase
- **THEN** it emits a parseable array of the ranked context candidate records without promoting any candidate into authority

### Requirement: Authority resolution uses a frozen fail-closed v1 decision contract
The authority-resolution contract SHALL use schema version `resolver-decision-v1` and SHALL accept only decision statuses `resolved`, `unresolved`, and `needs-human-decision`. A decision SHALL carry confidence in `[0.0, 1.0]`, at least one evidence handle, a source-role-map entry for every handle, a non-empty resolver version and reason, and no unknown payload fields. Resolver input SHALL require a universe-scoped question, conflict type, and at least one citation while allowing unknown source roles or surface types through the input boundary so the taxonomy guard can return an auditable unresolved decision.

#### Scenario: Decision payload round-trips exactly
- **WHEN** a valid v1 decision is serialized and reconstructed
- **THEN** schema version, status, confidence, evidence handles, source-role map, resolver version, and reason are preserved

#### Scenario: Unknown decision field is rejected
- **WHEN** a raw v1 decision payload includes a field outside the frozen dataclass shape
- **THEN** validation raises instead of silently treating a future schema as v1

#### Scenario: Unknown taxonomy fails closed
- **WHEN** a citation uses a surface type or source role outside the known v1 sets
- **THEN** the guard returns `unresolved` with confidence `0.0`, preserves every evidence handle, labels the unknown entry in `source_role_map`, and names the unknown taxonomy in its reason

### Requirement: The deterministic resolver preserves evidence and never forces a conflicting winner
`resolve_authority` SHALL first apply the unknown-taxonomy guard. For known taxonomy it SHALL return `resolved` with confidence `0.9` when all non-empty normalized claim texts agree; SHALL return `unresolved` with confidence `0.0` when normalized claims conflict; SHALL return `needs-human-decision` with confidence `0.0` when no citation has claim text; and SHALL reframe `surface-mismatch` as `resolved` with confidence `0.82` while preserving every evidence handle and typed surface label. It SHALL preserve the input source role for every known citation and SHALL not implement a configurable precedence policy.

#### Scenario: Matching claims resolve deterministically
- **WHEN** all cited claim texts differ only by case or whitespace
- **THEN** the resolver returns `resolved` at confidence `0.9` with all evidence handles preserved

#### Scenario: Direct conflict remains unresolved
- **WHEN** known citations make different non-empty normalized claims
- **THEN** the resolver returns `unresolved` at confidence `0.0` and does not choose a winner

#### Scenario: Surface mismatch is reframed rather than discarded
- **WHEN** the conflict type is `surface-mismatch` and all citation taxonomy is known
- **THEN** the resolver returns `resolved` at confidence `0.82` and its reason lists every evidence handle with its surface type

#### Scenario: Missing claim text needs human judgment
- **WHEN** no citation provides non-empty claim text
- **THEN** the resolver returns `needs-human-decision` at confidence `0.0`

### Requirement: OpenSpec Delivery Flow Is Inspectable

The development coordination runtime SHALL provide a read-only OpenSpec flow
inspector that enumerates active change directories, counts completed and
unchecked task checkboxes, derives ownership from git branches that name a change,
reports global and exact-session provider WIP, and reports
recent active-change admission and archive counts when a git comparison window
is requested. It MUST expose equivalent human-readable and JSON results and
MUST NOT create, edit, claim, split, sync, archive, or delete any change.
Audit mode SHALL be invoked on demand for dispatch/triage, not as a mandatory
session-start step.

#### Scenario: Current flow is reported without mutation

- **WHEN** a provider runs the inspector against a repository with active
  OpenSpec changes
- **THEN** the result includes aggregate task totals and one record per change
- **AND** the tracked working tree is byte-identical before and after inspection

#### Scenario: Incomplete active change has no owning branch

- **WHEN** an active change with unchecked tasks is named by no git branch
  row
- **THEN** the inspector classifies that change as `untracked`
- **AND** it does not infer implementation authority from the change artifacts

#### Scenario: Automation requests JSON

- **WHEN** the inspector is invoked in JSON mode
- **THEN** it emits a parseable object containing the same aggregate, change,
  provider-WIP, warning, and recommendation information as text mode

### Requirement: New OpenSpec Delivery Changes Are Bounded

The inspector's named change-admission check SHALL reject a candidate with more
than 12 total task checkboxes, completed or unchecked, and SHALL reject
admission when the requesting exact session-specific provider identity already
owns another claimed or in-flight OpenSpec change. It SHALL report global WIP
with the result and SHALL treat minting a provider suffix to evade the limit as
a process-review violation. It SHALL report umbrella/full-vision language as a
semantic-review warning rather than claiming keyword detection proves invalid
scope. Existing oversized changes MUST remain reportable in default audit mode
and MUST NOT make default inspection fail. The 12-task ceiling is a 2026-07-28
calibration that SHALL be reviewed on 2026-08-11 against observed cycle time and
current model capability. Admission mode SHALL run only after scaffolding and
before claiming or building a change.

#### Scenario: Candidate exceeds the task ceiling

- **WHEN** a named candidate change contains 13 task checkboxes
- **THEN** admission exits 2 and identifies the 12-task ceiling

#### Scenario: Provider already owns delivery WIP

- **WHEN** the requesting provider owns one claimed active change and asks to
  admit a different active change
- **THEN** admission exits 2 and identifies the existing change
- **AND** reports global active delivery WIP

#### Scenario: Legacy oversized change is audited

- **WHEN** default audit encounters a pre-existing change with more than 12
  total task checkboxes
- **THEN** it reports the change as oversized
- **AND** the audit remains read-only and exits successfully

### Requirement: OpenSpec Dispatch Is Finish-First

The inspector SHALL recommend complete-but-unarchived changes before any
change with unchecked tasks, then claimed changes by ascending unchecked-task
count, then queued changes by ascending unchecked-task count. It MUST report
untracked changes for triage without recommending that they be built.

#### Scenario: Completed active change exists

- **WHEN** one active change has zero unchecked tasks and another claimed
  change has unchecked tasks
- **THEN** the completed change is the first recommendation

#### Scenario: Claimed slices differ in remaining size

- **WHEN** two claimed changes have no completed change ahead of them
- **AND** one has fewer unchecked tasks than the other
- **THEN** the smaller claimed change is recommended first

#### Scenario: No change has an owning branch

- **WHEN** no active change is named by any git branch
- **THEN** the inspector reports no build recommendation

### Requirement: OpenSpec delivery flow supports exact-ref inspection
The OpenSpec flow inspector SHALL support a caller-selected validated Git ref and SHALL classify active change artifacts, task counts, ownership, and recommendations from one immutable snapshot of that ref. Ref inspection MUST remain read-only, MUST NOT move the working tree, and MUST fail closed rather than mixing working-tree and ref content.

#### Scenario: Detached controller is behind current main
- **WHEN** the working tree contains older OpenSpec content and the caller selects `origin/main`
- **THEN** the inspector reports only the active changes, tasks, and classifications stored at `origin/main`

#### Scenario: Ref snapshot is unavailable
- **WHEN** the selected ref cannot be validated or its coordination snapshot cannot be read
- **THEN** inspection exits non-zero with a bounded diagnostic
- **AND** it emits no fallback report from the working tree

### Requirement: Gate-defining and authority-critical changes need an exact-head review receipt

A pull request touching gate-defining or authority-critical paths SHALL carry an
exact-head review receipt in the pull-request BODY naming an APPROVE verdict, the
unchanged head SHA, and a durable artifact. Gate-defining paths are
`.github/workflows/tests.yml`, `.github/known-failing-tests.txt`,
`.github/heavy-test-files.txt`, `scripts/ci_required_tests.py`, and
`scripts/drain_review_gate.py`; authority-critical paths are `tinyassets/auth/`,
`tinyassets/credential_vault.py`, and
`tinyassets/api/{permissions,interlocutor,visibility,engine_helpers}.py`. The check SHALL run
from the trusted base checkout so a pull request cannot weaken the rule judging
it. Matching SHALL cover renames (via the previous path) and SHALL be
case-insensitive, and SHALL include the packaging runtime mirror of those paths.
A deletion-only edit to the quarantine ledger SHALL be exempt, because it only
tightens the ratchet; any addition SHALL keep the receipt requirement.

#### Scenario: Renaming a protected file does not evade the receipt
- **WHEN** a pull request renames `tinyassets/auth/provider.py` to an unprotected path
- **THEN** the guard reports the authority hit and requires the receipt

### Requirement: Worktree inspection supports narrowed provider scope

The worktree status tool SHALL accept a provider filter and MUST apply it before
running per-worktree probes, so a narrowed inspection does not pay the cost of
lanes it will discard.

#### Scenario: Filtered inspection probes only matching lanes
- **WHEN** the tool is invoked with a provider filter
- **THEN** only lanes matching that provider are probed and reported

### Requirement: Peer dispatch runs from a lane-local worktree with bounded autonomy

Peer dispatch SHALL run the peer CLI as a subprocess against an explicit
working directory so a write-enabled run is confined to a lane worktree rather
than the live checkout. Read-only SHALL be the default; write autonomy SHALL be
explicit. The peer's result SHALL always land in the declared output file, and a
provider failure, timeout, or non-launchable CLI SHALL produce a non-zero exit
and an explicit error marker rather than a silent empty result.

#### Scenario: A failed dispatch is distinguishable from an empty answer
- **WHEN** the peer CLI cannot launch
- **THEN** the output carries an explicit error marker and the exit code is non-zero

### Requirement: Provider launches are consoleless on Windows

Subprocess provider launches on Windows SHALL suppress console-window creation
so an unattended session does not spawn visible windows.

#### Scenario: A dispatched provider opens no console window
- **WHEN** a peer provider subprocess starts on Windows
- **THEN** it is created with no new console

---

## Retired capabilities (2026-08-26)

The requirements below were removed because the machinery they describe was
deleted by the harness reset, and `openspec/specs/` is **as-built** truth rather
than intent. Keeping them would assert behaviour the system no longer has, which
is worse than silence -- a reader cannot tell a stale requirement from a live one.

| Retired | Why |
|---|---|
| STATUS claims / STATUS row lifecycle | `STATUS.md` retired 2026-08-25; live state moved to typed homes (`openspec/changes/`, `docs/concerns/`, `docs/host-actions.md`, git branches) |
| Agent Village (2 requirements) | `command_center/` deleted -- it visualised a concurrent-agent fleet that no longer runs, and read the retired board |
| OpenSpec drain (18 requirements) | `openspec_drain_supervisor.py` and its watchdog/tray deleted; autonomous background workers are out of scope under the two-provider decision. **Three behaviours in that block survived their machinery and were re-specified above** rather than retired with it: the exact-head review receipt (still enforced by `pr-scope-guard.yml`, `auto-enroll-merge.yml`, `scripts/drain_review_gate.py`), lane-local worktree dispatch (`scripts/peer_agent.py`), and consoleless provider launch. A blanket retirement of the block would have deleted live contracts -- caught by cross-family review 2026-08-26. |

Recover any of it from git: the reset merged as `e4180697`, and this file's
history holds the full text.
