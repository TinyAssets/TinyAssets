# Shared installation ownership before the first install

Step 19 defers the shared app's install flow. A provider response identifies a workspace
or enterprise. Two authorized administrators can name the same installation from different
TinyAssets accounts. Before enabling installs, establish which component owns that single
assignment and how it retires one binding without disturbing a later assignment.

## Existing behavior

The old broker's `tinyassets/broker/inbound_installations.py` keys installations by app,
kind and provider ID. It refuses overlapping kinds for the same app and ID. Under one
SQLite write transaction, a reconnect rotates the binding and revision. Another owner's
existing live credential keeps its assignment, unknown status defers, and confirmed
revocation permits replacement. Retirement requires the matching owner and binding and
proof of revocation. Erasure removes only the erased owner's rows.

The new doors derive one `i:` directory key for each app, kind and provider identity in
`platform/doors/src/inbound.ts`. The entry holds an owner, binding and revision.
`platform/owner/src/inbound.ts` checks route uniqueness inside one owner's database.
A binding's own revision can reject an old delivery. Publication and removal in
`platform/doors/src/directory.ts` take effect by directory key. Revoke and
`platform/doors/src/erase.ts` use that removal. A delayed cleanup for an old binding
therefore has no way to distinguish a replacement at that key. Today ordinary shared
creation always refuses with `install_proof_required`.

## Acceptance before activation

Trace the current assignment through consent, publication, reconnect, uncertain or
revoked credentials, overlapping coverage, revoke and owner erasure. Establish where
competing installs serialize. Demonstrate that a retry after either write can finish,
a stale publish or cleanup leaves a later assignment intact, and fresh delivery goes
only to the current binding. Simultaneous and interrupted operations need independent
evidence. The choice of storage and protocol remains open.

At cutover, `platform/import/rules.ts` explicitly drops both old shared bindings and
the broker installation store. A returning owner can reinstall only after Step 19. The
receiver and its signing secret alone do not provide continuity. This review examined
no live installation counts and makes no claim about affected customers.

At main `f934871ad`, the three existing installation-authority tests for same-session
identity, conflicts and retirement in `tests/test_inbound_door.py` passed against local
SQLite. The cited runtime and import sources also matched combined tree `533733c40`.
These tests establish the historical behavior. The new install flow remains open.
