# Wiki Commons

> As-built baseline (2026-07-19, change `spec-out-existing-platform`): describes landed behavior on `main` at baseline time, known limitations included. Future behavior changes arrive as OpenSpec change deltas against this capability.

## Purpose

The shared markdown knowledge/coordination commons: draft-then-promote pages, typed filings (BUG/FEAT/DESIGN/PR) with per-kind IDs and dedup, sha-guarded patch, and seed (not closed) category taxonomy.

## Requirements

### Requirement: Wiki root resolution and page-substrate layout
The wiki-commons subsystem (`tinyassets/api/wiki.py`, path helpers in `tinyassets/api/helpers.py`) SHALL resolve its root through `tinyassets.storage.wiki_path`, honoring the `TINYASSETS_WIKI_PATH` env override. With no override, an already-present `data_dir() / "wiki"` directory remains the shared root; otherwise the shared root is `data_dir() / ".wiki"`, a dot-named platform directory that admission allows a fresh volume to create. A non-dot child of the data dir is a command-center root and is created only by admission, so scaffolding MUST NOT mkdir `data_dir() / "wiki"` on a fresh volume and MUST NOT describe that refusal as a filesystem permissions failure. A supplied `universe_id` SHALL instead resolve the root to that universe's private `wiki` directory, and an empty `universe_id` SHALL resolve the shared root wiki. Every page SHALL live under `pages/<category>/<slug>.md` (promoted) or `drafts/<category>/<slug>.md` (unpromoted). The tree SHALL be scaffolded idempotently on each call so read/list/search never error on a fresh deploy, and a `universe_id` containing path separators or a leading dot SHALL be rejected.

#### Scenario: default root falls back to the data dir
- **WHEN** the wiki resolves its root with no `TINYASSETS_WIKI_PATH` override and no `universe_id`
- **THEN** the root is `data_dir() / ".wiki"` when `data_dir() / "wiki"` is absent, and `data_dir() / "wiki"` when that directory already exists
- **AND** the `pages/` and `drafts/` category trees plus anchor files exist after the first call

#### Scenario: per-universe root is isolated from the shared root
- **WHEN** a call passes a valid `universe_id`
- **THEN** the root resolves to that universe's own `wiki` directory rather than the shared root wiki

#### Scenario: unsafe universe id is rejected
- **WHEN** a call passes a `universe_id` containing a path separator or leading dot
- **THEN** the dispatch returns an error and no wiki directory is created for it

### Requirement: Seed taxonomy is a set of defaults, not a closed whitelist
The category list in `_WIKI_CATEGORIES` SHALL be a seed taxonomy of sensible defaults, NOT a closed whitelist. A `write` to a category outside the seed set SHALL be accepted and sanitized into a lowercase slug (a path-traversal-safe path component), and SHALL be rejected only when the sanitized slug is empty. Category discovery for callers that omit a category (promote, supersede) SHALL union the seed categories with any custom category directories found on disk so organically grown categories stay discoverable. Read-side category filtering for search, changed-since, and exact-read ambient recommendations SHALL normalize the supplied value through the same safe slug rule and SHALL compare the exact page or draft category path component without validating against `_WIKI_CATEGORIES`; a valid custom or absent category SHALL remain queryable. For search or changed-since, an explicitly supplied non-empty value that normalizes to an empty slug SHALL return a structured error with no page results rather than becoming an unfiltered query; an exact read SHALL still return the requested body unchanged while its nested ambient feed reports that structured error and contains no recommendation items.

#### Scenario: a custom category is accepted and slugified
- **WHEN** a `write` names a category outside the seed set, such as `Magic Systems`
- **THEN** the page is stored under the sanitized `magic-systems` category
- **AND** the write is not rejected for being off-taxonomy

#### Scenario: an empty-slug category is rejected
- **WHEN** a `write` names a category that sanitizes to an empty slug
- **THEN** the call returns an error listing the seed categories

#### Scenario: omitted-category resolution sees custom directories
- **WHEN** a `promote` omits the category and the matching draft lives in a custom on-disk category directory
- **THEN** category discovery unions the seeds with that directory and finds the draft

#### Scenario: a custom category is filterable
- **WHEN** search, changed-since, or an ambient recommendation filters by a custom category that normalizes to a valid slug
- **THEN** matching pages and drafts in that exact category remain eligible
- **AND** a valid category with no matches returns an empty result rather than an error or widened corpus

#### Scenario: an empty-normalized read filter fails rather than widening
- **WHEN** a caller supplies a non-empty category such as `///` or `!!!` that normalizes to an empty slug
- **THEN** search and changed-since return a structured invalid-category error with no page results
- **AND** an exact read returns its requested body unchanged while its ambient feed reports the error with no recommendation items
- **AND** no surface treats the value as an omitted category

### Requirement: Draft-then-promote gate for freeform pages
A freeform `write` to a slug that has no already-promoted page SHALL land in `drafts/<category>/` and instruct the caller to call `promote`; a `write` to a slug whose `pages/<category>/<slug>.md` already exists SHALL update that promoted page in place instead of re-drafting. `promote` SHALL move a draft into `pages/`, run promotion lint unless `skip_lint` is set (blocking on lint issues), remove the source draft, and update the index. As-built limitation: the draft gate applies to freeform `write` only; typed filings (see the typed-filing requirement) and first-party canon writes bypass it.

#### Scenario: new content drafts, not publishes
- **WHEN** a `write` targets a slug with no existing promoted page
- **THEN** the content is stored under `drafts/<category>/` with a status indicating it must be promoted

#### Scenario: writing an existing promoted page updates in place
- **WHEN** a `write` targets a slug whose promoted `pages/<category>/<slug>.md` already exists
- **THEN** the promoted page is overwritten in place and no new draft is created

#### Scenario: promote moves draft to pages
- **WHEN** `promote` is called for a draft that passes promotion lint
- **THEN** the file is moved from `drafts/` to `pages/`, the draft is removed, and the index is updated

### Requirement: Typed filings bypass the draft gate with per-kind IDs and dedup
Typed filings routed through `file_bug` SHALL map `kind` to a category directory and ID prefix via `_KIND_ROUTING` (`bug`->BUG, `feature`->FEAT, `design`->DESIGN, `patch_request`->PR), allocate a server-assigned `<PREFIX>-NNN` id from an independent per-kind counter that scans both the kind's `pages/` and `drafts/` directories, and land the page directly in `pages/` bypassing the draft gate. Before filing, the handler SHALL run a per-kind duplicate check that compares the new title-plus-body token set against existing filings of the same kind and, when similarity is at or above the 0.5 threshold, SHALL return a `similar_found` result with candidates instead of minting a new id — unless `force_new` is set. `title`, `component`, and a valid `severity` SHALL be required.

#### Scenario: kinds use independent id counters and land in pages
- **WHEN** `file_bug` is called with `kind="feature"`
- **THEN** the filing is assigned a `FEAT-NNN` id independent of the BUG counter and is written directly under `pages/` for its kind

#### Scenario: a near-duplicate filing is deflected
- **WHEN** a new filing's title-and-body tokens overlap an existing same-kind filing at or above the 0.5 similarity threshold and `force_new` is not set
- **THEN** the handler returns `similar_found` with the matching candidates and does not mint a new id

#### Scenario: force_new mints a fresh id regardless of similarity
- **WHEN** `file_bug` is called with `force_new=true`
- **THEN** the similarity check is skipped and a new id is always allocated

### Requirement: Compare-and-swap patch and supersede lifecycle
The `patch` action SHALL perform an optimistic compare-and-swap: when `expected_sha256` is supplied it SHALL be compared against the current content hash and a mismatch SHALL return a `conflict` without writing; `old_text` SHALL be required to match the page exactly once (any other count returns a `conflict`); and `dry_run` SHALL default to true, previewing the old/new hashes and `would_write` without mutating the page. The `supersede` action SHALL require `old_page`, `new_draft`, and a `reason`. Protected anchor pages (`index`, `log`, `wiki`) SHALL NOT be deletable.

#### Scenario: stale expected hash is rejected
- **WHEN** `patch` is called with an `expected_sha256` that does not match the page's current content hash
- **THEN** the call returns a `conflict` with the expected and actual hashes and does not write

#### Scenario: non-unique match is rejected
- **WHEN** `patch` is called with `old_text` that occurs zero times or more than once in the page
- **THEN** the call returns a `conflict` reporting the match count and does not write

#### Scenario: dry-run previews without mutating
- **WHEN** `patch` is called with the default `dry_run`
- **THEN** the response reports the old and new hashes and `would_write` and the page on disk is unchanged

### Requirement: Action surface, universe ACL, and scope gating
The `wiki` tool SHALL dispatch exactly the fifteen actions in `WIKI_ACTIONS` (`read`, `search`, `since`, `list`, `lint`, `write`, `patch`, `delete`, `consolidate`, `promote`, `ingest`, `supersede`, `sync_projects`, `file_bug`, `cosign_bug`), returning an unknown-action error listing the available actions otherwise. For a universe-scoped call, the universe ownership/visibility ACL gate SHALL run BEFORE the tree is scaffolded so a denied call has no filesystem side effect, with write actions (the ten in `WIKI_WRITE_ACTIONS`) checked for write access and others for read access; the shared root wiki (no `universe_id`) SHALL NOT be gated here. An auth scope gate SHALL additionally run before the handler executes, returning an `auth_scope_required` error when the caller lacks the action scope.

#### Scenario: unknown action is rejected with the catalog
- **WHEN** the tool is called with an action not in `WIKI_ACTIONS`
- **THEN** it returns an error whose `available_actions` lists the fifteen known actions

#### Scenario: denied universe write creates no directory
- **WHEN** a universe-scoped write action is denied by the ownership ACL gate
- **THEN** the call returns an access error and the target universe's wiki directory is not scaffolded

#### Scenario: root wiki is a shared ungated surface
- **WHEN** an action targets the shared root wiki with no `universe_id`
- **THEN** the universe ownership ACL gate is not applied to it

### Requirement: First-party canon writes bypass the MCP ACL gate; in-node access is read-only
`write_universe_canon` SHALL be a first-party, in-process write into a universe's own wiki that is scoped to that universe by construction and SHALL NOT pass through the MCP ACL gate that authorizes untrusted external callers. In-node MCP access (graph-compiled node source) SHALL expose wiki actions only as READ-ONLY aliases (`read`, `search`, `list`, `since`, `lint`); wiki writes SHALL never be aliased in-node — a node that needs to publish SHALL go through the wiki write-back effector (`tinyassets/effectors/wiki_write_back.py`), which consumes an explicitly branch-declared external-write packet rather than auto-publishing loop output.

#### Scenario: canon write is scoped by construction, no external ACL gate
- **WHEN** the universe intelligence writes its own canon via `write_universe_canon`
- **THEN** the write is routed into that universe's wiki without invoking the external MCP ACL gate

#### Scenario: nodes can read but not write the commons inline
- **WHEN** a compiled node invokes a `wiki.*` action alias
- **THEN** only read-family actions resolve and no write action is reachable inline

#### Scenario: node publication goes through the effector
- **WHEN** a branch needs to publish output back to a wiki page
- **THEN** it declares an external-write packet consumed by the wiki write-back effector rather than writing inline

### Requirement: Similar filings can be cosigned without minting a second id
`cosign_bug` SHALL require a filing id and reporter context, resolve `BUG-`, `FEAT-`, `DESIGN-`, and `PR-` prefixes to their typed page directories, preserve the original filing id and content, append or extend a dated `## Cosigns` section, increment `cosign_count` in frontmatter, and append a wiki log entry. An unknown filing SHALL return an error and write nothing.

#### Scenario: Similar report cosigns the existing filing
- **WHEN** a caller chooses an existing similar filing and supplies `cosign_bug` with reporter context
- **THEN** the existing page retains its id, gains the dated context, and returns the incremented `cosign_count`

#### Scenario: Repeated cosigns increment monotonically
- **WHEN** the same filing is cosigned in later calls
- **THEN** each successful call increments the stored count by one without creating another typed filing

### Requirement: Wiki deletion is dry-run first, hash-guarded, and protects anchors
Wiki deletion SHALL resolve only an exact categorized `pages/<category>/<slug>` or `drafts/<category>/<slug>` path with an optional `.md` extension, or a unique slug; it SHALL reject traversal, ambiguity, and protected anchor pages. It SHALL default to a dry run that returns the current SHA-256 and `would_delete=true`; a real delete SHALL require a non-empty reason, SHALL reject a supplied mismatched expected hash, and SHALL log the exact deletion.

#### Scenario: Default delete is non-mutating
- **WHEN** a caller requests deletion without setting `dry_run=false`
- **THEN** the page remains and the response includes its hash and `would_delete=true`

#### Scenario: Hash mismatch blocks deletion
- **WHEN** a caller supplies an `expected_sha256` different from the current page hash
- **THEN** deletion returns `status=conflict` with expected and actual hashes and leaves the page intact

#### Scenario: Protected anchor cannot be deleted
- **WHEN** a caller targets `index`, `log`, `schema`, or the root wiki document
- **THEN** deletion returns a protected error even if a reason is supplied

### Requirement: Draft consolidation is seed-clustered and best-effort
Wiki consolidation SHALL compare each unmerged draft seed with later unmerged drafts and place every threshold match in that seed's cluster; members therefore need not meet the threshold with one another. It SHALL default to reporting clusters without mutation. In execution mode it SHALL choose the longest-body draft as primary, build content containing the other bodies with source/date markers, attempt to remove each secondary, and only then attempt to write the primary. Secondary unlink and primary-write errors are swallowed and the cluster is still reported as executed. This ordering can lose a removed secondary if the later primary write fails and is an explicit as-built limitation.

#### Scenario: Consolidation defaults to preview
- **WHEN** similar drafts exist and the caller omits `dry_run=false`
- **THEN** the response reports candidate clusters and no draft file changes

#### Scenario: Seed matches need not be pairwise matches
- **WHEN** two later drafts each meet the threshold against the cluster seed but not against one another
- **THEN** consolidation reports all three drafts in the same seed-based cluster

#### Scenario: Execution remains best-effort on filesystem failure
- **WHEN** a secondary unlink or the subsequent primary write raises an `OSError`
- **THEN** consolidation swallows that operation error and still reports the cluster in executed mode, without guaranteeing that every source body remains on disk

### Requirement: Wiki lint reports local and whole-wiki integrity without repairing it
Page-scoped lint SHALL report missing link targets and, for drafts, promotion blockers; for published pages it SHALL also report orphan/index and current metadata-freshness issues. Whole-wiki lint SHALL report orphaned, missing, unindexed, ghost, supersession, stale-confidence, source, and pending-draft conditions, returning `healthy` only when no issue is found. Lint SHALL not mutate pages or the index.

#### Scenario: Draft lint reports promotion blockers only for that page
- **WHEN** a caller lints one draft with missing required metadata or too little body content
- **THEN** the result identifies that page and its promotion blockers without adding unrelated whole-wiki backlog

#### Scenario: Whole-wiki lint exposes graph drift
- **WHEN** published pages contain missing links, orphans, index ghosts, or broken supersession targets
- **THEN** whole-wiki lint returns `issues_found` with the corresponding issue entries and performs no repair

### Requirement: Project sync creates only missing sibling-project pages
`sync_projects` SHALL scan non-hidden sibling directories of the wiki root while excluding `Wiki`, `wiki-mcp`, `.git`, and `node_modules`; it SHALL skip projects already represented by page slug or stored path. For each missing project it SHALL create a typed project page with an auto-discovered description/tags, add it to the projects index, append one sync log when anything was created, and return the created list without overwriting existing project pages.

#### Scenario: Missing sibling project is indexed
- **WHEN** a visible sibling directory has no project page
- **THEN** sync creates a slugged project page, adds the index entry, and includes it in `created`

#### Scenario: Existing project is skipped
- **WHEN** a project page already identifies a sibling by slug or stored path
- **THEN** sync leaves that page unchanged and does not create a duplicate

### Requirement: Default discovery scope separates commons knowledge from coordination history
Wiki search and changed-since feeds SHALL trim scope and resolve an omitted, empty, or whitespace-only value to `discovery`; an exact-read ambient relevance feed SHALL instead resolve that unset scope to the source page's audience class. The core handler SHALL accept only `discovery`, `coordination`, or `all`; any other value SHALL return a structured error naming those values and no page results. Every search, changed-since, and exact-read response with an ambient feed SHALL report the applied scope, and an omitted scope whose audience filtering removes one or more candidates that would otherwise have entered the result set SHALL include a non-fatal scope note explaining intentional coordination/all access for in-process callers. Invalid-scope errors SHALL NOT claim an applied scope, and unchanged list responses SHALL remain unscoped.

Frontmatter `audience: discovery|coordination` SHALL be authoritative. An `audience` key that is absent, empty, or whitespace-only SHALL be treated as unset. A page with an unset audience SHALL classify as coordination when its category is `notes`, `plans`, `bugs`, `feature-requests`, `design-proposals`, or `patch-requests`, and SHALL classify as discovery otherwise, including custom categories and pages with no category component. The audience value SHALL be compared after trimming surrounding whitespace and casefolding; a set value that is still neither `discovery` nor `coordination` SHALL classify as coordination and SHALL NOT fall back to a discovery category.

Audience scope SHALL remain a relevance boundary, not access control. The existing universe ACL gate and page-listing visibility requirements SHALL be evaluated before audience and category filtering, and `all` SHALL NOT bypass them. Exact page-body reads SHALL remain addressable and unfiltered by audience; list behavior SHALL remain unchanged; no page SHALL be moved, rewritten, deleted, or migrated.

#### Scenario: default onboarding search excludes coordination history
- **WHEN** a caller searches a mixed visible corpus without supplying scope
- **THEN** the response contains a non-empty discovery result set
- **AND** untagged notes, plans, bugs, feature requests, design proposals, and patch requests are absent

#### Scenario: changed-since defaults to discovery
- **WHEN** a caller requests changed pages from a mixed visible corpus without supplying scope
- **THEN** the response reports applied scope `discovery`
- **AND** contains discovery-classified results only

#### Scenario: explicit coordination returns preserved history
- **WHEN** an in-process caller searches with `scope=coordination`
- **THEN** the response contains coordination-classified results at their unchanged paths
- **AND** excludes discovery-classified results

#### Scenario: explicit all returns both audience classes
- **WHEN** an in-process caller searches with `scope=all`
- **THEN** the response contains both discovery and coordination results that pass existing authority checks

#### Scenario: invalid scope fails without results
- **WHEN** a caller supplies a scope other than discovery, coordination, or all
- **THEN** the handler returns a structured error naming all three valid scopes
- **AND** returns no page results

#### Scenario: explicit discovery metadata overrides a coordination category
- **WHEN** a page under `pages/plans/` declares `audience: discovery`
- **THEN** the page is eligible for default discovery retrieval

#### Scenario: explicit coordination metadata overrides a discovery category
- **WHEN** a page under `pages/workflows/` declares `audience: coordination`
- **THEN** the page is excluded from default discovery retrieval

#### Scenario: unrecognized audience fails toward coordination
- **WHEN** a page in a discovery category declares a non-empty unsupported audience value
- **THEN** the page classifies as coordination without falling back to its category

#### Scenario: audience is trimmed and case-insensitive
- **WHEN** a page under `pages/plans/` declares a padded, mixed-case `audience: Discovery `
- **THEN** it normalizes to `discovery` and is eligible for default discovery retrieval

#### Scenario: custom and category-less pages default to discovery
- **WHEN** a page with no audience lives in a custom category or directly under the pages or drafts root
- **THEN** it classifies as discovery

#### Scenario: exact coordination read keeps coordination recommendations
- **WHEN** a caller exactly reads a coordination-classified page without supplying scope
- **THEN** the requested page body is returned unchanged
- **AND** its ambient feed applies coordination scope and may return visible coordination siblings

#### Scenario: exact discovery read excludes coordination recommendations
- **WHEN** a caller exactly reads a discovery-classified page without supplying scope
- **THEN** the requested page body is returned unchanged
- **AND** its ambient feed applies discovery scope and excludes coordination siblings

#### Scenario: unrecognized source audience drives coordination ambient scope
- **WHEN** a caller exactly reads a source page with a set but unrecognized audience value and omits scope
- **THEN** the requested page body is returned unchanged
- **AND** its ambient feed applies coordination scope rather than falling back to the source category

#### Scenario: authority denial wins under every audience scope
- **WHEN** an existing universe ACL or page-listing visibility rule denies an ambient, search, or feed candidate
- **THEN** discovery, coordination, and all omit its path, title, excerpt, body, and metadata before relevance scoring

#### Scenario: default filtering is self-auditing
- **WHEN** omitted scope filters one or more otherwise-visible candidates
- **THEN** the response reports its applied scope and a non-fatal scope note
- **AND** an explicit scope response reports its applied scope without claiming privacy

#### Scenario: list remains an unscoped inspection surface
- **WHEN** an in-process caller lists the wiki
- **THEN** the existing visibility-filtered page and draft listing behavior is unchanged

#### Scenario: concurrent defaults are request-local and deterministic
- **WHEN** 256 default discovery searches execute concurrently against a fixed mixed corpus
- **THEN** every response is byte-identical to the non-empty single-threaded reference and excludes coordination paths
- **AND** the proof claims only request-local single-process determinism while the full-platform §14 load suite remains open

### Requirement: Reserved canary authority writes one fixed draft

The wiki subsystem SHALL route dedicated canary authority only to
`drafts/notes/uptime-probe.md`, SHALL reject any non-exact `write_page` argument
shape at the tool boundary, and SHALL NOT redirect that authority to a promoted
page, patch, filing, universe page, custom category, or alternate filename.

#### Scenario: Reserved draft is written
- **WHEN** dedicated canary authority reaches `write_page` with category
  `notes`, filename `uptime-probe`, content, and `dry_run=false`
- **THEN** the server writes exactly `drafts/notes/uptime-probe.md`
- **AND** the response reports that exact relative path

#### Scenario: Scope mutation cannot reach an adjacent path
- **WHEN** any routing argument differs from the reserved full-write shape
- **THEN** the dedicated authority is not accepted
- **AND** no wiki path is mutated under that authority
