---
severity: P1
title: Other consent-bearing requests still accept bearer answers
filed: '2026-10-05'
summary: PR 4477 protects grant_patch_intake; other authority-bearing answer branches lack interactive owner proof.
---

PR #4477 round 2 fixes `grant_patch_intake` through the protected owner-session
route. The shared executor in `tinyassets/api/pending_requests.py` still permits
ordinary bearer answers for these exact action kinds:

- `grant_workspace_consent`: writes typed repository effector consents.
- `bind_model_access`: confirms model/provider access against deposited authority.
- `publish`: publishes a pinned command-center snapshot.
- `install`: installs a pinned package after the requested owner confirmation.
- `extend_http`: extends an existing connection's endpoint/scope grant.
- `connect` and `connect_http`: deposit and authorize connection uses; some paths
  reuse saved sign-in authority rather than asking for a new secret.
- `rotate_http`: replaces a connection credential.
- `remove_http`: removes an existing connection incarnation.

All dispatch through `write_graph(target="connection", operation="answer_request")`
to `pending_requests.answer_request`. Its admin ACL check proves the principal,
not an interactive owner. The first five are explicit consent/approval paths;
the remaining connection paths are listed because the same authority distinction
applies even where fields or provider validation impose additional checks.
This is a source audit, not a claim that every branch was exploit-probed.
`approve_action` already refuses bearer answers. Plain `answer` has no grant;
proposal `start_activity` currently refuses execution as unavailable.

The lead owns PR #4477. Broader migration is outside its patch-intake recovery
scope: each affected rail flow and its consumer tests must move together to
protected interactive owner proof. Do not treat this patch as closing the
remaining consent routes. Verify bearer refusal, protected approval, stale
request handling and owner isolation for each kind before resolving this file.
