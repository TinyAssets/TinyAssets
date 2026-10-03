# Repair publishing and copying a command center

Jonathan's 2026-10-03 request is the complete product flow: publish a design,
find it, and install a working independent copy. The related feature group is
the existing picker (#4358 plus #4390), truthful publication/approval, and
bounded publication reads. Already-landed privacy and malformed-UI protections
remain prerequisites, not changes to undo. The consent-store migration and
preview-tooling trial are separate.

## Contract

- The primary command-center recipe explicitly selects `publish_kind:
  command_center`, the screen, workflows, and a `package` object. A missing
  package is an error for this explicit intent, never an implicit export of
  files. `workflows` means workflow-only; legacy component bundles remain
  readable/copyable and are never mislabeled as file packages.
- The platform preview names the publication kind, actual workflow step counts,
  selected screen/triggers, and the existing exact included/excluded file list.
  The digest and owner-confirmed protected pin remain authoritative. Existing
  approvals are never upgraded into broader exports.
- Confirmation/discovery returns metadata before potentially large UI bodies.
  A supported bounded component read can reconstruct the complete component.
- A single healthy package is discoverable. Good and malformed existing screen
  entries, saved choices and all account/frame/home/epoch fences survive.
- A copied screen must address the recipient's copied workflow through a
  supported structured reference. Arbitrary script text is not rewritten and
  no extra UI execution authority is introduced. A synthetic two-owner
  regression establishes the reference failure before the repair is chosen.

## Evidence and integration

Use synthetic owners and stores only. Prove selected content, nonempty copied
graphs, recipient authority and independent edits; paused copied automations;
unchanged source and pre-existing recipient data; all current privacy
exclusions; cancellation, stale previews, retries and failures without false
success or duplicate activation. Test zero/one/two catalogue rows and large UI
component retrieval. Keep test-retirement changes separately reviewed where
the hygiene gate requires them. Bind combined browser/Node/CI evidence and the
independent adversarial review to the final candidate before normal protected
integration. No real-user publication, installation, approval or workflow run.

## Portable bindings

`workflow_refs` is an optional UI map (at most 100 aliases, ASCII letter then
up to 63 letters/digits/dashes/underscores; nonempty trimmed workflow IDs of at
most 200 UTF-16 units). `whoami()` returns a copy from the active bundle only.
Publication maps selected owned branch IDs to component keys; installation
validates all keys before effects and maps them to the recipient's private
copies. Arbitrary script bytes are unchanged. An explicit new command-center
ask refuses recognizable selected workflow ID literals in script and explains
how to use bindings; this is an authoring diagnostic, not a proof that arbitrary
JavaScript is portable. Existing named events remain owner broadcasts, not an
exclusive run-by-ID capability. Legacy approvals are not expanded.
