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
