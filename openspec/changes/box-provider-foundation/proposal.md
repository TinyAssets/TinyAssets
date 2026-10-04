# BoxProvider foundation: the interface, the box-host record, a local driver, the contract suite

## Why

`target-architecture` makes `BoxProvider` the only way the platform touches a command center's
files or runs code on its behalf (design D2). The founder approved that direction on 2026-10-01/02.

Every later box slice codes against this interface:
- S3, which moves the ~350 daemon readers onto it;
- S4, the gVisor driver and the re-pointed tool runner;
- S5, Firecracker;
- S7, the thin loop.

So the interface and its contract come first, with a driver that runs anywhere POSIX runs. This
slice needs no spend and no KVM.

## What Changes

- **`tinyassets/boxes/provider.py`** holds the `BoxProvider` protocol and its types, as design D2
  fixes them:
  - binding is separate from waking, and `committed_generation` answers while a box is suspended;
  - execution has an op-id lifecycle: `start_exec`, `stream`, `cancel` and `exec_status`, with an
    `unknown_after_restore` state;
  - file operations are paginated and streaming, plus `read_many`;
  - export has two profiles, `share` and `migration`;
  - every path is a box path under `/cc`, validated before any driver sees it.
- **`tinyassets/boxes/state.py`** is the box host's own durable record: placement epochs, change
  generations and operation outcomes. After a restart, every in-flight operation becomes
  `unknown_after_restore`, so a retry never re-runs it.
- **`tinyassets/boxes/local.py`** is the local driver, for tests and dev only.
  - It has no kernel boundary, and refuses to start without `allow_unisolated=True`.
  - It resolves every path through directory descriptors opened with `O_NOFOLLOW`.
  - It authenticates every handle against the command center's owner and the current epoch.
  - It writes atomically (temporary file, then rename).
  - Its cancel kills the whole process group.
- **`tests/test_box_provider_contract.py`** is the driver-agnostic contract suite. The gVisor and
  Firecracker drivers join its driver list when they land.

`destroy` and `import_bundle` take a `BoxHandle` rather than a bare command-center id, so they
are authenticated like every other operation. That refines D2's sketch; `design.md` records it.

## Capabilities

### Modified Capabilities

- `command-center-box` (introduced by `target-architecture`): adds the requirement that the local
  driver is for tests and dev only and refuses unacknowledged use.

## Impact

- **Code:** a new package, `tinyassets/boxes/`. No caller is re-pointed here; the tool runner and
  provider launch move in S4.
- **Storage:** the box host's own SQLite file, under its state directory. No platform store
  changes.
- **Follow-up in this change:** the gVisor driver (PR 2), which must pass the same suite in the
  Linux oracle.
