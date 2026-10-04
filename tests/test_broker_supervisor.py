"""The broker cannot be switched on while every role shares one uid (v1 deviation (c))."""

from __future__ import annotations

import pytest

from tinyassets.broker.supervisor import (
    ENV_SWITCH,
    PROCESS,
    BrokerUidSplitRequired,
    start_broker,
)


def test_selecting_the_broker_on_a_shared_uid_host_refuses_to_start(monkeypatch, tmp_path):
    monkeypatch.setenv(ENV_SWITCH, PROCESS)
    with pytest.raises(BrokerUidSplitRequired, match="uid split"):
        start_broker(tmp_path)


def test_unselected_the_daemon_starts_without_it(monkeypatch, tmp_path):
    monkeypatch.delenv(ENV_SWITCH, raising=False)
    assert start_broker(tmp_path) is None
