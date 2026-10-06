"""The local `BoxProvider` driver: one host directory per command center, for tests and dev.

**This driver has no kernel boundary.** It runs commands as host processes in the
box directory. It exists so the platform can code against `BoxProvider` today,
and as the reference driver for the contract suite. The same descriptor-safe core
is what `boxd` runs *inside* an isolating box. It refuses to start unless the
caller passes ``allow_unisolated=True``.

Everything above the kernel is enforced:

* **Paths** resolve beneath the box directory through directory descriptors,
  with every component opened ``O_NOFOLLOW``, so a planted link is never
  followed. An exec's working directory is passed to the child as an open
  descriptor (``/proc/self/fd``), never re-resolved by name.
* **Authority.** Every operation re-checks, under the box's lock, that the
  handle's account owns the command center and that its epoch is current. A
  destroy therefore cannot interleave with an operation that authenticated a
  moment earlier.
* **Idempotency.** Mutations are idempotent by operation id. An operation that
  may have had an effect is never forgotten: it is recorded as done, or as
  failed, and a retry gets that record back. Outcomes are fenced to the box-host
  incarnation that started them (`BoxHostState`).
* **Coherent generations.** Mutations and running execs hold a pending count. A
  snapshot (`read_many`, `export`) is taken only while nothing is pending, and
  `cas` refuses while anything is pending.
* **Executions** are supervised from launch. Stdin is fed by its own thread, so
  a child that never reads it cannot stall the wall clock. When the leader exits,
  the rest of its process group is killed. The output limit is enforced even on
  a fast exit.

Linux only (POSIX ``openat`` semantics and ``/proc/self/fd``). Like
`tinyassets.workspace_fs`, it refuses elsewhere; there is no unsafe fallback.
"""

from __future__ import annotations

import errno
import hashlib
import os
import re
import secrets
import select
import signal
import stat
import subprocess
import tarfile
import tempfile
import threading
import time
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from tinyassets.boxes.provider import (
    BOX_ROOT,
    BoxAuthError,
    BoxBusy,
    BoxError,
    BoxHandle,
    BoxNotFound,
    BoxPathError,
    BoxUsage,
    DestroyReceipt,
    DirEntry,
    DirPage,
    ExecEvent,
    ExecLimits,
    ExecState,
    ExecStatus,
    ExportProfile,
    FileRead,
    FileStat,
    FileWrite,
    ImportReport,
    Snapshot,
    StaleHandle,
    StreamIn,
    WriteConflict,
    WriteMode,
    box_relpath,
)
from tinyassets.boxes.state import BoxHostState, op_digest, process_start_time

__all__ = ["LocalBoxProvider"]

_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_O_NONBLOCK = getattr(os, "O_NONBLOCK", 0)
_CC_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_ENV_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_TMP_PREFIX = ".boxtmp-"
_POLL_S = 0.02
#: Runs the command from the working directory the driver opened, by descriptor.
_CWD_SHIM = 'cd -- "/proc/self/fd/$0" || exit 126; exec "$@"'


def _kind(mode: int) -> str:
    if stat.S_ISREG(mode):
        return "file"
    if stat.S_ISDIR(mode):
        return "dir"
    if stat.S_ISLNK(mode):
        return "link"
    return "other"


def _path_error(exc: OSError, path: str) -> BoxError:
    if exc.errno == errno.ENOENT:
        return BoxNotFound(errno.ENOENT, f"no such box path: {path}")
    if exc.errno in (errno.ELOOP, errno.ENOTDIR):
        return BoxPathError(f"box path {path!r} crosses a link or a non-directory")
    return BoxError(exc.errno, f"{path}: {exc.strerror}")


def _bounded(data: StreamIn, max_bytes: int) -> bytes:
    """Freeze the input into immutable bytes, refusing as soon as it passes the bound."""
    if isinstance(data, (bytes, bytearray, memoryview)):
        body = bytes(data)
        if len(body) > max_bytes:
            raise BoxError(f"write of {len(body)} bytes is over its {max_bytes}-byte bound")
        return body
    parts: list[bytes] = []
    total = 0
    for chunk in data:
        chunk = bytes(chunk)
        total += len(chunk)
        if total > max_bytes:
            raise BoxError(f"write is over its {max_bytes}-byte bound")
        parts.append(chunk)
    return b"".join(parts)


class _FdChunks:
    """Chunks of an already-open file. Closing (or dropping) it always closes the fd."""

    def __init__(self, fd: int, chunk_bytes: int) -> None:
        self._fd: int | None = fd
        self._chunk = chunk_bytes

    def __iter__(self) -> _FdChunks:
        return self

    def __next__(self) -> bytes:
        if self._fd is None:
            raise StopIteration
        block = os.read(self._fd, self._chunk)
        if not block:
            self.close()
            raise StopIteration
        return block

    def close(self) -> None:
        if self._fd is not None:
            fd, self._fd = self._fd, None
            os.close(fd)

    def __enter__(self) -> _FdChunks:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def __del__(self) -> None:
        self.close()


@dataclass
class _Running:
    cc: str
    proc: subprocess.Popen
    handle: BoxHandle
    cancel: threading.Event = field(default_factory=threading.Event)
    done: threading.Event = field(default_factory=threading.Event)
    input_ready: threading.Event = field(default_factory=threading.Event)
    input_lock: threading.Lock = field(default_factory=threading.Lock)
    replies: dict = field(default_factory=dict)


class LocalBoxProvider:
    """`BoxProvider` over ``<boxes_root>/<command_center_id>/``. Dev and tests only."""

    def __init__(
        self,
        *,
        boxes_root: Path,
        state_dir: Path,
        owner_of: Callable[[str], str | None],
        allow_unisolated: bool = False,
        busy_wait_s: float = 5.0,
        destroy_wait_s: float = 10.0,
    ) -> None:
        if not allow_unisolated:
            raise BoxError(
                "the local box driver runs commands on the host with no kernel boundary; "
                "construct it with allow_unisolated=True only for tests and single-user dev"
            )
        if os.name != "posix" or not os.path.isdir("/proc/self/fd"):
            raise NotImplementedError(
                "the local box driver needs Linux (openat + O_NOFOLLOW + /proc/self/fd); "
                f"this host is {os.name!r}, and there is no safe fallback"
            )
        self._root = Path(boxes_root)
        self._root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._state_dir = Path(state_dir)
        self._exec_dir = self._state_dir / "execs"
        self._exec_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._state = BoxHostState(self._state_dir / "boxhost.db")
        self._owner_of = owner_of
        self._busy_wait_s = busy_wait_s
        self._destroy_wait_s = destroy_wait_s
        self._running: dict[str, _Running] = {}
        self._running_guard = threading.Lock()
        self._destroying: set[str] = set()
        self._closing = False
        self._locks: dict[str, threading.RLock] = {}
        self._locks_guard = threading.Lock()

    # -- authority -----------------------------------------------------------------

    def _lock(self, cc: str) -> threading.RLock:
        with self._locks_guard:
            return self._locks.setdefault(cc, threading.RLock())

    @staticmethod
    def _check_cc(cc: str) -> str:
        if not isinstance(cc, str) or not _CC_ID.match(cc) or set(cc) <= {"."}:
            raise BoxPathError(f"command center id {cc!r} is not a valid box id")
        return cc

    def _require_owner(self, cc: str, account_id: str) -> None:
        owner = self._owner_of(cc)
        if owner is None or owner != account_id:
            raise BoxAuthError(f"account {account_id!r} does not own command center {cc!r}")

    def _auth_locked(self, handle: BoxHandle) -> str:
        """Owner + epoch + not being destroyed. Call with the box's lock held."""
        cc = self._check_cc(handle.command_center_id)
        self._require_owner(cc, handle.account_id)
        current = self._state.epoch(cc)
        if handle.epoch != current:
            raise StaleHandle(f"handle for {cc!r} has epoch {handle.epoch}, current is {current}")
        if cc in self._destroying:
            raise StaleHandle(f"command center {cc!r} is being destroyed")
        if self._closing:
            raise BoxError("the box host is shutting down")
        return cc

    # -- descriptors -----------------------------------------------------------------

    def _root_fd(self) -> int:
        from tinyassets.workspace_fs import open_dir_nofollow

        return open_dir_nofollow(self._root.resolve())

    def _box_fd(self, cc: str, *, create: bool = True) -> int:
        root = self._root_fd()
        try:
            try:
                return os.open(cc, os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW, dir_fd=root)
            except FileNotFoundError:
                if not create:
                    raise BoxNotFound(errno.ENOENT, f"no box for {cc!r}") from None
                try:
                    os.mkdir(cc, 0o700, dir_fd=root)
                except FileExistsError:
                    pass
                return os.open(cc, os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW, dir_fd=root)
        except OSError as exc:
            if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                raise BoxPathError(f"box directory for {cc!r} is a link") from exc
            raise
        finally:
            os.close(root)

    @staticmethod
    def _open_dir(parent_fd: int, name: str, path: str, *, create: bool) -> int:
        try:
            return os.open(name, os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW, dir_fd=parent_fd)
        except FileNotFoundError:
            if not create:
                raise BoxNotFound(errno.ENOENT, f"no such box path: {path}") from None
            try:
                os.mkdir(name, 0o755, dir_fd=parent_fd)
            except FileExistsError:
                pass
            try:
                return os.open(name, os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW, dir_fd=parent_fd)
            except OSError as exc:
                raise _path_error(exc, path) from exc
        except OSError as exc:
            raise _path_error(exc, path) from exc

    def _walk(self, box_fd: int, rel: str, path: str, *, create: bool,
              created_dirs: list[str] | None = None) -> int:
        """Open the directory ``rel`` beneath ``box_fd`` (``""`` is the box root)."""
        current = os.dup(box_fd)
        try:
            for part in [p for p in rel.split("/") if p]:
                if create and created_dirs is not None:
                    try:
                        os.stat(part, dir_fd=current, follow_symlinks=False)
                    except FileNotFoundError:
                        created_dirs.append(part)
                child = self._open_dir(current, part, path, create=create)
                os.close(current)
                current = child
        except BaseException:
            os.close(current)
            raise
        return current

    def _parent(self, cc: str, rel: str, path: str, *, create: bool,
                created_dirs: list[str] | None = None) -> tuple[int, str]:
        if not rel:
            raise BoxPathError(f"{path!r} names the box root, not a file")
        head, _, leaf = rel.rpartition("/")
        box = self._box_fd(cc)
        try:
            return self._walk(box, head, path, create=create, created_dirs=created_dirs), leaf
        finally:
            os.close(box)

    @staticmethod
    def _open_regular(parent_fd: int, leaf: str, path: str) -> int:
        try:
            fd = os.open(leaf, os.O_RDONLY | _O_NOFOLLOW | _O_NONBLOCK, dir_fd=parent_fd)
        except OSError as exc:
            raise _path_error(exc, path) from exc
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            os.close(fd)
            raise BoxPathError(f"{path!r} is not a regular file ({_kind(info.st_mode)})")
        return fd

    # -- operation-id bookkeeping -------------------------------------------------------

    def _begin(self, cc: str, op_id: str, kind: str, payload: object) -> dict | None:
        record = self._state.begin(cc, op_id, kind, op_digest(kind, payload))
        if record is None:
            return None
        if record["state"] == "unknown_after_restore":
            raise BoxError(
                f"{kind} {op_id!r} was in flight across a box-host restart; its outcome is "
                "unknown, so it is not re-run (hold and reconcile)"
            )
        if record["state"] == "running":
            raise BoxError(f"{kind} {op_id!r} is still in flight")
        outcome = record["outcome"] or {}
        if "error" in outcome:
            raise BoxError(f"{kind} {op_id!r} failed after partial effects and is not re-run: "
                           f"{outcome['error']}")
        return outcome

    # -- binding and waking ----------------------------------------------------------

    def bind(self, command_center_id: str, *, account_id: str,
             turn_id: str | None = None) -> BoxHandle:
        cc = self._check_cc(command_center_id)
        self._require_owner(cc, account_id)
        return BoxHandle(cc, account_id, self._state.epoch(cc), turn_id)

    def committed_generation(self, handle: BoxHandle) -> int:
        with self._lock(handle.command_center_id):
            return self._state.generation(self._auth_locked(handle))

    def ensure_awake(self, handle: BoxHandle, *, reason: str) -> None:
        with self._lock(handle.command_center_id):
            os.close(self._box_fd(self._auth_locked(handle)))

    def suspend(self, handle: BoxHandle) -> None:
        with self._lock(handle.command_center_id):
            self._auth_locked(handle)  # no memory state to checkpoint in the local driver

    # -- reads ---------------------------------------------------------------------------

    def _read_locked(self, cc: str, path: str, offset: int, max_bytes: int) -> tuple[bytes, int]:
        rel = box_relpath(path)
        parent, leaf = self._parent(cc, rel, path, create=False)
        try:
            fd = self._open_regular(parent, leaf, path)
        finally:
            os.close(parent)
        try:
            size = os.fstat(fd).st_size
            os.lseek(fd, offset, os.SEEK_SET)
            return (os.read(fd, max_bytes) if max_bytes else b""), int(size)
        finally:
            os.close(fd)

    def read(self, handle: BoxHandle, path: str, *, offset: int = 0,
             max_bytes: int) -> FileRead:
        if max_bytes < 0 or offset < 0:
            raise ValueError("offset and max_bytes must be >= 0")
        with self._lock(handle.command_center_id):
            cc = self._auth_locked(handle)
            data, size = self._read_locked(cc, path, offset, max_bytes)
            return FileRead(data=data, generation=self._state.generation(cc), size=size)

    def read_many(self, handle: BoxHandle, paths: Sequence[str], *,
                  max_total: int) -> Snapshot:
        cc = self._check_cc(handle.command_center_id)
        deadline = time.monotonic() + self._busy_wait_s
        while True:
            with self._lock(cc):
                self._auth_locked(handle)
                # Mutations take this lock; running execs hold a pending count, and an exec can
                # neither start nor finish without this lock. So with the lock held and nothing
                # pending, nothing can change the files underneath the reads.
                if not self._state.pending(cc):
                    generation = self._state.generation(cc)
                    files: dict[str, bytes] = {}
                    missing: list[str] = []
                    total = 0
                    for path in paths:
                        try:
                            data, size = self._read_locked(cc, path, 0, max_total - total + 1)
                        except BoxNotFound:
                            missing.append(path)
                            continue
                        if size > max_total - total:
                            raise BoxError(f"read_many over its {max_total}-byte bound at {path!r}")
                        files[path] = data
                        total += size
                    return Snapshot(files=files, missing=tuple(missing), generation=generation)
            if time.monotonic() > deadline:
                raise BoxBusy(f"command center {cc!r} stayed busy past {self._busy_wait_s}s")
            time.sleep(_POLL_S)

    def download(self, handle: BoxHandle, path: str, *,
                 chunk_bytes: int = 64 * 1024) -> Iterator[bytes]:
        with self._lock(handle.command_center_id):
            cc = self._auth_locked(handle)
            rel = box_relpath(path)
            parent, leaf = self._parent(cc, rel, path, create=False)
            try:
                fd = self._open_regular(parent, leaf, path)
            finally:
                os.close(parent)
        return _FdChunks(fd, chunk_bytes)

    def list(self, handle: BoxHandle, path: str, *, cursor: str | None = None,
             limit: int = 200) -> DirPage:
        if limit < 1:
            raise ValueError("limit must be >= 1")
        with self._lock(handle.command_center_id):
            cc = self._auth_locked(handle)
            rel = box_relpath(path)
            box = self._box_fd(cc)
            try:
                dfd = self._walk(box, rel, path, create=False)
            finally:
                os.close(box)
            try:
                names = sorted(n for n in os.listdir(dfd) if not n.startswith(_TMP_PREFIX))
                if cursor is not None:
                    names = [n for n in names if n > cursor]
                page, rest = names[:limit], names[limit:]
                entries = []
                for name in page:
                    info = os.stat(name, dir_fd=dfd, follow_symlinks=False)
                    entries.append(DirEntry(name, _kind(info.st_mode), int(info.st_size)))
            finally:
                os.close(dfd)
        return DirPage(tuple(entries), page[-1] if rest else None)

    def stat(self, handle: BoxHandle, path: str) -> FileStat | None:
        with self._lock(handle.command_center_id):
            cc = self._auth_locked(handle)
            rel = box_relpath(path)
            if not rel:
                box = self._box_fd(cc)
                try:
                    info = os.fstat(box)
                finally:
                    os.close(box)
            else:
                try:
                    parent, leaf = self._parent(cc, rel, path, create=False)
                except BoxNotFound:
                    return None
                try:
                    info = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
                except FileNotFoundError:
                    return None
                finally:
                    os.close(parent)
        return FileStat(path, _kind(info.st_mode), int(info.st_size), info.st_mtime)

    # -- mutations ------------------------------------------------------------------------

    def write(self, handle: BoxHandle, op_id: str, path: str, data: StreamIn, *,
              max_bytes: int, mode: WriteMode = WriteMode.REPLACE,
              expect_generation: int | None = None) -> FileWrite:
        rel = box_relpath(path)
        mode = WriteMode(mode)
        if mode is WriteMode.CAS and expect_generation is None:
            raise ValueError("a cas write needs expect_generation")
        body = _bounded(data, max_bytes)
        payload = {"path": rel, "sha": hashlib.sha256(body).hexdigest(), "mode": mode.value,
                   "expect": expect_generation}
        with self._lock(handle.command_center_id):
            cc = self._auth_locked(handle)
            done = self._begin(cc, op_id, "write", payload)
            if done is not None:
                return FileWrite(path=path, size=done["size"], generation=done["generation"])
            if mode is WriteMode.CAS and (
                self._state.pending(cc) or self._state.generation(cc) != expect_generation
            ):
                self._state.abandon(cc, op_id)
                raise WriteConflict(f"box generation is not {expect_generation} (or is changing)")
            self._state.hold(cc)
            try:
                created: list[str] = []
                try:
                    self._place(cc, rel, path, body, mode, created_dirs=created)
                except BaseException as exc:
                    if created:  # parent directories now exist: an effect, recorded
                        self._state.bump_generation(cc)
                        self._state.finish(cc, op_id, {"error": str(exc)})
                    else:
                        self._state.abandon(cc, op_id)  # nothing visible changed
                    raise
                generation = self._state.bump_generation(cc)
                self._state.finish(cc, op_id, {"size": len(body), "generation": generation})
            finally:
                self._state.release(cc)
        return FileWrite(path=path, size=len(body), generation=generation)

    def _place(self, cc: str, rel: str, path: str, body: bytes, mode: WriteMode,
               created_dirs: list[str] | None = None) -> None:
        """Write ``body`` to a temp file, then publish it: ``link`` (no-clobber) or ``rename``.

        Nothing is visible at ``path`` until the final step succeeds; on any failure the
        temp file is removed, so the caller may treat a raised error as "no effect".
        """
        parent, leaf = self._parent(cc, rel, path, create=True, created_dirs=created_dirs)
        tmp = _TMP_PREFIX + secrets.token_hex(8)
        try:
            try:
                existing = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                existing = None
            if existing is not None:
                if mode is WriteMode.CREATE:
                    raise WriteConflict(f"{path!r} already exists")
                if stat.S_ISDIR(existing.st_mode):
                    raise BoxPathError(f"{path!r} is a directory")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_NOFOLLOW, 0o644,
                         dir_fd=parent)
            try:
                view = memoryview(body)
                while view:
                    view = view[os.write(fd, view):]
                os.fsync(fd)
            finally:
                os.close(fd)
            if mode is WriteMode.CREATE:
                try:  # link() refuses an existing name: no clobber, even under a race
                    os.link(tmp, leaf, src_dir_fd=parent, dst_dir_fd=parent,
                            follow_symlinks=False)
                except FileExistsError:
                    raise WriteConflict(f"{path!r} already exists") from None
                try:  # published: the temp name is cleanup only, never a failure
                    os.unlink(tmp, dir_fd=parent)
                except OSError:
                    pass
                return
            os.rename(tmp, leaf, src_dir_fd=parent, dst_dir_fd=parent)
        except BaseException:
            try:
                os.unlink(tmp, dir_fd=parent)
            except FileNotFoundError:
                pass
            raise
        finally:
            os.close(parent)

    def remove(self, handle: BoxHandle, op_id: str, path: str) -> None:
        rel = box_relpath(path)
        with self._lock(handle.command_center_id):
            cc = self._auth_locked(handle)
            if self._begin(cc, op_id, "remove", {"path": rel}) is not None:
                return
            try:
                parent, leaf = self._parent(cc, rel, path, create=False)
            except BaseException:
                self._state.abandon(cc, op_id)
                raise
            try:
                os.stat(leaf, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                os.close(parent)
                self._state.abandon(cc, op_id)
                raise BoxNotFound(errno.ENOENT, f"no such box path: {path}") from None
            self._state.hold(cc)
            try:
                try:
                    removed = _remove_beneath(parent, leaf, path)
                except BaseException as exc:
                    # Some children may already be gone: record the failure, never re-run.
                    self._state.bump_generation(cc)
                    self._state.finish(cc, op_id, {"error": str(exc)})
                    raise
                finally:
                    os.close(parent)
                generation = self._state.bump_generation(cc)
                self._state.finish(cc, op_id, {"removed": removed, "generation": generation})
            finally:
                self._state.release(cc)

    # -- execution ----------------------------------------------------------------------

    def _exec_id(self, cc: str, op_id: str) -> str:
        return "x" + hashlib.sha256(f"{cc}\0{op_id}".encode()).hexdigest()[:31]

    def start_exec(self, handle: BoxHandle, op_id: str, argv: Sequence[str], *,
                   stdin: bytes = b"", env: Mapping[str, str] | None = None,
                   cwd: str = BOX_ROOT, limits: ExecLimits = ExecLimits(),
                   interactive_stdin: bool = False) -> str:
        if not argv or not all(isinstance(a, str) and "\0" not in a for a in argv):
            raise ValueError("argv must be a non-empty list of strings without NULs")
        extra = {str(k): str(v) for k, v in (env or {}).items()}
        for key, value in extra.items():
            if not _ENV_KEY.match(key) or "\0" in value:
                raise ValueError(f"environment key {key!r} is not allowed")
        stdin = bytes(stdin)
        rel_cwd = box_relpath(cwd)
        payload = {"argv": list(argv), "stdin": hashlib.sha256(stdin).hexdigest(),
                   "env": extra, "cwd": rel_cwd,
                   "limits": [limits.wall_seconds, limits.output_bytes],
                   "interactive_stdin": interactive_stdin}
        with self._lock(handle.command_center_id):
            cc = self._auth_locked(handle)
            exec_id = self._exec_id(cc, op_id)
            if self._state.begin(cc, op_id, "exec", op_digest("exec", payload)) is not None:
                return exec_id  # done, running or unknown: never run twice
            out_path = self._exec_dir / f"{exec_id}.out"
            proc = None
            try:
                self._state.register_exec(cc, op_id, exec_id)
                self._state.update(cc, op_id, {"exec_id": exec_id,
                                               "output_bytes": limits.output_bytes})
                box = self._box_fd(cc)
                try:
                    cwd_fd = self._walk(box, rel_cwd, cwd, create=False)
                finally:
                    os.close(box)
                try:
                    out_path.unlink(missing_ok=True)  # leftover of a launch that never ran
                    out_fd = os.open(out_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    child_env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8",
                                 "HOME": BOX_ROOT, **extra}
                    try:
                        proc = subprocess.Popen(
                            ["/bin/sh", "-c", _CWD_SHIM, str(cwd_fd), *argv],
                            env=child_env, stdin=subprocess.PIPE, stdout=out_fd,
                            stderr=subprocess.STDOUT, start_new_session=True,
                            pass_fds=(cwd_fd,), close_fds=True,
                        )
                    finally:
                        os.close(out_fd)
                finally:
                    os.close(cwd_fd)
            except BaseException:
                out_path.unlink(missing_ok=True)
                self._state.forget_exec(cc, exec_id)
                self._state.abandon(cc, op_id)  # nothing ran
                raise
            held = False
            try:
                self._state.update(cc, op_id, {
                    "exec_id": exec_id, "output_bytes": limits.output_bytes,
                    "pgid": proc.pid, "start_time": process_start_time(proc.pid)})
                running = _Running(cc, proc, handle)
                with self._running_guard:
                    self._running[exec_id] = running
                self._state.hold(cc)  # a running exec may change files at any moment
                held = True
                threading.Thread(
                    target=self._supervise,
                    args=(cc, op_id, exec_id, running, stdin, limits, out_path,
                          interactive_stdin),
                    daemon=True, name=f"box-exec-{exec_id[:8]}",
                ).start()
            except BaseException as exc:
                # It is running but cannot be supervised: kill it, then record the failure.
                _kill_group(proc.pid)
                proc.wait()
                with self._running_guard:
                    self._running.pop(exec_id, None)
                if held:
                    self._state.release(cc)
                self._state.bump_generation(cc)  # it ran, however briefly
                self._state.finish(cc, op_id, {"exec_id": exec_id, "error": str(exc),
                                               "output_bytes": limits.output_bytes})
                raise
        return exec_id

    def _supervise(self, cc: str, op_id: str, exec_id: str, running: _Running,
                   stdin: bytes, limits: ExecLimits, out_path: Path,
                   interactive_stdin: bool = False) -> None:
        proc = running.proc
        started = time.monotonic()
        killed: str | None = None
        code: int | None = None
        try:
            def feed():
                try:
                    if interactive_stdin:
                        proc.stdin.write(stdin)
                        proc.stdin.flush()
                    else:
                        _feed(proc, stdin)
                except OSError:
                    pass
                finally:
                    running.input_ready.set()
            threading.Thread(target=feed, daemon=True).start()
            while proc.poll() is None:
                if running.cancel.is_set():
                    killed = "cancelled"
                elif time.monotonic() - started > limits.wall_seconds:
                    killed = "timeout"
                elif out_path.stat().st_size > limits.output_bytes:
                    killed = "output_limit"
                if killed:
                    break
                time.sleep(_POLL_S)
            # The exec ends with its leader: kill whatever else is left in its group.
            _kill_group(proc.pid)
            code = proc.wait()
            if out_path.stat().st_size > limits.output_bytes:
                with open(out_path, "r+b") as fh:
                    fh.truncate(limits.output_bytes)
                killed = killed or "output_limit"
        except BaseException:
            _kill_group(proc.pid)  # never record completion for a child still running
            code = proc.wait()
            killed = killed or "supervisor_error"
        finally:
            if interactive_stdin and proc.stdin is not None:
                # send_stdin retains the fd across select/write. Keep it alive
                # until that writer releases it, before another exec can reuse it.
                with running.input_lock:
                    try:
                        proc.stdin.close()
                    except OSError:
                        pass  # A killed child must still settle its host receipt.
            with self._lock(cc):
                generation = self._state.bump_generation(cc)  # the command may have changed files
                self._state.finish(cc, op_id, {"exec_id": exec_id, "exit_code": code,
                                               "killed": killed, "generation": generation,
                                               "output_bytes": limits.output_bytes})
                self._state.release(cc)
                with self._running_guard:
                    self._running.pop(exec_id, None)
            running.done.set()

    def send_stdin(self, handle: BoxHandle, exec_id: str, request_id: str, data: bytes) -> None:
        """Idempotent reply on a bound running exec; never a workspace mutation.

        A host restart kills these processes and reports UNKNOWN_AFTER_RESTORE;
        partially delivered replies are never sent again onto a corrupted stream.
        """
        if not re.fullmatch(r"[a-f0-9]{32}", request_id) or len(data) > 8 * 1024 * 1024 + 256:
            raise BoxError("invalid exec reply")
        with self._lock(handle.command_center_id):
            self._auth_locked(handle)
            with self._running_guard:
                running = self._running.get(exec_id)
            if running is None or running.handle != handle:
                raise BoxAuthError("exec reply binding refused")
        if not running.input_ready.wait(5):
            raise BoxError("exec input unavailable")
        digest = hashlib.sha256(data).hexdigest()
        with running.input_lock:
            previous = running.replies.get(request_id)
            if previous is not None:
                if previous != (digest, True):
                    raise BoxError("exec reply reused or outcome unknown")
                return
            if running.proc.stdin is None or running.proc.stdin.closed:
                raise BoxError("exec input unavailable")
            running.replies[request_id] = (digest, False)
            fd = running.proc.stdin.fileno()
            os.set_blocking(fd, False)
            deadline = time.monotonic() + 5
            remaining = memoryview(data)
            while remaining:
                wait = deadline - time.monotonic()
                if wait <= 0 or not select.select([], [fd], [], wait)[1]:
                    raise BoxError("exec reply delivery unknown")
                try:
                    remaining = remaining[os.write(fd, remaining[:4096]):]
                except BlockingIOError:
                    continue
            running.replies[request_id] = (digest, True)

    def stream(self, handle: BoxHandle, exec_id: str, *,
               from_offset: int = 0, timeout: float | None = None) -> Iterator[ExecEvent]:
        with self._lock(handle.command_center_id):
            cc = self._auth_locked(handle)
            if self._state.find_exec(cc, exec_id) is None:
                raise BoxNotFound(errno.ENOENT, f"no exec {exec_id!r} in this box")
        out_path = self._exec_dir / f"{exec_id}.out"
        deadline = None if timeout is None else time.monotonic() + timeout

        def events() -> Iterator[ExecEvent]:
            offset = max(0, from_offset)
            while True:
                record = self._state.find_exec(cc, exec_id) or {}
                state = record.get("state")
                cap = int((record.get("outcome") or {}).get("output_bytes") or 0)
                if out_path.exists() and offset < cap:
                    with open(out_path, "rb") as fh:
                        fh.seek(offset)
                        block = fh.read(cap - offset)  # never past the output limit
                    if block:
                        yield ExecEvent("output", offset=offset, data=block)
                        offset += len(block)
                if state == "done":
                    outcome = record.get("outcome") or {}
                    yield ExecEvent("exit", offset=offset, exit_code=outcome.get("exit_code"),
                                    killed=outcome.get("killed"))
                    return
                if state == "unknown_after_restore":
                    yield ExecEvent("exit", offset=offset,
                                    killed=ExecState.UNKNOWN_AFTER_RESTORE.value)
                    return
                if deadline is not None and time.monotonic() > deadline:
                    return
                time.sleep(_POLL_S)

        return events()

    def cancel(self, handle: BoxHandle, exec_id: str) -> None:
        with self._lock(handle.command_center_id):
            cc = self._auth_locked(handle)
            if self._state.find_exec(cc, exec_id) is None:
                raise BoxNotFound(errno.ENOENT, f"no exec {exec_id!r} in this box")
            with self._running_guard:
                running = self._running.get(exec_id)
        if running is not None:
            running.cancel.set()

    def exec_status(self, handle: BoxHandle, op_id: str) -> ExecStatus:
        with self._lock(handle.command_center_id):
            cc = self._auth_locked(handle)
            record = self._state.lookup(cc, op_id)
        if record is None or record["kind"] != "exec":
            raise BoxNotFound(errno.ENOENT, f"no exec operation {op_id!r} in this box")
        outcome = record["outcome"] or {}
        state = {"running": ExecState.RUNNING, "done": ExecState.EXITED,
                 "unknown_after_restore": ExecState.UNKNOWN_AFTER_RESTORE}[record["state"]]
        return ExecStatus(op_id=op_id, exec_id=outcome.get("exec_id", self._exec_id(cc, op_id)),
                          state=state, exit_code=outcome.get("exit_code"),
                          killed=outcome.get("killed"))

    # -- whole box -------------------------------------------------------------------------

    def usage(self, handle: BoxHandle) -> BoxUsage:
        with self._lock(handle.command_center_id):
            cc = self._auth_locked(handle)
            box = self._box_fd(cc)
        seen: set[tuple[int, int]] = set()
        total = 0
        try:
            for _dirpath, _dirs, files, dfd in os.fwalk(".", dir_fd=box, follow_symlinks=False):
                for name in files:
                    try:
                        info = os.stat(name, dir_fd=dfd, follow_symlinks=False)
                    except FileNotFoundError:
                        continue
                    if stat.S_ISREG(info.st_mode) and (info.st_dev, info.st_ino) not in seen:
                        seen.add((info.st_dev, info.st_ino))
                        total += int(info.st_size)
        finally:
            os.close(box)
        return BoxUsage(logical_bytes=total, bound_bytes=None,
                        generation=self._state.generation(cc))

    def export(self, handle: BoxHandle, *, profile: ExportProfile) -> Iterator[bytes]:
        """A tar of the box's regular files and directories, taken while nothing is pending.

        Links and special files are left out. Profile scrubbing (the ``share`` manifest,
        harness §4.17) is applied by the export layer above the driver.
        """
        ExportProfile(profile)
        cc = self._check_cc(handle.command_center_id)
        spool = tempfile.TemporaryFile(dir=self._state_dir)
        deadline = time.monotonic() + self._busy_wait_s
        while True:
            with self._lock(cc):
                self._auth_locked(handle)
                if not self._state.pending(cc):
                    _tar_box(self._box_fd(cc), spool)
                    break
            if time.monotonic() > deadline:
                spool.close()
                raise BoxBusy(f"command center {cc!r} stayed busy past {self._busy_wait_s}s")
            time.sleep(_POLL_S)
        spool.seek(0)

        def chunks() -> Iterator[bytes]:
            with spool:
                while True:
                    block = spool.read(64 * 1024)
                    if not block:
                        return
                    yield block

        return chunks()

    def import_bundle(self, handle: BoxHandle, op_id: str, chunks: Iterable[bytes], *,
                      profile: ExportProfile) -> ImportReport:
        profile = ExportProfile(profile)
        spool = tempfile.TemporaryFile(dir=self._state_dir)
        digest = hashlib.sha256()
        for block in chunks:
            digest.update(block)
            spool.write(block)
        spool.seek(0)
        with spool, self._lock(handle.command_center_id):
            cc = self._auth_locked(handle)
            done = self._begin(cc, op_id, "import", {"sha": digest.hexdigest(),
                                                     "profile": profile.value})
            if done is not None:
                return ImportReport(done["files"], done["bytes"], tuple(done["refused"]))
            files = size = 0
            effected = False
            refused: list[str] = []
            self._state.hold(cc)
            try:
                try:
                    with tarfile.open(fileobj=spool, mode="r:") as tar:
                        for member in tar:
                            name = member.name[2:] if member.name.startswith("./") else member.name
                            try:
                                rel = box_relpath(f"{BOX_ROOT}/{name}")
                                if not rel:
                                    raise BoxPathError(f"{member.name!r} names the box root")
                                if member.isdir():
                                    effected = True
                                    box = self._box_fd(cc)
                                    try:
                                        os.close(self._walk(box, rel, member.name, create=True))
                                    finally:
                                        os.close(box)
                                elif member.isreg():
                                    src = tar.extractfile(member)
                                    body = src.read() if src is not None else b""
                                    effected = True  # parents may be created before a failure
                                    self._place(cc, rel, member.name, body, WriteMode.REPLACE)
                                    files += 1
                                    size += len(body)
                                else:
                                    refused.append(member.name)  # links, devices, FIFOs
                            except (BoxPathError, WriteConflict):
                                refused.append(member.name)
                except BaseException as exc:
                    if effected:
                        self._state.bump_generation(cc)
                        self._state.finish(cc, op_id, {"error": str(exc)})
                    else:
                        self._state.abandon(cc, op_id)
                    raise
                self._state.bump_generation(cc)
                self._state.finish(cc, op_id, {"files": files, "bytes": size,
                                               "refused": refused})
            finally:
                self._state.release(cc)
        return ImportReport(files, size, tuple(refused))

    def destroy(self, handle: BoxHandle, op_id: str) -> DestroyReceipt:
        cc = self._check_cc(handle.command_center_id)
        with self._lock(cc):
            self._require_owner(cc, handle.account_id)
            recorded = self._state.lookup(cc, op_id)
            if (recorded is not None and recorded["kind"] == "destroy"
                    and recorded["state"] == "done"):
                outcome = recorded["outcome"] or {}
                if "error" in outcome:
                    raise BoxError(f"destroy {op_id!r} failed partway and is not re-run: "
                                   f"{outcome['error']}")
                return DestroyReceipt(cc, op_id, outcome["files_removed"], outcome["new_epoch"])
            self._auth_locked(handle)
            if self._begin(cc, op_id, "destroy", {}) is not None:
                raise BoxError(f"destroy {op_id!r} has an earlier record")
            self._destroying.add(cc)  # every other operation on this box now refuses
        removing = False
        try:
            with self._running_guard:
                victims = [r for r in self._running.values() if r.cc == cc]
            for running in victims:
                running.cancel.set()
            for running in victims:  # supervisors kill and reap the whole group, then finish
                if not running.done.wait(self._destroy_wait_s):
                    raise BoxError(
                        f"an exec in {cc!r} did not stop within {self._destroy_wait_s}s"
                    )
            with self._lock(cc):
                removing = True
                root = self._root_fd()
                try:
                    try:
                        removed = _remove_beneath(root, cc, cc)
                    except BoxNotFound:
                        removed = 0
                finally:
                    os.close(root)
                new_epoch = self._state.bump_epoch(cc)
                self._state.finish(cc, op_id, {"files_removed": removed, "new_epoch": new_epoch})
        except BaseException as exc:
            with self._lock(cc):
                if removing:
                    # Part of the box may be gone: every old handle is now stale, and the
                    # failure is recorded so this op id never re-runs.
                    self._state.bump_epoch(cc)
                    self._state.finish(cc, op_id, {"error": str(exc)})
                else:
                    self._state.abandon(cc, op_id)  # nothing removed: a retry may run
            raise
        finally:
            with self._lock(cc):
                self._destroying.discard(cc)
        return DestroyReceipt(cc, op_id, removed, new_epoch)

    def close(self) -> None:
        """A clean host shutdown: refuse new work, stop every exec, then give up ownership.

        Ownership of the state directory is released only once nothing is running. If an
        exec will not stop, the host keeps its lock and raises, so no second host can start
        beside work that is still changing files.
        """
        with self._locks_guard:
            self._closing = True
        with self._running_guard:
            victims = list(self._running.values())
        for running in victims:
            running.cancel.set()
        stuck = [r for r in victims if not r.done.wait(self._destroy_wait_s)]
        with self._running_guard:
            stuck += [r for r in self._running.values() if r not in victims]
        if stuck:
            raise BoxError(f"{len(stuck)} exec(s) did not stop; keeping ownership of the host")
        self._state.close()


def _feed(proc: subprocess.Popen, stdin: bytes) -> None:
    """Feed stdin on its own thread, so a child that never reads cannot stall supervision."""
    if proc.stdin is None:
        return
    try:
        proc.stdin.write(stdin)
    except (BrokenPipeError, OSError):
        pass
    finally:
        try:
            proc.stdin.close()
        except OSError:
            pass


def _kill_group(pgid: int) -> None:
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _tar_box(box_fd: int, spool) -> None:
    """Write the box's regular files and directories into ``spool``; closes ``box_fd``."""
    try:
        with tarfile.open(fileobj=spool, mode="w") as tar:
            for dirpath, dirs, files, dfd in os.fwalk(".", dir_fd=box_fd, follow_symlinks=False):
                base = dirpath[2:] if dirpath.startswith("./") else ""
                for name in sorted(dirs):
                    info = os.stat(name, dir_fd=dfd, follow_symlinks=False)
                    if stat.S_ISDIR(info.st_mode):
                        entry = tarfile.TarInfo(f"{base}/{name}".lstrip("/"))
                        entry.type, entry.mode = tarfile.DIRTYPE, 0o755
                        tar.addfile(entry)
                for name in sorted(files):
                    if name.startswith(_TMP_PREFIX):
                        continue
                    try:
                        fd = os.open(name, os.O_RDONLY | _O_NOFOLLOW | _O_NONBLOCK, dir_fd=dfd)
                    except OSError:
                        continue  # a link (ELOOP) or vanished: not exported
                    try:
                        info = os.fstat(fd)
                        if not stat.S_ISREG(info.st_mode):
                            continue
                        entry = tarfile.TarInfo(f"{base}/{name}".lstrip("/"))
                        entry.size, entry.mode = info.st_size, 0o644
                        entry.mtime = int(info.st_mtime)
                        with os.fdopen(os.dup(fd), "rb") as fh:
                            tar.addfile(entry, fh)
                    finally:
                        os.close(fd)
    finally:
        os.close(box_fd)


def _remove_beneath(parent_fd: int, name: str, path: str, depth: int = 0) -> int:
    """Remove ``name`` beneath ``parent_fd`` without following any link. Returns files removed."""
    if depth > 256:
        raise BoxError(f"{path!r}: directory tree too deep to remove")
    try:
        info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError as exc:
        raise _path_error(exc, path) from exc
    if not stat.S_ISDIR(info.st_mode):
        os.unlink(name, dir_fd=parent_fd)  # a link is removed itself, never its target
        return 1
    dfd = os.open(name, os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW, dir_fd=parent_fd)
    removed = 0
    try:
        for child in os.listdir(dfd):
            removed += _remove_beneath(dfd, child, f"{path}/{child}", depth + 1)
    finally:
        os.close(dfd)
    os.rmdir(name, dir_fd=parent_fd)
    return removed
