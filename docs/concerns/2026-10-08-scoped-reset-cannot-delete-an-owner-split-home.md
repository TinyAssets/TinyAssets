---
severity: medium
title: An offline scoped reset cannot delete an owner-split home
filed: '2026-10-08'
summary: scoped_reset runs as an operator CLI without the bounded launcher, so after the cutover it refuses every admitted (D60-labelled) home instead of running the two-pass deletion
---

# An offline scoped reset cannot delete an owner-split home

After the per-owner isolation cutover, every center root is labelled
`1001:<owner gid>`, and its owner's entries can be removed only by the
owner-delete cell (pass one) and then the daemon pass, followed by the admission
retire. All three need the bounded launcher client and the broker's owner
channel, and only the daemon holds them (the PID1 bootstrap installs them).

`tinyassets/scoped_reset.py` is an operator CLI. It renames the home into
`.scoped-reset-staging/` and then removes it with `shutil.rmtree`. As uid 1001
it can do neither for owner entries: a directory cannot be moved to another
parent without write permission on the directory itself, and nested owner trees
are closed to it. So `_walk_home_without_following` now blocks an admitted home
outright (gid 300001–399999) and never half-deletes one. Homes that are not
admitted still reset as before.

Fix: run the reset's filesystem phase inside the daemon. Expose it as a
founder/operator-only daemon action that calls
`role_owner_tree_deletion.delete_center` for the home before the row commit.
Alternatively, retire scoped reset in favour of account deletion with the
identity kept.
