"""Bounded children of OwnerBoxd; the persistent box is the OS boundary."""
from __future__ import annotations

import math
import os
import selectors
import signal
import subprocess
import time
import uuid
from pathlib import Path

from tinyassets.boxes.provider import BoxError


class OwnerProcessCancelled(BoxError):
    pass


def run(core, argv, *, cwd, stdin, env, wall_seconds, output_bytes,
        line_handler=None, should_cancel=None, input_source=None, output_handler=None,
        separate_stderr=False, terminal=False):
    from tinyassets.boxes.boxd import _Running
    from tinyassets.owner_public_egress import PROXY_ENV

    if (not argv or any(not isinstance(arg, str) or "\0" in arg for arg in argv)
            or not math.isfinite(wall_seconds) or wall_seconds <= 0
            or type(output_bytes) is not int or output_bytes <= 0):
        raise ValueError("invalid owner process arguments or bounds")
    if should_cancel is not None and should_cancel():
        raise OwnerProcessCancelled("owner cancelled the execution")
    relative = Path(cwd).absolute().relative_to(core._root.absolute())
    if ".." in relative.parts:
        raise PermissionError("process cwd leaves the owner root")
    key = uuid.uuid4().hex
    cc = core._owner_id
    with core._lock(cc):
        with core._running_guard:
            handle = core.bind(cc, session_id=key)
            core._auth_locked(handle)
            box = core._box_fd(cc)
            try:
                directory = core._walk(box, relative.as_posix() if relative.parts else "",
                                       str(cwd), create=False)
            finally:
                os.close(box)
            core._state.hold(cc)
            proc = None
            master = slave = None
            try:
                if terminal:
                    import fcntl
                    import pty
                    import struct
                    import termios

                    master, slave = pty.openpty()
                    attrs = termios.tcgetattr(slave)
                    attrs[3] &= ~termios.ECHO
                    termios.tcsetattr(slave, termios.TCSANOW, attrs)
                    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 4096, 0, 0))
                core._state.bump_generation(cc)
                proc = subprocess.Popen(
                    ["/bin/sh", "-c", 'cd -P -- "/proc/self/fd/$0" || exit 126; exec "$@"',
                     str(directory), *argv],
                    cwd=core._root, env={**os.environ, **PROXY_ENV, **(env or {})},
                    stdin=slave if terminal else subprocess.PIPE,
                    stdout=slave if terminal else subprocess.PIPE,
                    stderr=slave if terminal else (
                        subprocess.PIPE if separate_stderr else subprocess.STDOUT),
                    start_new_session=True, pass_fds=(directory,), close_fds=True,
                )
                if terminal:
                    proc.stdin = os.fdopen(os.dup(master), "wb", buffering=0)
                    proc.stdout = os.fdopen(master, "rb", buffering=0)
                    master = None
                running = _Running(cc, proc, handle)
                core._running[key] = running
            except BaseException:
                if proc is not None:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    proc.wait()
                    proc.stdin.close()
                    proc.stdout.close()
                core._state.release(cc)
                raise
            finally:
                for fd in (master, slave):
                    if fd is not None:
                        os.close(fd)
                os.close(directory)
    output, pending, errors = bytearray(), bytearray(), bytearray()
    incoming = bytearray(stdin)
    total = 0
    deadline = time.monotonic() + wall_seconds

    def bounded(reason):
        error = BoxError("owner process ended: " + reason)
        error.output = bytes(output)
        return error

    def kill():
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    try:
        with selectors.DefaultSelector() as selector:
            for stream in (proc.stdin, proc.stdout):
                os.set_blocking(stream.fileno(), False)
            selector.register(proc.stdout, selectors.EVENT_READ)
            if proc.stderr is not None:
                os.set_blocking(proc.stderr.fileno(), False)
                selector.register(proc.stderr, selectors.EVENT_READ)
            writing = False
            eof = False
            stderr_eof = proc.stderr is None
            while not eof or not stderr_eof or proc.poll() is None:
                if input_source is not None:
                    incoming.extend(input_source())
                    if len(incoming) > output_bytes:
                        raise BoxError("owner process input exceeded its bound")
                if running.cancel.is_set() or should_cancel is not None and should_cancel():
                    raise OwnerProcessCancelled("owner cancelled the execution")
                if time.monotonic() >= deadline:
                    raise bounded("timeout")
                if proc.poll() is not None:
                    kill()  # descendants cannot hold output or continue writing after exit
                if incoming and not writing and not proc.stdin.closed:
                    selector.register(proc.stdin, selectors.EVENT_WRITE)
                    writing = True
                elif (not incoming and line_handler is None and input_source is None
                      and not proc.stdin.closed):
                    if writing:
                        selector.unregister(proc.stdin)
                        writing = False
                    proc.stdin.close()
                for event, _ in selector.select(.05):
                    if event.fileobj is proc.stdin:
                        try:
                            sent = os.write(proc.stdin.fileno(), incoming[:65536])
                            del incoming[:sent]
                        except BrokenPipeError:
                            incoming.clear()
                        if not incoming:
                            selector.unregister(proc.stdin)
                            writing = False
                        continue
                    try:
                        data = os.read(event.fileobj.fileno(), 65536)
                    except OSError as exc:
                        import errno

                        if not terminal or exc.errno != errno.EIO:
                            raise
                        data = b""  # Linux PTY EOF after its slave closes.
                    if not data:
                        selector.unregister(event.fileobj)
                        if event.fileobj is proc.stdout:
                            eof = True
                        else:
                            stderr_eof = True
                        continue
                    total += len(data)
                    if total > output_bytes:
                        if line_handler is None:
                            output.extend(data[:max(0, output_bytes - len(output))])
                        raise bounded("output_limit")
                    if event.fileobj is proc.stderr:
                        errors.extend(data)
                        continue
                    if output_handler is not None:
                        output_handler(data)
                    if line_handler is None:
                        output.extend(data)
                    else:
                        pending.extend(data)
                        while b"\n" in pending:
                            line, _, rest = pending.partition(b"\n")
                            pending = bytearray(rest)
                            reply = line_handler(bytes(line))
                            if reply is None:
                                output.extend(line + b"\n")
                            else:
                                incoming.extend(reply + b"\n")
                                if len(incoming) > output_bytes:
                                    raise BoxError("owner process input exceeded its bound")
            if pending:
                raise BoxError("owner process ended with an incomplete frame")
            return subprocess.CompletedProcess(list(argv), proc.returncode,
                                               bytes(output), bytes(errors))
    finally:
        kill()
        proc.wait()
        proc.stdin.close()
        proc.stdout.close()
        if proc.stderr is not None:
            proc.stderr.close()
        with core._lock(cc):
            core._state.bump_generation(cc)
            core._state.release(cc)
            with core._running_guard:
                core._running.pop(key, None)
            running.done.set()
