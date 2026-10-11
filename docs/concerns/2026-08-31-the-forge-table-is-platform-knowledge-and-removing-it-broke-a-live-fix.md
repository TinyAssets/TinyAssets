---
severity: P2
title: The forge table is platform knowledge, and deleting it re-broke a live fix
filed: '2026-08-31'
summary: 'I removed #2753 FORGE_GIT_HOSTS and inverted its regression test, reopening the GitHub checkout 403; restored. The agnosticism argument is real and needs a migration, not a deletion'
---

# The forge table is platform knowledge — and deleting it re-broke a live fix

**Filed:** 2026-08-31
**Severity:** P2 — the code is back to the working state; what is unresolved is
the design argument, and the fact that a "purer shape" silently un-fixed a live
outage.

## What happened

`FORGE_GIT_HOSTS = {"api.github.com": "github.com"}` landed in #2753 to fix a
live failure: the founder's GitHub connection declares ten endpoints, every one
on the API host, so the derived git host was `api.github.com` and the clone
became `https://api.github.com/owner/name.git`, which GitHub answers **403**. The
same wrong value went into the consent key, so a perfectly good `github.com`
consent read as missing.

Hours later, on `claude/labeled-credential-fields`, I **deleted that table** —
and rewrote #2753's own regression test (`test_git_host_is_not_the_api_host.py`)
to assert the opposite. Caught by a Codex review before it landed
(`2026-08-31-codex-labelled-credential-fields.md (archived in git)`, R3), restored
from `main`.

## The argument I made, which is not worthless

A table of forges is platform-specific knowledge, and the founder's acceptance
test is explicit:

> "if we test anything else like another outside connection and another task we
> shouldnt have to do another patch"

Every forge that serves git and its API on different hosts needs a new row. That
is the platform being shaped like the services it has met, which is the thing
this project keeps ruling out.

## Why it was still wrong to act on it

1. **It reverted a deployed fix with no migration.** The founder's existing
   connection has git scopes on `api.github.com` endpoints. Under the deletion
   it derives `api.github.com` as the git host and 403s — the exact failure
   #2753 fixed, reintroduced in a PR about credentials.
2. **The replacement was a claim, not a path.** "A forge whose git lives apart
   from its API is simply two connections" is fine for a connection nobody has
   made yet. It is not a plan for the one that exists, and nothing told the
   owner to split it.
3. **I rewrote the test that pinned the fix.** That is the strongest available
   signal that a change is wrong: the test existed *because* someone had already
   paid for this lesson.

## What would actually resolve it

Not the table's deletion. Options, none chosen:

* **Let the connection declare its git host.** A `git_host` field on the
  deposit, defaulted from the endpoints, overridable by the owner's agent from
  what it read on the service's docs. The knowledge moves from the platform to
  the connection, which is the agnostic direction — and it needs a migration for
  rows that predate it.
* **Derive it from the scope, not the endpoints.** `git_write:owner/name` on a
  connection whose endpoints are `api.github.com` is already saying "git, on
  this forge"; the forge's git host could be asked for at scope-grant time.
* **Keep the table and bound it.** Explicitly one row, documented as a
  compatibility shim with an expiry condition: it goes when connections can
  declare a git host.

## The transferable lesson

**A shape argument is a reason to propose a migration, never a reason to silently
un-fix an outage.** And `git diff --name-only origin/main...HEAD` after a merge
is not a safety check — I ran it, saw this file in the list, and read it as "mine"
without asking what changed inside it. The file list tells you where to look; it
never tells you what happened.
