"""Linux key persistence, plus the real owner app across broker sleep/wake."""

import json
import os
from concurrent.futures import ThreadPoolExecutor

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from tests.support.admission import drive
from tests.test_broker_server import broker  # noqa: F401
from tests.test_owner_router import admitted, boxes, initialized_ledger  # noqa: F401
from tinyassets.boxes.session_key import load

pytestmark = pytest.mark.verifies('owner-box:Owner session sealing survives box restarts')


def test_owner_daemon_key_survives_sleep_wake_and_is_not_shared(boxes, broker):  # noqa: F811
    roots = boxes.roots
    keys = {owner: (root / ".session-seal.key").read_bytes()
            for owner, root in roots.items()}
    distinct = keys["alice"] != keys["bob"]
    assert distinct, "owners must have different seal keys"

    def verify(owner):
        proof = json.loads((roots[owner] / "seal-proof.json").read_text())
        plaintext = AESGCM(keys[owner]).decrypt(bytes.fromhex(proof["nonce"]),
                                               bytes.fromhex(proof["ciphertext"]), None)
        assert plaintext == b"owner startup"

    for owner in roots:
        verify(owner)
    uid = boxes.manager.admission.identities.owner_machine("alice")
    first_pid = boxes.runtime.processes[uid].pid
    drive(broker.server.routing.sleep("alice"))
    assert not boxes.runtime.alive(uid)
    drive(broker.server.routing.wake("alice"))
    assert boxes.runtime.processes[uid].pid != first_pid
    unchanged = (roots["alice"] / ".session-seal.key").read_bytes() == keys["alice"]
    assert unchanged, "key changed on wake"
    verify("alice")


def test_concurrent_first_loads_persist_one_private_key(tmp_path):
    with ThreadPoolExecutor(max_workers=8) as pool:
        keys = list(pool.map(lambda _: load(tmp_path), range(16)))
    assert len(set(keys)) == 1, "concurrent starts disagreed"
    assert len(keys[0]) == 32
    assert (tmp_path / ".session-seal.key").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("kind", ["short", "long", "symlink", "hardlink", "fifo", "public"])
def test_existing_unsafe_key_is_never_replaced(tmp_path, kind):
    path = tmp_path / ".session-seal.key"
    if kind in {"symlink", "hardlink"}:
        target = tmp_path / "other"
        target.write_bytes(b"x" * 32)
        target.chmod(0o600)
        if kind == "symlink":
            path.symlink_to(target)
        else:
            os.link(target, path)
    elif kind == "fifo":
        os.mkfifo(path, 0o600)
    else:
        path.write_bytes(b"x" * {"short": 31, "long": 33, "public": 32}[kind])
        path.chmod(0o644 if kind == "public" else 0o600)
    inode = path.lstat().st_ino
    with pytest.raises((OSError, RuntimeError)):
        load(tmp_path)
    assert path.lstat().st_ino == inode


def test_interrupted_first_start_never_leaves_an_unloadable_key(tmp_path):
    # A crash after the write began leaves only the aside file; the key is absent.
    partial = tmp_path / ".session-seal.key.partial"
    partial.write_bytes(b"")
    partial.chmod(0o600)
    key = load(tmp_path)
    assert len(key) == 32
    assert not partial.exists()
    assert load(tmp_path) == key
