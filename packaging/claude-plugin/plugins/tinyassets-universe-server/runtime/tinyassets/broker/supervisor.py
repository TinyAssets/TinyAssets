"""Start and keep the broker process alive from the daemon (S6, part 4).

``start_broker(data_root)`` spawns ``tinyassets.broker.process`` with the
daemon's environment minus the platform's own secrets
(``platform_secrets.child_env``), waits for its socket, runs the owner's fence
barrier, and publishes what a caller in this deployment needs to open streams:

    <data_root>/.broker/owner.json   (mode 0600: socket path, generation, token)

A supervisor thread restarts the broker if it exits. The fence and the op
records live in the broker's state directory, so a restarted broker reloads
them before it serves; the same owner re-runs the barrier with the same
proof, which is idempotent and returns the same token.

Selected by ``TINYASSETS_CREDENTIAL_BROKER=process``; unset, callers keep the
per-proxy worker. Until the per-role uid split, the daemon refuses to start
with it selected (:func:`start_broker`, v1 deviation (c)). The switch is
temporary: once the broker is proven it is the only path (change
``broker-streaming-contract`` task 2.8).
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
from hashlib import sha256
from pathlib import Path
from typing import Any

from tinyassets import rpc_frames as rf

_LOG = logging.getLogger(__name__)

ENV_SWITCH = "TINYASSETS_CREDENTIAL_BROKER"
PROCESS = "process"
OWNER_FILE = "owner.json"
_START_TIMEOUT_S = 30.0
_SUPERVISE_INTERVAL_S = 2.0


def broker_selected() -> bool:
    return (os.environ.get(ENV_SWITCH) or "").strip().lower() == PROCESS


def broker_dir(data_root: Path) -> Path:
    return Path(data_root) / ".broker"


def read_owner(data_root: Path) -> dict[str, Any] | None:
    """The published owner pair, or ``None`` when no broker serves this root."""
    try:
        document = json.loads((broker_dir(data_root) / OWNER_FILE).read_text("utf-8"))
    except (FileNotFoundError, ValueError):
        return None
    if not isinstance(document, dict) or not {"socket", "generation", "token"} <= set(document):
        return None
    return document if Path(document["socket"]).exists() else None


class BrokerSupervisor:
    def __init__(self, data_root: Path, *, allow_test_fixtures: bool = False,
                 child_env: Any = None) -> None:
        self._root = Path(data_root)
        self._child_env = child_env
        self._dir = broker_dir(self._root)
        self._socket = self._dir / "broker.sock"
        try:
            previous = json.loads((self._dir / OWNER_FILE).read_text("utf-8"))
        except FileNotFoundError:
            previous = {"generation": 0}
        self._generation = int(previous["generation"]) + 1
        self._proof = secrets.token_urlsafe(32)
        self._allow_test_fixtures = allow_test_fixtures
        self._process: subprocess.Popen | None = None
        self._stopping = threading.Event()

    @property
    def socket_path(self) -> Path:
        return self._socket

    def _spawn(self) -> None:
        child_env = self._child_env
        if child_env is None:
            # The platform's own secrets never reach the broker (#4267).
            from tinyassets.platform_secrets import child_env

        self._dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        argv = [
            sys.executable, "-m", "tinyassets.broker.process",
            "--socket", str(self._socket), "--state", str(self._dir / "state"),
            "--data-root", str(self._root), "--owner-uid", str(os.getuid()),
            "--generation", str(self._generation),
            "--proof-sha256", sha256(self._proof.encode("utf-8")).hexdigest(),
        ]
        if self._allow_test_fixtures:
            argv.append("--allow-test-fixtures")
        self._socket.unlink(missing_ok=True)
        self._process = subprocess.Popen(argv, env=child_env(os.environ), close_fds=True)
        deadline = time.monotonic() + _START_TIMEOUT_S
        while not self._socket.exists():
            if self._process.poll() is not None:
                raise RuntimeError(f"the broker exited during startup ({self._process.returncode})")
            if time.monotonic() > deadline:
                raise RuntimeError("the broker did not start within its timeout")
            time.sleep(0.05)
        self._fence()

    def _fence(self) -> None:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(_START_TIMEOUT_S)
            sock.connect(str(self._socket))
            sock.sendall(rf.control(rf.CONNECTION, {"op": "FENCE", "generation": self._generation,
                                                    "proof": self._proof}))
            answer = rf.read_frame_blocking(sock)
        document = answer.control() if answer is not None else {}
        if document.get("op") != "FENCE_ACK":
            raise RuntimeError("the broker refused the owner's fence")
        owner = {"socket": str(self._socket), "generation": document["generation"],
                 "token": document["token"]}
        target = self._dir / OWNER_FILE
        temp = target.with_suffix(".tmp")
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(owner, handle)
        os.replace(temp, target)

    def start(self) -> None:
        self._spawn()
        threading.Thread(target=self._supervise, name="broker-supervisor", daemon=True).start()

    def _supervise(self) -> None:
        while not self._stopping.wait(_SUPERVISE_INTERVAL_S):
            process = self._process
            if process is not None and process.poll() is None:
                continue
            _LOG.warning("credential broker exited (%s); restarting",
                         None if process is None else process.returncode)
            try:
                self._spawn()
            except Exception:  # noqa: BLE001 - the supervisor never dies
                _LOG.exception("credential broker restart failed")

    def stop(self) -> None:
        self._stopping.set()
        process = self._process
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
        self._socket.unlink(missing_ok=True)


class BrokerUidSplitRequired(RuntimeError):
    """The broker was selected on a host where every role shares one uid."""


def start_broker(data_root: Path | None = None) -> None:
    """Daemon startup: nothing when the broker is not selected; a loud refusal when it is.

    v1 deviation (c) of ``broker-streaming-contract``: the daemon, its engine
    children and the broker share one uid, so any same-uid child can read
    ``owner.json`` and claim another owner's principal on the owner channel.
    The broker serves production only after the per-role uid split (daemon /
    engine children / broker). Until then, selecting it fails the daemon's
    start instead of quietly running without that boundary; the split's change
    replaces this refusal with ``BrokerSupervisor(...).start()``.
    """
    if not broker_selected():
        return None
    raise BrokerUidSplitRequired(
        f"{ENV_SWITCH}={PROCESS} needs the per-role uid split (daemon / engine children / "
        f"broker): every role here runs as uid {getattr(os, 'getuid', lambda: '?')()}, so "
        f"the owner channel would "
        f"trust any same-uid child. Unset {ENV_SWITCH} until the split is deployed.")
