#!/usr/bin/env python3
"""G2/G5 owner-box probe on a production copy, across a daemon restart.

Run ``seed`` in the unprivileged daemon container, restart that container, then
run ``verify`` with the same owner/center arguments. The state file is on /data;
no host token or credential is written there. A failed verify leaves evidence
and the scratch owner file in place for investigation.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
import uuid
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tinyassets.boxes.bootstrap import channel_factory
from tinyassets.boxes.production_files import ProductionFileProvider, ProductionHandle
from tinyassets.boxes.provider import BoxAuthError, BoxError, ExecLimits, WriteMode


def _daemon_start() -> str:
    # Field 22, after the parenthesized process name, changes on container restart.
    fields = Path("/proc/1/stat").read_text().rsplit(")", 1)[1].split()
    return fields[19]


def _state_path(raw: str) -> Path:
    path = Path(raw)
    if (path.parent.resolve(strict=True) != Path("/data").resolve(strict=True)
            or not path.name.startswith(".box-acceptance-")
            or path.suffix != ".json"):
        raise ValueError("acceptance state must be /data/.box-acceptance-*.json")
    return path


def _save(path: Path, document: dict) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(document, output, sort_keys=True)
            output.flush()
            os.fsync(output.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _load(path: Path) -> dict:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "r", encoding="utf-8") as source:
        info = os.fstat(source.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
            raise PermissionError("acceptance state identity changed")
        return json.load(source)


def _g2(provider: ProductionFileProvider, *, owner_a: str, center_a: str,
        owner_b: str, session: str, phase: str, path: str, handle_a,
        handle_b) -> None:
    try:
        provider.bind(center_a, account_id=owner_b, turn_id=session)
    except BoxAuthError:
        pass
    else:
        raise AssertionError("G2: foreign owner acquired center A")

    forged = ProductionHandle(center_a, owner_a, handle_a.epoch, session)
    try:
        provider.stat(forged, path)
    except BoxAuthError:
        pass
    else:
        raise AssertionError("G2: a serialized handle gained authority")

    # Exchange the live channels' tokens. This must reach the host and refuse
    # there, rather than relying only on the frontend's object-identity check.
    _, channel_a, token_a = provider._bindings[id(handle_a)]
    _, channel_b, token_b = provider._bindings[id(handle_b)]
    for channel, token in ((channel_a, token_b), (channel_b, token_a)):
        try:
            channel.request({"token": token, "operation": "read",
                             "args": {"path": path, "offset": 0, "max_bytes": 64},
                             "op_id": None})
        except BoxAuthError:
            pass
        else:
            raise AssertionError("G2: foreign token crossed an admitted channel")

    target = f"/home/owner/{center_a}/{path.rsplit('/', 1)[1]}"
    command = ["/bin/sh", "-c", 'test ! -e "$1"', "sh", target]
    ident = provider.start_exec(handle_b, f"acceptance:g2:{phase}:{session}", command,
                                limits=ExecLimits(wall_seconds=30, output_bytes=4096))
    exits = [event for event in provider.stream(handle_b, ident, timeout=40)
             if event.kind == "exit"]
    if len(exits) != 1 or exits[0].exit_code != 0 or exits[0].killed:
        raise AssertionError("G2: owner B sandbox can see owner A scratch file")


def seed(args) -> None:
    session = uuid.uuid4().hex
    path = "/cc/.box-acceptance-" + uuid.uuid4().hex
    body = ("box-acceptance:" + uuid.uuid4().hex).encode()
    op_id = "acceptance:write:" + session
    with_provider = ProductionFileProvider(channel_factory=channel_factory)
    try:
        a = with_provider.bind(args.center_a, account_id=args.owner_a, turn_id=session)
        b = with_provider.bind(args.center_b, account_id=args.owner_b, turn_id=session)
        before = with_provider.usage(a)
        written = with_provider.write(a, op_id, path, body, max_bytes=64,
                                      mode=WriteMode.CREATE)
        after = with_provider.usage(a)
        if (after.generation <= before.generation
                or after.logical_bytes < before.logical_bytes + len(body)
                or after.bound_bytes is None or after.bound_bytes <= 0):
            raise AssertionError("G5: usage did not reflect a bounded owner write")
        _save(args.state, {"owner_a": args.owner_a, "center_a": args.center_a,
                           "owner_b": args.owner_b, "center_b": args.center_b,
                           "session": session, "path": path, "body": body.hex(),
                           "op_id": op_id, "written": asdict(written),
                           "usage": asdict(after), "daemon_start": _daemon_start()})
        _g2(with_provider, owner_a=args.owner_a, center_a=args.center_a,
            owner_b=args.owner_b, session=session,
            phase="seed", path=path, handle_a=a, handle_b=b)
    finally:
        with_provider.close()
    print("G2 seed passed; G5 usage passed; restart the daemon container, then run verify")


def verify(args) -> None:
    state = _load(args.state)
    identity = (args.owner_a, args.center_a, args.owner_b, args.center_b)
    if identity != tuple(state[key] for key in ("owner_a", "center_a", "owner_b", "center_b")):
        raise ValueError("acceptance identity differs from seed")
    if _daemon_start() == state["daemon_start"]:
        raise AssertionError("G5: daemon PID 1 did not restart between phases")
    provider = ProductionFileProvider(channel_factory=channel_factory)
    try:
        a = provider.bind(args.center_a, account_id=args.owner_a, turn_id=state["session"])
        b = provider.bind(args.center_b, account_id=args.owner_b, turn_id=state["session"])
        body = bytes.fromhex(state["body"])
        replay = provider.write(a, state["op_id"], state["path"], body,
                                max_bytes=64, mode=WriteMode.CREATE)
        if asdict(replay) != state["written"]:
            raise AssertionError("G5: persisted write receipt changed after restart")
        data = provider.read(a, state["path"], max_bytes=64)
        usage = provider.usage(a)
        if (data.data != body or usage.generation < state["usage"]["generation"]
                or usage.bound_bytes != state["usage"]["bound_bytes"]):
            raise AssertionError("G5: data, generation or quota changed after restart")
        _g2(provider, owner_a=args.owner_a, center_a=args.center_a,
            owner_b=args.owner_b, session=state["session"],
            phase="verify", path=state["path"], handle_a=a, handle_b=b)
        provider.remove(a, "acceptance:remove:" + state["session"], state["path"])
        if provider.stat(a, state["path"]) is not None:
            raise AssertionError("acceptance scratch file survived cleanup")
        args.state.unlink()
    finally:
        provider.close()
    print("G2 cross-owner and handle checks passed; G5 restart journal, usage and quota passed")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("seed", "verify"))
    for name in ("owner_a", "center_a", "owner_b", "center_b", "state"):
        parser.add_argument("--" + name.replace("_", "-"), required=True)
    args = parser.parse_args()
    if os.geteuid() != 1001:
        parser.error("run as the unprivileged daemon UID 1001 on a production copy")
    if args.owner_a == args.owner_b or args.center_a == args.center_b:
        parser.error("G2 requires distinct owners and centers")
    args.state = _state_path(args.state)
    try:
        (seed if args.phase == "seed" else verify)(args)
    except (BoxError, OSError, AssertionError, ValueError) as exc:
        parser.exit(1, f"box acceptance refused: {exc}\n")


if __name__ == "__main__":
    main()
