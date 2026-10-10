"""The two projections of one ephemeral git route (``git_egress``).

A route is a grant the broker will act on for the length of one invocation.
There are two callers and they need the same rewrite in different shapes: an
extension launch inherits it as ``GIT_CONFIG_*`` in a shell prefix, and an
owner cell is handed it as git ``-c`` options, because ``run_git_in_cell``
builds git's environment from empty and options are the only channel it has.

Both must be the SAME route registry, and both must leave none behind: a route
that outlives its invocation is a grant the box can use after the reason for
it is gone.
"""

from __future__ import annotations

import shlex
from types import SimpleNamespace

import pytest

from tinyassets import git_egress

REPO = "owner/name"
HOST = "forge.example"
SOURCE = f"https://{HOST}/{REPO}.git"


class _Proxy:
    """Only its identity matters: routes are keyed by the proxy they live on."""


def _connections(scopes=(f"git_read:{REPO}",)):
    grant = SimpleNamespace(grant_id="grant-1", connection_id="conn-1")
    view = SimpleNamespace(
        connection_id="conn-1", scopes=tuple(scopes), git_host=HOST,
        connection_type="http", allowed_endpoints=(),
    )
    return [(grant, view, "incarnation-1")]


@pytest.fixture()
def proxy():
    value = _Proxy()
    yield value
    assert not [key for key in git_egress._routes if key[0] == id(value)]


def _client():
    return SimpleNamespace(_fence=lambda: (1, "token"))


def test_the_shell_prefix_carries_the_rewrite_an_extension_launch_inherits(proxy):
    with git_egress.routes(proxy, _client(), _connections(), "agent-1") as prefix:
        assert prefix.startswith("export ")
        env = dict(
            item.split("=", 1) for item in shlex.split(prefix.rstrip("; ").removeprefix("export "))
        )
        assert env["GIT_CONFIG_COUNT"] == "1"
        assert env["GIT_TERMINAL_PROMPT"] == "0"
        assert env["GIT_CONFIG_VALUE_0"] == SOURCE
        assert env["GIT_CONFIG_KEY_0"].startswith(f"url.http://{git_egress.HOST}/")
        assert env["GIT_CONFIG_KEY_0"].endswith(f"/{REPO}.git.insteadOf")


def test_the_option_form_carries_the_same_rewrite_a_cell_is_handed(proxy):
    with git_egress.rewrite_options(proxy, _client(), _connections(), "agent-1") as options:
        assert options[0] == "-c"
        assert len(options) == 2
        key, _, value = options[1].partition("=")
        assert value == SOURCE
        assert key.startswith(f"url.http://{git_egress.HOST}/")
        assert key.endswith(f"/{REPO}.git.insteadOf")
        assert len([k for k in git_egress._routes if k[0] == id(proxy)]) == 1


def test_a_route_lives_exactly_as_long_as_its_invocation(proxy):
    with git_egress.rewrite_options(proxy, _client(), _connections(), "agent-1"):
        keys = [key for key in git_egress._routes if key[0] == id(proxy)]
        assert len(keys) == 1
    assert not [key for key in git_egress._routes if key[0] == id(proxy)]


def test_a_raising_body_still_closes_the_route(proxy):
    with pytest.raises(RuntimeError, match="the cell died"):
        with git_egress.rewrite_options(proxy, _client(), _connections(), "agent-1"):
            raise RuntimeError("the cell died")


def test_two_grants_for_one_repository_are_ambiguous_rather_than_guessed(proxy):
    """Which credential a repository is reached with is the owner's choice."""
    first = _connections()[0]
    other = (SimpleNamespace(grant_id="grant-2", connection_id="conn-2"),
             first[1], "incarnation-2")
    with pytest.raises(PermissionError, match="ambiguous git grants"):
        with git_egress.rewrite_options(proxy, _client(), [first, other], "agent-1"):
            pass


def test_a_connection_with_no_git_scope_opens_no_route(proxy):
    with git_egress.rewrite_options(
        proxy, _client(), _connections(scopes=("http_read:/x",)), "agent-1"
    ) as options:
        assert options == []
        assert not [key for key in git_egress._routes if key[0] == id(proxy)]
