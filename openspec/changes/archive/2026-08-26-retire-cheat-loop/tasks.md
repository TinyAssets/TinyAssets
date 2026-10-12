## 0. Target And Review Gates

- [x] 0.1 Inventory the complete shipped cheat-loop surface across bug filing/investigation and filing-effort routing, auto-ship, hidden community-change context, status, configuration, deployment, named workflows, live GitHub labels/open-item routing and auto-merge enrollments, active skills/souls, automatic patch-announcement effects, generated website snapshots, tests, and the Claude plugin mirror; distinguish generic primitives, independent repo integration, and historical records.
- [x] 0.2 Write the target proposal, design, seven capability deltas, and dated source audit for complete product removal rather than a disabled flag, alias, renamed loop, or replacement platform automation.
- [x] 0.3 Obtain exact-head independent architecture/spec/diff review and fresh Claude Opus 5 opposite-provider APPROVE; fold every adaptation into a new exact reviewed SHA.
- [x] 0.4 Record the host's standing 2026-07-26 acceptance only after reviewers agree the complete removal boundary is fully figured out; stop if any unresolved host design choice remains.
- [x] 0.5 Rebase or merge current `origin/main`, rerun provider-context/claim/collision checks, record the `retire-legacy-live-mcp-tools` task 4.1 cutover dependency, and publish the target-only draft PR without runtime, deployment, or production mutation.

## 1. Freeze The Removal Boundary

- [ ] 1.1 Add a `file_bug` contract test proving a valid filing creates no
  trigger receipt, BranchTask, run, Investigation/Patch Packet section, or
  `investigation`/`trigger` response block even when both retired environment
  variables are present; also prove it emits no fast-lane, carrier-review,
  navigator-triage, daemon-pickup, or other automatic dispatcher-facing route;
  delete `classify_filing_effort`, `filing_effort_dispatch_route`, their
  constants/tokens and markdown/frontmatter/response metadata, and prove
  ordinary filing fields plus per-kind duplicate detection remain.
- [ ] 1.2 Add absence tests for the `bug_investigation` request class, the
  `validate_ship_packet` and `open_auto_ship_pr` actions, and
  `get_status.auto_ship_health`, auto-ship coding-rubric aliases/config, and
  community-loop merge-readiness branding, while preserving generic Goal
  canonical, BranchTask, graph execution, generic KEEP/APPROVE evaluation,
  status, and effect behavior.
- [ ] 1.3 Add source/package/config scan tests that fail on cheat-loop runtime
  modules, imports, environment keys, extension registrations,
  `community-loop` named executable/build/website artifacts, active
  specs/milestones/exec plans/wiki guidance, public prompt/control-station
  promises, the `AUTO_FIX_DISABLED` repository variable, current "auto-fix
  loop" runtime/plugin/test/website wording, active `.agents`/`.claude` skill
  routes, loop souls, automatic patch-announcement workflows, stale site
  snapshots, or stale plugin copies.

## 2. Remove Privileged Bug-Investigation Automation

- [ ] 2.1 Remove the `file_bug` trigger-receipt/enqueue/Investigation response
  block and first deploy filing-only/no-enqueue behavior so no new retired
  receipt is created. Retain the dedicated receipt reader, store
  implementation, and persisted data through task 2.5. Before task 2.5 may
  inventory, prove every old API/worker instance capable of creating a receipt
  is drained or fenced; uncertainty stops the cutover. This stop-writer task is
  complete once the filing-only deployment and writer-drain proof are recorded;
  task 2.5 exclusively owns the later reader/store deletion.
  - Repository evidence (2026-07-28, not operational completion):
    `4c3855545fb1d5de7dd3559db075005c1378104e` removes receipt creation,
    enqueue, Investigation/Patch Packet rendering, and retired response
    metadata from the canonical and packaged `file_bug` paths while preserving
    ordinary filing, effort classification, and the historical receipt
    reader/store. TDD recorded 7 expected RED failures, then 136 passed and
    1 skipped from
    `python -m pytest -q tests/test_api_wiki.py tests/test_bug_investigation_wiring.py tests/test_file_bug_compact_response.py tests/test_wiki_file_bug.py tests/test_wiki_file_bug_dedup.py tests/test_wiki_trigger_receipts.py tests/test_daemon_wiki.py`;
    the two runtime copies have SHA-256
    `5C0EDB681AA13BCF6DD93E8919E38408FF10257A36CF34DC2D3ED5B3B5325005`.
    The checkbox stays open until this filing-only build is deployed and every
    old receipt-writing API/worker/plugin instance is inventoried and
    demonstrably drained or fenced. The production boundary is every process
    capable of writing the controlled production receipt store: the daemon,
    four in-process-code-capable workers, and any stray host/plugin server
    attached to that store. External BYOC plugin/MCPB installations cannot
    access the controlled volume and are not falsely treated as centrally
    drainable production writers.
  - Deploy-fence candidate evidence (2026-07-28, still not operational
    completion): `953f89db50da503012294112b816b5046ef10256`
    adds a transitional, fail-closed production cutover controller. It
    records normal and emergency intent before mutation; binds state to the
    current run; inventories the v1/v2 queue and read-only receipt snapshot;
    fences one daemon, four workers, extra volume consumers, restart policies,
    boot activators, stray attached processes, old images, and rollback; and
    serializes every known host mutation through bounded GitHub and host-local
    locks. The exact candidate passed 201 focused tests, Ruff, py_compile,
    four-workflow YAML parsing, extracted shell syntax checks, diff checks,
    strict OpenSpec validation, and independent workflow, security, and
    simplification APPROVE reviews. Claude Opus 5 also issued an exact-head
    repository-candidate APPROVE at `2dcc1139` with no blocking findings.
    `actionlint` was unavailable locally and remains a CI gate. The controller
    is explicitly transitional and must be deleted under task 2.5a; this
    checkbox stays open until the candidate lands,
    the filing-only image is deployed, the durable live receipt proves every
    old writer/queued write-back/rollback path fenced, and rendered filing
    leaves the receipt snapshot unchanged.
  - First live cutover evidence (2026-07-28, failed closed): production run
    `30407316207` deployed immutable image
    `ghcr.io/jonnyton/tinyassets-daemon@sha256:025187ccf7c459d7f99283cd1366a3a7abf2031105cbae591170f7e42af463e1`
    at revision `0a8b092b06be730af9724d85ed33a643be5f4ea9`;
    preflight, initial exact-five proof, unchanged receipt snapshot, canonical
    canaries, exact-seven surface, and the direct-URL access gate passed.
    Post-canary exact-five proof then failed without a diagnostic observation,
    rollback restored the prior post-stop-writer image, and restart-activator
    restoration observed two watchdog services transiently `activating`.
    Conservative cleanup therefore emergency-fenced all five containers,
    persisted `phase=unsafe_fenced`, and left the canonical MCP surface at
    HTTP 502. Recovery requires a tested, provenance-bound exit from that
    durable state plus bounded exact-fleet and unit-state convergence; task
    2.1 remains open until recovery, a clean immutable rerun, rendered filing,
    and unchanged receipt proof complete.
  - The production fence must also prove no pending/running
    `bug_investigation` queue item can append a late Investigation/Patch Packet,
    and that rollback cannot restore a pre-stop-writer image to traffic.
    Removing `TINYASSETS_BUG_INVESTIGATION_BRANCH_DEF_ID` alone is insufficient:
    older images create a receipt before resolving that handler. The exact
    image/traffic drain is the authority boundary. The now-inert `verbose` and
    `universe_id` compatibility inputs remain accepted in this slice; their
    final public-contract disposition belongs to task 1.1/retirement cleanup.
- [ ] 2.2 After task 2.5's locked migration and final rescan succeed, delete
  `tinyassets/bug_investigation.py` and remove its handler selection, payload
  mapping, dedicated request type, queue creation, formatting, and Patch Packet
  write-back consumers.
- [ ] 2.3 After task 2.5's locked migration and final rescan succeed, remove
  `bug_investigation` direct-execution and completed-run packet
  recovery/write-back special cases from `fantasy_daemon/__main__.py`,
  retaining generic completed-run reuse and branch execution.
- [ ] 2.4 Remove the two investigation environment keys, cloud request-type
  defaults, deploy workflow writes, current runbook instructions, dispatcher
  examples, prompts, and comments without adding no-op aliases. Delete the
  external `AUTO_FIX_DISABLED` GitHub repository variable and record absence.
- [ ] 2.5 Add and run an idempotent pre-worker migration for all v1/v2
  `bug_investigation` rows and trigger receipts: terminally refuse/quarantine
  pending rows; make claimed/running rows non-admissible; use #1803's
  authority-store-first reconciliation and exact task/claim/lease CAS before
  their queue transition; release only reserved-before-launch authority;
  retain readable ambiguous authority as fenced-indeterminate, hold unreadable
  authority without queue mutation, and keep conclusive consumed authority
  plus completed history immutable; record
  counts/ids/digests/prior-final states/retention; and prevent generic
  reinterpretation or replay. Retirement classification precedes ordinary
  recovery, but #1803 reconciliation remains mandatory before terminalization.
  **Dependency:** #1803's runtime authority store/reconciler must land before
  any claimed/running row is terminalized. Until then, an absent or
  unimplemented store is the unreadable-authority case: preserve row/receipt,
  hold non-runnable, and perform no queue CAS or authority release. Block
  runtime replacement unless every legacy worker is quiesced and a locked
  preflight proves there is no retired v1 `running` or v2
  `running`/`cancel_requested` row. Preserve #1803's
  `manual_resolution_required` action for ambiguity beyond the autonomous
  reconciliation window; resolution requires authoritative evidence and never
  retries retired work. Run only after task 2.1's filing-only deployment and
  proof that every old receipt-writing instance is drained/fenced. Acquire a
  cutover barrier over receipt and queue migration; under that barrier take the
  final inventory, migrate, record retention, and rescan to prove no late
  receipt or row escaped. Keep the no-create runtime active throughout. Only
  after the locked rescan succeeds does task 2.5 delete the unused reader/store
  implementation, while tasks 2.2-2.3 delete the executor and module in the
  final runtime deployment. Persisted evidence follows the recorded retention
  policy and is never deleted merely because its runtime consumer was removed.
- [ ] 2.5a After task 2.5's locked migration and final rescan succeed, delete
  `scripts/retire_cheat_loop_deploy_fence.py`, its product-specific deploy,
  restart, host-install, and P0 orchestration, its tests, and every persistent
  task-2.1 fence artifact or host helper. Restore the surviving workflows to
  generic host-mutation safety without retaining a disabled flag, alias,
  compatibility shim, or platform-owned retirement automation.
- [ ] 2.6 Remove `classify_patch_request`, its hard-coded free/paid claimant,
  Claude/Codex writer, opposite-family checker, meaning, and persisted
  `request_classification` policy from `tinyassets/api/market.py`,
  `tinyassets/api/universe.py`, `tinyassets/work_targets.py`, plugin mirrors,
  and tests. Ignore/strip pending legacy classification metadata. Rename
  surviving pickup-only constants/normalizers generically while preserving
  explicit requester pickup incentives, authorized directed-daemon selection,
  `_universe_loop_dispatch`, `TINYASSETS_SOUL_LOOP_DISPATCH`, and user-owned
  soul `loop_branch_def_id` execution under their independent authority.
- [x] 2.7 Before `retire-legacy-live-mcp-tools` task 4.1, remove the
  `community_change_context` wire callers from both the production React/Next
  and Svelte rollback sites and prove no supported site caller remains.
- [ ] 2.8 After `retire-legacy-live-mcp-tools` tasks 4.1/4.4 remove and rebuild
  the exact six hidden MCP registrations, delete the internal
  `community_change_context` action, action-map row, wrapper,
  `_CHANGE_LOOP_PLAN_HEADINGS`, auto-change/auto-fix queue aggregation,
  Codex-writer/Claude-checker rule, plugin behavior, and tests. Preserve
  generic GitHub/PLAN reads for user-authored review-context workflows; add no
  alias.

## 3. Remove Auto-Ship Product Automation

- [ ] 3.1 Delete `tinyassets/auto_ship.py`, `tinyassets/auto_ship_pr.py`,
  `tinyassets/auto_ship_ledger.py`, `tinyassets/api/auto_ship_actions.py`, and
  `auto_ship_ship_classes.yaml`, and their extension/auth registrations;
  remove `AUTO_SHIP_READY`, `APPROVE_AUTO_SHIP`, and `auto_shipped` rubric
  aliases plus community-loop merge-readiness branding while preserving only
  independently owned generic KEEP/APPROVE evaluation and GitHub effects.
- [ ] 3.2 Remove the auto-ship health computation and response field from
  `get_status`, plus ledger reset/file knowledge and all auto-ship environment
  configuration from compose, templates, deploy tooling, and current operator
  docs.
- [ ] 3.3 Remove or rewrite tests that pin the validator, action, PR, ledger,
  status, ship-class config, rubric aliases, merge-readiness branding, and
  isolation behavior; keep negative public-action/status tests and unrelated
  generic security/evaluation tests.
- [ ] 3.4 Inspect production data/configuration for obsolete auto-ship ledgers
  and keys, archive or delete them under operator retention policy, and record
  evidence without adding a runtime migration reader.
- [ ] 3.5 Delete the push/deploy-triggered
  `.github/workflows/announce-patch.yml` and its patch-loop announcement script
  when no independent explicit consumer remains. Preserve generic PR-create,
  exact-head merge, and outbound-effect primitives for explicit
  user/maintainer workflows. Preserve `scripts/post_x_update.py` only as an
  explicit generic outbound primitive after removing patch-loop-only wording;
  preserve `tinyassets/effectors/validate_patch.py` only after neutralizing
  retired product comments. Task 3.7 exclusively owns disabling and deleting
  `.github/workflows/auto-enroll-merge.yml`.
  **Current evidence (2026-07-26):** the workflow and announcement script are
  deleted on draft PR #1812, `post_x_update.py` is generic, and the latest 30
  hosted runs all completed with failure and zero active. Completion still
  requires the separately claimed `validate_patch.py` comment neutralization
  plus one final immediately-pre-merge run drain.
- [ ] 3.6 Add a dry-run/apply GitHub-label retirement migrator. Snapshot the 28
  exact retired definitions plus every fully paginated labelled issue/PR (open
  and closed) into a digest-bound receipt. Array inventory must receipt the
  exact GitHub Link-header chain and terminal absence of `rel="next"`; never
  synthesize a server total from the observed count or certify a full terminal
  page as complete. Apply only after task 4.2 and every
  other retired-label producer are disabled/removed and queued/in-progress
  runs are drained; record that quiescence. Strip retired labels from open
  items without closing or rewriting them; publish one idempotent
  repository-wide notice; delete the definitions; and verify no
  workflow/script/site/runtime consumer remains.
  Preserve generic `daemon-request`, `request:*`, `payment:*`,
  `gate-required`, `checker:*`, `writer:*`, `writer-pool:*`, `needs-human`,
  `priority:primitive-*`, `patch_request`, `merge-effector`, and `secure-merge`
  labels. Prove `patch_request` has no active routing consumer and cannot imply
  writer/checker/daemon/merge/effect authority.
- [ ] 3.7 Add a dry-run/apply auto-merge retirement migrator. Snapshot every
  open auto-enrolled PR's number/node/head/state/repositories/draft and full
  `autoMergeRequest` tuple into a digest-bound write-ahead receipt with an
  idempotency apply key. Before snapshot/mutation, disable and verify the live
  workflow and cancel/drain queued/in-progress runs. Attribute only exact
  eligible raw GraphQL actor tuples
  `Bot/github-actions/MDM6Qm90NDE4OTgyODI=` with historical
  Actions/repository evidence at `enabledAt`; current-source uniqueness is
  insufficient, and a later current PR head does not invalidate exact
  enrollment-run/log evidence. Bind any candidate log read/verification
  failure as typed uncertainty and preserve ambiguity; never infer uniqueness
  by dropping an unreadable competing run.
  Use structured explicit-GET REST reads and only the reviewed GraphQL query;
  receipt-bind Link-chain terminal evidence for array endpoints. Offline
  `plan` must reject attributed imports, and offline `verify` must identify
  itself as integrity-only rather than re-verifying GitHub provenance.
  Persist each per-PR intent before its exact tuple pre-read, skip changes,
  disable attributed enrollment, persist post-read outcome, and reconcile
  already-disabled planned tuples on restart. Preserve user/maintainer
  enrollments; hold ambiguity for host review. Finish with a full open-PR
  rescan and require a complete receipt, disabled/drained workflow, and zero
  attributed or ambiguous open enrollments before deleting the workflow file.
  **Repository increment (2026-07-28):** the current-main implementation
  exposes only read-only `inventory`, offline `plan`, and offline `verify`; it
  includes no live mutator. Sixty-one combined current-main focused tests pin the closed receipt
  envelope, canonical request/page-size binding, bounded Link-chain terminal
  oracle, stored repository/page/query scope, exact bot/PR eligibility tuple, ambiguity preservation,
  inventory-only label posture, terminal receipt digest, unsafe/encrypted log
  rejection, and exact planned before/after tuples. Tasks 3.6/3.7 remain open
  because no workflow, label, PR auto-merge state, or other GitHub state was
  changed. Pre-#1835 exact-current Claude Opus 5 review approved the initial terminal-oracle follow-up after
  independently mutation-testing every request/page-size and archive guard.
  Exact #1836-based semantic head `c95876fe` then received Claude Opus 5
  `APPROVE`: every named mutation died, 45 concrete forgery probes were
  rejected, and no correctness or authority defect remained.

## 4. Move Generic Observation To Uptime And Alarms

- [ ] 4.1 Replace `scripts/community_loop_watch.py` with the generic
  `scripts/platform_uptime_watch.py`, preserving only public MCP, P0 incident,
  Tier-3 clone, production/website deploy, and revert-rate reads plus bounded
  stdout/artifact output; the observer itself never writes the alarm sink.
- [ ] 4.2 Replace `.github/workflows/community-loop-watch.yml` and community
  labels/artifacts/tests with generic uptime/alarm names; remove all
  workflow-dispatch self-heal/manual inputs; give the observer job only
  `contents:read`/`actions:read`/metadata and no actions/content/issues/PR
  write authority; isolate any `issues:write` in the independent alarm-sink
  consumer with no dispatch/repair authority.
  **Current evidence (2026-07-26):** the workflow still carries the retired
  `self_heal_followup` input, `community-loop-red` label, and `actions:write`
  permission. The focused suite has one red test that still expects
  `createTinyAssetsDispatch`, while a separate green test asserts
  `permissions.get("actions") == "write"`; both pin the retired authority.
  The exact workflow/test slice is a pending STATUS lane until the active
  broad `tests/` claims release.
- [ ] 4.3 Verify the successor is read-only with respect to workflows, user
  tasks, queues, issues, wiki/repository content, and repairs; separately test
  that the independently owned incident sink consumes bounded evidence without
  actions-write or task/repair authority.
- [ ] 4.4 After every generic guarantee has moved to its surviving owner,
  verify the nine removal headings exactly match the nine remaining canonical
  `community-patch-loop` requirements and record their digest-bound removal
  manifest. Do not create an empty canonical spec or delete the directory in a
  separate intermediate state; task 6.7 owns the atomic canonical foldback and
  physical deletion.

## 5. Remove Public Product Presentation

- [x] 5.1 Remove the retained Svelte rollback site's patch-loop application,
  `community-loop-status.json`, community workflow/label/issue fallbacks, and
  fine-print branding; remove `WebSite/site/src/lib/components/ChatDemo.svelte`
  privileged file-to-daemon-to-gates-to-live narrative and the canonical
  `community_change_context` caller; make `/patch-loop` a static soft landing
  to generic patterns/commons and keep `/loop` only as provenance-labeled
  user-workflow activity.
- [ ] 5.2 Update the currently deployed React/Next site, its homepage, fine
  print, tests, `community_change_context` caller, and build artifacts; prove
  absence of every retired website reference. Do not delete the production
  tree as a "legacy mirror."
  **Current evidence (2026-07-26):** React and Svelte production builds pass;
  dev-server Chrome renders of 11 affected routes, live refreshes, and narrow
  active-source identifier scans pass. However, `/goals` in both built sites
  still renders a retired patch-loop narrative and `patch-loop` tag, while
  `/graph` renders the retired goal title as a clickable row. Shared client
  chunks loaded by `/fine-print`, `/goals`, `/graph`, `/host`, and React's
  `/goal` or Svelte's `/build` also ship the retired name/summary; the React
  query-param goal detail can render the full narrative. Production-exact
  static previews rendered every generated React `index.html` route (25) and
  every generated Svelte HTML route (25) with zero browser warnings/errors.
  The website-focused tests pass; the alarm-sink failure belongs to task 4.2.
  A follow-up 2026-07-26 slice removed the two privileged TinyAssets goals
  from both checked-in MCP snapshot mirrors and removed unsourced repository
  graph carry-forward. React and Svelte production `/goals` and `/graph`
  previews then rendered four retained generic goals, neither retired goal,
  and zero browser warnings/errors. **Superseded 2026-07-27:** later
  publication-provenance review retained only one independently proven-public
  Goal, as recorded under task 5.3. This task remains open on task 5.3's source
  retirement and final query-state proof.
- [ ] 5.3 Remove the loop core-team manifest and its six shipped loop-role
  souls from `docs/souls`; regenerate canonical and legacy MCP/repository
  snapshots so no retired branch, area, role, or automatic filing promise
  remains.
  **Current coordination (2026-07-27):** PR #1818 owns the exact seven
  prompt-asset deletions and is being retargeted to `main` after #1812 and
  #1815 landed. The task remains open on truthful live MCP snapshot
  regeneration and cleanup of synthetic retired-role fixture names after broad
  `tests/` claims release.
  **Checked-in snapshot cleanup proved 2026-07-26:** both MCP mirrors now omit
  goals `4ff5862cc26d` and `f10caea2e437`, their tags, retired promises, and
  inconsistent stats. The repository generator no longer carries forward
  unsourced areas/workflow branches/routes/edges or local/provider branch
  state; it rebuilds from an explicit schema, fails closed without
  `origin/main`, and its regenerated snapshot contains only that canonical
  reference. Node retirement tests pass 5/5, the retained Svelte deployment
  runs them after any optional refresh and warns that the legacy refresh must
  not be used, both production builds pass, and rendered `/goals` + `/graph`
  proof is clean in both sites.
  **Host-local zero-active proved 2026-07-26:** see
  `2026-07-26-retire-cheat-loop.md (archived in git)` §Host-local registered-role
  authority proof. Production and third-party hosts remain unproved.
  **Public-read boundary review 2026-07-27 — ADAPT, not final approval:**
  independent Codex and Claude Opus 5 exact-head reviews of draft PR #1812 at
  `9ea3c9eef9603496a62be64de7b5085312687a70` both returned ADAPT. Codex proved
  with a temporary database that anonymous `read_graph target=goals|goal`
  disclosed another actor's private Goal and exact description; it also proved
  that coercive `Number(...)` validation accepted `null`, string, and boolean
  completeness metadata, and identified the ambiguous 100-Goal ceiling and
  raw-origin credential footgun. A separate Codex privacy audit confirmed that
  `target=runs|run` is also unsafe because `_run_read_allowed` permits every
  non-`universe:` actor. A follow-up anonymous proof showed exact `read_page`
  returned full coordination plan and BUG bodies omitted from discovery, so
  public exact-page calls also require an execution-level deny/provenance gate.
  It also found raw `get_status` returning activity/recent-call records, task
  and worker identifiers, names/costs/paths, persona state, and auth health.
  Opus independently found the inverted discovery-scope rejection, acceptance
  of truncated page bodies that could overwrite prior incremental edges, and a
  required snapshot silently exiting zero when the MCP SDK was unavailable; it
  corroborated the Goal-privacy, raw Git-origin, and unpageable completeness
  concerns.
  The durable boundary is therefore: anonymous browser/snapshot callers MUST
  NOT invoke raw `get_status`, `goal`, `goals`, `run`, or `runs` until the
  server enforces public visibility and bounded completeness. Among
  `read_graph` projections,
  `target=graphs` discovery is the only graph read currently proven to enforce
  visibility; it is not complete-inventory proof when an unpageable cap is hit.
  Checked-in Goal data may remain only as an explicitly labelled snapshot, and
  exact page bodies must be bound to the validated inventory from the same
  refresh. Required regeneration must run without caller credentials, require
  full-scope inventory before replacing the full snapshot, and fail before
  writing on malformed/ambiguous metadata, SDK absence, or truncated bodies.
  This review does not complete task 5.3.
  A live anonymous probe returned 78/78 **discovery-scope** pages with no
  truncation and an explicit `scope_note` that coordination pages were omitted.
  That is an acceptable bounded discovery projection when the omission remains
  visible; it is not a complete wiki inventory and cannot replace the existing
  full-scope snapshot. Public `read_page` has no cursor beyond its 100-result
  ceiling, so a response that exactly fills that cap is ambiguous and MUST fail
  closed. Snapshot regeneration remains blocked until every retained
  collection has a server-enforced public projection with truthful
  completeness semantics. The first live fail-closed proof left the
  pre-review mirrors byte-identical at SHA-256
  `597CD61E1A7A15576376F2DAB87698AA5ED19132346FEDACE3CB4BD522538740`.
  Independent review then found that the historical generator defaulted a
  missing Goal visibility to `public`; that normalized field was not
  publication proof. Three Goals without independent explicit-public
  provenance were removed manually from both mirrors. The sole retained Goal,
  `dd187997039b`, is independently declared public by
  `branches/echoes-reddit-source-intake.yaml` and
  `README.md (archived in git)`; both provenance paths are
  embedded in and tested against the snapshot. Exact review then found
  orphaned `goal:<id>` tag records for removed Goals plus two retired
  Community Change Loop draft records. Those residues were removed; every
  remaining Goal identity now resolves to `dd187997039b`, draft stats match
  the retained rows, and the matching curated mirrors have SHA-256
  `2CECC3CB4F6AA5E9349CFD91D9F458E511342F6CD1518D14ABD1C7DE9C067562`.
  **Fail-closed website implementation evidence 2026-07-27:** both public
  browser clients now keep their generic MCP caller module-private and allow
  only bounded, visibility-filtered `read_graph target=graphs` plus the
  discovery-scoped page inventory. Every page-count surface renders the
  server's validated omission note; arbitrary exact-page, raw `get_status`,
  and `goal|goals|run|runs` reads are absent. Exact snapshot page bodies are
  bound to immutable paths from the validated same-refresh inventory, a full
  replacement requires explicit untruncated `scope=all`, and exactly filling
  an unpageable 100-result cap fails closed. Snapshot startup rejects bearer
  credentials, URL userinfo, credential-like query/hash values, and bearer
  values before connect/logging; every browser initialization notification
  omits ambient credentials. The public Playground validates and reduces each
  tool response before rendering parsed, raw, or wire views, strips response
  and session headers, and withholds bodies on validation/request failure.
  The snapshot requires the MCP SDK, and the repository generator always
  publishes the canonical public GitHub URL rather than a local Git origin.
  Exact-head Codex review of implementation commit `afb77f12` returned ADAPT
  on Playground/credential gaps, residual whole-brain/live-Goal copy,
  conflated Graph provenance, and a run-execution claim inferred from universe
  timestamps. Cumulative-head `4b80da18` then received ADAPT from full-diff,
  security, public-truth, and Claude Opus 5 reviewers: encoded fragment
  separators bypassed credential detection; configurable browser endpoints,
  collection counts/visibility, and exact-body response proofs needed stricter
  validation; removed Goal/draft metadata remained; and global/alias/ARIA
  copy still totalized bounded discovery or inferred current Goal/run/release
  evidence. Commit `e4126458` attempted those repairs, but fresh exact-head
  general, security, and public-truth review returned ADAPT: nested/deep query
  credentials, backslash-relative cross-origin URLs, continuation metadata and
  101-row page inventories still passed; Commons/VitalSigns retained totalizing
  map/run copy; React `/loop` passed a limit the server ignores; and the old
  snapshot still published unproven coordination
  wiki, draft, universe, edge, and tag metadata. The next follow-up repairs
  those exact cases. Public collections
  now require exact counts and explicit discoverable visibility; body path/proof/hash
  must match before extraction; browser endpoints and recursively decoded URL
  credentials fail closed; snapshot residues are gone; and public copy labels
  bounded discovery, checked-in evidence, or unavailability on every tested
  surface. Both curated mirrors now retain only the independently published
  Goal and remove all unproven wiki, draft, universe, edge, and dependent tag
  rows; their SHA-256 is
  `29EB970F36C52A4E624932C8AEF78E1D24B5F5220BF08A8AF6EADE82E8952777`.
  Exact-head general and security review of `773cd24f` then returned ADAPT
  while public-truth review approved: nested value/fragment credential
  assignments, plaintext snapshot transport, `scope=all` without independent
  audience proof, raw error detail, Host failure-as-empty rendering, missing
  visibility defaulting, and one Svelte dated-Goal overclaim remained. The
  current repair recursively scans nested values and both separator families,
  requires HTTPS, disables full wiki regeneration without a separate
  publication manifest, emits generic errors, requires totals to equal the
  exact count, distinguishes unavailable Host state, requires explicit
  discoverable visibility, and restores Start parity.
  Exact-head review of `a4b58cc6` then returned ADAPT on nested-URL userinfo,
  two raw SDK/unhandled CI-log paths, contradictory collection totals, and
  discovery labelled as active/running on Playground and Host. The current
  repair rejects nested URL userinfo, keeps every snapshot log generic,
  requires total/count equality, and labels those surfaces as public
  shared-engine discovery.
  Exact-head general and security review of `3310d0b4` returned ADAPT while
  public-truth review approved: the nested-userinfo prefilter missed leading
  whitespace/control characters and WHATWG-equivalent backslash authority
  spellings. The current repair removes spelling-based prefiltering and parses
  every nested value with the same WHATWG URL semantics as the consumer.
  Exact-head review of `40c9f895` returned ADAPT on two remaining boundaries:
  same-scheme base parsing hid zero/single-separator absolute userinfo, parse
  errors treated malformed credential authorities as safe, and rollback docs
  still advertised the deliberately disabled full-snapshot refresh. The
  current repair parses absolute candidates first, rejects apparent
  credential authorities when parsing fails, and directs rollback operators
  to the checked-in vetted snapshot.
  A delayed Claude Opus review of `3310d0b4` also found three coordination and
  truth gaps: this branch had dropped three live STATUS claims, discovery with
  no reported omissions was treated as unavailable, and Build/error copy
  totalized discovery or echoed response-controlled scope. The current repair
  restores the claims, removes one landed STATUS row and folds one concern to
  stay at 60 lines, accepts successful empty discovery with bounded trusted
  omission labels, makes boundary errors generic, and bounds Build copy.
  Exact-head security and public-truth review of `a2a387dd` returned ADAPT:
  malformed authority checks still missed arbitrary schemes and
  WHATWG-removable controls inside a URL, while a present wrong-typed
  `scope_note` was treated as missing. The current repair normalizes those
  controls, uses a scheme-generic apparent-authority check, and rejects
  malformed omission metadata.
  Exact-head general review of `1d9644ae` returned ADAPT because
  parser-successful opaque arbitrary-scheme spellings returned before the
  apparent-credential check. The current repair evaluates the normalized
  scheme-generic check first for zero, single, and multiple separators.
  Exact-head security review of `1d9644ae` also proved that a credential-name
  blacklist cannot establish anonymity: standard OAuth, OIDC, SAML, cloud,
  and ticket parameter names remained open-ended. The current repair removes
  the blacklist and requires a bare public MCP endpoint with no query or
  fragment data.
  Exact source head `60e29c80f8491792efb9d68c54dcf346284cefa0`
  subsequently received APPROVE from independent general, security, and
  public-truth Codex reviews plus Claude Opus 5 opposite-provider review.
  Their durable exact-SHA evidence is recorded in
  `docs/reviews/2026-07-27-retire-cheat-loop-60e29c80-reviews.md`.
  Opus independently reran every cited gate and directly probed 43 endpoint
  inputs. Its two non-defect accuracy notes preserve the intended provider
  boundary: empty `?` or `#` delimiters carry no query/fragment data, and an
  operator-selected bare HTTPS provider remains allowed rather than pinning
  TinyAssets as the compute or API host.
  The full Node suite passes 81/81 on the repaired working head. Both
  production builds pass; Svelte check
  reports 0 errors and 3 unrelated existing warnings; Svelte production
  dependency audit reports zero vulnerabilities; strict validation passes all
  58 OpenSpec items; and `git diff --check` passes. A fresh live anonymous
  required/full refresh failed closed on `scope=discovery` with exit 1 and
  left the then-current curated mirrors byte-identical at
  `2CECC3CB4F6AA5E9349CFD91D9F458E511342F6CD1518D14ABD1C7DE9C067562`.
  That live proof is historical and does not authorize regenerating the new
  fail-closed mirrors. This is reviewed implementation evidence, not final
  live acceptance: server-side privacy enforcement, source-data retirement,
  rendered live proof, and post-fix clean-use evidence remain.
  The probe also proved two remaining source-data blockers: public page
  `pages/concepts/community-patch-loop-as-project-steward.md` still presents
  the retired loop as future platform guidance, and public universe
  `patch-loop-live` still exists. Do not regenerate the checked-in snapshots
  until those live records receive a reviewed retention/supersession/removal
  disposition; hiding them in the generator is not source retirement. Rendered
  browser proof is still missing because no browser backend was available in
  this session.
- [x] 5.4 Build/test the production React/Next site and retained Svelte rollback
  site; correct `deploy-site-react.yml` header, active runbook, and website
  skill prose that reverse their deployment ownership; prove platform uptime
  evidence is separately labeled and never presented as privileged task-loop
  activity.

## 6. Rebuild Packaging And Verify

- [ ] 6.1 Rebuild the Claude plugin and prove its runtime contains none of the
  removed modules, actions, imports, environment names, status fields, request
  special cases, rubric aliases, prompt/control-station promises, or
  community-loop executable names.
- [x] 6.2 Retire `.agents/skills/loop-uptime-maintenance` and its active catalog
  routes, preserve incident records only as clearly historical non-skill
  evidence if needed, rewrite website-editing guidance around generic
  provenance-labelled workflow activity, separate uptime evidence, and current
  React-production/Svelte-rollback truth, run the skill sync, prove
  `.agents`/`.claude` contain no privileged loop instruction, and remove any
  stale ignored `.codex/skills` copy from an active host so fresh catalog
  discovery cannot resurrect the retired route.
- [ ] 6.3 Run focused wiki, dispatcher/executor/migration, extension/auth,
  filing-route, community-context, auto-merge-state, status, uptime,
  deploy-workflow, reset, and plugin tests plus ruff, plugin probe/build,
  `git diff --check`, and strict validation for this change and all OpenSpec.
- [ ] 6.4 Run repository scans that distinguish clearly marked historical docs
  from shipped runtime, active configuration, current operator guidance,
  active specs/milestones/exec plans/wiki pages, website source/static/build,
  executable tests, workflows, and package output; all shipped/current scopes
  must be clean. Query live GitHub labels and open items to prove the 28 retired
  definitions and their routing/status consumption are absent while generic
  labels remain; prove no workflow-owned open auto-merge enrollment or
  unresolved attribution is hidden by workflow deletion.
- [ ] 6.5 Perform the final rendered-chatbot `ui-test` through
  `https://tinyassets.io/mcp`: file a page and inspect status, proving filing has
  no hidden automation/routing claim and status has no cheat-specific projection.
- [ ] 6.6 Freshness-stamp production evidence for generic uptime/deploy
  observation and post-fix user filing; if no post-fix real-user use exists,
  leave the required short `STATUS.md` watch item rather than claiming clean use.
- [ ] 6.7 In one canonical foldback commit, explicitly apply the coordination,
  daemon, graph, wiki, uptime, and website deltas to their canonical specs;
  require final canonical `wiki-commons`
  Purpose prose to no longer promise trigger receipts, `uptime-and-alarms`
  Purpose prose to no longer name the neighboring patch loop, and the
  `public-website-surface`
  historical-run scenario to no longer require patch-loop
  feed/community-watch fallback. After task 4.4's exact-heading manifest is
  satisfied and the stable
  completed-run reuse guarantee is owned by `daemon-runtime-and-dispatch`,
  physically delete the canonical `community-patch-loop` directory in that same
  commit. Do not invoke archive until tasks 0.1 through 6.6, all receipt-backed
  label/auto-merge/data migrations, #1803 authority gates, rendered/public
  acceptance, and every audit Release Gate are complete, and task inspection
  proves exactly 6.7 remains unchecked. Before archive, strictly validate and
  use the installed OpenSpec engine's exported selective sequence:
  `findSpecUpdates` MUST discover exactly the reviewed six-capability allowlist
  plus exact `community-patch-loop`; isolate the community removal delta for
  the physical-delete audit and never write its zero-requirement rebuild;
  `buildUpdatedSpec` all six survivors; validate every rebuilt spec with
  `Validator.validateSpecContent`; require counts daemon `added=2`,
  coordination `added=2`, graph `added=4`, website `renamed=1, modified=3`,
  uptime `added=1`, and wiki `added=1, removed=1`; only after every build,
  validation, and count check succeeds may `writeUpdatedSpec` write those exact
  six. Resolve/import those engine symbols from the installed module files;
  do not assume the package root re-exports them. Never use `skipValidation`,
  and fail on a missing or extra capability. After the engine writes, manually
  rewrite only the reviewed `wiki-commons` and `uptime-and-alarms` Purpose
  prologues, which the requirements-only engine preserves; verify the website
  historical-run replacement came from the engine delta and do not hand-edit
  it. Prove a scoped working-tree/index diff contains only the six canonical
  files with their engine-generated requirement updates plus those two Purpose
  edits, the physical capability deletion, and this foldback's coordination
  edits, with no unrelated change. Then run
  `openspec archive retire-cheat-loop --yes --skip-specs`, mark 6.7 complete in
  the archived task record, remove the STATUS row, prove no empty or
  resurrected capability exists, strictly validate all OpenSpec again, and
  commit the entire foldback atomically.
