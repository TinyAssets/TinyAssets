"""One bounded file request, executed ONLY inside the owner's gVisor box.

The temporary core state is not authority. Epochs, generations, CAS and retry
receipts belong to the host. No exec or arbitrary method dispatch is exposed.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from tinyassets.boxes.boxd import OwnerBoxd
from tinyassets.boxes.file_wire import MAX_MESSAGE, decode, encode
from tinyassets.boxes.provider import WriteMode


def operate(core: OwnerBoxd, request: dict) -> object:
    handle = core.bind("owner", session_id="file-worker")
    operation, args = request["operation"], request["args"]
    if operation == "read":
        return core.read(handle, **args)
    if operation == "read_many":
        return core.read_many(handle, **args)
    if operation == "stat":
        return core.stat(handle, **args)
    if operation == "list":
        return core.list(handle, **args)
    if operation == "write":
        # CAS is enforced against the trusted host generation before dispatch.
        args = {**args, "mode": WriteMode(args["mode"])}
        return core.write(handle, "file-operation", **args)
    if operation == "remove":
        return core.remove(handle, "file-operation", **args)
    if operation == "usage":
        return core.usage(handle)
    raise ValueError("unsupported file operation")


def main() -> None:
    request = decode(sys.stdin.buffer.read(MAX_MESSAGE + 1))
    # /tmp is the sandbox's bounded memory mount. Never persist a host journal
    # in the owner tree, and never accept a state/root path from the message.
    with tempfile.TemporaryDirectory(prefix="box-file-") as state:
        core = OwnerBoxd(owner_id="owner", owner_root=Path("/home/owner"),
                         state_dir=Path(state))
        try:
            result = {"result": operate(core, request)}
        except Exception as error:
            result = {"error": type(error).__name__}
        finally:
            core.close()
        sys.stdout.buffer.write(encode(result))


if __name__ == "__main__":
    main()
