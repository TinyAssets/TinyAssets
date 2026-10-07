## 2026-08-01 — squash-merged drain foldback

- **What surprised me:** rebasing the implementation branch onto its squash
  merge dropped the claim commit automatically but required an explicit skip
  for the first implementation commit before Git recognized the second as
  already upstream.
- **Pattern worth capturing:** verify the merged PR and exact merge SHA first,
  then restack and prove `HEAD == origin/main` before creating a coordination
  diff. A merged implementation branch is evidence, not foldback content.
- **What I would do differently:** inspect the PR merge strategy before the
  first rebase so the expected duplicate-commit conflict is named up front.

## 2026-08-01 — governed custom-agent component compilation

- **What surprised me:** arbitrary agent extensibility does not require a
  platform component taxonomy; deterministic safety comes from exact governed
  descriptor pins plus exhaustive refusal, while unfamiliar descriptive data
  can remain portable and inert.
- **Pattern worth capturing:** compile every public component in sorted order,
  erase all partial output when any diagnostic exists, and keep plan topology
  in a separate adapter-owned stage so component names and object order never
  become accidental orchestration authority.
- **Follow-up:** frozen dataclasses are not a trust boundary by themselves;
  validate public constructors, revalidate at registry admission, and retain a
  detached canonical copy so post-registration caller mutation cannot alter a
  governed descriptor. Compiler refusal must also cover malformed JSON shapes,
  not only well-typed unhappy paths.
- **What I would do differently:** separate component and plan compilation in
  the first test matrix instead of treating “compiler” as one broad subsystem.

## 2026-08-01 — immutable custom-agent runtime manifest

- **What surprised me:** the shared test environment did not contain the
  declared RFC-canonicalization package, while the packaged runtime must remain
  independently importable; the manifest did not need that dependency to get a
  deterministic, bounded content digest.
- **Pattern worth capturing:** freeze arbitrary agent designs at a narrow trust
  boundary: validate and canonicalize compiler output, pin its exact public and
  private sources, then keep activation, authority, conversations, outputs, and
  effects in separately owned ledgers.
- **What I would do differently:** include packaged-runtime importability in the
  initial red test matrix, alongside idempotency, privacy, tamper, and race
  cases, before choosing any canonicalization implementation.
- **Follow-up:** an aggregate source digest proves which binding was named, not
  that compiler output was derived from it. The persistence boundary must also
  compare exhaustive component coverage, private component configuration,
  runtime selections, requested references, and budgets; replay keys belong in
  the same integrity envelope as the content digest.

## 2026-07-31 — local drain observer self-heal

- **What surprised me:** the actual watchdog failure was a one-second Windows
  health-file replacement limit, but safe recovery required preserving explicit
  session stop across tray, scheduler, installer, and abandoned-mutex races.
- **Pattern worth capturing:** a liveness repair needs two independent layers
  and truthful versioned activation proof; restarting a process is not proof
  that the reviewed process is running or that user stop authority survived.
- **What I would do differently:** design the stop/install serialization and
  real Windows fault-injection matrix before the first implementation review.

## 2026-07-31 — exact process ownership before mutation

- **What surprised me:** the availability failure was caused by a conservative
  preliminary scan, but the safe repair still needed two independent freshness
  proofs: an exact container generation and a Linux process generation.
- **Pattern worth capturing:** exclusion snapshots used for security decisions
  need the same immutable identity and generation checks as confirmation
  snapshots; silently dropping an unavailable identity is itself a failed proof.
- **What I would do differently:** include malformed expected/extra identities
  and the exact load boundary in the initial red matrix, not only Docker-output
  and PID-reuse races.

## 2026-07-31 — reconcile stricter sidecar review after concurrent landings

- **What surprised me:** two independently reviewed sidecar PRs landed while a
  stricter review was still finding reboot, name-race, and write-ahead gaps;
  green review of one head did not make the combined current-main state closed.
- **Pattern worth capturing:** after an overlapping concurrent merge, restack
  first, preserve its new behavior and tests, then port only still-failing
  threat cases. Exact-head review must follow the final combined commit.
- **What I would do differently:** open one current-main successor immediately
  after the first overlap instead of continuing to polish a branch that could
  no longer merge cleanly.
- **Follow-up:** set equality is not structural equality for security-sensitive
  mount records; validate type, multiplicity, mapping shape, and explicit
  read-only posture before comparing source/destination values.
- **Follow-up:** a post-mutation global absence proof is too late. Replay must
  prove both the recorded ID absent and its fixed name globally absent before
  removing any remaining member of the generation.

## 2026-07-31 — autonomous recovery-sidecar retry

- **What surprised me:** durable refencing made a partial sidecar start safe,
  but still left the 24/7 recovery path dependent on a human issuing a second
  recovery command after a transient Compose failure.
- **Pattern worth capturing:** a fail-safe state is not autonomous convergence.
  Retry only inside the narrow owned stage, only after durable exact-ID capture
  and cleanup, and bound the attempt count so repeated faults return to the
  existing fence.
- **What I would do differently:** write the single-invocation acceptance test
  alongside the original partial-start fault test, rather than treating a later
  manually initiated retry as sufficient proof of recovery ownership.

- **Follow-up:** ownership validation or a stop failure for a secondary
  non-writer must never preempt the primary writer fence. After a fixed-name
  race, stop only the
  previously proved immutable ID if it still exists, leave the replacement
  untouched, and continue fencing every current volume consumer.
- **Follow-up:** process exit success is not state convergence. Fault tests must
  cover zero-exit incomplete inventories as well as thrown Compose failures.
- **Follow-up:** duplicate best-effort safety mutations are extra failure
  boundaries. Capture ownership once, then funnel every refence through one
  writer-first path that owns error accumulation and final proof.
- **Follow-up:** an ownership guard can remain fail-closed while becoming
  diagnosable. Emit the failed fixed predicate, not the observed private value,
  so one pre-mutation run can select the next safe action without raw host data.

## 2026-07-30 — public-safe production startup evidence

- **What surprised me:** a short-lived Actions artifact is still readable to
  repository readers in a public repository, and even `docker compose ps` can
  expose a secret because the command column contains the tunnel invocation.
- **Pattern worth capturing:** treat diagnostic artifacts as public data unless
  there is a separately proved confidentiality boundary. Reduce raw evidence to
  a fixed allowlist before publication, put hard deadlines before rollback, and
  move fallible uploads after the production safety path.
- **What I would do differently:** threat-model the artifact reader and every
  captured field before writing the first workflow step, then write secret
  fixtures alongside the ordering test.

- **Follow-up:** syntax-shaped filtering was still not an allowlist: a forged
  traceback could put token text in a valid-looking path, function, or line.
  Public diagnostic values must originate from public source inventory, not
  merely match a safe character class; container evidence also needs an exact
  image-and-revision join before raw collection.

- **Follow-up:** a test that recognizes one framing boundary does not lock a
  multi-field protocol. Assert the complete template, the exact boundary count,
  and acceptance by the real consumer so partial delimiter drift cannot pass.

- **Follow-up:** an identity-matched container in `created` state with no logs
  disproves an application-startup hypothesis. Capture Docker's pre-start error
  at the same identity boundary, but reduce it to a fixed class; raw host errors
  are evidence inputs, not safe artifact fields.

- **Follow-up:** `created` plus an empty Docker start error moves the boundary
  one layer outward again. Prefer a read-only classifier over another deploy:
  historical journals can answer the next question without repeating an outage,
  provided raw host text is streamed only into a fixed-output sanitizer.

- **Follow-up:** a downstream byte cap limits parsing, not transport. Bound
  sensitive diagnostics at the source before SSH, and frame retry-aware evidence
  around the terminal attempt rather than unioning markers across a time window.

- **Follow-up:** terminal-attempt framing must recognize every phase that can
  restart independently, not only the earliest create phase. A fixed class also
  must not suppress a separate unknown failure; diagnostic sets are additive.

- **Follow-up:** when a remote pipeline fails behind suppressed stderr, expose
  fixed numeric boundaries first. Then remove implicit-shell ambiguity with a
  static explicit-Bash script and distinct substage exits, never raw diagnostics.

## 2026-07-30 — production-shaped startup evidence

- **What surprised me:** the current image passed a fresh-volume container
  smoke while the same image failed before health convergence on production
  state, and ordinary rollback removed the only useful candidate logs.
- **Pattern worth capturing:** a deploy rollback boundary is also an evidence
  boundary. Capture bounded state and logs before rollback, exclude environment
  values, and make the artifact short-lived so one controlled failure can
  produce an actionable diagnosis.
- **What I would do differently:** add fail-path artifacts when first designing
  the rollback state machine, rather than relying on live workflow logs that
  cannot inspect a container after cleanup.

## 2026-07-30 — dark just-in-time target-attempt issuance

- **What surprised me:** logical-key uniqueness and exact store fences were not
  sufficient by themselves; a replay also had to match the same physical
  universe and executor audience so a guessed non-bearer key could not disclose
  or reuse another issuance context.
- **Pattern worth capturing:** keep fresh canonical resolution inside the same
  opaque transaction as prior-attempt lookup, limit counting, and reservation,
  then persist only a `RESERVED` non-authorizing record. Claim, provider, and
  runtime authority remain later independent gates.
- **What I would do differently:** define the trusted resolution snapshot while
  building the store protocol so task 2.4 would need less interface code after
  the persistence seam landed.

## 2026-07-30 — executable authority inventory closure

- **What surprised me:** a green exact-callsite manifest was still materially
  incomplete because synchronous execution, scheduler callbacks, compiled graph
  streaming, and the independently shipped plugin runtime sat outside its
  lexical boundary.
- **Pattern worth capturing:** authority inventories must count occurrences,
  resolve aliases, scan every shipped runtime, and separately pin indirect
  callback/stream edges plus the exact canonical read seams. Function-name
  markers and a set of call names do not prove closure.
- **What I would do differently:** enumerate all synchronous and indirect
  execution primitives and package copies before writing the first expected
  manifest, then begin with new-file, duplicate-call, and alias mutations.

## 2026-07-30 — background authority boundary rereview

- **What surprised me:** closed nested records were still porous because generic
  top-level IDs used free text, direct dataclass construction could treat a
  string as characters, and shared source/operation enums admitted authority
  combinations that each enum permitted but the spec forbade.
- **Pattern worth capturing:** security-record tests must exercise both the JSON
  parser and direct typed construction, require canonical container shapes, and
  validate each identity, receipt, revision, digest, and timestamp through its
  own allowlisted semantic field class. A finite blacklist of known token
  prefixes cannot prove a serialized reference is non-bearer.
- **What I would do differently:** derive negative tests from every field class
  in the approved table before implementing the model, including invariants
  between source identity, parent lineage, and separately named limits.

## 2026-07-29 — dark background authority contracts

- **What surprised me:** the cloud-drain blocker was not another scheduler; it
  was the absence of a closed, serializable distinction between durable target
  authorization and one exact execution attempt.
- **Pattern worth capturing:** security records should reject unknown fields
  and open-ended enum values at their first dark model boundary. That prevents
  later stores and queue adapters from accidentally treating legacy actor
  strings, wiki filings, or arbitrary lifecycle labels as authority.
- **What I would do differently:** write the as-built/target distinction into
  the architecture decision before the first merge, then derive the model
  fixtures directly from the approved minimum-field table.

## 2026-07-29 - outcome settlement recovery

- **What surprised me:** making sequential receipt replay repair a missing
  outcome exposed a second race: two repairers could each create their own
  outcome even though the external effect remained exactly once.
- **Pattern worth capturing:** exactly-once effects and exactly-once derived
  evidence need the same transactional identity. A preflight read is not a
  deduplication boundary; the handoff-keyed get-or-create must happen under the
  store's write lock, with a two-party replay regression.
- **What I would do differently:** start the crash-recovery test matrix with
  simultaneous replacement workers, not only one sequential replay, and review
  every compatibility write for both authenticated attribution and lifecycle
  read parity.

## 2026-07-29 — OpenSpec drain progress hardening

What surprised me: the controller's reported slice count was not evidence of delivery. Raw PR-string equality, fixed receipt eviction, private blockers, lossy task identities, and ambiguous restart audits could each manufacture progress independently.

Pattern worth capturing: autonomous drains need durable external truth for every terminal state, idempotent canonical receipts for the full bounded run, and fail-open retry whenever legacy evidence cannot identify which work truly succeeded. Independent review is valuable when each finding becomes executable regression coverage.

One thing I would do differently: model restart migration and historical audit ambiguity in the first test matrix, alongside the happy-path result parser, before trusting a persisted controller state shape.

## 2026-07-23 — deploy receipt observability

What surprised me: the deploy workflow already had nearly all facts needed for a release receipt; the gap was mostly that none of them were written into a machine-readable runtime artifact.

Pattern worth capturing: release observability should be deploy-published and status-read-only. That keeps `get_status` safe while still making live drift checkable by chatbots and local tools.

One thing I would do differently: start by adding the deploy workflow structure test before editing YAML, because this repo already has strong workflow-contract tests and they make the intended step ordering explicit.

## 2026-07-23 — MCPB staged catalog parity

What surprised me: the bundle already staged the canonical runtime correctly; the install break was entirely in stale manifest metadata and the absence of a semantic packaging gate.

Pattern worth capturing: package validation should compare the artifact’s declared catalog with middleware-visible behavior from the staged artifact itself. Comparing source constants or relying on a schema validator leaves room for silent product drift.

One thing I would do differently: identify the `--skip-probe` plus `--validate` escape path in the first red test, because a semantic gate is incomplete while the official validator can bypass it.

## 2026-07-23 — PR #1574 research archive

What surprised me: current main already had an independently approved paid-market
consumer of one research slice while the source reports themselves remained only
on a stale draft branch. Approval of a consumer must not be generalized into
approval of its source lane or architectural amendments.

Pattern worth capturing: archive dated research with its review verdict adjacent,
stamp the exact evidence checkpoint, and keep living design/coordination files out
of the archival commit. `ADAPT` is durable evidence and a gate, not a synonym for
approval.

One thing I would do differently: build the source-to-archive hash manifest before
creating the STATUS claim so the mechanical-copy proof and permitted header drift
are explicit from the first increment.

## 2026-07-23 — Provider-attempt receipt specification

What surprised me: the router already carries provider/model/family evidence, so the hard part is not provider discovery; it is preserving call-local attribution across the string bridge, retry waves, and the separate learning call.

Pattern worth capturing: an audit envelope needs two orthogonal terminal fields when fallback exists — how output completed (`outcome`) and why routing stopped (`route_condition`). Combining them makes missing-router fallback and exhausted-chain fallback ambiguous.

One thing I would do differently: model synthetic fallback and missing-router behavior before drafting the first enum list, because that boundary exposed the only internal contradiction found by the consistency pass.

## 2026-07-23 — rollback-safe full-volume restore

What surprised me: extracting with `--strip-components=1` into a `mktemp`
directory preserves file contents but silently replaces the Docker volume
root's access mode with `0700` unless ownership and mode are copied explicitly.

Pattern worth capturing: recovery tooling must fail closed at every discovery
boundary—archive metadata, Docker mountpoint resolution, and consumer
enumeration—before the first live rename.

One thing I would do differently: model the directory metadata and Docker
enumeration failure paths in the first test batch, alongside corrupt archives
and rename rollback.

## 2026-07-23 — Operator-request contract refresh

What surprised me: the four-commit branch collapsed to one substantive OpenSpec commit after rebasing; every conflict was stale coordination, while current main independently preserved the unfixed runtime P1.

Pattern worth capturing: a planning-only PR should carry durable contract artifacts but no live spec-claim row. Runtime truth stays as a dated concern until a separate implementation lane produces evidence.

One thing I would do differently: compare the branch against current canonical capability owners before replaying coordination commits, because that makes it obvious which conflicts should resolve entirely to current main.

## 2026-07-23 — terminal deploy/rollback truth

What surprised me: structural workflow tests and actionlint both passed while an old rollback tail still mutated the image after terminal publication. Independent semantic review then found several cross-layer tuples that were individually valid-looking but jointly contradictory.

Pattern worth capturing: deployment truth needs one final-state invariant across shell outputs, the pure classifier, durable receipts, job exit status, and incident wording. Every dangerous path needs an executable cross-layer regression, not only syntax or token-order assertions.

One thing I would do differently: read the entire rendered step body immediately after the first green structural run, then derive tests from each post-publication mutation and each boundary-crossing tuple before asking for review.

## 2026-07-23 — fresh-host backup configuration

What surprised me: three active guides named three different rclone locations,
while the root-run unit never set the `HOME` override one guide relied on.

Pattern worth capturing: configuration truth spans the consumer, its service
identity, templates, and every runtime-linked runbook; checking only the
primary deploy guide leaves a believable but unusable path.

One thing I would do differently: start the contract test from the runtime's
actual environment and enumerate every linked guide before drafting the
write-set.

## 2026-07-23 — convergent host uptime installation

What surprised me: installing every unit was not sufficient for a fresh host;
the disk-rotation import closure and disabled-timer repair had independent
drift, while backup configuration required its own follow-up boundary.

Pattern worth capturing: a systemd installer should own units, executable
assets, configuration names, activation state, and rollback as one versioned
transaction. Existing content-addressed releases still need byte/mode checks.

One thing I would do differently: make the first fake systemd reject missing
units and inject a mid-stop failure, because permissive doubles hid the two
fresh-host rollback edges found later.

## 2026-07-23 — hardened DR drill evidence

- **What surprised me:** the first live drill failure was not a restore failure
  at all; `curl -sf` erased the provider response before provisioning, and the
  previous PASS ordering could have hidden a later cleanup failure.
- **Pattern worth capturing:** recovery evidence is a state machine, not a
  collection of successful steps. Bind one artifact by digest across every
  boundary, encode path metadata crossing workflow protocols, and publish PASS
  only after cleanup reaches its terminal success state.
- **What I would do differently:** model provider API failures and resource
  deletion as explicit tested states in the first proposal, including request
  timeouts and adversarial output fields, instead of adding them after the
  happy path is sketched.

## 2026-07-23 — provider-inventory DR selection

- **What surprised me:** once API diagnostics became truthful, the next outage
  was provider inventory drift rather than restore logic; a retired base-image
  slug stopped the drill before it could test recovery.
- **Pattern worth capturing:** provider inventory should be resolved from a
  bounded, schema-validated catalog before any mutation, with pagination and
  the resolved identity retained in terminal evidence.
- **What I would do differently:** include pagination, exact provider field
  semantics, permission prerequisites, and provenance in the first selector
  design instead of treating a large first page as the whole catalog.

## 2026-07-23 — bootstrap checkout ownership

- **What surprised me:** the obvious exact-path `safe.directory` fix still
  granted root trust to a service-user-writable repository; independent review
  also exposed the separate interrupted-clone rerun path.
- **Pattern worth capturing:** privilege-sensitive convergence must model every
  ownership state, including partial prior runs. Run repository tools as the
  current owner instead of weakening their trust guard, then validate immutable
  identity before a privileged installer consumes it.
- **What I would do differently:** enumerate fresh, completed-repeat, and
  interrupted-repeat ownership states before drafting the first design, and
  treat a security guard failure as a boundary signal rather than an obstacle
  to bypass.

## 2026-07-23 — DR runtime-image pin

- **What surprised me:** a daemon-only Compose start still validates required
  image interpolation across the complete compose model, while the fresh-host
  template correctly leaves that image empty.
- **Pattern worth capturing:** recovery inputs need distinct authorities and
  evidence names. The provider base image comes from bounded live inventory;
  the daemon runtime image comes from validated immutable production
  configuration; neither should be conflated or sourced from a mutable tag.
- **What I would do differently:** model Compose interpolation and the
  quoted/unquoted environment grammar in the first DR test matrix, and design
  the minimal nonsecret configuration transfer before the first live run.

## 2026-07-23 — DR fresh-env startup truth

- **What surprised me:** a successful Compose command and a green GitHub job
  both concealed a red recovery outcome—the container restart-looped because
  interpolation did not populate its env file, and the probe step emitted red
  outputs before ending on a successful shell command.
- **Pattern worth capturing:** recovery workflows need two truth checks:
  resource/service evidence must reach the process boundary, and structured
  red evidence must be followed by an explicit terminal failure. Cleanup tools
  should verify resource identity, not trust a numeric ID alone.
- **What I would do differently:** test failed-step `if: always()` semantics
  and container-visible environment separately from Compose model validation
  before the first live drill.

## 2026-07-23 — shared in-node enqueue caps

- **What surprised me:** boundary tests aimed at lock correctness also exposed
  authority bugs in run scope, universe identity, private visibility, and
  corrupt-history handling.
- **Pattern worth capturing:** concurrency proof must test the authority used
  to choose the lock, budget, and target—not only the atomicity of the lock.
- **What I would do differently:** map cap scope, physical storage identity,
  and request authority before the first implementation pass, then make those
  boundaries the first independent security-review checklist.

## 2026-07-24 — production backup preservation and retention

- **What surprised me:** the first exact backup was green while GitHub
  retention remained one release above policy. Two sequential uploads could
  each observe a different stale list, and a finite retry count still was not
  a time bound until every network and delete operation shared one deadline.
- **Pattern worth capturing:** operational acceptance must inspect the
  resulting external state, not only terminal workflow markers. Eventual-
  consistency reconciliation needs current-object visibility, typed
  already-absent handling, and a wall-clock budget covering every nested
  request and sleep.
- **What I would do differently:** make release-count verification and
  invocation-warning rejection part of the first executable production proof,
  then run independent review against the full two-upload sequence before the
  first live retention exercise.

## 2026-07-24 — transactional operator-request storage

- **What surprised me:** the legacy request tests were mostly failing because
  their fixtures lacked a declared Loop, while the actual operator bug was a
  swallowed split-write failure that persisted a Request without its task.
- **Pattern worth capturing:** a durable admission is one aggregate—Request,
  admission receipt, epoch-specific task, and event—and its idempotency,
  authority recheck, random-ID allocation, and fault boundary belong inside
  the same transaction.
- **What I would do differently:** write the quarantine-replay and lock-error
  tests in the first red batch. Both are small but distinguish genuinely
  transactional behavior from a store that only passes happy-path concurrency.

## 2026-07-24 — exact-universe operator-priority grants

- **What surprised me:** the legacy capability table's three-column primary
  key made revocation history impossible even though most existing read paths
  could remain unchanged once activity became a timestamped query.
- **Pattern worth capturing:** elevation authority should be immutable
  generations plus an exact-scope transactional reread at a server-controlled
  transaction timestamp. Wildcard or host identity can remain useful for
  ordinary administration without becoming an accidental substitute for the
  elevated grant, and caller-supplied audit time must never become an
  authorization-time override.
- **What I would do differently:** include mismatched-expiry replay and
  revoked-issuer backdating tests in the first red batch; both catch ways an
  apparently idempotent admin API can silently widen authority.

## 2026-07-24 — request-local operator-priority authority

- **What surprised me:** replacing the environment check exposed a second
  identity boundary: production WorkOS `sub` values are the founder key, while
  the legacy account helper generated `user::...` aliases. A verdict could be
  logically correct yet never find a real user's grant.
- **Pattern worth capturing:** elevation must bind the opaque authenticated
  subject end to end. Trusted provisioning may create its referential account
  row, but it must not grant ordinary action scope or ACL authority; those
  remain separate conjuncts in the request-local verdict.
- **What I would do differently:** use a production-shaped opaque subject in
  the first authority test, then exercise host/environment labels only as
  adversarial non-authority inputs.

## 2026-07-24 - operator-priority replay reauthorization

- **What surprised me:** replay and new admission deliberately diverge after
  priority revocation. Both must re-check current ordinary access, but only a
  new effect may require the still-active elevation grant.
- **Pattern worth capturing:** perform non-enumerating authorization before
  idempotency lookup, then use a named replay verdict that skips only the
  prospective elevation leg. This preserves committed truth without turning
  historical possession of a key into ongoing universe access.
- **What I would do differently:** start with a store spy that fails on lookup;
  it makes the security-sensitive operation order executable rather than an
  inference from a returned error.

## 2026-07-24 - canonical public request admission

- **What surprised me:** an ACL check immediately before replay lookup was
  still too weak when it used a different database connection. The meaningful
  boundary is the authorization read and idempotency read in one transaction.
- **Pattern worth capturing:** idempotency is an authority-sensitive read
  before it is a duplicate-write optimization. Validate exact UTF-8 input,
  derive the scope server-side, HMAC the public key, bind the exact body with
  RFC 8785, and re-check access inside the lookup transaction. Authorized
  replay also precedes mutable new-admission viability such as the current
  Loop declaration; committed history does not disappear with later topology.
- **What I would do differently:** make malformed Unicode, ACL loss between
  verdict and lookup, and lost-response replay part of the first red batch.
  Each catches a boundary that happy-path same-body replay does not exercise.

## 2026-07-24 - epoch-2 queue adapter and pure selection

- **What surprised me:** the existing v1 file queue already rejected the new
  operator trigger tier, so isolation started stronger than expected. The
  missing boundary was the inverse: giving v2 workers a typed path to both
  epochs without ever giving v1 code a transactional-store handle.
- **Pattern worth capturing:** selection, reservation, and execution authority
  are three different operations. Cross-epoch selection stays read-only; each
  epoch keeps its own conditional claimer; and a v2 claim remains inert for
  external execution until the separate signed B2 authority is present.
- **What I would do differently:** include expired-heartbeat revival and
  cancel-requested worker loss in the first lease test matrix. Both expose
  lifecycle wedges that a happy claim/finish test cannot see.

## 2026-07-24 - epoch-2 trusted transaction time

- **What surprised me:** exact descriptor equality did not make descriptor
  freshness trustworthy when the comparison instant still came from the
  claimant. The same backdating hole existed independently in heartbeat and
  terminal transitions.
- **Pattern worth capturing:** authority freshness uses a server clock read
  after the write transaction begins. Request APIs may choose a shorter lease,
  but they cannot provide event time or extend the 90-second maximum.
- **What I would do differently:** make a backdated stale claim, a backdated
  terminal transition, and a genuinely simultaneous two-writer claim the
  first red tests for any leased authority boundary.

## 2026-07-24 - boot-bound worker protocol evidence

- **What surprised me:** moving release reads into a memoized helper was still
  too late: registration-delay and auth-quarantine paths could reach a new
  receipt before first publication. The snapshot must happen at supervisor
  entry, and only a terminal-proof version-2 receipt is positive evidence.
- **Pattern worth capturing:** positive protocol evidence exists only when the
  exact worker/runtime/universe tuple is durably recorded and the same
  descriptor appears in that worker's named heartbeat. Metadata publication
  must preserve concurrent runtime control, and loss of runtime identity must
  clear the process's last durable descriptor.
- **What I would do differently:** start with registration-delayed receipt
  replacement, partial legacy receipts, concurrent pause/retire, and runtime-ID
  loss. Those four negative tests expose false-upgrade and stale-authority
  paths that a complete-looking heartbeat fixture misses. Also exercise the
  full retired-A to replacement-B path: it exposed both stale cleanup blocking
  publication and an older registry path that resurrected retired slots. A
  status check before a metadata write is not control-safe; reuse must be one
  atomic “still provisioned” operation. The worker-ID choice belongs in that
  same transaction too: otherwise different workers can steal one unassigned
  slot and identical concurrent starts can create duplicates.

## 2026-07-24 - transactional epoch-2 quarantine

- **What surprised me:** the receipt/disable transaction already existed, but
  pure selection decoded JSON before validating protocol and linkage. A corrupt
  row could therefore poison the read path even though claim SQL rejected its
  protocol.
- **Pattern worth capturing:** selectors classify raw rows without mutation;
  maintenance alone owns receipt+disable; claim repeats the integrity boundary.
  When maintenance rolls back, the selector and claimer still keep the source
  inert and return a bounded red health result.
- **What I would do differently:** begin with malformed JSON, broken aggregate
  links, both precommit fault points, and concurrent maintenance. Those tests
  distinguish real quarantine isolation from merely having a quarantine table.
  Treat every imported SQLite storage class as adversarial too: ordinary
  `TEXT PRIMARY KEY` columns can contain NULL, TEXT-affinity columns can contain
  BLOBs, and REAL values can be non-finite when constraints were bypassed.
  Address corrupt sources by rowid, totalize their digest representation, and
  bound maintenance by rows scanned—not receipts written—while persisting a
  rotating cursor. Finally, validate the public result and lifecycle
  semantically at both selection and claim; parseable evidence is not proof.
  A rotating cursor also needs a per-cycle high-water mark: last-rowid alone
  can starve an older row forever under sustained inserts. Bound the SQL batch
  by physical rows (including terminal/disabled rows, which are skipped after
  fetch) so the writer-lock budget is real rather than a post-filter illusion.
  Physical rowids are locators only and never digest material. Receipt
  sanitization must validate identifier formats and enum values before
  preserving strings, and authority integrity must bind canonical
  hash/version/policy formats, receipt generation, actor, tenant metadata, and
  Request lifecycle—not merely JSON shape.
  Physical cursor progression and semantic classification are separate:
  terminal tombstones still consume the bounded cursor budget but must not be
  reclassified after legitimate compaction; disabled rows are skipped only
  when a quarantine receipt already explains them, otherwise valid rows remain
  policy-parked and invalid rows gain an audit receipt. Reuse the platform's
  path-safe custom-universe contract for eligibility while applying a stricter
  display-safe slug rule to receipts. Closed reason enums, allowed directed
  scopes, canonical soul hashes, and a non-overridable scan ceiling belong at
  the storage boundary.
  Stored evidence is only meaningful when it binds the executable payload:
  recompute the RFC 8785 body digest from the canonical Request plus task
  inputs, and bind directed scope/hash to the actual daemon owner/delegation
  metadata and soul. Terminal rows need two paths: full validation before
  compaction, or an exact compacted tombstone contract afterward. A status
  string alone is never permission to skip integrity.

## 2026-07-24 - executable identity and pre-compaction integrity

- **What surprised me:** a canonical request-body digest still left the
  resolved `branch_def_id` and unexpected task-input keys outside the checked
  execution envelope. Separately, a correct post-compaction tombstone checker
  could not recover evidence that compaction had already erased.
- **Pattern worth capturing:** bind every downstream executable input to at
  least two authoritative aggregate records, reject surplus keys, and run the
  same full classifier inside the compaction write transaction before private
  evidence is replaced. Terminal timestamps are one state transition and must
  agree across task/admission records before the compaction time can follow
  them.
- **What I would do differently:** enumerate the exact fields consumed by the
  execution handoff and every evidence-destroying maintenance operation during
  the first threat-model pass, then make each a red mutation test before the
  initial review.

## 2026-07-24 - mixed epoch isolation proof

- **What surprised me:** the full mixed valid/forged/missing-receipt/
  unsupported-protocol scenario passed without another runtime change once
  selection and claim shared the same aggregate classifier.
- **Pattern worth capturing:** an isolation proof must advance both valid
  epochs, not merely show that invalid rows are absent from one candidate
  list. Claim valid v2, then prove valid v1 becomes selectable while invalid
  v2 rows remain unclaimable and quarantine remains a separate mutation.
- **What I would do differently:** design the first quarantine integration
  test around the complete mixed queue. It exposes selector, claim, epoch
  fallback, purity, and maintenance behavior in one production-shaped trace.

## 2026-07-24 - dark distributed-execution authority spine

- **What surprised me:** the most consequential review findings were not
  signature failures; they were ordinary-language authority leaks between
  otherwise valid mechanisms—transplantable evidence provenance, mutable
  verified metadata, an unbound multi-blob result digest, and SQLite
  hardening that accidentally weakened POSIX locks.
- **Pattern worth capturing:** prove authority at the consuming sink with
  executable mutations, and stop hardening at the honest threat boundary.
  A fake/test root can reject aliases and own its connection without claiming
  hostile-host custody; production must wait for the OS/custom-VFS boundary
  that can actually uphold that stronger promise.
- **What I would do differently:** write the threat-boundary table and the
  exact designated-result rule before the first storage implementation. That
  would have prevented both the unsafe descriptor experiment and the late
  multi-blob ambiguity.

## 2026-07-24 - canonical MCP route retirement

- **What surprised me:** deleting the runtime and edge routes was the easy
  part; dated submission runbooks and PLAN still looked current enough to send
  an operator back to the retired product after every focused test was green.
- **Pattern worth capturing:** a public-route retirement is one transaction
  across application mounts, edge routing, generated packages, registrations,
  canaries, current guidance, and architectural truth. Preserve dated evidence,
  but fence it from execution and record a live red pre-image before deploy.
- **What I would do differently:** inventory and classify every old-route
  reference as current, historical, generated, or test-only before the first
  code edit, then make the operational-guidance boundary part of the initial
  review brief.

## 2026-07-24 - epoch-2 operational truth

- **What surprised me:** a fresh worker heartbeat was insufficient evidence
  of compatible capacity. Status has to match the complete heartbeat
  descriptor against the durable runtime descriptor or it can promise
  capacity that the claim transaction will correctly reject.
- **Pattern worth capturing:** operational state is an exclusive integrity
  classification layered over lifecycle state. Count physical depth once,
  keep valid pending lifecycle counts, and expose invalid, quarantined, and
  policy-parked rows separately with bounded ID/digest diagnostics. Corrupt
  rows outside the known status enum or authoritative universe scope must
  reduce completeness explicitly; a filtered-out row is not a clean queue.
  Collapse corrupt dimensions in SQL, and include authorization class in any
  cache key whose response contains privilege-conditioned diagnostics.
- **What I would do differently:** define the read-model schema beside the
  quarantine receipt schema at the start. Stable pre-quarantine digests,
  bounded diagnostics, and durable-descriptor matching would then arrive in
  the first red test rather than during integration hardening.

## 2026-07-24 - epoch-2 wakeup staging

- **What surprised me:** wakeup eligibility is inseparable from executable
  claim readiness. A truthful descriptor cannot be published just because the
  queue adapter exists; the supervised child must be able to select, claim,
  materialize, and lifecycle-manage the same task or the supervisor creates a
  restart loop.
- **Pattern worth capturing:** stage dormant readers with code-owned readiness
  truth, visible status, exact-worker durable identity, and an epoch-filtered
  selector. A live-child restart guard stays read-only and protects only
  current live leases; expired/dead-peer recovery belongs in the lifecycle
  integration that owns failover.
- **What I would do differently:** test repeated restarts, empty canonical
  daemon identity, plugin-isolated imports, real-data-dir leakage, partial
  schemas, and cross-epoch selector identity on the first pass. Those
  adversarial cases would have exposed the unsafe sequencing immediately.

## 2026-07-24 - epoch-2 claim-bound request materialization

- **What surprised me:** a correctly gated materialization write was not
  enough. The durable target could outlive its lease and re-enter selection
  through the producer override, while an SQL `LIMIT` ahead of integrity
  filtering let one corrupt row hide valid work.
- **Pattern worth capturing:** lease-gated artifacts need a fail-closed check
  at the final selection boundary using the complete claim identity. Bounded
  readers cap rows scanned, not rows fetched before validation, and optional
  epoch readers must never mask legacy work when they fail.
- **What I would do differently:** lead with corrupt-first pagination,
  cross-worker selection, lease expiry/reclaim, same-basename root aliasing,
  and optional-reader failure tests before implementing the happy path.

## 2026-07-25 - release reconciliation wake-up and chain recovery

- **What surprised me:** GitHub token-dispatched image builds rarely chained
  into deploys, and Python 3.14 accepted indented `-c` programs that the
  production runner's Python 3.12 rejected. A locally green exact-script test
  was therefore still environment-incomplete.
- **Pattern worth capturing:** executable workflow proofs need the production
  interpreter, the exact YAML-dedented shell body, a stateful scheduler/run
  model, and mutation probes. Retry policy belongs in that state model too:
  permit bounded recovery, then stop before repeating production mutation.
- **What I would do differently:** model the full token-dispatch-to-deploy
  chain and pin the hosted runner interpreter in CI before adding the trigger.
  That would have exposed both the missing deploy chain and parser mismatch in
  the first red test.

## 2026-07-25 - Agent Village security containment

- **What surprised me:** fixing the server's unauthenticated default exposed a
  browser recovery state machine, not just a bearer check. Three independent
  fetch paths, same-document fragment navigation, chat timers, and ordinary
  toasts could each preserve a silent or misleading locked-out state.
- **Pattern worth capturing:** authentication failure belongs at the shared API
  boundary, while recovery needs one dedicated, persistent, reversible UI
  state. Security reviews should rotate credentials under an already-open
  interactive view, not only test fresh page loads.
- **What I would do differently:** establish the product-ordering boundary
  before shaping the local UI. The connector-backed chatbot is the canonical
  first-class experience; an experimental operator view should receive only
  minimum containment until mature platform primitives reveal whether it has a
  durable role at all.

## 2026-07-25 - branch authority capability boundary

- **What surprised me:** the first security draft correctly found cross-surface
  leaks but incorrectly made one graph change own requirements that already
  belonged to run, evaluation, goals, and paid-market capabilities.
- **Pattern worth capturing:** an umbrella audit may discover the whole failure
  chain, but each normative delta should live with the capability whose
  as-built requirement it replaces; sibling changes can remain explicit,
  non-blocking successors.
- **What I would do differently:** inventory both runtime call sites and
  existing OpenSpec requirement ownership before drafting the first delta,
  then separate the core shared helper boundary from dependent surfaces.

## 2026-07-27 - public retirement boundary

- **What surprised me:** canonical MCP handles were not a public-data
  boundary. Visibility-filtered universe discovery was safe, while adjacent
  Goal, run, exact-page, and operator-status reads exposed private or
  operational state.
- **Pattern worth capturing:** public callers need an allowlisted descriptor
  layer, server-enforced visibility, explicit completeness metadata, and
  provenance-bound follow-up reads. Discovery with an omission note is useful
  but must never be relabeled as a complete snapshot.
- **What I would do differently:** threat-model every public projection before
  migrating names, then lead with negative tests for credentials, exact cap
  fills, truncation, SDK absence, and local-origin leakage. That would have
  prevented a naming cleanup from appearing safer than the underlying data.
- **Follow-up:** response validation must cover every rendered representation,
  not only the parsed view; checked-in `visibility=public` also needs an
  independent publication record when a historical generator could have
  defaulted it.
- **Follow-up:** retirement must remove derived identifiers and draft metadata,
  not only primary rows; URL credential checks must inspect bounded recursive
  decodings so encoded separators cannot hide parameters.
- **Follow-up:** a historical anonymous response is not durable publication
  proof. Checked-in wiki, draft, universe, edge, and tag rows fail closed unless
  independent audience-safe provenance authorizes them.
- **Follow-up:** completeness and public audience are orthogonal. `scope=all`
  cannot authorize publication, and failure UI/error channels need the same
  fail-closed review as successful data.
- **Follow-up:** security validation must model parser equivalence, not only
  canonical spelling. Feed nested URL values to WHATWG parsing so whitespace,
  control prefixes, and backslash authority forms cannot bypass userinfo checks.
- **Follow-up:** URL parsing context is itself security-sensitive. Parse
  absolute candidates without a base first, and never turn a malformed
  credential-looking authority into an affirmative safe result.
- **Follow-up:** a scoped response can be valid with nothing omitted. Preserve
  the bounded scope, convert server prose to trusted status labels, and keep
  successful-empty distinct from unavailable without implying completeness.
- **Follow-up:** parser normalization includes removable characters inside a
  URL, and credential-bearing authority syntax is not confined to common web
  schemes. Fail closed generically; never use a finite scheme allowlist.
- **Follow-up:** a successful parse is not affirmative safety when a parser
  treats authority-like text as an opaque path. Apply the conservative
  credential syntax check before accepting parser output.
- **Follow-up:** credential-name blacklists are structurally incomplete. When
  the canonical public endpoint needs no URL parameters, the smallest safe
  contract is a bare HTTPS URL with no query or fragment channel at all.

## 2026-07-27 - hosted preview trust boundary

- **What surprised me:** moving a secret to `workflow_run` was necessary but
  not sufficient. A safe preview also needed exact run/PR/artifact provenance,
  normalization before the environment, a fresh credentialed job, serialized
  per-PR version aliases, a blocked production data path, and a separate
  Cloudflare account because Workers write permission is account-scoped.
- **Pattern worth capturing:** treat CI artifacts as hostile transport, not
  trusted build output. Authenticate identity before download, normalize bytes
  outside the secret boundary, then revalidate the normalized receipt in a
  fresh credentialed workspace.
- **What I would do differently:** inspect the external provider's actual
  permission granularity and GitHub trigger bootstrap semantics before drafting
  the first workflow. That would have ruled out the production-account token,
  singleton deployment, and in-branch-only listener immediately.

## 2026-07-27 - privileged loop skill retirement

- **What surprised me:** deleting the canonical and Claude skill trees was not
  enough to retire the behavior on this host. An ignored Codex mirror and then
  the intentionally stale primary checkout each kept advertising the same
  escape-hatch skill to newly spawned agents.
- **Pattern worth capturing:** retirement proof must follow every discovery
  path, including ignored host-local catalogs. Compare hashes before deleting
  residue, preserve unique evidence in tracked history, and use a genuinely
  fresh agent session to prove the catalog changed.
- **What I would do differently:** inventory all provider discovery roots
  before checking the OpenSpec task. The tracked-tree scan was correct but too
  narrow to prove that the active host had stopped teaching the old behavior.

## 2026-07-27 - privileged loop soul source retirement

- **What surprised me:** the source deletion itself is intentionally much
  smaller than task 5.3. Removing seven platform-owned prompt assets does not
  prove generated snapshots, stored role identities, or third-party hosts are
  clean.
- **Pattern worth capturing:** split source retirement from data retirement,
  and keep the parent task open until every projection and live store has its
  own provenance-aware proof. A deleted default must not become a filter that
  blocks equivalent user-authored designs.
- **What I would do differently:** name the PR “source-only” from its first
  commit so reviewers do not have to infer that live deregistration remains a
  separate gate.

## 2026-07-27 - privileged loop runtime residue audit

- **What surprised me:** the highest-impact runtime stop-writer is blocked by
  broad test ownership, while the old GitHub-state inventory migrator is
  independently restackable because its tests live beside the script. The first
  audit also reversed a cross-change dependency: the dark replacement authority
  store/reconciler must precede legacy-row migration.
- **Pattern worth capturing:** inventory durable platform state before deleting
  its producers; workflow source removal does not cancel queued runs, remove
  labels, or revoke existing auto-merge instructions.
- **What I would do differently:** query live workflow runs and durable
  auto-merge requests before proposing any workflow deletion, then separate the
  read-only inventory PR from the later receipt-backed mutation.

## 2026-07-27 - GitHub-state migrator selective restack

- **What surprised me:** the reviewed migrator lived on an 80-commit branch
  whose three-dot diff touched 111 files; the useful payload itself was only
  three new files and two narrow OpenSpec deltas. A digest-valid receipt could
  still smuggle unnormalized authority fields until exact envelope
  reconstruction was added.
- **Pattern worth capturing:** use final-file hashes plus a current-main
  semantic spec replay for stacked migrations; commit lists are provenance, not
  permission to cherry-pick mixed coordination history.
- **What I would do differently:** record the replacement PR and parked parent
  PR disposition at the first stack split, so a safe restack never has to
  reconstruct whether old drafts should be retargeted, closed, or preserved.

## 2026-07-28 - retired-label receipt peer-schema closure

- **What surprised me:** exact receipt-envelope reconstruction still left
  authority-shaped data admissible inside label definition and association
  records because those peer records were normalized by copying every key.
- **Pattern worth capturing:** a digest-bound receipt is only as closed as its
  deepest record schema; every inventory-only operation should also reject
  action records it cannot yet execute.
- **What I would do differently:** enumerate allowed keys for every nested
  external record during the first receipt-schema review, rather than closing
  the envelope and pagination layers before their item records.
- **Review follow-up:** a handwritten allow-list still needed an executable
  collector-through-verifier contract. Exact keys without exact value types
  also left nested authority-shaped data admissible even though no consumer
  could use it.
- **Final follow-up:** reject an unimplemented non-empty action list before
  parsing its members; validation order is part of a stable fail-closed error
  contract, even when both orders deny mutation.

## 2026-07-28 — Retire the writer before migrating its history

- What surprised me: the smallest safe retirement slice is not deleting every
  related module; it is first making the public filing path incapable of
  creating any more privileged-loop state.
- Pattern worth keeping: preserve historical readers and receipts long enough
  to inventory and reconcile them, while tests prove both that the writer is
  unreachable and that ordinary filing still works.
- What I would do differently: map deployment identities and old-writer fencing
  at the same time as the repository change, so landing the image and proving
  operational retirement form one explicit sequence.

## 2026-07-28 — Stop-writer production fence

- What surprised me: GitHub workflow concurrency was not sufficient once a
  canceled runner could leave a host-side command alive. The actual remote
  mutation needed the same bounded host lock as its fresh state/residue check.
- Pattern worth keeping: destructive cutovers need write-ahead intent before
  every normal and emergency mutation, run-scoped terminal truth, bounded
  recovery locks, and an executable two-run regression so stale success cannot
  authorize or misclassify a later failure.
- What I would do differently: model missing/corrupt state, power loss, runner
  cancellation, and rollback as first-class lifecycle transitions before
  writing the first workflow step. That would have avoided several correction
  rounds and produced the centralized guarded-command seam earlier.

## 2026-07-28 - terminal-page oracle follow-up

- **What surprised me:** the safe restack landed concurrently while its older
  source payload was under review; treating untracked files as the diff hid
  that `main` already had a stronger closed receipt envelope. Opus caught both
  the stale base and a separate full-terminal-page completeness bug.
- **Pattern worth capturing:** absence of a pagination continuation is only a
  terminal oracle when the final page is strictly smaller than the requested
  bound, and both live collection and stored-receipt validation need
  load-bearing tests for that invariant.
- **What I would do differently:** fetch immediately before every
  opposite-provider review and compare against the exact remote head, then
  freeze that base in the brief so a concurrent landing cannot masquerade as a
  local regression.
- **Review follow-up:** a correct ASCII regex was not enough when the claimed
  Unicode regression only exercised an out-of-range value. Mutation-test the
  exact semantic distinction, and scope-check newly stored provenance-shaped
  fields against the receipt-bound repository.

## 2026-07-29 - first overnight OpenSpec drain evaluation

- **What surprised me:** reboot recovery and work preservation succeeded while
  throughput still remained zero; operational resilience can mask a broken
  delivery boundary unless merged PRs, not edited files or passed tests, are
  the throughput measure.
- **Pattern worth capturing:** a durable task blocker and a publication
  infrastructure failure need different terminal states. The former releases
  admission and idles; the latter preserves the exact worktree/admission for a
  fresh bounded delivery retry.
- **What I would do differently:** run a real linked-worktree stage/commit
  probe before the first unattended shift. Unit coverage for `--add-dir`
  missed Codex's protected Git-metadata rule; the real probe exposed that
  `danger-full-access` was required on this already-unsandboxed Windows host.

## 2026-07-29 - current-main drain selection

- **What surprised me:** admission was correctly fresh while the earlier
  selector was stale, so the safety check itself became a bounded retry loop;
  the first real end-to-end probe also exposed UTF-8 output being decoded as
  Windows cp1252 even though every mocked JSON test was green.
- **Pattern worth capturing:** a long-lived controller may pin its executable
  code, but every mutable coordination decision must bind all derived reads
  (rows and stale-history evidence) to one freshly fetched ref and refuse to
  dispatch when that snapshot is unavailable.
- **What I would do differently:** run the exact live subprocess boundary
  immediately after the first red unit test, including non-ASCII STATUS data,
  rather than waiting until the focused suite is complete.

## 2026-07-29 - legacy outcome evidence foldback

- **What surprised me:** the schema migration correctly upgraded historical
  outcome rows, but a new legacy-router write happened after that migration and
  therefore escaped the evolved evidence lifecycle.
- **Pattern worth capturing:** compatibility writers must invoke the canonical
  migration bridge for each new row; startup backfill alone cannot preserve an
  invariant for writes that continue after startup.
- **What I would do differently:** add the router-bound lifecycle assertion
  when the evidence tables first land, alongside the lower-level store tests,
  so the old action cannot remain a parallel partial writer.

## 2026-07-29 - serialized mutation verification

- **What surprised me:** a timed-out mutation probe briefly outlived its parent
  shell and overlapped a focused test rerun, producing two transient failures
  while it intentionally changed the normalization invariant.
- **Pattern worth capturing:** mutation probes and ordinary suites must run
  serially, with a clean-tree check after the probe restores its mutations.
- **What I would do differently:** run each long verification as its own
  bounded command and confirm no probe process remains before the green rerun.

## 2026-07-29 - drain review-before-merge gate

- **What surprised me:** disabling stale auto-merge on `synchronize` still left
  a merge race because cancellation was not itself a required check.
- **Pattern worth capturing:** reactionary cleanup is defense in depth, not an
  authorization gate; bind the current head to an already-required check so a
  new head is pending or red before it can merge.
- **What I would do differently:** model the full GitHub auto-merge state
  machine—including retained enrollment across pushes—before selecting the
  first enforcement point.

## 2026-07-30 - dark authority store boundary

- **What surprised me:** a method named `list_*` with a required `limit` still
  does not make the result structurally bounded; the returned page also needs
  an enforced maximum so a future adapter cannot satisfy the type while
  returning an unbounded collection.
- **Pattern worth capturing:** keep cross-domain ordering separate from storage
  transactions. A serial coordination sequence can include several locks and
  effects while still forbidding any of those locks from nesting inside the
  authority transaction.
- **What I would do differently:** write the cursor-page and non-nesting tests
  alongside the first protocol sketch, rather than relying on docstrings to
  carry safety constraints that callers and adapters need to share.
- **Review follow-up:** named generation fields are not a complete CAS fence
  when budgets can mutate without rotating those generations. Fence the exact
  immutable record (or a revision advanced by every mutation), and test the
  stale second writer against the mutable envelope itself.

## 2026-07-30 - deterministic background attempt keys

- **What surprised me:** checking that source families produce distinct keys
  does not prove that every field inside one family is load-bearing; an omitted
  generation or retry ordinal can remain invisible behind a valid-looking
  digest.
- **Pattern worth capturing:** validate nominal IDs, canonical revisions,
  durable period identities, digests, and ordinals before hashing a versioned
  canonical envelope. A schedule key follows the nominal period across missed
  ticks and DST shifts; it must not follow the later wall-clock execution
  instant. The resulting key is stable and non-secret while each source
  boundary remains independently mutation-tested.
- **What I would do differently:** define one golden vector and the
  all-fields-mutate table with the first builder, so later source builders must
  join the same compatibility contract rather than adding ad hoc string
  concatenation.
- **Review follow-up:** due instant and due-period identity are not
  interchangeable. Spring-forward recovery can execute at the next valid
  instant while retaining the skipped nominal period, so only the upstream
  schedule-period identity belongs in the replay key.

## 2026-07-30 - background authority inventory closure

- **What surprised me:** the cloud host, scheduler, queue, immutable Branch
  snapshots, and GitHub effector already existed; the launch-critical gap was
  joining them under one authority chain, while a failed deployment fence had
  independently taken the public control surface down.
- **Pattern worth capturing:** make authority-root inventory executable. An
  exact AST callsite manifest catches both newly introduced bypasses and stale
  inventory entries, while source markers preserve indirect roots such as
  scheduler callbacks and cloud supervisors.
- **What I would do differently:** separate the service-recovery path from the
  implementation dependency graph immediately. That turns “cloud drain is
  blocked” into two bounded lanes: restore the control surface, then close the
  dark authority prerequisites.

## 2026-07-30 - public MCP response-cookie boundary

- **What surprised me:** the proxy correctly denied hop-by-hop headers but
  still leaked the more sensitive end-to-end class; protocol correctness alone
  did not encode the public/internal trust boundary.
- **Pattern worth capturing:** a stateless public proxy should fail closed on
  every upstream response cookie, not parse credential names. A single
  case-insensitive header boundary preserves SSE streaming and prevents future
  cookie variants from bypassing a name-specific filter.
- **What I would do differently:** include response credential egress in the
  first front-door threat model alongside request-secret injection, rather than
  treating generic non-hop-by-hop pass-through as sufficient.

## 2026-07-30 - recovery-to-normal deploy handoff

- **What surprised me:** emergency recovery itself was correct; the outage
  appeared one deployment later because Docker Compose project identity is
  lifecycle authority even when fixed container names and images are equal.
- **Pattern worth capturing:** a temporary recovery owner needs an explicit,
  durable handoff back to the canonical owner. Carry exact provenance through
  the next write-ahead record, retire only exact stopped IDs, and make the
  mutation replayable before releasing the canonical start gate.
- **What I would do differently:** include “next normal deploy after successful
  recovery” in the first recovery acceptance matrix, not only recovery canary
  and finalization.

## 2026-07-30 - Ringer-informed repository-to-spec contract

- **What surprised me:** the tempting “work packet” abstraction duplicated
  authority already owned by activation, background attempts, provider
  reservations, evaluation, and outbound effects. Ringer's useful lesson was
  executor boundedness, not a new aggregate state owner.
- **Pattern worth capturing:** keep the user-authored definition immutable and
  make operational status an ephemeral projection that revalidates every
  identity, generation, source, executor, and inherited budget envelope.
- **What I would do differently:** begin external-orchestrator adaptations with
  an authority ownership matrix and an executable-surface inventory before
  proposing a schema. That would have exposed both the duplicate-owner risk and
  the unsafe `AcceptanceScenario` dispatcher boundary in the first draft.
# 2026-07-30 - Ringer GitHub reconciliation

- What surprised me: the repository auto-enroll workflow can merge a non-draft PR within seconds, before an independent review requested in the same session returns.
- Pattern worth capturing: destination reconciliation needs a complete reserved-marker-family check, not substring presence; creation and reconciliation must share the same ambiguity rule.
- What I would do differently: open review-gated follow-ups as draft PRs immediately, especially when auto-enroll is active, and keep the adapter credential-blind from the first implementation commit.

## 2026-07-30 - Ringer epoch-2 activation substrate

- **What surprised me:** the existing epoch-2 queue already exposed the atomic
  claim callback needed for future integration; the missing primitive was the
  authoritative activation identity, not another scheduler or worker.
- **Pattern worth capturing:** represent executor cutover as stop-then-activate
  CAS transitions over one exact record. A direct tray/cloud switch is an
  invalid state transition, and stale epoch/version/lease tuples are data to
  reject rather than identities to recover locally.
- **What I would do differently:** decompose the umbrella blocker into a dark
  authority-owner slice before discussing runtime wiring. It made one day of
  work independently reviewable while keeping unsafe activation impossible.

## 2026-07-30 - Activation-bound epoch-2 claims

- **What surprised me:** the queue's existing transaction callback was enough
  to validate activation and worker authority without introducing a second
  claim owner or widening the public task model.
- **Pattern worth capturing:** an all-or-none tuple alone permits full erasure
  to masquerade as a legacy row. Bind optional authority identity into a
  second existing integrity record, cross-check both representations, and
  re-read the authoritative record inside the mutation transaction.
- **What I would do differently:** add the transaction-aware read method to
  each authority store when that store is first introduced; reopening a
  connection during a caller-owned transaction silently weakens atomicity.

## 2026-07-30 - Consumed drain merge suppression

- **What surprised me:** duplicate-receipt detection was already correct; the
  throughput collapse came from retaining the same admission while counting
  the correct rejection as a worker failure.
- **Pattern worth capturing:** classify stale, already-proved work separately
  from malformed work. Suppress the exact stale candidate in a bounded set and
  preserve audit evidence without manufacturing progress or spending the
  failure budget. Do not reuse a set whose reconciliation and bypass semantics
  belong to a different class of suppression.
- **What I would do differently:** state-transition tests should assert the
  next admission decision, not only that counters did not advance. A retained
  resume target can turn a safe rejection into a deterministic retry loop.
## 2026-07-30 — dark background authority persistence

- Surprised: a generic exact-record CAS still permits authority regression
  unless immutable attempt facts, budget narrowing, lifecycle edges, and
  claim/lease fencing are checked separately.
- Keep: store parent bindings and attempts as lossless typed canonical JSON,
  but duplicate query fields with a digest and cross-check both on every read
  so index or payload tamper fails closed.
- Surprised: RFC3339 text order is not chronological when otherwise-valid
  values mix `Z`, offsets, or fractional seconds; recovery indexes need one
  normalized integer time key.
- Next time: write the 16-way logical-key contention test at the same time as
  the uniqueness schema; single-thread replay does not prove the cloud case.
## 2026-07-30 — dark background binding transitions

- Surprised: exact store CAS is necessary but insufficient for a safe public
  transition API; accepting a caller-built replacement would still let the
  caller choose authority fields.
- Keep: transition methods accept only a closed root lookup or an exact stored
  fence, then reconstruct every ID, digest, generation, and status server-side
  from a trusted canonical resolver.
- Next time: test stale-invalid transitions as well as stale-valid ones; local
  state-table validation must not hide a newer winning generation.

## 2026-07-30 — atomic background-attempt reservation seam

- What surprised me: the durable store already enforced logical-key uniqueness,
  but future issuance still could not safely combine binding revalidation,
  replay detection, and `max_attempts` counting without transaction-local reads.
- Pattern worth capturing: expose the smallest reads needed on the opaque
  transaction protocol, validate denormalized indexes against canonical records
  before filtering aggregate counts, and prove count/check/insert with competing
  writers; a filtered integrity check can hide compound relocation corruption.
  Keep resolution and activation outside the storage seam.
- What I would do differently: include the independently shipped model mirror
  in the first persistence slice so later store extensions do not discover an
  untracked package dependency during parity verification.

## 2026-07-30 — dark background-attempt claim fencing

- What surprised me: an exact claim-generation fence still permits authority
  rotation unless executor audience fields are independently constrained.
- Pattern worth capturing: obtain executor, predecessor, and boundary evidence
  from a trusted resolver, then revalidate the exact current binding and
  observed queue row inside their mutation boundaries; lease expiry is only a
  candidate signal, never recovery authority.
- What I would do differently: include cross-audience negative cases in the
  first RED batch instead of finding that gap during the security pass.

## 2026-07-30 - Ringer drain hot-path recovery

- Surprised: the expensive `--provider` inventory filter was applied only after
  every historical worktree had already paid its git-probe cost; an apparently
  scoped command was still global work.
- Keep: put selection before expensive observation, and write prompt budgets in
  terms of one disposable attempt so continuation workers cannot confuse prior
  delivery artifacts with their own bounded output.
- Next time: make the foldback PR identity explicit in the first continuation
  contract and test it before an overnight controller can repeat `PARTIAL`.

## 2026-07-30 - Public-safe startup diagnostics

- Surprised: sanitizing log text was not enough; even traceback paths,
  functions, line numbers, and a mismatched private image identity could carry
  unapproved information into a public artifact.
- Keep: reduce diagnostics to repository-proved identities and fixed fields,
  bind raw collection to the exact candidate, and bind publication to explicit
  restored-or-authoritatively-fenced plus terminal-receipt outputs.
- Next time: write the negative workflow states first: pre-mutation failure,
  skipped health after mutation, identity mismatch, failed fence proof, and
  failed terminal publication.

- Follow-up: a structurally correct Docker template still produced unusable
  evidence because `\t` remained literal. Treat cross-process text framing as a
  protocol: choose a separator excluded by every field grammar and drive a
  round-trip fixture through the real validator.
# 2026-07-31 — deploy terminal-state recovery

## Follow-up: predecessor state is not the postcondition

- What surprised me: an emergency recovery can serve the exact production
  fleet while `tinyassets-daemon.service` is authoritatively `failed` or
  `inactive`; preserving that active state after a successful normal deploy is
  therefore wrong even though preserving its enablement is correct.
- Pattern worth capturing: separate predecessor policy from target health.
  Save and restore enablement exactly, but define the successful target's
  active state as a postcondition.
- What I would do differently: enumerate every authoritative state class in
  the first production-shaped test instead of repairing only the first
  observed transition (`activating`).

- What surprised me: every forward deployment proof was green, yet preserving
  a transient systemd `activating` snapshot made exact cleanup convergence
  impossible and turned a healthy target into a safe but public outage.
- Pattern worth capturing: forward, rollback, and cleanup are separate facts.
  Terminal receipts must model them separately instead of rewriting an earlier
  valid tuple after a later safety action.
- What I would do differently: production-shaped restore tests should include
  transitional systemd states and should assert container running state—not
  mere container existence—before deriving active image identity.

## 2026-07-31 - Stable intent versus transient systemd observation

- What surprised me: the exact-state cleanup was strict but not truthful; it
  froze `activating` as durable intent, so a healthy unit settling to its normal
  state became a safety violation after every fleet and canary proof passed.
- Pattern worth capturing: write restoration contracts only from stable
  observations. Bound the settling wait before mutation, preserve exact stable
  state and enablement, and separately normalize the daemon's startup transition
  to its healthy active terminal state.
- What I would do differently: drive the first deploy test with transient
  systemd states and an active-but-disabled daemon, not only idealized stable
  unit fixtures.

## 2026-07-31 - Recovery handoff live closure

- What surprised me: two correct repairs developed concurrently—stable
  preflight snapshots and a successful-deploy daemon postcondition—and only
  their integrated exact-head review proved that neither weakened the other.
- Pattern worth capturing: after a recovery deployment, accept closure only
  when workflow success, downloaded exact-state artifacts, and a fresh public
  probe agree.
- What I would do differently: check draft successor branches against current
  `main` before calling them deployable; a diagnostics-only change can still
  regress production when its image omits newer platform capabilities.

## 2026-07-31 - Predecessor state is not terminal intent

- What surprised me: waiting for a stable snapshot revealed a truthful but
  still wrong contract—the recovered fleet was healthy while its systemd unit
  was stably failed, so preserving that predecessor state could never describe
  a successful normal handoff.
- Pattern worth capturing: distinguish observed predecessor state from the
  post-operation invariant, and do not commit that invariant until success is
  durable. Preserve exact enablement and auxiliary units; only a proved normal
  target may explicitly own an active daemon, while rollback retains the
  predecessor posture.
- What I would do differently: enumerate every authoritative stable predecessor
  state in the first terminal-state table, not only transitions.
## 2026-07-31 — OpenSpec backlog refinery

- **What surprised me:** the controller's exact STATUS calculation was correct,
  but it answered a much narrower question than the user asked: zero immediately
  claimable rows coexisted with 37 active changes and 832 unchecked tasks.
- **Pattern worth capturing:** unattended orchestration needs a work-production
  stage as well as a work-consumption stage. Reconcile one existing hidden or
  blocked target into ordinary reviewed claim authority; never turn backlog
  visibility itself into permission to edit product code.
- **What I would do differently:** wire the existing OpenSpec flow inspector into
  candidate exhaustion when the supervisor was first built, and model shared
  coordination-row edits separately from product write-set collisions.

## 2026-07-31 — requester-owned cloud provider binding

- **What surprised me:** the exact requester-owned GitHub grant already existed in `ConnectionLedger`; the older effector-consent row was the wrong authority source for cloud activation.
- **Pattern worth capturing:** distinguish durable non-bearer binding intent from just-in-time execution receipts. That lets activation prerequisites land safely without treating queue identity as provider authority.
- **What I would do differently:** start each cloud slice by mapping every authority noun in the task to its concrete current-main owner; the stale audit made the missing provider binding look like a wider compute-runtime gap than it was.

## 2026-07-31 — prepared cloud continuation

- **What surprised me:** a fixed-clock concurrency test hid that an ordinary
  restart at a later time would conflict with the continuation it had already
  prepared.
- **Pattern worth capturing:** idempotency compares immutable request identity,
  not observational timestamps. Persist the first receipt and replay it on an
  equivalent retry.
- **What I would do differently:** make the first restart test advance the
  clock as well as reconstruct the store, and audit budget comparisons for
  authority widening before treating equality-shaped fixtures as proof.

## 2026-07-31 — dark provider-work ledger core

- **What surprised me:** a binding and a prepared continuation still leave a
  crucial gap: without a receipt, live worker claim, and conserved invocation
  reservation, “use this provider” has no crash-safe bounded meaning.
- **Pattern worth capturing:** separate immutable authority ceilings from
  mutable consumption. Keep the receipt content-addressed and derive remaining
  budget from atomic reservation rows instead of rewriting the receipt on every
  launch.
- **What I would do differently:** identify the missing production assignment
  owner before naming the next slice “provider issuance”; the safe buildable
  dependency was the dark ledger, not a synthetic production root.
## 2026-08-01 — Drain refinery quarantine

- Surprised: the fresh candidate snapshot correctly counted dozens of refinery
  lanes, but the post-block predicate used an older three-class eligibility set.
- Pattern: every scheduler predicate that answers “can I continue?” must share
  the same dispatchable classification vocabulary as candidate selection.
- Differently next time: add a table-driven classification parity test whenever
  a new candidate class is introduced, rather than testing selection and
  post-result flow independently.
- Live follow-up: require the controller log to prove the exact blocked result,
  alternative decision, and next dispatch timestamps before retiring a
  scheduler hotfix; repeated ordinary dispatch is useful but not equivalent.
## 2026-08-01 — governed custom-agent plan compilation

- **What surprised me:** preserving arbitrary agent topology did not require a
  universal graph model. A plan adapter can declare its own structural fields,
  entry cardinality, compatibility envelope, coverage field, and topology
  label while the platform enforces only canonicality, confinement, and exact
  executable-component coverage.
- **Pattern worth capturing:** run exhaustive component compilation first,
  resolve one explicit governed plan adapter second, and construct the
  immutable manifest only after every diagnostic is clear. Unknown
  descriptive data remains pinned but never enters execution coverage.
- **What I would do differently:** include malformed nested coverage values and
  post-registration descriptor mutation in the initial red matrix; both are
  recurring trust-boundary probes for public frozen dataclasses.

## 2026-08-01 — delivery-oriented OpenSpec refinery

- **What surprised me:** the scheduler and claim checker were behaving exactly
  as specified; throughput collapsed because coordination rows described final
  project completion while the checker interpreted them as next-slice gates.
- **Pattern worth capturing:** backlog production and backlog consumption need
  a machine-checked handoff. Accept a refinery continuation only when its merged
  row becomes claimable inside the assigned change boundary.
- **What I would do differently:** define `Depends` from the first slice's
  start condition, then keep downstream proof in acceptance tasks; never derive
  admission from an umbrella change's entire completion checklist.

## 2026-08-01 — durable drain continuation

- **What surprised me:** preserving an identity in memory was insufficient;
  the restart marker, cleared terminal timestamp, and consumed PR receipt each
  needed their own crash-safe persistence point.
- **Pattern worth capturing:** continuation authority is a durable receipt, not
  a control-flow branch. Persist intent before external work, consume it only
  after the successor exists, and deduplicate every merge-backed outcome.
- **What I would do differently:** test abrupt exit at every persistence
  boundary in the first restart matrix, including before recovery and during
  process launch, instead of only interrupting before the next worker attempt.

## 2026-08-01 — host-enrollment retry convergence

- **What surprised me:** two older same-target drafts existed, but both were
  closed by a superseded host-priority gate; current STATUS deliberately
  re-admitted a narrower exact-file RED/GREEN slice.
- **Pattern worth capturing:** for drain review receipts, publish the review
  comment first, return the PR to draft before any body mutation, update the
  body through stdin, verify the receipt/head, then mark ready.
- **What I would do differently:** use stdin for the first PR-body receipt
  update instead of PowerShell's multi-line `--body` argument, which briefly
  marked the PR ready before the receipt was attached.

## 2026-08-02 — host-principal lifecycle foldback

- **What surprised me:** after PR #2148 squash-merged, rebasing onto current
  main recognized both the implementation and its durable claim as upstream
  patch-equivalents and dropped them without conflict.
- **Pattern worth capturing:** a continuation worker should prove the merged
  implementation is absent from `origin/main...HEAD` before creating the
  coordination diff, then retire only the exact landed claim while the larger
  OpenSpec change remains incomplete.
- **What I would do differently:** separate ambient fleet counters from tracked
  source state before dispatch so a foldback worker starts with a clean tree.

## 2026-08-02 — bounded custom-agent provider outcome

- **What surprised me:** initializing a SQLite schema with `executescript`
  inside the finalization fence silently committed the caller's transaction;
  the RED test exposed that the apparent atomic write no longer had a live
  transaction.
- **Pattern worth capturing:** initialize schemas before opening a mutation
  fence, then CAS the launched reservation and insert its inert typed outcome
  in one transaction. If authority disappears after the paid call returns,
  persist an indeterminate blocker and discard the output.
- **What I would do differently:** include transaction-liveness and
  authority-loss-during-call tests in the first test batch, alongside timeout
  and oversized-output cases.

## 2026-08-02 — uncertain provider-call restart fence

- **What surprised me:** the persisted `launch_started` state is enough to
  prevent remint, but not enough to decide when a previous process has really
  stopped; the original durable claim expiry supplies that missing time fence.
- **Pattern worth capturing:** pre-launch recovery may resume the same IDs,
  while post-launch recovery must first refuse normal execution, wait out the
  original claim window, and then close the same reservation as indeterminate.
- **What I would do differently:** make each restart test construct a fresh
  service from the beginning; reusing the original object proves replay but
  does not prove restart recovery.

## 2026-08-02 — no-mint useful-progress health

- **What surprised me:** reusing the transition validator from an observation
  path briefly registered a spend-capable grant even though the caller never
  received it. Read-only projections need validation factored below minting.
- **Pattern worth capturing:** progress is the newest exact canonical owner
  transition, not liveness noise. Bind alarm identity to that record digest
  plus the configured threshold, and derive alarm time from the milestone so
  concurrent observers converge byte-for-byte.
- **What I would do differently:** design observation APIs around a pure
  authority validator first, then let mutation paths wrap that result in
  one-shot capabilities.
## 2026-08-02 — dark background target holds

- **What surprised me:** task 2.6 names queue behavior, but task 5.3 still owns
  concrete BranchTask shape and runtime wiring; the safe slice is the dark CAS
  contract both queue generations can implement without activating either.
  Independent review also exposed that a correct projection is unsafe unless
  the transition service itself enforces which hold reasons may exit automatically.
- **Pattern worth capturing:** preserve exact typed binding/attempt fences in a
  held owner record, then make automatic recovery prove the same records while
  authenticated repair must rotate the binding and never revive the attempt.
- **What I would do differently:** start with typed fences rather than duplicate
  scalar IDs/digests/generations; that would have made the first green diff
  smaller and stronger.

## 2026-08-02 - Windows lifecycle supervisor escape

- **What surprised me:** the intended private-file capture invariant had
  regressed into anonymous pipes even though the workflow still advertised a
  five-minute supervisor bound; descendant-owned pipe handles can outlive that
  bound without any product process still being the root owner.
- **Pattern worth capturing:** prove a timeout at the exact output-handle
  boundary, keep the PR draft until the real Windows runner emits the
  lifecycle child's replayed phase notices and the supervisor's own terminal
  checkpoint, and treat a green outer job alone as insufficient evidence.
- **What I would do differently:** include the workflow path touch and the
  release-critical declaration in the first PR increment so exact CI evidence
  and the scope guard start together.

# Canonical agent admission rejection repair (2026-08-02)

- Surprised: structural digest/link validation still let raw database rows reach a real provider carrier; a witness must be server-sealed, not merely self-consistent.
- Pattern: bind ephemeral drafts to an exact request nonce, persist a keyed admission witness atomically, and revalidate it on every durable authority read.
- Differently: test claim expiry with an advanced restart clock from the first recovery slice; same-time restart tests hid the pre-launch dead-end.
## 2026-08-02 — cloud-agent shaped load

- **What surprised me:** a 64-contender launch exposed a raw storage
  `PermissionError` that every eight-way unit race had missed; increasing the
  population changed which side of finalization losing workers observed.
- **Pattern worth capturing:** load evidence needs both process-visible effect
  markers and exact blocker assertions. Row-count convergence alone can be
  green while the service leaks the wrong error or crosses a process boundary.
- **What I would do differently:** start concurrency acceptance with spawned
  process single-flight plus revocation-during-replay, then add thread load for
  density, rather than treating thread contention as a proxy for worker loss.

## 2026-08-02 - Crash-consistent background authority owners

- **What surprised me:** a typed owner-store protocol and passing fake-store
  tests still allowed recovery and reauthorization to publish authority that
  had never existed in canonical storage; even persisting a fresh attempt was
  unsafe until the transaction re-applied the binding's issuance limit.
- **Pattern worth capturing:** an authority fence is meaningful only when its
  referenced binding and attempt are validated and committed in the same
  transaction. Exact absence may authorize a safe closed hold, but typed
  resolver output alone can never authorize a runnable owner.
- **What I would do differently:** split queue and source reauthorization exit
  shapes at the start; queue recovery must create a fresh attempt, while a
  source owner may safely exit without one.

## 2026-08-03 - Provider-bound physical cloud workers

- **What surprised me:** provider validation at activation still allowed the
  wrong fleet to lease an already-active Trigger, and queue custody alone did
  not prove the runtime's provider at launch.
- **Pattern worth capturing:** validate immutable executor identity before the
  first mutable lease, then revalidate the same physical worker, runtime, and
  requester provider binding inside every spend-bearing transaction.
- **What I would do differently:** model the four physical fleet identities in
  the first concurrency fixture; a one-worker replacement test hid both the
  logical-ID collapse and the cross-provider claim race.
- **Follow-up surprise:** passing the physical identity through one pump was
  insufficient because the pump rewrites the process environment; the
  supervisor must freeze and explicitly pass the original identity on every
  poll. Provider checks likewise belong inside activation/admission write
  transactions, not only before and after them.

## 2026-08-04 - Server-owned app outbound adapter

- **What surprised me:** a mocked transport boundary can still prove useful if
  it is strict about the only values crossing it and persists no message body;
  the idempotency and redaction invariants are independently testable before
  live Slack credentials exist.
- **Pattern worth capturing:** make the response digest a shared contract at
  the authority-to-transport seam, then derive the replay key from the signed
  authorization rather than from caller text.
- **What I would do differently:** run the independent reviewer earlier with
  a smaller exact-head brief; repeated peer CLI timeouts should not be allowed
  to delay hosted verification or be mistaken for an approval.

## 2026-08-10 - Requester-owned serving authority

- **What surprised me:** a path can resolve inside the universe and still be
  unsafe custody input when a symlink or Windows junction remains retargetable.
- **Pattern worth capturing:** authorize spend from one exact chain—current
  request lease, serving agent revision, requester-local assignment, active
  `ProviderWorkBinding`, and generation-bound custody—and hold the admission
  fence through the provider launch.
- **What I would do differently:** start the first RED suite with both lexical
  and resolved path containment, plus registry-lifetime assertions for every
  ephemeral capability.

## 2026-08-10 - BYO-LLM slice 1 adversarial fix round

- **What surprised me:** config-only routing and recording adapters jointly hid
  that the production path was both unauthorized and unable to launch.
- **Pattern worth capturing:** security acceptance needs a real adapter path,
  cross-process exclusion, material-bound custody, and mutation proof at every
  authority seam; a mocked router alone is insufficient.
- **What I would do differently:** run the ambient-authority and real-adapter
  probes before expanding happy-path coverage, then run the broad graph callers
  as soon as universe routing becomes fail-closed.

## 2026-08-10 - BYO-LLM slice 1 adversarial fix round 2

- **What surprised me:** a cross-process fence and a current digest still left
  the launched process reading mutable bytes through a writable bind; the lock
  described cooperation, not custody.
- **Pattern worth capturing:** derive launch authority from copied bytes, expose
  only that per-launch snapshot read-only, and keep request-call budgets
  separate from durable binding high-water accounting.
- **What I would do differently:** make the first real-adapter test spawn an
  on-disk wrapper target and inspect the exact mount graph; mocking subprocess
  creation allowed both a missing `/opt` tree and a vacuous bypass assertion.

## 2026-08-10 - BYO-LLM slice 1 credential snapshot fix round 3

- **What surprised me:** Windows `DirEntry.stat()` returned zero device/inode
  values while `Path.lstat()` preserved the volume/file-index across rename.
- **Pattern worth capturing:** temporary-secret cleanup must track filesystem
  identity, revalidate names, and search only a validated immediate parent.
- **What I would do differently:** start snapshot work with root-alias and
  renamed-decoy mutation tests before describing any copy as sealed.

## 2026-08-25 - Foreground run provider authority

- **What surprised me:** the dormant run receipt lane already had sound
  carrier provenance, while the shared reservation record stopped at launch
  and therefore could not safely release unused budget.
- **Pattern worth capturing:** asynchronous entry points should mint durable
  work authority at admission, then mint and settle one process-bound carrier
  for each actual provider attempt.
- **What I would do differently:** run the generic authority regression suite
  immediately after changing the carrier constructor so private mint-proof
  compatibility is caught before the focused foreground tests are green.
