"""Tests for the user-facing mint/revoke/list webhook operations (Floor 1)."""

from __future__ import annotations

import json

import pytest

from tinyassets.api import webhook_ops
from tinyassets.storage import webhook_hooks


def _wire(monkeypatch, tmp_path, *, universe="u-a", branch_exists=True):
    monkeypatch.setattr("tinyassets.api.runs._request_universe", lambda x: universe)
    monkeypatch.setattr("tinyassets.api.helpers._base_path", lambda: str(tmp_path))
    monkeypatch.setattr("tinyassets.api.branches._resolve_branch_id", lambda b, base: b)

    def _get_branch(base, *, branch_def_id):
        if not branch_exists:
            raise KeyError("no such branch")
        # Authored by the universe actor, so the author-gate (Codex #1) is satisfied for
        # this universe's own branch. Cross-author rejection is proven end-to-end in
        # test_webhook_inbound_hardened.py against a REAL authored branch + real identities.
        return {"id": branch_def_id, "author": f"universe:{universe}"}

    monkeypatch.setattr("tinyassets.daemon_server.get_branch_definition", _get_branch)


@pytest.mark.parametrize("base, origin", [
    ("https://tinyassets.io", "https://tinyassets.io"),
    ("https://hooks.example.com/", "https://hooks.example.com"),
    ("https://tinyassets.io/mcp", "https://tinyassets.io"),
])
def test_mint_returns_a_url_for_an_owned_branch(monkeypatch, tmp_path, base, origin):
    _wire(monkeypatch, tmp_path)
    monkeypatch.setenv("TINYASSETS_PUBLIC_BASE_URL", base)
    out = json.loads(webhook_ops._action_mint_webhook(
        {"universe_id": "u-a", "branch_def_id": "b-1"},
    ))
    assert out["branch_def_id"] == "b-1"
    assert out["url"] == origin + "/hooks/inbound/" + out["token"]
    # The token resolves to this universe + branch (no source binding) AND to
    # the principal who minted it. A webhook token is somebody's: an exact-dict
    # assertion written when nothing owned it would hide that.
    from tinyassets.api import permissions

    resolved = webhook_hooks.resolve(tmp_path, token=out["token"])
    assert resolved["universe_id"] == "u-a"
    assert resolved["branch_def_id"] == "b-1"
    assert resolved["source_id"] is None
    assert resolved["owner_principal_id"] == permissions.current_actor_id()
    assert resolved["owner_principal_id"], "a token minted by nobody"


def test_mint_refuses_a_branch_not_in_the_universe(monkeypatch, tmp_path):
    _wire(monkeypatch, tmp_path, branch_exists=False)
    out = json.loads(webhook_ops._action_mint_webhook(
        {"universe_id": "u-a", "branch_def_id": "b-nope"},
    ))
    assert "error" in out and "not found" in out["error"]


def test_mint_requires_a_universe_and_branch(monkeypatch, tmp_path):
    _wire(monkeypatch, tmp_path, universe="")
    assert "error" in json.loads(webhook_ops._action_mint_webhook({"branch_def_id": "b"}))
    _wire(monkeypatch, tmp_path, universe="u-a")
    assert "error" in json.loads(webhook_ops._action_mint_webhook({"universe_id": "u-a"}))


def test_revoke_only_affects_your_own_token(monkeypatch, tmp_path):
    # u-b's token cannot be revoked by u-a (indistinct "no matching").
    tok_b = webhook_hooks.mint(
        tmp_path,
        universe_id="u-b",
        branch_def_id="b-b",
        owner_principal_id="owner-test",
    )
    _wire(monkeypatch, tmp_path, universe="u-a")
    out = json.loads(webhook_ops._action_revoke_webhook({"universe_id": "u-a", "token": tok_b}))
    assert out["revoked"] is False
    assert webhook_hooks.resolve(tmp_path, token=tok_b) is not None   # still active

    # u-a can revoke its own token.
    tok_a = webhook_hooks.mint(
        tmp_path,
        universe_id="u-a",
        branch_def_id="b-a",
        owner_principal_id="owner-test",
    )
    out2 = json.loads(webhook_ops._action_revoke_webhook({"universe_id": "u-a", "token": tok_a}))
    assert out2["revoked"] is True
    assert webhook_hooks.resolve(tmp_path, token=tok_a) is None


def test_list_shows_only_your_active_hooks(monkeypatch, tmp_path):
    webhook_hooks.mint(
        tmp_path,
        universe_id="u-a",
        branch_def_id="b-1",
        owner_principal_id="owner-test",
    )
    revoked = webhook_hooks.mint(
        tmp_path,
        universe_id="u-a",
        branch_def_id="b-2",
        owner_principal_id="owner-test",
    )
    webhook_hooks.revoke(tmp_path, token=revoked)
    webhook_hooks.mint(
        tmp_path,
        universe_id="u-b",
        branch_def_id="b-3",
        owner_principal_id="owner-test",
    )
    _wire(monkeypatch, tmp_path, universe="u-a")
    out = json.loads(webhook_ops._action_list_webhooks({"universe_id": "u-a"}))
    assert out["count"] == 1                                   # only the active u-a hook
    assert out["webhooks"][0]["branch_def_id"] == "b-1"
    # list returns a non-secret prefix, never the raw token / full URL (Codex #6)
    assert out["webhooks"][0]["token_prefix"] and "url" not in out["webhooks"][0]
