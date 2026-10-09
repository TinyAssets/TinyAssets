# A center admitted after the cutover has nothing its owner may write

Found 2026-10-08 by `scripts/role_image_oracle.py` booting the cutover image on
a PR1-migrated volume (task 2.5). Release-blocking for task 2.4: the first tool
preparation of every command center created after the window fails.

## What happens

`role_center_admission.admit_center` publishes the root with the canonical
label, `1001:<machine> 0750`, whose ACL is
`user::rwx user:<machine>:r-x user:1002:--x mask::r-x` — the owner gets **r-x**,
not write. `_seed_entries` then creates exactly one entry, `previews`, as
`1001:1001 0700`.

So a freshly admitted center is:

```
drwxr-x---+  3 tinyassets     300004  u-dana
drwx------   2 tinyassets tinyassets  u-dana/previews
```

A center the migration relabelled is different:

```
drwxr-x---+  8 tinyassets     300002  u-bob
drwxr-xr-x+  2     300002     300002  u-bob/.agent-workspace
drwx------+  2     300002     300002  u-bob/previews
```

`universe_tools` → `provider_jail.ensure_agent_workspace` → `role_tools.prepare`
runs `role_tools.maintain` **inside the owner's tool-files cell**, and that does

```python
os.mkdir(name, 0o770, dir_fd=root)        # '.agent-workspace', AGENT_HARNESS_DIRS
...
if not stat.S_ISDIR(info.st_mode) or (info.st_uid, info.st_gid) != (uid, uid):
    raise PermissionError('tool directory is not owned by the admitted owner')
```

On a migrated center the `mkdir` raises `FileExistsError` and the entries are
already `(owner, owner)`, so it passes. On a center admitted after the cutover
the `mkdir` is `EACCES` at the center root, the cell exits non-zero and
`role_tools._files_exchange` fails with `invalid tool cell frame` (the cell's
stderr goes to `/dev/null` for a non-provider cell, so the cause is invisible
from the daemon side).

Reproduce:

```sh
python scripts/role_image_oracle.py --image <cutover image> \
    --legs bootstrap,admission,tool_files
```

Two things are wrong, not one:

1. **No owner-writable entry exists.** Nothing can create one: the daemon holds
   no `CAP_CHOWN`, and `tinyassets/workspace_owner_pool.py` (branch
   `iso/cutover-ws`) states the same constraint in its own module docstring —
   "The daemon cannot make a directory the owner owns … a command center's root
   admits the owner r-x only".
2. **`previews` disagrees with the migration's own target.** The migration
   labels `previews` as owner content (`300002:300002`); `_seed_entries` creates
   and asserts `1001:1001`. So `ta-migrate.py --check` run against a volume with
   a post-cutover center reports a diff for every one of them, and a migrated
   center and an admitted center are not the same shape.

## The fix this points at

Extend DA3's setgid hand-off, which already solves exactly this problem for the
root. `deploy/role_decoder.center_root_handoff` creates `g` (owner-owned, 2777)
inside daemon-private staging; the daemon then creates `g/root` and renames it
into place. Have the same cell also create the owner-owned seed entries inside
`g` (`.agent-workspace`, `previews`, and `AGENT_HARNESS_DIRS` if they belong in
the seed), have `_handoff` validate them in its proof, and have `label_root`
rename each into the new root before publishing it. The daemon can do those
renames with no capability: it has rwx on `g` (2777) and on the new root (it owns
it). `_seed_entries` then asserts the seeded labels instead of creating
`previews` itself.

The alternative — the `workspace_owner_pool` shape, where the daemon creates the
directory and grants the owner rwx by ACL — does not work here, because
`role_tools.maintain` and `node_sandbox`'s workspace mount both require
`(owner, owner)` ownership, not merely owner-writability. Relaxing those is a
weakening of the mount predicate, not a fix.

This is a protocol change to the center-root cell, so it is floor-class: one
cross-family `peer-agents` round (AGENTS.md § The loop, item 4).

## Until then

`scripts/role_image_oracle.py` keeps the `tool_files` leg, excludes it from its
default leg set, and prints `NOT PROVEN: leg tool_files is blocked by <this
file>` at both ends of every run. Delete this file when the leg is in
`DEFAULT_LEGS` and green.
