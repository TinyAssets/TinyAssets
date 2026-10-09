"""The daemon's side of a workspace git operation (``workspace_worker``).

No git and no network here: this module's whole job is authority, a route and
a cell, so that is what these tests watch. The route is the REAL
``git_egress`` registry on a stand-in proxy, the authority is the REAL broker
transaction through the in-process broker double, and the cell seam
(``role_remote_git.run``) is replaced so the request and the answer are
observable.

What every test asserts about the request is the same thing: it names a grant,
never a credential, and never a host path.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tinyassets import git_egress, role_remote_git
from tinyassets import workspace_worker as ww
from tinyassets.storage.outbound_connections import ConnectionLedger

UNIVERSE = "universe-worker"
REPO = "owner/name"
HOST = "github.com"
SHA = "a" * 40
TOKEN = "ghp_WORKERTOKEN0123456789ABCDEFGHIJKL"


def _principal() -> str:
    from tinyassets.auth.middleware import current_identity

    return current_identity().user_id


@pytest.fixture()
def rig(tmp_path: Path):
    """A data root with one git connection, its grant, and the proxy seams."""
    principal = _principal()
    data_root = tmp_path / "data"
    universe_dir = data_root / UNIVERSE
    universe_dir.mkdir(parents=True)
    ledger = ConnectionLedger(
        data_root / ".broker" / "outbound.db", data_root=data_root,
        verify_authenticated_principal=lambda: principal,
    )
    ledger.create_connection(
        connection_id="conn-git",
        owner_user_id=principal,
        connection_class="outbound-http",
        scopes=(f"git_read:{REPO}", f"git_write:{REPO}"),
        provider="http",
        destination=f"{HOST}/{REPO}",
        credential_ref="vault://http/github",
        connection_type="http",
        auth_scheme="bearer",
        allowed_endpoints=[{"host": HOST, "path_template": f"/{REPO}", "methods": ["GET"]}],
    )
    ledger.grant_connection(
        grant_id="grant-git", connection_id="conn-git", owner_user_id=principal,
        universe_id=UNIVERSE,
    )
    return universe_dir


class _Proxy:
    """Stands in for the command center's egress proxy: an identity to key on."""


class _Client:
    """Stands in for the broker client the route hands traffic to."""

    def _fence(self):
        return (1, "proof")


@pytest.fixture()
def seams(monkeypatch: pytest.MonkeyPatch):
    """Replace the two things a test process cannot have: the proxy and the cell."""
    proxy = _Proxy()
    state: dict[str, Any] = {"requests": [], "routes": [], "answer": None, "socket": None}

    def _proxy(universe_dir):
        state["socket"] = Path(universe_dir).parent / ".universe-sidecars" / "egress.sock"
        return proxy, state["socket"]

    def _broker_client(request, universe_dir):
        return _Client()

    def _run(request, *, universe_dir, principal, egress_socket):
        state["requests"].append(request)
        state["routes"].append(sorted(
            key for key in git_egress._routes if key[0] == id(proxy)
        ))
        state["egress_socket"] = egress_socket
        answer = state["answer"]
        if answer is None:
            answer = {"ok": True, "resolved_sha": SHA, "bytes": 7,
                      "ref_name": "refs/tiny/export",
                      "lease": "/".join((*request.get("lease_parent", ()),
                                         request.get("lease_name", "x"))),
                      "content": "repo"}
        return answer

    monkeypatch.setattr(ww, "_proxy", _proxy)
    monkeypatch.setattr(ww, "_broker_client", _broker_client)
    monkeypatch.setattr(role_remote_git, "run", _run)
    return state


def _checkout(universe_dir: Path, **over: Any) -> dict[str, Any]:
    request = {
        "op": "checkout",
        "universe_dir": str(universe_dir),
        "principal": _principal(),
        "agent": "workspace-run-abc",
        "grant_id": "grant-git",
        "connection_id": "conn-git",
        "host": HOST,
        "owner_repo": REPO,
        "ref": "main",
        "storage": "scratch",
        "lease_parent": ["workspaces", "scratch"],
        "lease_name": "b" * 32,
        "checkout_ref": "tiny/u/checkout",
    }
    request.update(over)
    return ww.execute_workspace_operation(request)


# --------------------------------------------------------------------------- #
# the request the cell receives
# --------------------------------------------------------------------------- #


def test_a_checkout_reaches_the_cell_with_no_credential_of_any_kind(rig, seams) -> None:
    answer = _checkout(rig)
    assert answer["ok"] is True, answer
    request = seams["requests"][0]
    text = json.dumps(request)
    assert TOKEN not in text
    assert "vault://" not in text, "a credential reference is a credential"
    assert "credential_ref" not in request
    assert "principal" not in request, "the cell is already the owner"
    assert str(rig) not in text, "a cell resolves its own paths"
    assert set(request) == {
        "op", "options", "timeout_s", "host", "repo", "storage",
        "lease_parent", "lease_name", "ref", "checkout_ref",
    }


def test_the_cell_is_told_to_use_the_in_cell_proxy_and_the_route(rig, seams) -> None:
    """The rewrite is the whole transport: a URL git builds any other way
    reaches nothing, because the cell has no network of its own."""
    _checkout(rig)
    options = seams["requests"][0]["options"]
    assert options[:2] == ["-c", "http.proxy=http://127.0.0.1:3128"]
    rewrites = [value for flag, value in zip(options, options[1:]) if flag == "-c"]
    assert any(
        value.startswith(f"url.http://{git_egress.HOST}/")
        and value.endswith(f".insteadOf=https://{HOST}/{REPO}.git")
        for value in rewrites
    ), rewrites


def test_the_egress_socket_handed_over_is_the_command_centers_own(rig, seams) -> None:
    _checkout(rig)
    assert seams["egress_socket"] == seams["socket"]


def test_a_route_exists_while_the_cell_runs_and_is_gone_after(rig, seams) -> None:
    _checkout(rig)
    assert len(seams["routes"][0]) == 1, "exactly one route for one grant"
    assert git_egress._routes == {}, "the route outlived the operation"


def test_the_route_carries_only_the_scope_this_operation_needs(rig, seams) -> None:
    """A checkout must not leave a push route open, even for one call.

    The connection carries both verbs; the view the route is opened with is
    narrowed to the one this operation needs, so the broker would refuse a
    receive-pack through a checkout's route.
    """
    for op, verb in (("checkout", "git_read"), ("push", "git_write")):
        git_egress._routes.clear()
        opened: list[tuple] = []
        real_routes = git_egress._open_routes

        import contextlib

        @contextlib.contextmanager
        def watching(proxy, client, connections, agent, *, authority=lambda: None,
                     _real=real_routes):
            opened.append(tuple(view.scopes for _grant, view, _inc in connections))
            with _real(proxy, client, connections, agent, authority=authority) as value:
                yield value

        git_egress._open_routes = watching
        try:
            if op == "checkout":
                _checkout(rig)
            else:
                ww.execute_workspace_operation({
                    "op": "push", "universe_dir": str(rig), "principal": _principal(),
                    "agent": "workspace-run-abc", "grant_id": "grant-git",
                    "connection_id": "conn-git", "host": HOST, "owner_repo": REPO,
                    "remote_ref": "refs/heads/tiny/u/slug", "commit_sha": SHA,
                    "lease_parent": ["workspaces", "scratch"], "lease_name": "b" * 32,
                })
        finally:
            git_egress._open_routes = real_routes
        assert opened == [((f"{verb}:{REPO}",),)], (op, opened)


# --------------------------------------------------------------------------- #
# authority
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("over,reason", [
    ({"grant_id": "nope"}, "connection authority refused"),
    ({"connection_id": "nope"}, "connection authority refused"),
    ({"principal": "somebody-else"}, "connection authority refused"),
    ({"host": "evil.example"}, "does not reach the requested git host"),
])
def test_an_operation_without_live_authority_never_opens_a_route(
    rig, seams, over, reason
) -> None:
    answer = _checkout(rig, **over)
    assert answer["ok"] is False
    assert answer["stderr_class"] == "auth"
    assert reason in answer["error"]
    assert seams["requests"] == [], "nothing reached a cell"
    assert git_egress._routes == {}


def test_a_connection_without_the_ops_git_scope_is_refused(rig, seams, tmp_path) -> None:
    """A push needs ``git_write``; a read-only connection cannot be borrowed."""
    with ConnectionLedger(
        tmp_path / "data" / ".broker" / "outbound.db", data_root=tmp_path / "data",
        verify_authenticated_principal=_principal,
    )._connect() as conn:
        conn.execute("UPDATE outbound_connections SET scopes_json=?",
                     (json.dumps([f"git_read:{REPO}"]),))
    answer = ww.execute_workspace_operation({
        "op": "push", "universe_dir": str(rig), "principal": _principal(),
        "agent": "workspace-run-abc", "grant_id": "grant-git",
        "connection_id": "conn-git", "host": HOST, "owner_repo": REPO,
        "remote_ref": "refs/heads/tiny/u/slug", "commit_sha": SHA,
        "lease_parent": ["workspaces", "scratch"], "lease_name": "b" * 32,
    })
    assert answer["ok"] is False
    assert answer["stderr_class"] == "auth"
    assert "no git scope" in answer["error"]
    assert seams["requests"] == []


def test_an_operation_without_an_acting_agent_is_refused(rig, seams) -> None:
    """The broker records who acted; an unnamed actor is not a record."""
    answer = _checkout(rig, agent="")
    assert (answer["ok"], answer["stderr_class"]) == (False, "bad_argument")
    assert seams["requests"] == []


# --------------------------------------------------------------------------- #
# create: no authority, no route, no socket
# --------------------------------------------------------------------------- #


def test_a_create_needs_no_grant_and_gets_no_socket(rig, seams) -> None:
    answer = ww.execute_workspace_operation({
        "op": "create", "universe_dir": str(rig), "principal": _principal(),
        "storage": "scratch", "lease_parent": ["workspaces", "scratch"],
        "lease_name": "c" * 32,
    })
    assert answer["ok"] is True, answer
    request = seams["requests"][0]
    assert request["options"] == [], "an empty workspace reaches nothing"
    assert seams["egress_socket"] is None
    assert git_egress._routes == {}


# --------------------------------------------------------------------------- #
# the answer
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("answer", [
    {"ok": True, "resolved_sha": SHA, "smuggled": "anything"},
    {"ok": True, "resolved_sha": 7},
    {"resolved_sha": SHA},
    {"ok": False},
    {"ok": False, "error": "no"},
    "not a mapping",
])
def test_an_answer_the_cell_did_not_shape_is_refused(rig, seams, answer) -> None:
    """The answer becomes a run's evidence, so its shape is checked here.

    An unknown key, a wrong type, or a refusal that does not name its class is
    not something to pass into the effector and hope.
    """
    seams["answer"] = answer
    got = _checkout(rig)
    assert got["ok"] is False
    assert got["stderr_class"] == "other"


def test_a_refusal_from_the_cell_keeps_its_class(rig, seams) -> None:
    seams["answer"] = {"ok": False, "error": "push refused: non-fast-forward",
                       "stderr_class": "non_fast_forward", "observed_sha": "b" * 40}
    got = _checkout(rig)
    assert got["stderr_class"] == "non_fast_forward"
    assert got["observed_sha"] == "b" * 40


def test_a_cell_that_raises_becomes_a_scrubbed_refusal(rig, seams, monkeypatch) -> None:
    def boom(*_a, **_k):
        raise RuntimeError("the cell died with a /host/path in its message")

    monkeypatch.setattr(role_remote_git, "run", boom)
    got = _checkout(rig)
    assert got["ok"] is False
    assert got["stderr_class"] == "other"
    assert "RuntimeError" in got["error"]
    assert git_egress._routes == {}, "a crash still closes the route"


# --------------------------------------------------------------------------- #
# the request the DAEMON receives
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("op", ["", "provision", "ls-remote", "CHECKOUT"])
def test_an_unknown_op_is_refused_before_any_authority_read(rig, seams, op) -> None:
    answer = ww.execute_workspace_operation({"op": op, "universe_dir": str(rig)})
    assert (answer["ok"], answer["stderr_class"]) == (False, "bad_argument")
    assert seams["requests"] == []


def test_a_non_mapping_request_is_refused() -> None:
    for bad in (None, [], "checkout", 7):
        answer = ww.execute_workspace_operation(bad)
        assert answer["ok"] is False
        assert answer["stderr_class"] == "bad_argument"


def test_execute_uses_the_injected_spawn(rig) -> None:
    """``spawn`` stays injectable: the callers' own tests ride on it."""
    seen: list[dict] = []

    def spawn(request):
        seen.append(request)
        return {"ok": True, "resolved_sha": SHA}

    answer = ww.execute_workspace_operation({"op": "checkout"}, spawn=spawn)
    assert answer == {"ok": True, "resolved_sha": SHA}
    assert seen == [{"op": "checkout"}]


def test_every_answer_is_json_safe(rig, seams) -> None:
    json.dumps(_checkout(rig))
    json.dumps(_checkout(rig, grant_id="nope"))


def test_reconcile_push_intents_is_exported_where_runs_py_calls_it() -> None:
    assert callable(ww.reconcile_push_intents)
    assert "reconcile_push_intents" in ww.__all__
