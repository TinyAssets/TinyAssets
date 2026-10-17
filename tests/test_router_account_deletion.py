"""Identity erasure stays at the shared door after the box-only sweep cutover."""
import urllib.error

import pytest

from tinyassets import router_account_deletion as account_deletion
from tinyassets.router_account_deletion import AccountDeletionError

A = "user_01AAAAAAAAAAAAAAAAAAAAAAAA"


class _Resp:
    def __init__(self, status: int) -> None:
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_workos_deletion_is_not_configured_without_a_key(monkeypatch):
    monkeypatch.delenv("WORKOS_API_KEY", raising=False)
    assert account_deletion.delete_workos_user(A) == "not_configured"


def test_workos_deletion_skips_non_workos_principals(monkeypatch):
    monkeypatch.setenv("WORKOS_API_KEY", "sk_test_x")
    calls: list[str] = []
    assert account_deletion.delete_workos_user(
        "host:jonathan",
        request=lambda req, timeout: calls.append(req.full_url) or _Resp(200),
    ) == "not_applicable"
    assert calls == []


def test_workos_deletion_sends_a_delete_and_treats_gone_as_done(monkeypatch):
    monkeypatch.setenv("WORKOS_API_KEY", "sk_test_x")
    seen: list[tuple[str, str]] = []

    def _ok(req, timeout):
        seen.append((req.get_method(), req.full_url))
        assert req.get_header("Authorization") == "Bearer sk_test_x"
        return _Resp(200)

    assert account_deletion.delete_workos_user(A, request=_ok) == "deleted"
    assert seen == [("DELETE", f"https://api.workos.com/user_management/users/{A}")]

    def _gone(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 404, "gone", {}, None)

    assert account_deletion.delete_workos_user(A, request=_gone) == "deleted"


def test_workos_deletion_failures_raise_without_the_key(monkeypatch):
    monkeypatch.setenv("WORKOS_API_KEY", "sk_test_SECRET")

    def _fail(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 500, "boom sk_test_SECRET", {}, None)

    with pytest.raises(AccountDeletionError) as exc:
        account_deletion.delete_workos_user(A, request=_fail)
    assert "500" in str(exc.value) and "SECRET" not in str(exc.value)

    def _timeout(req, timeout):
        raise TimeoutError()

    with pytest.raises(AccountDeletionError):
        account_deletion.delete_workos_user(A, request=_timeout)
