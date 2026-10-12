"""Tests for the provider-auth health probe and how status surfaces it.

Behind the 2026-06-25 loop-wedge: a worker whose writer-provider credentials
were missing kept claiming tasks and failing every one for ~3 weeks, poisoning
the queue, with no signal in get_status. What remains under test:

  1. ``subscription_auth_health`` — a presence-based auth probe (one source of
     truth shared by every claim gate + get_status).
  2. ``_compute_supervisor_liveness`` provider_auth block — surfaces dead writer
     auth + an ``all_writers_unauthenticated`` roll-up warning.

The ``run_supervisor`` self-quarantine cases were deleted on 2026-08-29 with
the host-run `tinyassets.cloud_worker` fleet: nothing runs outside a user's
universe (ADR-009). The probe those cases gated on is still covered above.
"""

from __future__ import annotations

import sys
from pathlib import Path

_WORKFLOW = Path(__file__).resolve().parent.parent / "workflow"
if str(_WORKFLOW.parent) not in sys.path:
    sys.path.insert(0, str(_WORKFLOW.parent))

from tinyassets.api.status import _compute_supervisor_liveness  # noqa: E402
from tinyassets.providers.base import subscription_auth_health  # noqa: E402

# ---- subscription_auth_health: codex --------------------------------------


def test_codex_ok_when_auth_json_present(tmp_path, monkeypatch):
    """A host Codex auth file is not a verdict. The package runner reads owner custody."""
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    (tmp_path / "auth.json").write_text("{}", encoding="utf-8")
    health = subscription_auth_health("codex")
    assert health == {
        "provider": "codex",
        "status": "unknown",
        "detail": "no subscription-auth probe for this provider",
    }


def test_codex_not_logged_in_when_auth_json_absent(tmp_path, monkeypatch):
    """Absence of a host auth file is not logged-out either. Same inconclusive probe."""
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    assert subscription_auth_health("codex") == {
        "provider": "codex",
        "status": "unknown",
        "detail": "no subscription-auth probe for this provider",
    }


# ---- subscription_auth_health: claude-code --------------------------------


def test_claude_ok_with_oauth_token(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "tok")
    # Empty config dir — the token must win regardless.
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    assert subscription_auth_health("claude-code")["status"] == "ok"


def test_claude_ok_with_populated_config_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    (tmp_path / ".credentials.json").write_text("{}", encoding="utf-8")
    assert subscription_auth_health("claude-code")["status"] == "ok"


def test_claude_not_logged_in_when_no_token_and_empty_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "absent"))
    assert subscription_auth_health("claude-code")["status"] == "not_logged_in"


# ---- subscription_auth_health: unknown providers are never gated -----------


def test_unknown_provider_is_unknown():
    assert subscription_auth_health("gemini-free")["status"] == "unknown"


def test_empty_provider_is_unknown():
    assert subscription_auth_health("")["status"] == "unknown"


# ---- supervisor_liveness provider_auth block ------------------------------


def test_liveness_provider_auth_block_ok(tmp_path, monkeypatch):
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    (codex_home / "auth.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "tok")

    out = _compute_supervisor_liveness(tmp_path)
    # The host file does not authenticate Codex. Claude's token does.
    assert out["provider_auth"]["writers"]["codex"]["status"] == "unknown"
    assert out["provider_auth"]["writers"]["claude-code"]["status"] == "ok"
    assert out["provider_auth"]["all_writers_unauthenticated"] is False
    assert not any("all_writers_unauthenticated" in w for w in out["warnings"])
    assert not any("writer_unauthenticated" in w for w in out["warnings"])


def test_liveness_all_writers_unauthenticated_warns(tmp_path, monkeypatch):
    """Both checked writers logged out is the total alarm. An unknown writer is not."""
    from tinyassets.providers import base as provider_base

    def logged_out(name, *, allow_probe=True):
        return {"provider": name, "status": "not_logged_in", "detail": "absent"}

    monkeypatch.setattr(provider_base, "subscription_auth_health", logged_out)

    out = _compute_supervisor_liveness(tmp_path)
    assert set(out["provider_auth"]["writers"]) == {"codex", "claude-code"}
    assert all(
        info["status"] == "not_logged_in"
        for info in out["provider_auth"]["writers"].values()
    )
    assert out["provider_auth"]["all_writers_unauthenticated"] is True
    assert any("all_writers_unauthenticated" in w for w in out["warnings"])
    assert not any("writer_unauthenticated" in w for w in out["warnings"])


def test_liveness_partial_writer_warns(tmp_path, monkeypatch):
    # Claude dead, Codex inconclusive. A host auth.json must not read as Codex
    # alive, and it must not turn the one dead writer into "every writer is
    # unauthenticated" (the 2026-06-25 partial-outage distinction).
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    (codex_home / "auth.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-absent"))

    out = _compute_supervisor_liveness(tmp_path)
    assert out["provider_auth"]["all_writers_unauthenticated"] is False
    assert out["provider_auth"]["writers"]["claude-code"]["status"] == "not_logged_in"
    assert out["provider_auth"]["writers"]["codex"]["status"] == "unknown"
    assert any("writer_unauthenticated" in w for w in out["warnings"])
    assert not any("all_writers_unauthenticated" in w for w in out["warnings"])


