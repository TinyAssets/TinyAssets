# Isolation by box: no per-center host directory

## Why

C6 gives every account its own box: a gVisor sandbox under the owner's
permanent uid, on its own quota project. That box is the isolation. The
founder ruled on 2026-10-16 (Direction 5) that cross-owner checks the OS
already enforces get deleted, not added.

C6 merged with a second namespace still in place: the host directory
`<data>/<center>/`. Owner content was copied into the box, but the platform
kept reading and writing that host directory. The platform also learned to
read any owner's box by binding as that center's admitted owner. Every
finding in four review rounds of #5167/#5168 came from that shared namespace
and not from the boxes:

- a host stub symlinked to another owner's center;
- branch deletion naming another owner's private center;
- a missing stub reading as "no soul" or "not paused";
- pause written to the box but read from the host.

Each fix added another check: symlink refusal, admission as existence, a stub
creator, fail-closed pause. #5168 was pulled before merging for that reason.

## What changes

There are exactly two homes per center, and nothing else:

1. **The owner's box** holds all owner content. C6 already moves it there.
2. **The platform sidecar `<data>/.universe-sidecars/<center>/`** holds every
   per-center platform file. This directory already exists for consents and
   seed locks. Only the platform creates it, at admission. No box mounts it,
   and no owner can write it.

The host directory `<data>/<center>/` stops existing. The C6 one-pass move
relocates its platform files into the sidecar, then removes it. The verified
paired backup keeps the pre-move bytes.

Owner-facing reads bind the box as the **requesting account**. Broker box
admission (`broker/box_admission.resolve`) refuses unless the requester is the
center's admitted owner, so the box host enforces isolation and the platform
adds no cross-owner check. Nothing ever binds as another account.

**Deleted:**

- per-center host stubs;
- the platform-wide bind-as-admitted-owner reader;
- the symlink, stub and admission-as-existence checks;
- the host-versus-box name lists and their guard.

They are replaced by one guard: no platform code performs filesystem I/O
under `<data>/<center>/`.

## Impact

- **Floor:** cross-user, storage shape and migration. This supersedes #5168,
  which stays a draft and will be closed, and #5167 (abandoned).
- **C6 deploy gate:** this change plus the `box_layout_migration` sidecar
  step.
- **C12:** its host retire step becomes "the C6 move removes the host
  center". `HOST_CENTER_MOVED`/`HOST_CENTER_ENTRIES` are not added.
- **Net:** expected negative, because the stub, reader and guard code
  written in #5168 never lands.
