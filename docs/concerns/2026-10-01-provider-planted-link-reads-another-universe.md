---
severity: P1
title: A provider can plant a link in its universe that the daemon follows into another universe
filed: '2026-10-01'
summary: 'The provider jail binds the owning universe read-write and allows symlink(2). The daemon reads some universe files with a plain read_text that follows links, for example activity.log in the universe inspect action. A link planted by universe A''s provider, pointing at /data/<B>/founder.md, makes the daemon return B''s file in A''s inspect. Reproduced in the Linux oracle with the real jail on main''s shape. Reachable today through any provider whose launch has a shell and a read-write universe, such as codex workflow-node calls.'
---

# A planted link turns a daemon read into a cross-user read

## U1 founder D73 preview evidence (2026-10-06)

`role_reader_alias_probe.py --image tinyassets-uid-d73-preview:cells` now
includes `previews/owner-preview.png`: **132 denied, 22 own reads, zero foreign
reads**, with foreign bytes and metadata unchanged. Production image
`sha256:0969ce54e8f5177b24ed4fc383c15f308f36d546903b5bb5963b1cffaa78ba86`;
the successful final build tag has identical RootFS layers. The D60 bounded
namespace relabel/copy diagnostic also retains zero foreign reads under all
three existing profiles. The actual preview renderer consumes only admitted
UI/asset bytes; it mounts no owner filesystem. The separate cell-deny output
writer replaces planted leaf symlinks/hardlinks/FIFOs with owner-labelled
single-link files, without changing the foreign target. Its daemon readers
remain the common descriptor-validated universe/API/platform readers above.
Do not delete this concern: remaining actual classes and their full writable
path/reader matrix still gate resolution.

**Filed:** 2026-10-01. Found by the gpt-6-astra refute of the provider-jail
egress fix (branch `fix/provider-jail-egress`) and reproduced the same day. It
predates that branch: on main the provider jail loads no seccomp filter at all.

## The sequence

1. Universe A's provider jail (`tinyassets/providers/provider_jail.py`
   `default_view`) binds A's directory read-write at its own path. Inside the
   jail, `/data/<B>` does not exist, so B cannot be read directly.
2. Inside the jail: `ln -sf /data/<B>/founder.md /data/<A>/activity.log`. The
   target only needs to be a string, so creating the link succeeds.
3. A's owner calls the universe `inspect` action.
   `tinyassets/api/universe.py` (~L2029) calls `_read_text(udir / "activity.log")`.
   `tinyassets/api/helpers.py` `_read_text` sends every non-wiki path to
   `_read_platform_text`, which calls `path.read_text()` and follows the link.
4. The last ten lines of B's `founder.md` are returned in A's `recent_activity`.

Reproduced in the Linux oracle (uid 1001, real bubblewrap, the shipping
`aspawn_owned` + `provider_launch_scope` path). `cat /data/<B>/founder.md`
inside the jail fails with "No such file or directory", and the daemon-side
`_read_text` of A's `activity.log` then returned B's synthetic content.

## Who can reach it

- **codex**: the ordinary (non-`sandbox_workspace`) path launches with the
  default read-write view and `--sandbox workspace-write`, so codex's shell can
  write the universe root and create links. Coding turns bind `/workspace`
  read-only and chat turns a tmpfs, so neither can plant one.
- **claude**: served turns and workflow nodes deny `Bash` and the file tools via
  `--disallowedTools` (`HOST_REACH_TOOLS`). That is a per-vendor deny list,
  which `provider_jail`'s own docstring says is not a floor.
- **any future command adapter** with a shell, through the router.

You need B's universe id. That is not a secret wherever a universe or branch is
public.

## Why the seccomp filter does not close it

The universe tool jail refuses `symlink`. The provider jail cannot do the same
today: codex 0.153 runs each command in its own nested bubblewrap, which needs
`symlink` for its `/dev`. That was measured, and `tinyassets/providers/jail_seccomp.py`
documents it. The `universe_files` safe reader was meant to be the belt, but
`_read_platform_text` and other plain `read_text` callers do not use it.

## Fix directions

1. **Daemon side (the real fix):** every daemon read of a universe-relative
   path goes through a link-refusing reader (`tinyassets.universe_files`, or
   `O_NOFOLLOW` with a component walk), not `Path.read_text`. Start with
   `_read_platform_text`, then grep for other `read_text`/`open` calls on paths
   under a universe directory.
2. **Jail side:** run codex with its own sandbox off inside the provider jail.
   The jail is already the sandbox. The provider jail could then refuse
   `symlink` like the tool jail does.
