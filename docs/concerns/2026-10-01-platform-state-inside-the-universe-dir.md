---
severity: P1
title: Per-universe platform state lives inside the universe dir a provider can write
filed: '2026-10-01'
summary: 'The daemon keeps each universe''s platform state (credential vault, run, consent, usage and attention databases, other hidden root files) inside the universe directory that the provider jail binds read-write. Launch-time masks (#4252) and the provider seccomp filter (#4253) close the cross-user vectors, but a mount mask cannot close three same-universe ones: a daemon rename re-exposes a masked file, a hard link captures a daemon temp write, and a pre-created database forges owner consent. Target: move platform state out of the universe dir (the sealed-box rule).'
---

# Platform state inside the universe dir

**Filed:** 2026-10-01. Found by the gpt-6-astra refute of #4252 (the mask over
hidden root files), round 2. Escalated instead of hardened further, because a
launch-time mount mask cannot close these.

## Background

The provider jail binds the owning universe read-write, so a provider CLI can
work in its own folder (`tinyassets/providers/provider_jail.py` `default_view`).
The daemon keeps that universe's platform state in hidden files at the same
root, and reads and writes them from outside the jail. That state includes
`.credential-vault.json`, `.runs.db` and its sidecars, the consent, usage,
attention and conversation databases, and lock and stamp files. Some of these
are created lazily, on first use, so the set differs between universes.
## What the jail change already closes

`#4252` masks every hidden root entry present at launch, and `#4253` turns off
codex's own nested sandbox so the provider seccomp can refuse `symlink`/
`symlinkat`, `mknod`/`mknodat` and new user namespaces. Measured in the Linux
oracle, a provider can no longer:

- read a masked file at rest, write through the mask, or unlink/rename the
  mount point;
- create a symlink, a FIFO or a device node at any hidden name;
- hard-link to another universe (its directory is not mounted) or to a masked
  file (a separate mount: `EXDEV`).

So the **cross-user** floor holds: a provider cannot reach another universe's
state through these files.

## What a launch-time mount mask cannot close (same-universe)

All three are same-universe: they affect the provider's OWN universe, which is
an agent-versus-owner authority boundary, not user-versus-user. Filed P1 for #3.

1. **Rename re-exposes a masked file.** The mask is a mount over one path. The
   daemon updates `.credential-vault.json` by writing a temp file and renaming
   it over the name (`tinyassets/credential_vault.py`). The replacement is a new
   inode at the same path, no longer under the mount, visible through the
   read-write universe bind. A provider running across that write reads its own
   universe's full vault, defeating the mask as defence in depth. Accepted as
   residual until platform state moves out.

2. **Hard link captures a daemon temp write.** A hidden name absent at launch is
   not masked. A provider creates a file under a path it owns (e.g. `notes/x`)
   and hard-links it to `.credential-vault.json.tmp`; both are on the one
   universe mount. When the daemon writes that temp file its bytes land in the
   inode the provider still holds under `notes/x`.

3. **A pre-created database forges owner consent.** With `.effector_consents.db`
   absent at launch, a provider creates a regular SQLite file there carrying the
   schema (`tinyassets/storage/effector_consents.py`) and an active consent row.
   The daemon opens it with `CREATE TABLE IF NOT EXISTS`, which preserves the
   row, and the external-call consent gate
   (`tinyassets/effectors/authenticated_external_call.py`) then accepts it. The
   agent has granted itself the owner's consent for an external effect.

## Target fix: platform state lives outside the universe dir

The by-construction close is to move every per-universe platform file the daemon
owns (the credential vault, the run/consent/usage/attention/conversation
databases, locks and stamps) OUT of the universe directory, into a daemon-only
location neither the provider jail nor the tool jail mounts -- the same shape the
egress proxy socket already uses (`<data>/.universe-sidecars/<universe>/`). This
is the approved sealed-box design's rule that the platform record lives outside
the box; it is a storage-shape change and should be specced with that work, not
hand-built here.

## Interim, handed to the link-reader lane

Until the move, the daemon-side readers/writers reduce the window:

- refuse a pre-existing database the daemon did not create (closes #3);
- the daemon's own temp files use `O_EXCL`|`O_NOFOLLOW` with an unpredictable
  suffix in a directory the provider cannot write (closes #2);
- #1 is accepted as residual.
