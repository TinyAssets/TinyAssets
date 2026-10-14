## Contract and current evidence

Authority: Direction, ADR-017, ADR-018, shape-convergence C12 and
universe-agent-harness §4.17. Current base: a1fb3d91062feb1257af11c2cce065ea02dfa7a4.
No open PR owns C12. C6 P2b is pushed through `conv/c6-p2b-4` (bf11f6d5),
not merged; the lead's handoff says the stack is unfinished.

C12 is not merely wiring the curated wiki exporter. It includes daemon brain,
episodic knowledge and derived retrieval; all existing user records need a
migration census before deleting a canonical store. Transactional session,
approval and execution records remain platform state, not knowledge indexes.

## Independent local runner slice

The standalone artifact lives outside the platform runtime under
`tinyassets/okf/assets/agent.py.txt`; it is copied verbatim into `run/agent.py` by
the eventual exporter. It imports only the Python standard library and has no
TinyAssets account, import or network dependency. It accepts a chat-completions
endpoint supplied by the owner. Model output can invoke only read/write/edit/bash.
The agent's instructions and skills remain files in the exported folder.

The runner is local owner code, not the hosted isolation mechanism. File tools
stay inside the export root and refuse symlink components, hard-linked writes,
credentials and policy/runtime files. Bash is a host process, requires explicit
terminal consent for every invocation by default, and is denied when input is
unavailable. Policy is loaded once from the owner's run/rules.json; the model
cannot change it through file tools. The owner can explicitly allow or deny
bash in that file. No schedule starts by invoking the chat runner.

This initial slice proves a bounded chat turn and tools. Schedule execution,
full D1 rules mapping, export/import orchestration and live acceptance remain
explicit tasks, not simulated successes.

## File authority and migration preparation

The canonical store binds one existing BoxProvider handle and uses only its
coherent reads and journalled CAS writes. Each operation stages a complete OKF
revision with one Markdown entry per record, root index.md containing only
okf_version frontmatter, and plain log.md. An atomic current.json pointer outside
the revision is the commit point. Interrupted staging is never visible; indexes
read only the committed revision. Owner edits to committed Markdown win on the
next read. Old revisions are history, never scanned into a rebuilt index.
Physical redaction of historical content needs a separate purge transaction;
ordinary logical deletion is not claimed to erase historical bytes.

Migration preparation reads verified offline SQLite snapshots in read-only
transactions. Every table must be classified with explicit scoped columns or an
exclusion reason. All SQLite values, unknown columns and schema are retained;
editable prose has exactly one canonical representation in the Markdown body.
The real-schema projection check covers daemon brain, project memory, episodic
memory, knowledge graph, resolved series promises, historical/current drafts and
superseded temporal facts. Prose and description columns become the editable
body without a second canonical metadata copy. This validates seven source
schemas, not an assertion that production ownership mapping is complete.
Owner-authored YAML frontmatter refuses aliases/anchors, over-1-MiB headers and
nesting beyond 64 levels before constructing values; duplicate keys, malformed
UTF-8 and nonportable values fail explicitly. JSON-form metadata retains exact
scientific-notation types.

Preparation never changes a live database, infers owner mappings, or starts on
application boot. Cross-store backup consistency, writer pause and cutover remain
the deploy transaction's prerequisites. Snapshot commits are bound to the same
owner, command center and epoch that produced them.

## Cutover and export (remaining integration)

1. Finish and deploy C6; bind the owner box once per operation.
2. Enumerate legacy knowledge stores and preserve scoped records, metadata,
   provenance, tombstones and unknown fields in the OKF source representation.
3. Under a verified full backup and paused writers, project all records to a
   staged bundle. Compare source counts and canonical serialized values. Commit
   one all-or-nothing layout transition; never migrate at startup.
4. Change all reads/writes to bundle authority; build indexes from files and
   prove deleting/rebuilding each index cannot lose or resurrect knowledge.
5. Export via the owner door with the shared manifest, preview-bound content
   selection and credential exclusions. Fold the existing OKF exporter into
   that one writer. Private output only; publishing is separate authorization.
6. Prove restore, owner editing, restart, export, local execution without an
   account and import quarantine. Assert the deployed SHA before closing S17.

## Blockers observed in this cloud environment

- `scripts/linux_oracle.py --ci`: base image request to mirror.gcr.io returned
  Forbidden locally; hosted exact-head Linux oracle shards passed at d10eec39.
  The final candidate still needs its own exact-head CI pass.
- `scripts/deployed_sha.py --json`: tinyassets.io/app tunnel returned HTTP 403;
  live SHA cannot currently be observed.
- Claude CLI absent; no cross-family review receipt can be manufactured.
- Every PR requires a trusted exact-head receipt. Builders may not self-stamp
  one (lead handoff); independent review must be supplied by the review lane.

## Proposed guard boundary requiring independent review

The original S1 second-path guard rejects the standalone runner's model_call
callback even under packaging/. D11 requires this account-free local loop.
The proposed change names exactly that artifact, enforces declared standard
library imports, rejects dynamic Python loading and hosted references, and
keeps all hosted call paths and S1 ceilings unchanged. Architecture records the
export-only boundary. This gate change needs independent review before merge.

## Transfer preparation and remaining limitations

The shared versioned file manifest now drives private zip export and import.
Arbitrary content is captured through one coherent box read, starts excluded,
and cannot be exported after its generation changes. Detected credentials stay
blocked; binary inclusion requires an exact named selection. Import validates
hashes, bounded expansion, regular file types and normalized path collisions
before writing a fresh platform-owned quarantine with no activation record.

The owner door, shared manifest, private contact choices and standalone files
are implemented below. Structured configuration, roster, complete D1 policy and
schedule mapping, hosted activation remain open. Curated wiki conversion is integrated below.
The arbitrary-file scanner conservatively excludes canonical records containing
generated hashes; schema-aware serialization must resolve that without allowing
secrets through. Passing ingestion tests do not establish a shipped feature.

## Owner transfer integration

`POST /app/api/transfer` is the one owner transfer door. Preview uses the named
owner identity and the configured BoxProvider's ownership-checked binding;
there is no host-path fallback. Both preview and download require the existing exact-origin protected owner
session for that same account; bearer access alone cannot reveal preview bytes. The server retains
immutable previews for a short lifetime, bound to owner, center, provider
instance, placement epoch and generation. A process restart invalidates a
preview; it never reconstructs approval from caller-supplied bytes. Capacity
refusal is explicit, and only one preview per owner is retained. Download checks
ownership and generation again and returns an opaque private attachment.

The owner selects exact preview paths and names binary files explicitly.
Contact occurrences have stable per-file indices in the captured preview;
default output redacts them. Private export may include individually selected
occurrences; share rejects all such selections. Credential detection runs on
original bytes before any redaction and cannot be overridden. These are content
selections, never authorization to publish or execute an import.

The C6 production resolver remains the prerequisite for this route to operate
in production. An absent provider is an explicit service-unavailable response.
The route must not instantiate the development LocalBoxProvider.

## Distribution of the standalone runner

The single runner source is inert package data at `tinyassets/okf/assets/agent.py.txt`,
so the wheel and existing Docker package copy both contain the exact artifact.
Only `okf/export_assets.py` serializes these bytes into `run/agent.py`; hosted
code never imports or executes them. The exact-artifact stdlib and dynamic-code
guard follows the move; no directory or hosted model-call exemption is added.
Generated files cannot collide with selected content. The export includes a
blank-key environment example, conservative policy, a license-choice placeholder
and instructions that distinguish supported local behavior from outstanding
schedule/rule integration. Import accepts only blank variable assignments in
`.env.example`, never real `.env` content or executable shell lines.

The public package and private zip now use the existing package manifest shape
through `okf/manifest.py`, preserving the public `publish` profile's byte layout.
The zip adds the `share`/`export` profiles to that same serializer. Existing
published definitions are not rewritten. Current private metadata describes the
included instruction folders; structured workflow/schedule/model-reference
capture and the command-center responsibility still need production integration.

## Import custody

Protected JSON/base64 import uses the same origin/session gate as preview and
export, with a request-byte bound before JSON parsing and an archive bound after
decoding. The complete archive is validated before custody changes. Validated
files are stored as the existing package store's inert, content-addressed JSON
blob, outside every box, rather than extracted into an agent-visible directory.
A platform `bundle_imports` record binds an opaque import id to authenticated
owner, command center, placement epoch and digest. Its state is quarantined;
there is no pending executable install action or activation receipt. Storage
uses the existing account packages pool and the account-deletion writer fence.
Erasure inventory is durably recorded before any blob write and survives every
later failure. Account erasure removes private bytes and metadata by owner. A future explicit activation
must recheck that record and epoch, construct a reviewed installation plan and
use BoxProvider; importing alone never writes an owner box or starts code.

Private-import erasure removes both the custody record and its private blob
ownership/physical bytes. Peer ownership or a public package version retains
shared bytes. Blob creation and reference-counted removal share the package
writer transaction; a concurrent peer import cannot record a now-missing file.
Quarantine rows survive failed removal for retry. The unused extracted-directory
quarantine helper has been deleted; there is one production custody path.

## Owner interaction

The profile's transfer control opens a protected preview. File discovery lists
eligible regular files through BoxProvider only, excludes never-exported trees,
and refuses a changed generation or an incomplete bounded walk. The owner
chooses files for content preview, then explicitly selects final content and
individual private contacts before downloading. Text is rendered with DOM text
nodes, never HTML; binaries are named rather than rendered. The same control
accepts a local zip and reports durable quarantine without claiming activation.
All asynchronous UI responses are discarded after identity/center/login changes.
The account-clear boundary also closes and clears an already visible private preview.

## Single wiki writer

The existing wiki exporter is now a pure writer called inside private transfer,
using captured and scrubbed selected bytes. Its unused host-path entrypoints,
filesystem copying and associated I/O guard exemptions are removed. Concept
metadata, wikilink conversion, root index and dated log remain one implementation.
Only selected pages enter link resolution and generated metadata; no source is
read again. Source timestamps win; missing timestamps use the explicit export
time. Wiki-private exclusions are shared with preview/discovery, including nested
workspace wiki trees. Generated path collisions or selected reserved names fail
rather than overwrite or silently drop approved content. Named binary files with
Markdown suffixes retain their exact bytes without parsing. Existing behavioral
wiki tests now exercise this same writer, and transfer tests cover its integration.

## Portable roster and settings

The preview now reuses the shared-package root harness allowlist (root memory
still needs item selection) and maps selected root files into agents/main.
Root and nested agent paths may not collide. Root platform state stays absent.
The canonical settings parser validates bounds, duplicates and typed fields;
only model identifiers/connections and source owner bindings are replaced or
omitted before the preview. Other behavior/configuration fields retain ordinary
credential screening. Contact selection traverses typed string values, so a
phone-shaped number stays numeric. Serialization uses the canonical parser's
YAML scalar rules, preserving scientific floats and numeric-looking strings.

The shared manifest accepts an optional roster for private/share exports while
existing public-package bytes remain unchanged. The roster is derived only from
included instructions and settings, and import recomputes it before custody.
Each model-bearing agent gets a distinct AGENT_n environment group, written as
empty placeholders. The standalone runner can use that complete local group or
the global model group; it never inherits the global credential into a partially
overridden endpoint. Workflow definitions and schedule records remain outstanding.

Local .env loading uses a 64-KiB, no-follow, single-link regular-file read and a
closed model-variable grammar. Values are literal; no shell is sourced, no
process environment is mutated, and existing process values take precedence.
An endpoint override cannot inherit the file credential for a different endpoint.
Malformed local files fail before fallback. The file tools still refuse .env.

## Broker rules in private exports

The protected route reads the existing owner-policy broker after binding the
owner's center. It captures a data-only behavior projection for each previewed
agent; no second rule store or policy writer is introduced. Canonical settings
are read in the same box snapshot even when not selected, solely to resolve a
directory to its durable policy identity. Unselected settings and source IDs
are not added to the preview. Root main uses its canonical fixed main identity.
Only account and
that center's rules enter the projection. Source grants, identifiers, notes,
connections and operation names do not travel. Qualified rules conservatively
constrain the entire action class, retaining the broker default outside the
qualified operation; this can make local behavior stricter. Generation/digest
must still match at download. Captured rules count toward preview resource bounds.

The UI shows these rules alongside content. Selecting an agent includes its
previewed rules and records their path in the verified roster; selecting another
file does not include that agent's rules. Agent-controlled files cannot replace
the captured projection. The local dispatcher enforces workspace.files and
workspace.shell; preapproval requires fresh consent and hand_off refuses.
Policies/settings/manifest/schedule state are read-only to model file tools.
The model sees the remaining action behaviors, but a host shell has no broker
effect classification, egress or spend enforcement. Full D1 is still incomplete;
the README preserves that limit and the default per-command shell prompt.

## Explicit local schedules

The same inert standalone runtime accepts a separate schedule/enable invocation;
chat never starts it. A disabled example documents the local prompt schema.
This does not yet serialize platform graph schedules or claim that a model
prompt is equivalent to a workflow graph. Numeric five-field cron (all fields
match, Sunday zero) uses an explicit IANA zone; interval and once use aware
timestamps. Only the present minute's slot may run; offline backlog is not
replayed. The previous local wall-clock slot suppresses DST-fold duplicates.

A no-follow regular-file flock serializes local schedulers. Bounded atomic
state is fsynced before any model turn and again after completion. An exception
or process death leaves a claimed attempt; restart stops for owner inspection,
never guesses that effects did not happen. Configuration changes also stop the
loop. The state stores only each schedule's latest attempt and remains within
the closed 100-schedule/1-MiB limits. Existing tool consent remains in force.

## Reviewed private import copying

The transfer door now has a separate preview and confirmation after quarantine.
The immutable existing package pin binds owner, center, epoch, custody hash,
initial destination generation and exact file map. Files land under a fresh
imports/<import-id>/ folder; existing files and the active harness are never
replaced. This materializes a complete portable bundle without adopting its
agents, grants, hooks or schedules into the active native runtime.

Existing install-kind pins carry an explicit private-import discriminator;
public package validation and native installation are not widened. Confirmation
uses the existing claim token and lease, durable pre-write progress and stable
per-file CREATE operation IDs. A lost response requires explicit retry of the
same operation; the provider owns uncertain-outcome handling. A final coherent
capture must match all selected bytes before the pin finishes. UTF-8 decoding
failures and NUL-containing data require named binary confirmation. Imported
content is rendered only as text nodes. Tombstones block starting a copy;
placement fencing and authenticated box destruction handle in-flight writes.

## Current migration ownership input

Project IDs are opaque caller inputs; project_memory.updated_by is the writing
actor, not an ownership relation. A cutover needs a verified project-to-owner
and command-center mapping. NULL legacy knowledge scopes likewise need explicit
resolution under the backup snapshot. Neither is inferred from the last editor,
current session, or an arbitrary first owner. Preparing adapters and restore
verification does not authorize a production migration with unresolved scopes.

## Canonical source adapters and offline restore

`SourceRecords` binds the configured C6 provider using the explicit owner and
command center. It reads only the current OKF revision, validates the original
source census and every complete typed row, and commits related table changes
atomically. Migration and ongoing writes use one identity/body encoder. Unknown
frontmatter survives updates; deleted Markdown records never reappear from a
legacy database. There is no SQLite fallback in the adapter. Existing legacy
consumer APIs other than project memory still require explicit wiring and
verified ownership context.

Restore creates a new exclusive destination from the original checksum-verified
backup. Owner-editable frontmatter supplies values, never executable DDL. Only
the verified scope is replaced, triggers are temporarily disabled, conflicts
abort regardless of schema defaults, and peer/excluded rows must remain equal
before commit. Foreign keys, integrity, canonical typed values and the original
backup checksum are checked before a receipt. Failed copies are retained for
inspection. The receipt explicitly does not activate the copy and lists excluded
stores that may require derived-index rebuilding. Tests exercise edits and a
real SQLite restore for daemon, project, episodic, KG, promises, versions and
temporal schemas; these are fixtures, not a production migration proof.

## Complete terminal rule decisions

The local shell dispatcher asks the terminal owner to classify all applicable
D1 action classes whenever any effect class has a restrictive behavior. The
strictest selected behavior governs; the model cannot supply classification or
consent. Missing interactive classification refuses. `hand_off` asks the owner
to perform the action and report the result, and never executes it on approval.
Files and workflows follow the same handoff semantics. This maps every D1
class/behavior to a terminal decision, but human classification is not a broker
or OS sandbox guarantee. Explicit all-do policy plus the separate bash enable
flag permits unattended shell execution.

## Reviewed namespace custody and project memory

The offline namespace census enumerates every configured table, including NULL
and zero-row namespaces, against checksum-verified snapshots. A binding names
the source, table, exact typed namespace, owner, center, snapshot digest,
reviewer, evidence digest and review reference. The caller revalidates current
authority before planning. Neither updated_by nor the account charged for a
write establishes ownership. Unresolved rows require a durable 0600 receipt in
an existing operator-owned 0700 custody directory outside owner boxes; they
cannot enter an owner projection, retrieval or export. Projection compares exact
typed records after SQL filtering, refusing collation/affinity expansion.
Zero unresolved rows completes only the ownership census, never authorizes live
activation or source deletion.

Project-memory get/list/set now bind the authenticated owner's configured box
provider and read only its canonical project records. An omitted center resolves
from authoritative current-home metadata, without a host-directory probe.
Both current and history tables require matching reviewed namespace bindings.
One revision atomically commits value and history with optimistic version
checks. Runtime writes require a stable request ID; a repeated committed request
returns its original version, while reuse for different content refuses. The
provider enforces bounded message envelopes. Account-quota enforcement has not
been established in the checked-out provider; C6 must provide and prove that
boundary before activation. The retired global SQLite writer and writer-account
accounting tests are replaced by canonical storage, refusal, retry and
owner-isolation coverage. Provider-refusal tests do not prove quota enforcement.

## Native private-agent adoption

A separate preview/confirmation can adopt one imported agent into a fresh
deterministic roster directory. Source model connections and broker rules are
removed; recipient defaults apply. The existing package pin binds the exact
transformed files and immutable import custody. Settings arrive retired before
instructions, and the agent becomes active only after the complete destination
inventory and bytes match approval. Unexpected files, directories and links
refuse. A coherent generation check and final settings CAS close concurrent
insertion races. Existing per-file operation IDs and progress recover interrupted
copies and lost final replies. Adoption starts no process or schedule. Native
session loading still depends on the C6/C7 binding-before-file-load integration.

## Restore coordination with C6

Knowledge restore creates only a fresh offline data copy and never installs an
old authority database. C6 #4920 at e0925bb6 retains responsibility for paired
receipt journals, epoch advancement and the union of no-replay operation IDs.
Activation must retain newer tombstones, destruction fences and permanent
UID/admission reservations; differing authority frontiers currently refuse in
C6. An OKF restore receipt cannot override that refusal. No production snapshot,
authority merge, source deletion or activation was performed in this lane.


## Remaining six canonical consumers

Daemon, episodic, knowledge, promises, versions and temporal consumers now accept
only explicit SourceRecords. Repository-owned DDL creates disposable in-memory
SQL indexes; no runtime disk database or vector directory is consulted. Each
public operation refreshes a coherent canonical revision, checks exact typed SQL
round trips, and publishes all related rows under one generation CAS. Unknown
source columns and metadata survive updates. A lost reply retains the original
snapshot/diff/operation ID for exact retry on the same connection. Deterministic
validation and CAS refusals release the connection after a fresh canonical read
proves the operation is absent; unknown I/O outcomes retain the pending diff.
Already committed IDs refuse instead of guessing a new operation. Daemon API
mutations require stable request_id in inputs_json (or internal kwargs).

Deletion adds a durable tombstone with source identity and committing operation. Forgotten
IDs cannot be revived by normal writes. Trusted AUTOINCREMENT indexes seed their
counter from verified backup counters and later tombstones, so deletion/rebuild
cannot recycle identities. Current source edits and deletions rebuild temporal,
full-text and vector indexes; historical files are never a recovery read path.
Daemon promotions put their human-readable summary in the canonical promotion
record and return that Markdown path. They retain source entry IDs, logical target
path and provenance in the same atomic revision, without a duplicate host wiki.

Commit logs and compact deletion markers roll into immutable content-addressed
history pages; the current manifest retains only page heads, bounded recent
markers, and per-table deletion counters. Operation replay and forgotten-ID
checks include archived pages. Historical source bodies are never restored by
these checks. Derived community membership deletions are replaceable relations,
while an explicit owner forget remains permanent. Daemon search and prompt
assembly are read-only; their trace IDs stay in the response rather than adding
canonical telemetry on every prompt. Mutation events remain durable. Explicit
center API requests check the principal deletion fence before provider binding;
the provider retains current ownership/epoch enforcement for every operation.

C7 must enter owner_memory_scope after authenticated C6 admission and reviewed
migration, keeping it alive until children finish. The namespace is explicitly
reviewed, not inferred from paths/state. Async context inheritance carries the
exact owner binding; plain threads need copy_context and serialized workers must
re-admit independently. Retained services/adapters refuse after scope exit or
under another current owner's context. The module's non-memory configuration
remains separate; memory services have no process-global default or fallback.
C7 production scope entry and C6 quota enforcement remain activation gates. The
six consumers and real scoped graph test fixtures do not establish those gates.

History lookup authenticates bounded immutable pages by content hash and caches
verified pages within the owner-bound bundle. Unrelated box generations cannot
invalidate those immutable reads; generated read-operation IDs do not scan the
commit history. Bulk forget pages split at the history target, with deletion
counters folded before archival. Explicitly forgotten graph memberships are
excluded from later derived clustering. Malformed history heads/counters refuse
as InvalidBundle. StorageRefused remains a deterministic pre-admission refusal,
not proof that the C6 provider currently enforces account quotas.

Portable policy translation refuses unknown scoped tables/kinds/qualifiers and
unmapped egress/spend limits. Standing grants never transfer. Missing agent
identities preserve the broker's explicit main default. Every generated roster
names a local policy, and a manifest-bearing runner refuses a missing file even
when the declaration was omitted. A selected inherited nonempty process model
key requires its endpoint in that same process environment.
