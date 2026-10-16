"""Fixed in-box exec entry; the host owns limits, cancellation and receipts."""

import os
import struct

from tinyassets.boxes.file_wire import MAX_MESSAGE, decode
from tinyassets.boxes.provider import box_relpath


def _read(size):
    chunks = []
    while size:
        chunk = os.read(0, min(size, 65536))
        if not chunk:
            raise ValueError("truncated exec entry")
        chunks.append(chunk)
        size -= len(chunk)
    return b"".join(chunks)


def main():
    size = struct.unpack("!I", _read(4))[0]
    if size > MAX_MESSAGE:
        raise ValueError("exec entry exceeds bound")
    request = decode(_read(size))
    if "cli" in request:
        from tinyassets.boxes.cli_wire import run

        return run(request)
    # No buffered stdin reader: bytes after this control record belong to the
    # command, including the bounded interactive ta replies.
    path = box_relpath(request["cwd"])
    fd = os.open("/home/owner", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for component in path.split("/") if path else ():
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        os.fchdir(fd)
    finally:
        os.close(fd)
    environment = {"PATH": "/opt/venv/bin:/usr/local/bin:/usr/bin:/bin",
                   "HOME": "/home/owner/.agent-home", "TMPDIR": "/tmp",
                   "PLAYWRIGHT_BROWSERS_PATH": "/opt/ms-playwright",
                   **request["env"]}
    from tinyassets.boxes.network import FORWARDER, PROXY_ENV

    environment.update(PROXY_ENV)
    environment["TINYASSETS_BOX_CENTER"] = path.split("/", 1)[0]
    # Shared loopback forwarder; the only outbound socket is host-bound to this
    # owner. The entire guest (including this child) is stopped before receipt.
    python = "/opt/venv/bin/python"
    os.execvpe(python, [python, "-I", "-B", "-c", FORWARDER,
                       "3128=/run/boxhostd/relay.sock", "--", *request["argv"]], environment)


if __name__ == "__main__":
    main()
