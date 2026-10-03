"""DESIGN MODEL ONLY: inherited process handle + serving-epoch channel.

No production routing/import changes. No account/provider/device calls. This
models the additional trusted launcher/bootstrap boundary required by design.
"""

from __future__ import annotations

import fcntl
import os
import select
import signal
import stat
import threading
import uuid
import weakref
from contextlib import contextmanager

ALIVE, UNKNOWN = "alive", "unknown"
_HANDLES = weakref.WeakSet()


def _after_fork():
    # No inherited Python lock acquisition: another thread may have held one.
    for handle in list(_HANDLES):
        handle._drop_in_child()
    _HANDLES.clear()


os.register_at_fork(after_in_child=_after_fork)


class Observer:
    def __init__(self, label, pidfd, epoch_reader):
        # Only the trusted launch/bootstrap adapter may construct this. A label
        # alone, caller-provided fd, or receipt is never an observation handle.
        self.label = label
        self._lock = threading.RLock()
        self._fds = [pidfd, epoch_reader]
        _HANDLES.add(self)
        try:
            if not label or pidfd < 3 or epoch_reader < 3 or pidfd == epoch_reader:
                raise ValueError("invalid inherited pin")
            for fd in self._fds:
                os.set_inheritable(fd, False)
            if not stat.S_ISFIFO(os.fstat(epoch_reader).st_mode):
                raise ValueError("not an epoch pipe")
            if fcntl.fcntl(epoch_reader, fcntl.F_GETFL) & os.O_ACCMODE != os.O_RDONLY:
                raise ValueError("epoch observer must not own a writer")
            # Validates actual pidfd type and existing permission, without a
            # signal or numeric-PID lookup. EPERM/ENOSYS/other errors refuse.
            signal.pidfd_send_signal(pidfd, 0)
        except BaseException:
            self.close()
            raise

    @classmethod
    def adopt_inherited(cls, metadata):
        # Synthetic trusted bootstrap document, never an endpoint argument.
        pidfd, reader = metadata["pidfd"], metadata["reader"]
        return cls(metadata["epoch"], pidfd, reader)

    def state(self, expected_epoch):
        with self._lock:
            if not self._fds or expected_epoch != self.label:
                return UNKNOWN
            try:
                pidfd, reader = self._fds
                signal.pidfd_send_signal(pidfd, 0)
                poll = select.poll()
                poll.register(pidfd, select.POLLIN | select.POLLHUP | select.POLLERR)
                poll.register(reader, select.POLLIN | select.POLLHUP | select.POLLERR)
                # Data, EOF, death, POLLNVAL and every exceptional observation
                # all hold. Neither contender locks nor PID metadata are read.
                return UNKNOWN if poll.poll(0) else ALIVE
            except (OSError, ValueError):
                return UNKNOWN

    def close(self):
        with self._lock:
            fds, self._fds = self._fds, []
            for fd in fds:
                try:
                    os.close(fd)
                except OSError:
                    pass

    def _drop_in_child(self):
        self._lock = threading.RLock()
        self.close()


class Epoch:
    def __init__(self):
        # Self-capture is before receipt preparation and child launch. Never
        # reconstruct an old issuer by pid/name/start-time/lock-file fallback.
        self.label = uuid.uuid4().hex
        self._lock = threading.RLock()
        self._pidfd = os.pidfd_open(os.getpid(), 0)
        try:
            self._reader, self._writer = os.pipe2(os.O_CLOEXEC | os.O_NONBLOCK)
        except BaseException:
            os.close(self._pidfd)
            raise
        self._active = True
        _HANDLES.add(self)

    @contextmanager
    def serving_gate(self):
        # Caller rejects false while still under the owned gate. The gate stays
        # held through mutation COMMIT; retirement cannot race a checked writer.
        with self._lock:
            yield self._active

    @contextmanager
    def launch_binding(self):
        # Hold through Popen: retirement cannot close/recycle exported numbers.
        # These observer duplicates, unlike the originals, survive a deliberate
        # launch across fork hooks; the writer is NEVER among them.
        with self._lock:
            if not self._active:
                raise RuntimeError("retired epoch")
            fds = []
            try:
                fds.append(os.dup(self._pidfd))
                fds.append(os.dup(self._reader))
                metadata = {"epoch": self.label, "pidfd": fds[0], "reader": fds[1]}
                yield metadata, tuple(fds)
            finally:
                for fd in fds:
                    os.close(fd)

    def local_observer(self):
        with self.launch_binding() as (metadata, _fds):
            pidfd = os.dup(metadata["pidfd"])
            try:
                reader = os.dup(metadata["reader"])
            except BaseException:
                os.close(pidfd)
                raise
            return Observer(self.label, pidfd, reader)

    def retire(self):
        with self._lock:
            if not self._active:
                return
            self._active = False
            # Irreversible epoch retirement. Pidfd alone remains live on exec
            # or orderly stop; closing the only epoch writer closes that gap.
            for name in ("_writer", "_reader", "_pidfd"):
                fd = getattr(self, name)
                setattr(self, name, None)
                os.close(fd)

    def _drop_in_child(self):
        self._lock = threading.RLock()
        self.retire()
