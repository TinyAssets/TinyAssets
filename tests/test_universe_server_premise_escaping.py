"""Tests for set_premise / read_premise escape-normalization (task #13).

Some MCP clients transmit multi-line strings as JSON string literals,
and the server receives the escape sequences verbatim. This corrupts the
stored PROGRAM.md so later `read_premise` returns literal ``\\n`` in
place of real newlines. The server now normalizes at write time and
provides a read-time fallback for already-corrupted files.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

from tinyassets.universe_soul import read_universe_soul, write_universe_soul


@pytest.fixture
def us(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    base = tmp_path / "output"
    base.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(base))
    import tinyassets.api.universe as module

    importlib.reload(module)
    yield module
    importlib.reload(module)


def _mkuniverse(base: Path, uid: str) -> Path:
    udir = base / uid
    udir.mkdir(parents=True, exist_ok=True)
    return udir




@pytest.fixture(autouse=True)
def _restore_auth_provider():
    """Put the auth provider back after every test in this module.

    `_own_universes_as` installs a static authenticated provider through the real
    middleware, and process-global auth state does not unwind itself — it leaked into
    `test_actor_defaults_to_anonymous_without_env`, which asserts the anonymous default
    and passed in isolation while failing in a full run.
    """
    from tinyassets.auth.middleware import auth_middleware, set_provider
    from tinyassets.auth.provider import DevAuthProvider

    set_provider(DevAuthProvider())
    auth_middleware("dev")
    yield
    set_provider(DevAuthProvider())
    auth_middleware("dev")


def _own_universes_as(actor_id: str = "user_01TESTOWNER") -> None:
    """Authenticate, because a universe must now belong to someone.

    Creation used to succeed for an anonymous caller and return `founder_id: ""` —
    a universe nobody owned. The founder's rule (2026-08-28) forbids that state, and
    it is enforced in `_action_create_universe` since 2026-08-29, so a test that
    creates a universe has to say who it belongs to.
    """
    from tinyassets.auth.middleware import auth_middleware, set_provider
    from tinyassets.auth.provider import AuthProvider, Identity

    identity = Identity(
        user_id=actor_id,
        username=actor_id,
        capabilities=[
            "tinyassets.universe.read",
            "tinyassets.universe.write",
            "tinyassets.universe.admin",
            "tinyassets.universe.create",
            # create_universe is a COSTLY action — it provisions storage — so the
            # scope gate wants this one too. Omitting it produced a confusing
            # "Missing OAuth scope" rather than an ownership refusal.
            "tinyassets.universe.costly",
        ],
    )

    class _Static(AuthProvider):
        def resolve_token(self, token: str):
            return identity if token == "ok" else None

        def is_auth_required(self) -> bool:
            return True

        def register_client(self, metadata: dict) -> dict:
            return {"client_id": "test-client", **metadata}

        def create_authorization(self, *_a, **_kw) -> str:
            raise NotImplementedError

        def exchange_code(self, *_a, **_kw) -> dict:
            raise NotImplementedError

    set_provider(_Static())
    auth_middleware("ok")

def test_normalize_leaves_already_newlined_text_alone(us):
    already = "Line one\nLine two\nLine three"
    assert us._normalize_escaped_text(already) is already or \
        us._normalize_escaped_text(already) == already


def test_normalize_converts_backslash_n_to_real_newline(us):
    corrupt = "Line one\\nLine two\\nLine three"
    assert us._normalize_escaped_text(corrupt) == "Line one\nLine two\nLine three"


def test_normalize_handles_escaped_crlf_pair(us):
    corrupt = "A\\r\\nB\\r\\nC"
    assert us._normalize_escaped_text(corrupt) == "A\nB\nC"


def test_normalize_decodes_even_with_trailing_newline(us):
    """Real-world case: file was written with literal \\n and has a
    trailing real newline at EOF. Still need to decode because the
    literal \\n sequences are the bug, not the intent."""
    corrupt = "# Title\\n\\nParagraph.\n"
    assert us._normalize_escaped_text(corrupt) == "# Title\n\nParagraph.\n"


def test_normalize_noop_on_empty_string(us):
    assert us._normalize_escaped_text("") == ""


def test_set_premise_normalizes_on_write(us, tmp_path):
    base = Path(us._base_path())
    _mkuniverse(base, "u1")
    corrupt = "# Title\\n\\nParagraph one.\\n\\nParagraph two."

    result = json.loads(us._action_set_premise(universe_id="u1", text=corrupt))
    assert result["status"] == "updated"

    on_disk = read_universe_soul(base / 'u1').purpose
    assert on_disk == "# Title\n\nParagraph one.\n\nParagraph two."


def test_set_premise_preserves_intentional_newlines(us):
    base = Path(us._base_path())
    _mkuniverse(base, "u1")
    already = "# Title\n\nA normal paragraph.\n\nAnother one."

    us._action_set_premise(universe_id="u1", text=already)
    on_disk = read_universe_soul(base / 'u1').purpose
    assert on_disk == already


def test_read_premise_decodes_pre_existing_corrupt_file(us):
    """Files written by a buggy client before the fix still render cleanly."""
    base = Path(us._base_path())
    udir = _mkuniverse(base, "corrupt-u")
    write_universe_soul(udir, purpose='# Ashwater Chronicles\\n\\nA dark fantasy.')

    result = json.loads(us._action_read_premise(universe_id="corrupt-u"))
    assert result["premise"] == "# Ashwater Chronicles\n\nA dark fantasy."
    assert "\\n" not in result["premise"]


def test_read_premise_passes_through_healthy_file(us):
    base = Path(us._base_path())
    udir = _mkuniverse(base, "healthy-u")
    write_universe_soul(udir, purpose='# Sporemarch\n\nCharacters seed fungal memory.\n')

    result = json.loads(us._action_read_premise(universe_id="healthy-u"))
    assert result["premise"] == "# Sporemarch\n\nCharacters seed fungal memory."
    assert result["source"] == "soul.md"


def test_read_premise_reports_missing_premise(us):
    base = Path(us._base_path())
    _mkuniverse(base, "empty-u")
    result = json.loads(us._action_read_premise(universe_id="empty-u"))
    assert result["premise"] is None
    assert "no premise set" in result["note"].lower()


def test_create_universe_normalizes_embedded_premise(us):
    _own_universes_as()
    from tinyassets.api.universe import _action_create_universe

    base = Path(us._base_path())
    corrupt = "# New Universe\\n\\nFirst premise line."
    result = json.loads(_action_create_universe(
        universe_id="newverse", text=corrupt,
    ))
    assert result["status"] == "created"
    on_disk = read_universe_soul(base / 'newverse').purpose
    assert on_disk == "# New Universe\n\nFirst premise line."
