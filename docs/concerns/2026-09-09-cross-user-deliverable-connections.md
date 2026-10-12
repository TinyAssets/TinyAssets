---
severity: note
title: Cross-user deliverable connections
filed: '2026-09-09'
summary: structured delivery candidate awaits release proof; full artifact transfer remains open
---

# Cross-user node-to-node deliverable connections

**Filed:** 2026-09-09
**Verified:** rendered owner conversation through the Chrome extension at about
08:12 UTC in a newly opened authorized app tab; not a live messaging test or
independent verification of the app agent's repository claims.

## Source and conversation direction

The conversation moved from marketing/tester readiness, through the question of
feedback intake, to replacing bespoke issue reporting with general collaboration.
Reading only the 00:32 PDT inability-to-file reply produced the wrong patch.

Owner, 00:41 PDT (appears twice in saved history):

> That patch request system is now depercated old spagetee code from old implimintations of the idea. Needs to be removed from the code. The new system will be a more general user to user capability. Universes should be able to build nodes that accept messages from other universes. What is currently available for user to user messaging or universe to universe or one persones workflow to another persons workflow?

Owner, 00:42 PDT:

> Just like nodes within a universe can pass any deliverable to another node, users should be able to biuld nodes that can intake desired deliverables from desired other users, how ever they biuld the node and how ever other users deside to like to it

Owner, 00:50 PDT:

> Identify tbe gaps  between what you can currently do and the general collaboration capability

## App's reported gaps — reverify against code before implementation

1. A receiver node users can expose and manage; inbound webhook deployment and
   usable create/revoke controls are unverified.
2. A stable receiving address and cross-owner output link; public workflow reuse
   executes in the sender's universe and does not establish a receiver inbox.
3. Verified sender identity, receiver-controlled permissions and revocation.
4. Advertised/validated deliverable contracts, including cross-owner access to
   files and artifact references.
5. Accepted delivery enters receiver-defined behavior under receiver authority
   and resource limits.
6. Receipts, rejection reasons, retries and duplicate protection.
7. Untrusted-content and authority isolation throughout delivery.
8. Expose/inspect/connect/disconnect/track controls available to the app agent.

The app explicitly said no current connection establishes a verified receiving
node in another user's universe. Its 00:50 table relies partly on earlier source
inspection, not fresh repository verification. Its suggested webhook foundation
is a candidate, not approval to treat a webhook-only implementation as completion.

## Acceptance and disposition

September 9 follow-up: source checks, a local synthetic intake probe and an
independent Claude review confirm useful existing receiver execution/revocation
and sender receipt pieces, but not the whole collaboration connection. The
webhook success response lacks an outcome identifier, its bearer does not verify
the sender, and authoring file handles remain owner/session scoped. A separate
test-order failure was reproduced without runtime changes and repaired in the
test fixture; combined ownership tests now pass, without relaxing runtime ACLs.
Evidence: `2026-09-09-cross-user-delivery-existing-boundaries.md (archived in git)`.

One user exposes a node accepting a chosen deliverable from a selected user;
the sender links an output and sends; the receiving workflow processes it; both
sides inspect the outcome. Unauthorized sender, invalid deliverable, duplicate
and revoked link each have defined observed results. Include artifact access,
receiver-owned execution and plain app requests, not operator workflow editing.

Deprecating/removing old patch-request code is a separate inventory-backed cleanup
that must preserve existing user records and unrelated wiki capabilities.
Do not expose or extend that system as the fix. Issue intake is a possible
user-built workflow on the general connection, not a platform-specialized end state.

`serve-platform-reports` was a premature, unimplemented proposal based on stale
context. It is superseded. No reporting runtime change, peer review, PR, deployment
or app write was made. The active goal's high-level stage table and requirements
are in `openspec/changes/consolidate-platform-resource-policy/goal-completion-contract.md`.

Implementation proposal: `openspec/changes/connect-cross-user-nodes/` (September 9).
It includes selected-node execution, explicit artifact copies and durable
occurrence/attempt identity. First independent shape review returned ADAPT;
the revised design no longer assumes queued rows are a durable worker queue or
that runtime file handles already exist. Concrete recovery/file-binding seams
were resolved into explicit implementation interfaces. Selected-node projection
is locally implemented/tested; receiver/link/delivery/file integration is still
unfinished. See `2026-09-09-cross-user-node-shape-review.md (archived in git)`.

Fresh rendered read at about 08:36 UTC includes a 01:33 PDT owner request:
"Lets work on that go to market stratagy". The agent proposed agency/client
handoffs, a possible link-based submission route and pricing hypotheses, while
explicitly saying they are unverified proposals. These are not owner approval
to narrow the general capability, enable anonymous delivery or decide pricing.
No app message was sent during this read.

The next fresh extension read also recovered the owner's broader strategy:
dogfood as the first power user/cofounder, do useful startup work, test and publish
reusable workflows to the Commons, then invite relevant users. The agency example
is included but not the whole strategy. This reinforces the general-capability
scope; it is not permission for the operator to build the startup's private
marketing workflows or publish unverified showcase claims. No app input sent.

Initial retirement inventory (source only): `universe_server.write_page(kind=...)`
still routes to `api/wiki._wiki_file_bug`, legacy descriptions still recommend
filing, and wiki filed-page canonicalization preserves existing report filenames.
Additional references occur in market, prompts, universe, daemon registry,
bug_investigation, wiki trigger receipts, CLI and generated plugin runtime.
Removing every string match would destroy unrelated page-read/update behavior;
separate retired creation/automation from retained user records before removal.
