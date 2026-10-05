## Context

Publish approvals return a definition ID to the browser but resolve the request
without retaining it in the answer the agent reads. The preview renderer uses a
private library row; publication needs the immutable public component instead.

## Goals / Non-Goals

Expose successful publication facts and a public-only picture. Keep all posting
behaviour in editable skills. No platform-specific posting code or automatic post.

## Decisions

Use a `completion` object in publish responses and `answer.completion` in resolved
publish requests. Persist metadata in the publish pin before rendering; enrich
the picture only after the claim is durably finished. Reconstruct missing
completion metadata when resolving older completed pins. Reuse the preview's component-to-render-spec conversion and
isolated renderer with empty bridge data and a synthetic command-center identity.
Never render the live private UI for a publication. Preview failure is explicit
and cannot undo an already successful publication. Package versions and verified
release links identify updates; other immutable definitions use their fingerprint
as version and describe a new immutable listing, including on idempotent replay.
No listing-specific public web route exists today, so `share_url` is
null rather than an invented link. Direct owner publication returns metadata and
renders when an owner command-center context exists.

Seed `skills/share-after-publish/SKILL.md` exclusively on creation, and expose the
same text through the handbook for existing owners. The skill offers a draft and
picture, asks explicit approval, and uses existing connection and approval tools.

## Risks / Trade-offs

- Rendering can fail or be busy: preserve success, return an explicit preview status.
- A private UI changes after publishing: only read the public immutable definition.
- Publishing has already happened when preview runs: never report it as unpublished.


## Approval latency correction (merge-group 37266089374)

The original post-commit enrichment still ran synchronously inside approval.
Existing publication fixtures rose from 0.5-1s to 9-13s and owners waited for
Chromium after tapping Approve. Publication approval now commits the pin and
resolves the request with `preview_status=pending`, then starts background work.
Approval never waits for the picture. Direct explicit publication/preview calls
retain their existing rendering contract; this correction moves the approval path.

A nonblocking single-worker admission bounds background work without an executor
queue. It reuses the renderer's existing process/host one-render-at-a-time limit
and supervised wall, process and memory bounds. Busy admission or render failure
updates the completion to `unavailable`, with no image and no publication rollback.
The worker captures the authenticated owner's destination and immutable published
screen before dispatch, never a private UI row. The render retains empty bridge
data, synthetic identity and refusal of private asset references.

After rendering, a transactional update matches the answered publish request's
listing ID and pending status, replaces only its completion, and emits the existing
pending-request completion event and activity wake. Readers find `ready` and the
image path in the same owner-scoped `answer.completion`. No additional approval or
external message is introduced. A failed request resolution starts no renderer;
a retry reconstructs metadata from the completed pin without republishing.
Background work is best effort within the serving process; a process exit can
leave pending metadata, and an explicit owner preview remains available.
The editable share-offer skill explains that the picture may take a moment.
