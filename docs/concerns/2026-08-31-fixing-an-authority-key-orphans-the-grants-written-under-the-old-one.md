---
severity: P1
title: Fixing an authority key orphans every grant written under the old one
filed: '2026-08-31'
summary: hit live, blocking the founder's universe mid-run with `missing_consent`; a correctness fix to a key is a silent migration
---

# Fixing an authority key silently orphans every grant written under the old one

**Hit live 2026-08-31**, blocking the founder's universe mid-run:

```
external write failed - checkout_repo/workspace:
  no active workspace_checkout consent for
  checkout:http_7931…:api.github.com/jonnyton/tinyassets   [missing_consent]
```

The consent was there. It was written as:

```
checkout:http_7931…:github.com/jonnyton/tinyassets
```

Same universe, same connection, same repository, same owner, same grant —
`github.com` versus `api.github.com`.

## How it happened

`workspace_consent_destination` used to default `host` to `github.com` while
the sink passed the host it derived from the connection. That was fixed:
`host` became required and both sides now take it from
`connection_git_host()`, which reads the connection's declared endpoints.

The fix was right. The migration was missing. The founder's consent had been
granted under the old spelling, so after the fix the effector looked up a key
nobody had ever written, and reported it as **"no consent"** — which reads to
the user as *you never granted this*, not as *we changed where we look*.

This is the second time this exact hazard has been named and the first time it
has been paid. Codex raised it against the (rejected) repo-casing change on the
same day — *"a pre-change consent destination is silently orphaned: the new
lookup builds a different key while SQLite matches `destination` exactly"* —
and it was already true, in production, for a key that had shipped weeks
earlier.

## The rule this establishes

**Changing the shape of an authority key is a migration, not an edit.** The
grants are user decisions; a key change strands them silently, and it fails in
the most misleading possible way — as an authorization refusal against a user
who did authorize it.

Any future change to `workspace_consent_destination`, `format_git_scope`, or
anything else that builds a stored authority key must ship with one of:

1. a migration that rewrites existing rows to the new spelling; or
2. a read path that accepts the old spelling and rewrites on use; or
3. an explicit, deliberate re-consent flow — with the user told *why* they are
   being asked again, rather than being told they never consented.

Silence is the one option that is not available. And the checklist is not
"consents" — it is **every store keyed on that value**: consents, git scopes,
lease and generation keys, request-rail dedupe and suppression keys.

## What unblocked it here

Option 3, by accident rather than design: the universe raised a rail request
and the owner approved, which wrote the consent at the current key. That worked
because the agent behaved well — it reported the exact refusal and asked. A
less careful agent, or a user who did not recognise the message, would have
concluded the feature was broken.

## Related

* `docs/concerns/2026-08-31-one-repository-two-authority-keys-by-capitalisation.md`
  — the same family (one repository, two keys), still open, and the reason the
  casing fix was rejected rather than shipped.
* `openspec/changes/archive/2026-10-06-script-authoring-surface/` — the shape argument. This is
  gate five in a day; every one has been a defect in a hand-written description
  of what the code was going to do.
