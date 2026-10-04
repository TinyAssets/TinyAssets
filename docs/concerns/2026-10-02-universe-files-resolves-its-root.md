---
severity: P2
title: universe_files resolves its root, so its no-follow guarantee holds only below it
filed: '2026-10-02'
summary: '_parent_dir_fd opens its no-follow walk at root.resolve(strict=False), so any link on the universe-dir path is replaced by its target before the walk can refuse it. Reproduced on Linux: write_universe_file through a linked ancestor writes the link target. The module docstring claims no component may be a link, which is true only for relpath components. No caller was found passing an unresolved attacker-influenced root, so this is an overstated guarantee rather than a demonstrated cross-user path.'
---

# `universe_files` resolves its root, so "no component may be a link" is only true below it

Found 2026-10-02 while routing `harness_history._replace` through the one
universe writer (PR #4342). Raised by a Codex cross-family review and then
reproduced directly.

## The claim that is overstated

`tinyassets/universe_files.py`'s module docstring says the daemon "resolves
every path component with `O_NOFOLLOW` (no component may be a link)". That is
true for the components of `relpath`, and false for the universe root itself.

`_parent_dir_fd` opens the walk at `fs.open_dir_nofollow(root.resolve(strict=False))`.
`workspace_fs.open_dir_nofollow` walks from `/` and refuses a link at *every*
component -- but `root.resolve(strict=False)` has already replaced any link on
the root path with its target, so there is nothing left for the walk to refuse
there. The guarantee is link-free traversal *below a trusted, resolved root*.

## Reproduction (Linux, 2026-10-02)

With `base/real/u1/AGENTS.md` holding `original` and `base/link -> base/real`:

- `fs.open_dir_nofollow(base/"link"/"u1")` raises `UnsafePoolPath`.
- `universe_files.write_universe_file(base/"link"/"u1", "AGENTS.md", b"new")`
  succeeds, and `base/real/u1/AGENTS.md` becomes `new`.

So a caller that reaches a universe through a linked ancestor writes to the
link's target, and the write is not refused.

## Why it is not filed as a live vulnerability

No caller was found that passes an unresolved, attacker-influenced root. The
HTTP path resolves the universe dir before it gets here
(`tinyassets/api/helpers.py`), and a provider jail does not expose the host
parent of a universe for replacement. The reviewer and this author agree the
accurate description is a narrower guarantee, not a demonstrated cross-user
path.

It still matters because the docstring is what every caller trusts. A future
caller that derives a universe dir from a less trusted source -- a restore, a
migration, a path read out of a database -- would read "no component may be a
link" and skip a check it actually needs.

## What would resolve this

Either of:

1. Drop `.resolve(strict=False)` from `_parent_dir_fd` (and the read path's
   equivalent) so the no-follow walk covers the root too. Needs a sweep of
   callers first: `open_dir_nofollow` requires an absolute path, so any caller
   passing a relative or non-canonical universe dir would start refusing.
2. Keep resolving, and correct the module docstring to say the guarantee is
   link-free traversal below a resolved root -- then make the root's
   trustworthiness an explicit precondition each caller has to meet.

Until then, `harness_history._replace` checks its own root with
`fs.open_dir_nofollow` before delegating, so routing it to the shared writer
did not lose the refusal it had when it did the I/O itself
(`tests/test_harness_history.py::test_a_link_at_or_above_the_universe_root_is_refused`).
Other callers of `universe_files` were not audited for whether they need the
same check.
