"""Synchronous, bounded process handoff on an already admitted owner box."""

from __future__ import annotations

import contextlib
import hashlib
import subprocess
import threading
from collections.abc import Callable, Mapping, Sequence

from tinyassets.boxes.provider import BoxError, BoxOperationRefused, ExecLimits
from tinyassets.control_plane.binding import SessionBinding


class BoxExecCancelled(BoxError):
    """The caller's cancel predicate stopped the execution while it ran."""


def run_bound(
    binding: SessionBinding, purpose: str, argv: Sequence[str], *,
    stdin: bytes = b"", env: Mapping[str, str] | None = None,
    cwd: str = "/cc", wall_seconds: float, output_bytes: int,
    operation_id: str | None = None,
    line_handler: Callable[[bytes], bytes | None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    """Run once; a lost response never causes a fresh operation to be issued."""
    binding.validate(binding.handle.account_id, binding.universe_dir)
    op_id = operation_id or binding.operation_id(purpose)
    limits = ExecLimits(wall_seconds=wall_seconds, output_bytes=output_bytes)
    kwargs = dict(stdin=stdin, env=env, cwd=cwd, limits=limits,
                  interactive_stdin=line_handler is not None)
    try:
        exec_id = binding.provider.start_exec(binding.handle, op_id, argv, **kwargs)
    except BoxOperationRefused:
        raise
    except Exception:
        # The first dispatch may have succeeded. Only its same ID may be retried.
        try:
            exec_id = binding.provider.start_exec(binding.handle, op_id, argv, **kwargs)
        except Exception as exc:
            raise BoxError("owner box execution outcome is unknown") from exc

    finished = threading.Event()
    cancelled = threading.Event()
    if should_cancel is not None:
        def watch():
            while not finished.wait(0.25):
                if should_cancel():
                    cancelled.set()
                    with contextlib.suppress(Exception):
                        binding.provider.cancel(binding.handle, exec_id)
                    return
        threading.Thread(target=watch, daemon=True, name="box-exec-cancel").start()
    try:
        output = bytearray()
        pending = bytearray()
        offset = 0
        for attempt in range(2):
            try:
                for event in binding.provider.stream(
                    binding.handle, exec_id, from_offset=offset, timeout=wall_seconds + 5,
                ):
                    if event.kind == "output":
                        if event.offset != offset or offset + len(event.data) > output_bytes:
                            raise BoxError("owner box output exceeded its bound or changed offset")
                        offset += len(event.data)
                        if line_handler is None:
                            output.extend(event.data)
                        else:
                            pending.extend(event.data)
                            while b"\n" in pending:
                                line, _, rest = pending.partition(b"\n")
                                pending = bytearray(rest)
                                reply = line_handler(bytes(line))
                                if reply is None:
                                    output.extend(line + b"\n")
                                else:
                                    request_id = hashlib.sha256(
                                        op_id.encode() + b"\0" + line,
                                    ).hexdigest()[:32]
                                    binding.provider.send_stdin(
                                        binding.handle, exec_id, request_id, reply + b"\n",
                                    )
                    elif event.kind == "exit":
                        if cancelled.is_set() and event.killed:
                            raise BoxExecCancelled("owner cancelled the execution")
                        if pending:
                            raise BoxError("owner box execution ended with an incomplete frame")
                        if event.killed or type(event.exit_code) is not int:
                            killed = event.killed or "unknown"
                            raise BoxError(f"owner box execution ended: {killed}")
                        return subprocess.CompletedProcess(
                            list(argv), event.exit_code, bytes(output), b"",
                        )
                raise ConnectionError("owner box stream ended without exit")
            except BoxError:
                raise
            except Exception as exc:
                if attempt:
                    raise BoxError("owner box execution outcome is unknown") from exc
        raise BoxError("owner box execution outcome is unknown")
    finally:
        finished.set()
