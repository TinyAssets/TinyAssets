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
  Forbidden; no oracle pass exists.
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

This does not yet supply the owner HTTP door, schema-aware structured config,
roster, the complete runnable folder, contact-by-contact private inclusion,
wiki writer integration or hosted activation. The raw arbitrary-file scanner
also conservatively excludes canonical records containing generated hashes;
schema-aware serialization must resolve that without allowing secrets through.
The quarantine caller must resolve its root outside every agent box. There is
no production caller yet. These limitations must be resolved before cutover;
the passing ingestion tests are not evidence of a shipped export feature.

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
