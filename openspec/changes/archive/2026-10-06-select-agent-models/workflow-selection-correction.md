# Workflow selection must compose with accepted model authority

## September 14, 2026 UTC — CI fixture and import-isolation correction

PR3832 remains draft at remote089129e8; required-tests and scope review are
terminal failures, not pending jobs. This correction changes tests only.

The four-file Windows reproduction initially failed18 cases. Legacy mock carriers
did not specify selected_model=None, and a synthetic assignment omitted the real
record's empty manifest_digest default. Three invalid background-attempt cases
expected an old hold path; they now assert the earlier identity rejection's exact
terminal reason and no credential snapshot, reservation or provider call.
The corrected group passes135 in18.42s on Windows Python3.14.

Running the provider registration probes before workflow tests then reproduced
two additional failures (59passed/2failed,22.51s): the missing-native-executor
negative test incorrectly succeeded, and background HTTP hit force-mock refusal.
Those probes restored sys.modules but left the parent package's call attribute
pointing at a different module. Package imports and direct module imports then
configured/executed different routers and mock flags. Three probe fixtures now
share a test-only context manager restoring both paths on success or exception.
Four new cases cover present/absent prior modules and failed/successful probes.
Production provider availability and authority checks are unchanged.

Windows Python3.14 expanded command:
`python -m pytest -q tests/test_provider_import_isolation.py
tests/test_provider_binary_probe.py tests/test_provider_stub_registration.py
tests/test_grok_provider_registration.py tests/test_providers.py
tests/test_background_served_provider.py tests/test_run_provider_session.py
tests/test_work_model_selection.py tests/test_background_budget_finalization_e2e.py
tests/test_providers_call.py --tb=short --show-capture=no`
passes172 in29.87s, zero skips,102 dependency deprecation warnings.
`python -m ruff check` for all seven changed Python files and `git diff --check`
pass. The same ten-file Linux oracle55286 completed169passed/3skipped in20.32s
(Python3.11.16, Git2.47.3, bubblewrap0.12.0). The three registration checks require
optional SDKs absent from the image; no workflow or authority case skipped.
Invocation: the existing Ubuntu WSL Docker route documented below, running
`python3 scripts/linux_oracle.py -- -q <the same ten files> --tb=short --show-capture=no`.
A reverse-order Windows group (provider bridge, HTTP/native workflow, then the
three registration probes and new isolation tests) passes72 in23.68s. Strict
OpenSpec validation also passes. This establishes focused correction evidence;
it does not establish that every full-suite CI failure is resolved.
Full fresh CI, remaining native catalogue/workflow-tool capabilities, independent
release review, deployment and rendered model-selection proof remain required.

September 11, 2026, 20:25 UTC. Correction proposal and reproduced blocker;
The initial proposal below is superseded by the reviewed disposition immediately
below. Runtime integration remains unfinished.

## Remaining tool-loop design boundary — September12 00:36UTC

Read-only inspection at089129e8 confirms the selected HTTP workflow tool gap is
not just the router's operation predicate. `check_served_agent_tool_authority`
explicitly rejects a provider_invocation carrier and requires a current claimed
writer/converse request. `InteractiveHttpAgentTurn._check_scope` uses that fence
before every tool; its `_begin` records served-binding and inference-reservation
facts. The foreground `_call` instead arms one RUN_GRAPH_OPERATION invocation
around the whole provider call, with immutable workflow/run authority.

Therefore removing the router's writer/converse guard, fabricating a chat request,
or reusing one launched invocation for multiple HTTP rounds is not an integration
fix. Remaining design must define a workflow-owned coordinator: each inference
round reserves within the same aggregate work receipt, each tool rechecks the
current immutable work/claim/member/owner authority, and progress journals cannot
grant execution or replay uncertain effects. Foreground and assigned-background
cancellation/lease fences must both survive. Existing chat coordination stays
unchanged. No new grant, alternate user identity or owner workflow edit is allowed.

The prior extra review covered aggregate receipts and selected-member provider
invocations, not this tool-dispatch authority adaptation. Include this boundary
with native discovery in the pending remaining-design review, before authority
implementation. The final exact-head release review remains separate. Both need
the owner's requested additional-review exception; none has been dispatched.
This note is a design requirement, not an implemented capability or release proof.

## Cross-platform fixture correction — September12 00:33UTC

At ce424a57, actual Ubuntu WSL Docker oracle12421 returned335passes/13failures
in35.79s with zero skips. All thirteen failures happened while enabling the
synthetic native assignment: readiness consulted the ambient router before the
test's counting executors were installed. Windows passed because native CLIs
were present there. Production availability checks are correct and unchanged.

The foreground helper now installs the same simulated router for readiness and
execution. Scheduler-only tests explicitly supply their simulated executors.
A negative case proves an empty executor inventory still refuses serving.
No approval, custody, admission, budget, router or runtime check was mocked away.

Windows Python3.14: `python -m pytest -q tests/test_work_model_selection.py
tests/test_run_provider_session.py tests/test_background_budget_finalization_e2e.py
tests/test_provider_invocation_selection.py tests/test_provider_work_authority.py
tests/test_selected_model_authority.py tests/test_interactive_http_agent.py
tests/test_custom_source_execution.py tests/test_app_model_picker.py
tests/test_app_model_choice.py --tb=short --show-capture=no`
passes337 in49.53s,102 dependency warnings, zero skips. Ruff and diff checks pass.

Actual Linux oracle34740, ce424a57 plus this test-only correction, runs the same
ten files plus tests/test_shared_background_self.py through
`python3 scripts/linux_oracle.py -- -q <those eleven files> --tb=short --show-capture=no`.
It passes349 in39.63s, zero skips; Python3.11.16, Git2.47.3, bubblewrap0.12.0.
Invocation uses the existing Ubuntu WSL Docker engine and process-local git paths
documented in docs/reviews/2026-09-09-linux-oracle-wsl-proof.md. This also exercises
the POSIX symlink case that cannot run under this Windows user's privileges.
Neither full CI, independent exact-head approval nor live release is established.

## HTTP workflow model execution connected — September12 00:17UTC

Foreground and assigned background text prompts now prepare owned-source
discovery outside assignment admission and SQL transactions, then reconstruct
current home/member/custody/model/price authority inside the reservation fence.
The store records exact selected model, executor, contract and discovery interval
in the existing version3 reservation. The sealed carrier supplies those facts
to the router; ordinary ModelConfig values remain non-authorizing. No new grants,
credentials or alternate work identities. An omitted HTTP model retains its
source's declared default; explicit missing models refuse, never silently default.

Affordable output is bounded before arming; the router additionally enforces
context and reserved-cost limits. Selected text HTTP responses now use the same
contract usage decoder as agent responses, while legacy unselected responses
retain unknown-cost handling. Missing usage is not invented as zero.

Windows Python3.14, 4f503d23 plus this working-tree correction:
`python -m pytest -q tests/test_work_model_selection.py
tests/test_run_provider_session.py tests/test_background_budget_finalization_e2e.py
tests/test_provider_invocation_selection.py tests/test_provider_work_authority.py
tests/test_selected_model_authority.py tests/test_interactive_http_agent.py
tests/test_custom_source_execution.py --tb=short --show-capture=no`
passes286 in39.22s,102 dependency warnings, zero skips. The new eight-case file
proves real store/compiler/router/HTTP executor composition with synthetic network:
default and explicit model across two foreground nodes, background exact selection,
shared receipt and actual usage, absent model/stale catalogue/revoked source/paid
refusal before invocation, finite affordable output. Its discovery reader admits
a second thread through both fences, proving remote IO is outside them.
Ruff, generated435-file plugin build/import and strict OpenSpec validation pass.

Not live or all-model complete: native explicit catalogue/execution remains
unimplemented, and selected HTTP workflow engine-tool loops are still refused.
Native discovery's source-derived design needs its independent review; final
implementation release approval also remains pending. The prior one-review
exception was consumed by workflow authority and authorizes neither new review.
No private workflow or cloud project was edited/run. Subsequent Linux proof is
recorded in the newer entry above; this Windows result alone did not establish it.

## Native background compatibility connected — September11 23:58UTC

The assigned background consumer now uses the same accepted-member selection
fence for manifest assignments. It preserves canonical task, activation, attempt,
owner and executor checks; it issues one aggregate receipt and claim for the
background attempt, with per-call selected-member reservations. No synthetic
provider binding is created for a manifest and its claim identity does not depend
on the structural default's credential. Actual selected custody is snapshotted.
Legacy background admission remains intact. No user workflow was edited or run.

Windows Python3.14 at ce5837fb plus this working-tree correction:
`python -m pytest -q tests/test_background_budget_finalization_e2e.py --tb=short
--show-capture=no` passes9 in8.16s, zero skips. Real-store consumer proofs cover
owner model-access opt-in, independent provider with empty fallback, unrelated
anchor rotation, selected-member rotation refusal, unknown model refusal and
two sources sharing one receipt/claim/allowance (including exhaustion before
the second launch). Actual usage settles once and the main choice is unchanged.

`python -m pytest -q tests/test_run_provider_session.py
tests/test_assigned_queue_consumer.py tests/test_background_budget_finalization_e2e.py
tests/test_provider_work_authority.py tests/test_provider_invocation_selection.py
tests/test_selected_model_authority.py tests/test_app_model_picker.py
tests/test_app_model_choice.py --tb=short --show-capture=no`
passes312 in32.77s,84 dependency warnings, zero skips. Ruff, plugin build/import
and strict OpenSpec validation pass. This group excludes the separately documented
main-baseline Windows symlink privilege failure, not via a skip or quarantine.
No new Linux, independent exact-head approval, deployment or live selector proof.

Native defaults only remain supported here. Prepared HTTP workflow selection,
native explicit catalogue/execution and integrated live selector acceptance are
still open; do not call these local compatibility tests a usable MVP release.

## Foreground native-manifest execution connected — September11

The two real bind/enable/run regressions now pass. Foreground work issues one
version4 aggregate receipt, claims it through manifest-aware admission, and
reserves/arms version3 selected-member invocations through current home, serving
agent, assignment, member, custody and native-default model checks. The carrier
derives the executed provider/custody from that reservation, never the manifest
anchor. Legacy single-provider guards and wire forms remain intact.

The work allowance is bounded by common member ceilings and the immutable
node-policy plan, including the compiler's bounded policy retry count and the
declared fallback tail. Reservation sums enforce both aggregate and member
usage. Launched failures retain their charge. Public raw reserve/arm calls still
cannot activate supplied selection records; selected activation is private to
the validated workflow admission path. Existing cancellation/settlement remains.

Windows Python3.14, September11: six-file group `python -m pytest -q
tests/test_run_provider_session.py tests/test_assigned_queue_consumer.py
tests/test_background_budget_finalization_e2e.py tests/test_provider_work_authority.py
tests/test_provider_invocation_selection.py tests/test_selected_model_authority.py
--tb=short --show-capture=no` passes254cases in22.04s, zero skips. Additional
`-k parallel_manifest` real compiler/router test passes1case in3.71s with a
two-provider rendezvous, one receipt and two settled reservations. The focused
manifest group passes9cases in6.19s: default/pin, mixed members, independent member
surviving anchor rotation, selected member rotation refusal, stale/revoked/home
refusals and unsupported explicit model not silently defaulted. Synthetic owned
credentials/providers only; private workflow and main-provider choice unchanged.
Ruff and plugin mirror/import pass. No Linux or live execution claim yet.

Still required: HTTP prepared model/discovery admission and router propagation,
HTTP background workflow integration, full fallback/loop budget coverage, native
explicit-model catalogue/execution, final integrated tests/review/CI/deploy and
rendered selector acceptance. Passing the two foreground regressions does not
complete these requirements or make this candidate release-ready.

## September11 reviewed implementation decisions

The owner's one extra independent review completed in336s, verdict ADAPT.
Full recovered verdict: docs/reviews/2026-09-11-workflow-model-authority-extra-review.md.
The wrapper saved a later stop-hook recap; the actual review was recovered from
that same invocation's transcript, not by dispatching another review. No tests
were run by the reviewer. This approves the adapted shape, not unimplemented code.

Adopt its typed aggregate design: ProviderUniverseWorkReceipt version4 adds
authority_scope and manifest_digest. Manifest scope has no provider, provider
binding, credential or parent-binding fields (all null); assignment generation,
digest, owner, universe, immutable work subject/claim and aggregate ceilings remain.
Provider scope preserves all legacy semantics. Versions1–3 retain exact wire
documents and reject new fields. ProviderWorkBinding remains provider-specific.
Never fill required legacy columns with a fictional provider or credential.

Reservation version3 will persist exact selected member/binding/custody,
assignment/manifest, model/executor and discovery/cost evidence, covered by the
existing carrier seal. Receipt SQL must support nullable provider-binding columns
through an atomic migration preserving dependent claim/reservation rows and FKs;
old binaries fail closed on new records. No new work IDs or per-provider budget
stores. Reserve/arm revalidate current assignment AND exact selected member.
Replay compares the full selection/fence, not only tokens/cost. Router selection
comes only from sealed authority; caller config never grants execution.

Budget clarification: preserve aggregate arithmetic and ALSO sum charged tokens,
cost and invocation count for each member, not merely check each request against
the member maximum. Use explicit aggregate authorized ceilings where present,
otherwise conservative common ceilings; do not synthesize a union of maxima.
Pre-launch refusals release reservations. A launched failure still consumes its
actual/indeterminate allowance; it is not relabelled pre-launch to fund fallback.
The compiled authorized retry/fallback plan must fit a finite shared work budget;
one-call-per-node is not a sufficient allowance for requested retries. No fresh
full budget per retry or invented identity is permitted.

First implementation step is the inert version4 record and strict parsing tests;
it cannot issue/launch work until the migration and validated store/caller paths
are integrated. Then version3 reservations/carriers, shared admission, both
foreground/background callers and router. Preserve cancellation/recovery and
independent members after anchor revocation. Native explicit model discovery and
executor propagation are still required for the requested all-available-models
selector; a provider-default-only menu is not that MVP.

Owner September11 priority: ship the usable model-selector MVP now, including
seeing the answering source and selecting from all available authorized models.
Do not let unrelated workflow-project or diagnostic lanes delay this release.
Keep the broader goal and fallback/default requirements; this priority is not
permission to deploy the existing workflow regressions or fabricate model names.

Owner reiterated urgency: get model selection usable now. The first user-facing
release target is actual answering-model display, switching and saved default.
Do not extend unrelated lanes or add further fallback enhancements before this
MVP. Existing workflow compatibility remains a release gate; broader requested
fallback behavior stays in the overall goal rather than blocking on new polish.

## User capability and ownership

Users must be able to choose the main serving provider, and independently
choose a provider/model for an individual agent, workflow or task. Their cloud
universe builds and manages those projects. This development task repairs the
general platform, never the private definitions or the universe's project plan.
Permission to use a particular reviewer/model is not a requirement to use it.

## Reproduction beyond a mocked guard

`tests/test_run_provider_session.py::test_accepted_native_model_manifest_preserves_foreground_execution`
has default and provider-pinned variants. Both use the existing synthetic vault,
real bind_serving_provider, real set_serving readiness and authenticated run
admission/compiler/router plumbing. Neither mocks assignment lookup, readiness
or the failing guard. Binding reaches ready with a real manifest digest. The
workflow definition remains unchanged. Both fail before the provider receives
a call; the existing legacy one-call/once-settled control passes.

Command (Windows Python3.14, September11 around20:21UTC):

`python -m pytest -q tests/test_run_provider_session.py -k 'accepted_native_model_manifest or launches_active_serving_provider' --tb=short`

Result: 2failed,1passed,19deselected,6upstream deprecation warnings in4.73s.
Full changed-file follow-up: `python -m pytest -q
tests/test_run_provider_session.py --tb=short --show-capture=no` returned
2failed,20passed,38upstream deprecation warnings in9.07s. Only the two new
regressions fail; all existing cases pass. This is a local synthetic test,
not evidence of live user execution.

September11 20:33UTC follow-up adds real scheduler eligibility, parameterized
over legacy/model-access binding. The legacy case passes; the manifest case
successfully enables serving but list_serving_universes returns an empty list.
The same complete-file command now reports3failed/21passed/38upstream warnings
in9.08s, zero skips. This is not a background-project test: it exercises the
platform's authoritative universe inventory used by its generic coordinator.
Underlying refusal is `model selection authority is not active`, wrapped into
a misleading connect-provider failure by the foreground session/compiler.
Ruff on the changed test passes. Linux verification attempted from an isolated
archive plus this exact test file; Docker's Linux engine was unavailable before
any test started. The archive is not a completed Linux verification.

## Reverified integration boundaries

- `provider_serving_binding._current_serving_authority` rejects all manifest
  assignments. Keep that legacy fail-closed boundary; do not erase it globally.
- `_current_selected_member_authority` validates an accepted connection member,
  not its model selection or permission to launch a particular operation.
- Foreground `_admit` and `_authorize_attempt` still use the legacy helper and
  require the declared providers to match one assignment provider. Background
  `_authorize_launch` repeats that restriction before its activation/lease and
  attempt-budget checks. Those lifecycle checks must survive the correction.
- `list_serving_universes` also calls the legacy helper and catches its refusal,
  silently omitting enabled manifest universes. AssignedQueueConsumer.poll_once
  uses this list for automation submission and heartbeat publication. Thus
  background work can disappear before reaching the per-attempt authority path.
  Correct serving-intent inventory/readiness separately from actual launch
  admission; do not add remote discovery inside each SQL inventory read.
- Both workflow wrappers pass only the one-use invocation carrier to the
  router, not validated model selection. The router clears caller-injected
  `ModelConfig.selected_model`; setting that field alone is intentionally not
  an authority repair. Do not replace the carrier with a caller-made authority.
- A work receipt has one provider, binding and custody digest. Its identity and
  SQL unique constraint are one `(universe, work kind, work id)`; separate nodes
  cannot obtain independent full-budget receipts under invented work IDs.
- The carrier derives provider from that receipt. Model selection must be bound
  to the exact reserved invocation and checked before launch/settlement, not
  attached later as ordinary configuration.
- Native model validation currently accepts only provider default (empty model
  ID). Merely enabling a second subscription cannot establish an explicit Opus
  selection. Native explicit-model discovery/executor propagation remains part
  of the broader feature, not something this guard repair can claim to solve.

## Proposed correction shape and pre-build decisions

Use the existing authority store and operation lifecycle. The work-level
receipt/claim remains the aggregate invocation/token/cost budget and immutable
subject fence. Each invocation selects a currently accepted member under that
same work budget, preserving exact custody, assignment generation, requested
role, operation, executor and model/cost constraints. Refresh remote discovery
outside SQLite/admission locks, then revalidate its source and authority under
the launch fence. Main choice must not be changed to route a child invocation.

Before authority implementation, specify and review the exact versioned
receipt/reservation representation for a work-level aggregate plus per-attempt
member/model facts. It must preserve existing persisted receipts, one-use
carrier provenance, settlement ownership, cancellation and recovery. No union
of per-provider maxima, multiplied per-node allowances, fabricated run IDs or
new unbounded fallback authorization. Legacy receipt parsing remains legacy.
This is a real storage/authority decision, not a one-line provider-name change.

For ordinary agent-accessible main switching, reuse existing agent_binding
operations and owner confirmation where access/spending would expand. The
served wrapper currently omits them. A preference change within already
authorized scope must not masquerade as a new connection/approval request.
Generic pending-request answers cannot be reported as an executed switch.
No new top-level tool or developer-console requirement is proposed.

Primitive checks for bind_serving_provider/set_serving returned CLEAN, but
direct `git show origin/main:tinyassets/api/custom_agents.py` and
`tinyassets/universe_server.py` prove both handlers and mappings already exist.
The diagnostic's negative result is not primitive truth; reuse these actions.

## Required proof before release

Real binding/activation followed by foreground and background execution must
cover no pin, explicit same-source model, independent second source, mixed-node
parallel work and an explicitly empty fallback list. Verify that saved/main
choices and private definitions do not change. Also cover stale/foreign/revoked
members, custody rotation, cost/model exclusion, aggregate exhaustion across
sources, stopped activation, cancelled runs and once-only settlement/recovery.
Each refusal must identify its true class without disclosing secrets.
Verify the configured universe remains in scheduler polling after model access
is enabled. An available selected member must not depend on the structural
anchor's unrelated credential, while revoked/disabled work still cannot launch.

The owner explicitly answered **Allow one additional review** on September11.
That review is now complete: ADAPT336s, disposition at the top of this file.
No further review is authorized by that exception. No unimplemented runtime
or rollout is approved by the shape verdict.
After implementation and release
gates, verify the live deployment and ask only `Retest your workflow checklist`.
The app must confirm usable general capabilities; its own project completion
is neither taken over nor substituted as this task's implementation.

## Inventory integration correction — September 11, 22:21 UTC

## Version4 aggregate receipt implementation — September11 23:00UTC

### Per-invocation selected-member record implemented — September11

Version3 reservations now carry an immutable ProviderInvocationSelection with
exact member binding/generations/digests, custody reference/generation/digest,
assignment/manifest/member digests, opaque model ID, executor and canonical
model evidence. HTTP evidence captures discovery identity/timestamps, context,
tool support, price caps and the installed or configured execution contract.
It serializes data only, never callbacks or credentials; returned dictionaries
cannot mutate the sealed record. Legacy reservation1/2 wire fields stay exact.
Terminal settlement retains version3 and all selection facts instead of
downgrading to2. Replay comparison includes selection.

Activation is deliberately still closed: ordinary reserve refuses selection,
and carrier mint refuses manifest/selected records until the real manifest
admission fence is connected. These tests do not turn fixture writes into
permission to launch. Next work is that admission/claim/reserve/arm integration,
followed by foreground/background callers and router, then native discovery.

September11 Windows Python3.14 command: `python -m pytest -q
tests/test_provider_invocation_selection.py tests/test_provider_work_authority.py
tests/test_selected_model_authority.py --tb=short --show-capture=no` passes195
cases in12.15s, zero skips. The new file has37cases covering round-trip, immutability,
strict version/type/cost/model checks, inertness and once-only settlement.
Ruff and canonical plugin mirror/import pass. No new Linux, independent exact-head
review, deployment or app-selection proof is claimed. The two known foreground
manifest failures remain open; this is not an MVP-ready claim.

### Receipt storage migration verified — September11

The receipt table now supports an aggregate manifest without a provider anchor.
The atomic migration preserves existing authority JSON, claims, reservations,
indexes and triggers; interrupted copies/drop/rename roll back, concurrent opens
are idempotent, and unfamiliar or malformed schemas refuse without discarding
data. The foreign-key setting is restored on success and failure. No public
issuer emits manifest receipts and a stored inert manifest still cannot claim
launch authority through the legacy path.

Windows Python3.14: `python -m pytest -q tests/test_provider_work_authority.py
--tb=short --show-capture=no` passes101cases in5.69s, zero skips, including9 new
migration cases. Ruff and canonical plugin mirror/import pass. The expanded
run/consumer/background command below remains50passed/2failed in11.70s: both
known native-manifest foreground launch failures, no additional failures. This
is local migration evidence, not Linux, release review or live readiness.

Implemented the reviewed inert receipt representation and strict serialization.
Manifest receipts preserve the existing aggregate identity, subject and budget,
while refusing every provider/member/credential/parent field. Provider-bound
legacy wire documents remain unchanged. No store schema or launch path emits
this version yet; no migration, model selection or production readiness claim.

WindowsPython3.14: `python -m pytest -q tests/test_provider_work_authority.py
--tb=short --show-capture=no` passes92cases in5.22s, zero skips. Includes27 new
manifest/strictness cases (full fixture-backed legacy coverage also remains).
Ruff, strict OpenSpec validation and canonical plugin mirror/import pass.
Expanded real run/consumer/background group remains50passed/2failed in11.63s,
42dependency warnings, zero skips. Both known native-manifest foreground launch
failures remain; do not conceal them with the92record tests. No Linux proof yet.

### Earlier inventory evidence

The scheduler inventory now uses the existing accepted-member validator for
manifest enrollments, preserving the legacy validator for legacy enrollments.
It re-reads enrollment after admission, accepts any still-current member without
depending on the anchor credential, and makes no remote discovery request or
invocation/receipt. This is an internal caller correction, not a new storage or
launch-authority representation and not completion of workflow selection.

WindowsPython3.14 command:
`python -m pytest -q tests/test_run_provider_session.py -k 'background_scheduler or scheduler_inventory' --tb=short --show-capture=no`
passes5cases in1.42s, zero skips: legacy/manifest inventory, pause, credential
rotation and an independent member surviving anchor rotation. Synthetic vault
fixtures only; no live credential or user configuration changed.

`python -m pytest -q tests/test_run_provider_session.py tests/test_assigned_queue_consumer.py tests/test_background_budget_finalization_e2e.py --tb=short --show-capture=no`
reports50passed/2failed/42dependency warnings in14.29s, zero skips. The two failures
remain the explicit/default foreground-manifest release blockers, not hidden or
xfail cases. Ruff and plugin mirror/import pass. No Linux/review/deployment claim.
