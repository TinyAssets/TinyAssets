"""A deposited credential can be taken back — and the name is free afterwards.

`docs/concerns/2026-08-27-no-reachable-remove-for-http-connections.md`, filed
2026-08-27 and still true on 2026-08-31: `ConnectionLedger` had `revoke_grant`
and `revoke_connection`, and **no surface exposed either**. A user who pasted a
key — including one pasted against a host they did not intend — could not
withdraw it.

That concern also names the trap, which is why removal DELETES rather than
revokes. A connection id is deterministic on `(universe_id, destination)`, and
`connect_http` refuses any re-provision whose row has `revoked_at` set. So a
revoke-based remove burns the destination name forever: remove ``github`` and
you can never deposit ``github`` again. A remove you cannot undo is a trap, not
a remove — `test_the_destination_is_free_afterwards` is the test that says so.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_http_connection_provisioning import (  # noqa: F401 - fixtures
    _connect,
    _http_records,
    _ledger,
    _login,
    _make_universe,
    _reset_auth,
)

# `_reset_auth` is imported deliberately, not incidentally. It is an AUTOUSE
# fixture defined in the module above, and autouse only reaches the module that
# defines it -- so without this import every login here leaks into the next
# test, and `test_an_anonymous_caller_cannot_remove` inherits an identity and
# passes against a gate it never exercised. That is exactly how it failed the
# first time this file was written.


@pytest.fixture
def base(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(root))
    return root


def _remove(universe_id: str, destination: str = "webhook:acme"):
    from tinyassets.api.http_connection import remove_http

    return remove_http(
        universe_id=universe_id, payload=json.dumps({"destination": destination})
    )


def test_removing_takes_away_the_secret_and_the_ledger_rows(base: Path) -> None:
    from tinyassets.api.http_connection import _ids

    udir = _make_universe(base, "u-owner", admin="founder")
    _login("founder")
    _connect("u-owner")
    assert len(_http_records(udir)) == 1

    result = _remove("u-owner")

    assert result["status"] == "removed"
    assert result["secrets_removed"] == 1
    assert result["connection_removed"] is True

    # The secret is gone from the vault, not merely flagged.
    assert _http_records(udir) == []

    # And so are the connection and its grant.
    conn_id, grant_id = _ids(owner="founder", destination="webhook:acme")
    ledger = _ledger(base, "founder")
    assert ledger._get_connection_resource(conn_id) is None
    assert ledger.get_grant(grant_id) is None


def test_the_destination_is_free_afterwards(base: Path) -> None:
    """THE test. A revoke-based remove would burn the name forever.

    `connect_http` refuses a re-provision when `revoked_at is not None`, and the
    id is deterministic on (universe, destination) — so if removal stamped a
    revoke instead of deleting, this second deposit would come back
    `connection_conflict` and the user would have lost that destination for
    good.
    """
    udir = _make_universe(base, "u-owner", admin="founder")
    _login("founder")
    _connect("u-owner")
    _remove("u-owner")

    again = _connect("u-owner")

    assert again.get("status") == "provisioned", again
    assert again.get("error") is None
    assert len(_http_records(udir)) == 1


def test_removing_twice_is_not_an_error(base: Path) -> None:
    """"Take this away" and "it is already away" are the same outcome."""
    _make_universe(base, "u-owner", admin="founder")
    _login("founder")
    _connect("u-owner")

    first = _remove("u-owner")
    second = _remove("u-owner")

    assert first["status"] == "removed"
    assert second["status"] == "removed"
    assert second["secrets_removed"] == 0
    assert second["connection_removed"] is False


def test_removing_something_never_deposited_is_not_an_error(base: Path) -> None:
    _make_universe(base, "u-owner", admin="founder")
    _login("founder")

    result = _remove("u-owner", destination="never:deposited")

    assert result["status"] == "removed"
    assert result["secrets_removed"] == 0


def test_a_removal_never_echoes_the_secret(base: Path) -> None:
    _make_universe(base, "u-owner", admin="founder")
    _login("founder")
    _connect("u-owner")

    blob = json.dumps(_remove("u-owner"))

    assert "sk-SECRET-token" not in blob
    assert "vault://http" not in blob


def test_only_one_destination_is_removed(base: Path) -> None:
    """A remove must not take the neighbours with it."""
    udir = _make_universe(base, "u-owner", admin="founder")
    _login("founder")
    _connect("u-owner")
    _connect("u-owner", destination="webhook:other")
    assert len(_http_records(udir)) == 2

    _remove("u-owner", destination="webhook:acme")

    left = _http_records(udir)
    assert [r["destination"] for r in left] == ["webhook:other"]


def test_an_anonymous_caller_cannot_remove(base: Path, nobody) -> None:
    """No login at all — the suite's canonical way to be unauthenticated.

    An earlier draft logged in, deposited, then called `set_provider(None)` and
    asserted a refusal. The removal SUCCEEDED: clearing the provider does not
    clear an identity the middleware already established, so the test was
    checking nothing. Never trust an authz test that has not been made to fail.
    """
    _make_universe(base, "u-anon", admin="founder")

    result = _remove("u-anon")

    assert result.get("error") == "authentication_required", result


def test_a_write_collaborator_cannot_remove_and_the_secret_survives(base: Path) -> None:
    """Admin-only, mirroring connect_http: removing is at least as sensitive as
    depositing, so a write collaborator must not be able to do it."""
    udir = _make_universe(base, "u-collab", admin="founder", write="collab")
    _login("founder")
    _connect("u-collab")
    assert len(_http_records(udir)) == 1

    _login("collab")
    result = _remove("u-collab")

    assert result == {"error": "not_found", "resource": "connection"}
    assert len(_http_records(udir)) == 1, "the secret survived, as it must"


def test_a_foreign_universe_admin_cannot_remove(base: Path) -> None:
    """Admin of A is nobody on B."""
    _make_universe(base, "u-a", admin="founder-a")
    victim = _make_universe(base, "u-b", admin="founder-b")
    _login("founder-b")
    _connect("u-b")
    assert len(_http_records(victim)) == 1

    _login("founder-a")
    result = _remove("u-b")

    assert result == {"error": "not_found", "resource": "connection"}
    assert len(_http_records(victim)) == 1, "another universe's secret survived"


def test_remove_http_is_reachable_from_the_served_surface() -> None:
    """Defined is not reachable, and reachable is not documented.

    Every gate this week was a capability that existed and could not be reached
    or was not taught: the builder dropped a keyword, the allowlist refused a
    sink, the docs omitted a required field. So this asserts the ROUTE and the
    DOCS, not the function.
    """
    import inspect

    import tinyassets.universe_server as server

    source = inspect.getsource(server)
    assert '"remove_http"' in source, "no route for remove_http on write_graph"
    assert "from tinyassets.api.http_connection import remove_http" in source

    # And an agent has to be told it exists, or it will ask the owner to live
    # with a credential they wanted gone.
    import tinyassets.engine_mcp_server as engine

    # REACHABLE, not resident (2026-09-26): removal lives in the `connections`
    # handbook chapter, which the resident index names. Not knowing it exists
    # costs a fetch, never a wrong effect.
    doc = engine.served_tool_guidance("write_graph")
    assert "remove_http" in doc, "the served docs never mention remove_http"
