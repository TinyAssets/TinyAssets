"""Daemon restart and expired login cleanup stay inside the owner box."""
# ruff: noqa: F811
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.support.broker_ipc import broker  # noqa: F401
from tests.test_owner_broker_egress import box  # noqa: F401
from tests.test_subscription_source_integration import connected  # noqa: F401


@pytest.mark.parametrize("case", ["restart", "expired_login"])
def test_cleanup_stale_native_work(connected, monkeypatch, case):
    from tinyassets import owner_daemon, owner_runtime, universe_server
    from tinyassets.providers import subscription_runtime as native
    from tinyassets.providers.subscription_lease import tmpfs_root

    if case == "restart":
        with tempfile.TemporaryDirectory(prefix="ta-native-", dir=tmpfs_root()) as directory:
            stale = Path(directory)
            (stale / "token").write_text("credential")
            monkeypatch.setattr(owner_runtime, "start", lambda relay: connected.owner)
            monkeypatch.setattr(universe_server, "create_streamable_http_app", lambda: object())
            secret = connected.home / "test-secret"
            secret.write_text("s" * 43)
            owner_daemon.create_app(relay=Path("unused"), secret_file=secret)
            assert not stale.exists()
        return

    joins = []
    class Installing:
        alive = True
        def is_alive(self):
            return self.alive
        def join(self, timeout=None):
            assert timeout is not None and 0 < timeout <= 10
            joins.append(timeout)
            connected.directory.mkdir(parents=True, exist_ok=True)
            (connected.directory / "late-install").write_text("package")
            self.alive = False

    login = SimpleNamespace(runtime=connected, handle="expired", expires=0,
                            cancelled=threading.Event(), thread=Installing())
    monkeypatch.setattr(native, "_logins", {login.handle: login})
    with pytest.raises(PermissionError):
        native.login_operation("poll", flow=login.handle)
    native.disconnect_source("fake-cli")
    assert joins and login.cancelled.is_set()
    assert not connected.directory.exists()
