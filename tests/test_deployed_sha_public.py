"""Public release projection needs no credential and never invents a receipt."""

from types import SimpleNamespace
from unittest.mock import MagicMock
from urllib.error import URLError

import pytest

from tests.test_deployed_sha import load


def response(monkeypatch, mod, sha):
    result = MagicMock()
    result.__enter__.return_value = SimpleNamespace(
        status=200, headers={"X-TinyAssets-Build": sha},
    )
    opener = MagicMock(return_value=result)
    monkeypatch.setattr(mod.urllib.request, "urlopen", opener)
    monkeypatch.delenv("TINYASSETS_WIKI_CANARY_TOKEN", raising=False)
    return opener


def test_default_reads_only_public_header_without_credentials(monkeypatch):
    mod = load()
    opener = response(monkeypatch, mod, "a" * 40)
    monkeypatch.setattr(mod, "_git", lambda *args: "0")
    monkeypatch.setattr(mod.runtime_paths, "runtime_commits", lambda *args: [])
    info = mod.report(mod.DEFAULT_URL, 2)
    request = opener.call_args.args[0]
    assert request.full_url == "https://tinyassets.io/app"
    assert request.method == "HEAD"
    assert request.get_header("Authorization") is None
    assert info["deployed_sha"] == "a" * 40
    assert info["image_tag"] is None
    assert info["receipt_source"] == "public_app_header"
    assert info["proves"] == "receipt"


@pytest.mark.parametrize("sha", ["", "abc", "g" * 40, "a" * 39, "a" * 41])
def test_missing_or_malformed_public_header_is_unknown(monkeypatch, sha):
    mod = load()
    response(monkeypatch, mod, sha)
    assert mod.main(["--json"]) == 2


def test_public_network_failure_is_unknown(monkeypatch):
    mod = load()
    opener = response(monkeypatch, mod, "a" * 40)
    opener.side_effect = URLError("offline")
    assert mod.main([]) == 2


@pytest.mark.parametrize("contained, expected", [(True, 0), (False, 1)])
def test_public_assertion_preserves_exit_codes(monkeypatch, contained, expected):
    mod = load()
    response(monkeypatch, mod, "a" * 40)
    monkeypatch.setattr(mod, "_git", lambda *args: "0")
    monkeypatch.setattr(mod.runtime_paths, "runtime_commits", lambda *args: [])
    monkeypatch.setattr(mod.runtime_paths, "is_ancestor", lambda *args: False)
    monkeypatch.setattr(mod, "contains", lambda *args: contained)
    assert mod.main(["--assert-contains", "HEAD"]) == expected
