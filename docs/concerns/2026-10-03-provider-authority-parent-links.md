---
severity: P1
title: Provider authority records still resolve parent links
filed: '2026-10-03'
summary: 'Authority migration uses ordinary parent-path resolution after an absent assignment-file check. Existing hidden-directory masking does not prove parent provisioning before every launch. Static validation gap; production reachability is not established.'
---

# Provider authority records still resolve parent links

Inherited from main `6004a6364b9a6fb3fbff4c9e7719bb5c00e697b7` during
#4254 integration. The initial raw-I/O inventory includes these existing sites;
it does not certify them as link-safe.

`provider_authority._read` checks `lexists(assignment.json)` before rejecting a
linked parent. If `.provider-authority` is a link and its target lacks the file,
the read returns `None`; migration can then use ordinary `mkdir` and `mkstemp`
inside the linked directory. Reads, temporary-file cleanup, and replacement all
depend on a trustworthy parent. Existing hidden entries are masked by the
provider view, but it does not pre-create this directory, and failed migration
may fall back to usable in-memory authority. Production reachability has not
been demonstrated; the filesystem validation gap is visible in the code.

A separate fix needs descriptor-anchored, no-follow reads, writes, cleanup and
publication, with linked-parent regressions and Linux proof. Preserve migration's
fully written temporary file followed by exclusive atomic `os.link` publication:
direct exclusive creation exposes the destination before the write completes.
Also verify parent provisioning before every provider launch. Do not mark this
resolved solely because the initial inventory or ordinary migration tests pass.
