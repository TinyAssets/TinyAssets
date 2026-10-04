---
severity: P2
title: A wiki page alias redirects a write the authority and consent checks approved
filed: '2026-10-03'
summary: 'wiki_write_back resolves the requested destination with .resolve() and accepts it as long as the result is still inside the wiki root, but the soul-effect authority check and the consent check both read the REQUESTED path. So a link at pages/<cat>/allowed.md pointing to another page inside the same wiki is approved as allowed.md and written to the other page. The link-free writer is not bypassed and nothing leaves the wiki; what moves is which page an approved write lands on.'
---

# A wiki page alias redirects a write the authority and consent checks approved

**Filed:** 2026-10-03, from the cross-family review of PR #4291 (the link-free
leftovers), which raised it as a P2 outside that PR's subject.
**Verified:** 2026-10-03 by reading the code at `e28620ebe`. Not reproduced on a
filesystem — the reasoning is from the call order, and the test that would catch it
does not exist (see below).
**Severity:** P2. Confined to one wiki: the destination must still resolve inside the
wiki root, so this is not a cross-command-center or cross-user issue. What it breaks is
that an approval names one page and a different page is written.

## What is true

`tinyassets/effectors/wiki_write_back.py`:

- `_resolve_target_page` validates the shape of `requested` (must be
  `pages/...` or `drafts/...`, must end `.md`, at least three components, lines
  ~118-127), then does `candidate = (wiki_root / requested).resolve()` (128) and accepts
  it when `candidate.relative_to(root)` succeeds (131). **`.resolve()` follows links.** A
  link at `pages/a/allowed.md` → `pages/b/other.md` resolves to a path still inside the
  wiki, so it passes.
- The two gates read the **requested** string, not the resolved page:
  `resolve_soul_effect_authority(..., destination, ...)` (~430) and
  `_check_consent(universe_dir, destination)` (~445).
- The section update then writes the resolved path, and by that point it is an ordinary
  path with no link left in it, so the link-free writer (`write_data_path`, ~605) sees
  nothing wrong and correctly does not refuse.

So authority and consent are evaluated for `allowed.md` and the bytes land on
`other.md`. Each component in isolation behaves as designed; the gap is that two of them
are looking at different strings.

## Why the existing test does not catch it

`tests/test_link_free_leftovers.py`'s wiki case plants a link at the *parent* and drives
the writer directly, so it never goes through `_resolve_target_page` — which is where
the aliasing happens. A test that exercises the real entry point with a link at the leaf
is what would have caught this.

## Minimal fix

1. Resolve **once**, then gate on the resolved page. `_resolve_target_page` returns the
   path that will actually be written; `resolve_soul_effect_authority` and
   `_check_consent` should both be given that, not the caller's string. One resolution,
   one subject, every check agreeing on it.
2. Or refuse an aliased page outright: after validating the shape, require that the leaf
   is not a link (`lstat` and reject `S_ISLNK`). Simpler, and consistent with how the
   rest of the daemon now treats a planted name — but it forbids a legitimate alias, if
   wiki aliases are a feature anyone wants.
3. Either way, a test through the real entry point: plant a link at
   `pages/<cat>/allowed.md` → `pages/<cat>/other.md`, request `allowed.md` with
   authority and consent for it, and assert `other.md` is unchanged.

(1) is the better shape — it keeps every check on one subject rather than adding a
second rule. (2) is the smaller change if aliases are not wanted at all; that is a
product question.

## How to resolve

Land one of the two with the test from step 3, and delete this file.

## Related

- `2026-09-29-tool-jail-binds-by-pathname.md` and
  `2026-10-01-tool-jail-bind-sources-are-paths.md` — the same shape one layer out: a
  check performed on a name, and the real operation performed on what the name resolved
  to.
- PR #4291 closed the remaining link-following reads, writes and locks in the daemon.
  This one is not in that class: no link survives into the write, and the writer is not
  bypassed. It is an authority-subject mismatch, which is why it is filed rather than
  folded there.
