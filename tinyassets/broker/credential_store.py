"""Daemon-facing credential operations. The broker is the only process that unwraps.

Production daemon calls cross the broker socket. Tests install the in-memory
root and run the same operations in process. Neither path writes a cleartext
vault file.
"""

from __future__ import annotations

import json
import os
import socket
import struct
from pathlib import Path
from typing import Any

from tinyassets.broker.root_key import KeyServiceUnavailable, ReconnectRequired, RootKeyError


def _universe(universe_dir: str | Path) -> tuple[Path, str]:
    universe = Path(universe_dir).resolve(strict=False)
    return universe.parent, universe.name


def _store_absent(data_root: Path) -> bool:
    """A data root with no key database has no sealed records.

    The daemon only checks that the file is absent. It does not open it. Once
    the file exists, an unreachable broker is an outage, not an empty vault.
    ``.broker`` is mode 0700 and uid 1002, so the daemon's stat is ``EACCES``
    when the database is there. That is not an empty store.
    """
    path = data_root / ".broker" / "vault-keys.db"
    try:
        os.lstat(path)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return False


def load_center(universe_dir: str | Path) -> list[dict[str, Any]]:
    data_root, _center = _universe(universe_dir)
    if _store_absent(data_root):
        return []
    return _rpc(universe_dir, {"action": "load"})["records"]


def has_center(universe_dir: str | Path) -> bool:
    data_root, _center = _universe(universe_dir)
    if _store_absent(data_root):
        return False
    return bool(_rpc(universe_dir, {"action": "has"})["present"])


def replace_center(
    universe_dir: str | Path,
    records: list[dict[str, Any]],
    *,
    owner: str,
) -> None:
    if not owner:
        raise RootKeyError("account is required to seal a credential")
    _rpc(universe_dir, {"action": "replace", "owner": owner, "records": records})


def list_center(universe_dir: str | Path) -> list[dict[str, Any]]:
    data_root, _center = _universe(universe_dir)
    if _store_absent(data_root):
        return []
    return _rpc(universe_dir, {"action": "list"})["items"]


def owner_for_center(universe_dir: str | Path) -> str:
    data_root, _center = _universe(universe_dir)
    if _store_absent(data_root):
        return ""
    return str(_rpc(universe_dir, {"action": "owner"}).get("owner") or "")


def pending_reconnects(universe_dir: str | Path) -> list[dict[str, str]]:
    data_root, _center = _universe(universe_dir)
    if _store_absent(data_root):
        return []
    return _rpc(universe_dir, {"action": "reconnects"})["items"]


def health(data_root: str | Path) -> dict[str, Any]:
    """Probe KMS. A failure is ``status=unavailable`` and names the key service."""
    return _rpc_root(Path(data_root), "health", {})


def local_operation(
    *, data_root: Path, command_center: str, document: dict[str, Any]
) -> dict[str, Any]:
    from tinyassets.broker.key_service import broker_service

    service = broker_service(data_root)
    action = document.get("action")
    if action == "load":
        try:
            return {"records": service.load_center(command_center)}
        except KeyServiceUnavailable as exc:
            return {"error": exc.reason}
        except ReconnectRequired as exc:
            return {"error": exc.reason}
    if action == "has":
        return {"present": service.has_center(command_center)}
    if action == "list":
        return {"items": service.list_center(command_center)}
    if action == "reconnects":
        return {"items": service.pending_reconnects(command_center)}
    if action == "replace":
        try:
            service.replace_center(
                owner=str(document.get("owner") or ""),
                center=command_center,
                records=list(document.get("records") or []),
            )
        except (KeyServiceUnavailable, ReconnectRequired, RootKeyError) as exc:
            reason = getattr(exc, "reason", "refused")
            return {"error": reason}
        return {"replaced": True}
    if action == "health":
        try:
            return service.probe()
        except KeyServiceUnavailable:
            snapshot = service.health_snapshot()
            snapshot["status"] = "unavailable"
            snapshot["reason"] = "key service"
            return snapshot
    if action == "owner":
        try:
            return {"owner": service.owner_for_center(command_center)}
        except RootKeyError:
            return {"owner": ""}
    raise ValueError("invalid credential vault operation")


def _rpc(universe_dir: str | Path, document: dict[str, Any]) -> dict[str, Any]:
    data_root, center = _universe(universe_dir)
    result = _rpc_root(data_root, center, document)
    if result.get("error") == "key_service_unavailable":
        raise KeyServiceUnavailable("key service unavailable")
    if result.get("error") == "reconnect_required":
        raise ReconnectRequired("credential vault authentication failed")
    if result.get("error"):
        raise RootKeyError("credential vault operation was refused")
    return result


class _PublishedTestBroker:
    """Socket the in-process test double published for a child of this process.

    Production never constructs one. The flag is refused when
    ``TINYASSETS_ENV=production``, and the value is not a broker path the
    daemon can discover on its own.
    """

    def __init__(self, socket_path: Path, generation: int, token: str) -> None:
        self.socket_path = socket_path
        self._pair = (generation, token)

    def fence(self) -> tuple[int, str]:
        return self._pair

    def verify_broker(self, channel: socket.socket) -> None:
        from tinyassets.broker.supervisor import BrokerUidSplitRequired

        peer = struct.unpack("3i", channel.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        if peer[1] != os.getuid():
            raise BrokerUidSplitRequired("broker peer does not have the broker identity")


def _published_test_broker(data_root: Path) -> _PublishedTestBroker | None:
    if os.environ.get("TINYASSETS_ENV") == "production":
        return None
    if os.environ.get("TINYASSETS_VAULT_TEST_DOUBLE") != "1":
        return None
    raw = os.environ.get("TINYASSETS_VAULT_TEST_BROKER")
    if not raw:
        return None
    try:
        document = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(document, dict):
        return None
    entry = document.get(str(Path(data_root).resolve()))
    if not isinstance(entry, dict):
        return None
    socket_path = entry.get("socket")
    generation = entry.get("generation")
    token = entry.get("token")
    if (
        not isinstance(socket_path, str)
        or not socket_path.startswith("/")
        or type(generation) is not int
        or generation < 1
        or not isinstance(token, str)
        or not token
    ):
        return None
    return _PublishedTestBroker(Path(socket_path), generation, token)


def _in_broker() -> bool:
    from tinyassets.broker.key_service import broker_process

    return broker_process()


def _rpc_root(data_root: Path, command_center: str, document: dict[str, Any]) -> dict[str, Any]:
    # The broker's own HTTP resolver loads vault records. A nested socket call
    # from that process deadlocks the single broker thread and the proxy
    # reports a generic outbound failure.
    if _in_broker():
        if command_center == "health":
            return local_operation(
                data_root=data_root,
                command_center="health",
                document={"action": "health"},
            )
        return local_operation(
            data_root=data_root, command_center=command_center, document=document
        )
    from tinyassets import rpc_frames as rf
    from tinyassets.broker.supervisor import get_supervisor

    supervisor = get_supervisor(data_root)
    if supervisor is None:
        supervisor = _published_test_broker(data_root)
    if supervisor is None:
        raise KeyServiceUnavailable("the credential broker is not running")
    generation, token = supervisor.fence()
    principal = str(document.get("owner") or command_center)
    wire = {
        "op": "CREDENTIAL_VAULT",
        "principal": principal,
        "command_center": command_center if command_center != "health" else principal,
        "generation": generation,
        "token": token,
        "document": document if command_center != "health" else {"action": "health"},
    }
    if command_center == "health":
        wire["command_center"] = "health"
        wire["document"] = {"action": "health"}
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
        channel.settimeout(30)
        try:
            channel.connect(os.fspath(supervisor.socket_path))
            supervisor.verify_broker(channel)
            channel.sendall(rf.control(rf.CONNECTION, wire))
            frame = rf.read_frame_blocking(channel)
        except OSError as exc:
            raise KeyServiceUnavailable("the credential broker is not running") from exc
    if frame is None or frame.kind != rf.CONTROL or frame.stream != rf.CONNECTION:
        raise KeyServiceUnavailable("credential vault outcome unknown")
    answer = frame.control()
    if answer.get("op") != "CREDENTIAL_RESULT":
        reason = answer.get("reason") or "key_service_unavailable"
        if reason == "reconnect_required":
            raise ReconnectRequired("credential vault authentication failed")
        raise KeyServiceUnavailable("key service unavailable")
    return answer["result"]
