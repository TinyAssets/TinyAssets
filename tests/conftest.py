"""Shared fixtures for TinyAssets tests.

Provides checkpointer, compiled graphs, and default state dicts
that all test modules can reuse.
"""

from __future__ import annotations

import os
import pathlib
import sys
import tempfile
from collections.abc import Sequence
from typing import Any, Callable

import pytest
from langgraph.checkpoint.sqlite import SqliteSaver

from tinyassets.auth.middleware import auth_middleware, set_provider
from tinyassets.auth.provider import AuthProvider, DevAuthProvider, Identity
from tinyassets.providers import call as _provider_call

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def pytest_configure(config: pytest.Config) -> None:
    """Refuse to run if pytest's temp root is inside the repo.

    A sandboxed agent (Codex, Cursor) that redirects ``--basetemp``/``TMPDIR``
    into the checkout creates those directories under a RESTRICTED TOKEN. On
    Windows the resulting ACL is owned by e.g. ``CodexSandboxUsers`` and denies
    the interactive user everything -- not just delete, but even reading the
    ACL. They then survive ``git worktree remove`` and need an elevated
    ``takeown`` to clear. Seventeen such husks were left behind on 2026-08-25
    and could not be removed from an ordinary shell.

    Failing here costs one clear error; the alternative costs an elevated
    cleanup and a directory nobody can delete. Recovery:
    ``scripts/clear_sandbox_temp_dirs.ps1 -Apply`` from an elevated shell.

    On Windows, ALSO pass a SHORT ``--basetemp`` (e.g.
    ``C:/Users/<you>/AppData/Local/Temp/ta-pt``): pytest's default root plus a
    workspace lease plus ``.git/objects/pack/pack-<40 hex>.pack`` crosses
    MAX_PATH, and the failure reads as a real defect (a lease that will not
    delete) rather than as a path-length limit. Tests that build a real git
    checkout skip themselves when the root is too long to be safe.
    """
    roots: list[tuple[str, str]] = []
    basetemp = config.getoption("basetemp", default=None)
    if basetemp:
        roots.append(("--basetemp", str(basetemp)))
    for var in ("PYTEST_DEBUG_TEMPROOT", "TMPDIR", "TEMP", "TMP"):
        value = os.environ.get(var)
        if value:
            roots.append((var, value))

    for label, raw in roots:
        try:
            resolved = pathlib.Path(raw).resolve()
        except (OSError, ValueError):
            continue
        if resolved == _REPO_ROOT or _REPO_ROOT in resolved.parents:
            raise pytest.UsageError(
                f"{label}={raw!r} points INSIDE the repo ({_REPO_ROOT}). "
                "Sandbox-created temp dirs here get an ACL the interactive user "
                "cannot delete and that survives worktree teardown. Point it at "
                "the system temp dir instead."
            )


# Force mock provider responses in all tests to avoid real API calls
_provider_call.set_force_mock(True)


@pytest.fixture(autouse=True)
def _request_admission_hmac_key(monkeypatch):
    """Install non-production server seal material for authority tests."""

    monkeypatch.setenv(
        "TINYASSETS_REQUEST_IDEMPOTENCY_HMAC_KEY",
        "pytest-only-request-admission-hmac-key-0123456789abcdef",
    )


# The capability set an issued test credential carries unless a test asks for
# something else. Deliberately EXCLUDES `tinyassets.extensions.costly`, which
# gates run_branch and friends: several suites authenticate a subject and then
# assert a costly action is refused, so granting it by default would silently
# convert those into vacuous passes.
_DEFAULT_TEST_CAPABILITIES = (
    "tinyassets.extensions.read",
    "tinyassets.extensions.write",
    "tinyassets.extensions.admin",
)


class _CredentialSubjectProvider(AuthProvider):
    """Resolve only issued test credentials to persisted subjects."""

    def __init__(self) -> None:
        self._identities_by_token: dict[str, Identity] = {}

    def issue_credential(
        self, subject: str, capabilities: Sequence[str] | None = None
    ) -> str:
        token = f"pytest-credential::{subject}"
        self._identities_by_token[token] = Identity(
            user_id=subject,
            username=f"{subject}-display",
            # `is None`, NOT `or`: an explicit empty list is falsy, so `or`
            # would silently hand a test that asked for NO capabilities the
            # full default set — turning an authorization-refusal test into a
            # vacuous pass, which is the exact hazard this parameter exists to
            # avoid. Pinned by test_credential_capabilities.py.
            capabilities=list(
                _DEFAULT_TEST_CAPABILITIES if capabilities is None else capabilities
            ),
        )
        return token

    def resolve_token(self, token: str) -> Identity | None:
        return self._identities_by_token.get(token)

    def is_auth_required(self) -> bool:
        return True

    def register_client(self, metadata: dict[str, Any]) -> dict[str, Any]:
        return {"client_id": "pytest-credential-subject", **metadata}

    def create_authorization(
        self,
        client_id: str,
        redirect_uri: str,
        scope: str,
        state: str,
        code_challenge: str,
        code_challenge_method: str,
    ) -> str:
        return "pytest-credential-subject-code"

    def exchange_code(
        self,
        code: str,
        client_id: str,
        redirect_uri: str,
        code_verifier: str,
    ) -> dict[str, Any] | None:
        return None


@pytest.fixture
def authenticate_request() -> Callable[[str | None], None]:
    """Bind branch-authority tests to a credential-derived request subject."""
    provider = _CredentialSubjectProvider()
    set_provider(provider)
    auth_middleware(None)

    def authenticate(
        subject: str | None, capabilities: Sequence[str] | None = None
    ) -> None:
        """Bind the request subject. `capabilities` defaults to read/write/admin.

        Pass an explicit list to grant `tinyassets.extensions.costly`, which
        `run_branch` requires. It is opt-in rather than default so that suites
        asserting a costly action is refused keep asserting something.
        """
        token = provider.issue_credential(subject, capabilities) if subject else None
        auth_middleware(token)

    yield authenticate
    auth_middleware("dev")
    set_provider(DevAuthProvider())


#: Modules that must see the REAL strict visibility resolver, not the
#: public-for-legacy-modules stand-in below. A test whose subject IS the
#: visibility boundary has to opt out, or it asserts the double's behaviour
#: instead of the code's.
_STRICT_VISIBILITY_MODULES = frozenset({
    "test_universe_visibility",
    "test_private_by_default",
})


@pytest.fixture(autouse=True)
def _emulate_deployed_visibility_backfill(request, monkeypatch):
    """A legacy module's bare universe stands for one whose owner chose public.

    The universe-visibility contract fails closed on an *undeclared* universe
    (openspec/changes/universe-visibility). Hundreds of pre-visibility tests
    create bare universe directories and assert public-reader behaviour — status
    shape, word count, telemetry, ledger — none of which is about visibility.
    This fixture resolves an undeclared universe to ``PUBLIC`` for those modules
    so each one does not have to re-declare, while an explicit (or forged)
    declaration, a corrupt store, and a blank id all defer to the real strict
    resolver.

    Until 2026-09-26 this was literally "emulate the deployed backfill": the
    production backfill derived ``public`` from the ``public_read`` bit, so the
    harness matched it. It no longer does — per the founder, the backfill now
    declares ``private`` and the platform never declares an open level on an
    owner's behalf (openspec/changes/archive/2026-09-30-private-by-default-universes). The fixture
    keeps its behaviour and changes its MEANING: these modules' bare directories
    now stand in for a universe whose owner chose public, which is what each of
    those tests is actually about. Flipping the fixture to ``private`` instead
    would silently rewrite what several hundred unrelated tests assert.

    Modules in ``_STRICT_VISIBILITY_MODULES`` opt out and exercise the real,
    un-emulated resolver. Everything created through the real
    ``_action_create_universe`` is unaffected either way, because creation writes
    an *explicit* declaration and this fixture defers on those.
    """
    module_name = getattr(request.node.module, "__name__", "")
    if module_name.rpartition(".")[2] in _STRICT_VISIBILITY_MODULES:
        return  # these modules test the real, un-emulated strict resolver.

    from tinyassets.api import visibility as _vis

    _real = _vis.universe_visibility

    def _post_backfill(universe_id: str):
        if not (universe_id or "").strip():
            return _vis.CLOSED
        rules = _vis._read_rules(universe_id)
        if rules is _vis._CORRUPT:
            return _vis.CLOSED
        if rules is _vis._MISSING:
            # A bare universe with no rules row: read as "the owner chose public".
            return _vis.PUBLIC
        if not isinstance(rules, dict):
            return _vis.CLOSED
        meta = rules.get("metadata")
        if isinstance(meta, dict) and _vis.LEVEL_METADATA_KEY in meta:
            return _real(universe_id)  # explicit/forged -> real strict resolver.
        return _vis.PUBLIC if bool(rules.get("public_read", True)) else _vis.PRIVATE

    monkeypatch.setattr(_vis, "universe_visibility", _post_backfill)


def own_universe(base_path, *universe_ids: str) -> None:
    """Give each universe an OWNER, which is what makes it a universe.

    Since 2026-09-02 a universe exists because an ownership row names it, and a
    universe nobody owns grants no capability -- so a fixture that makes a
    directory and then reads it back is describing a state production cannot be
    in: `_action_create_universe` claims the owner before the directory exists.

    A `founder_home` binding rather than a `universe_acl` grant, deliberately:
    `daemon_server.universe_is_private` is literally "has any ACL rows", so
    granting would flip every fixture from public to private and change what the
    surrounding assertions mean. One synthetic founder per universe, because the
    table is keyed by founder.

    Call it beside the mkdir. There is no autouse version on purpose -- most
    fixtures make a bare directory with no function call to hook, and a helper
    that guessed would be a permissive double rather than a truth-maker.
    """
    from tinyassets.daemon_server import set_founder_home

    for uid in universe_ids:
        set_founder_home(
            base_path, founder_sub=f"test-owner::{uid}", universe_id=uid,
        )


@pytest.fixture(autouse=True)
def _no_live_oauth_discovery(monkeypatch):
    """No test reaches a real authorization server.

    Every ``connect`` ask runs standard OAuth discovery against its hosts, and
    the suite's hosts are invented. Discovery is switched off by injection; a
    test that exercises it turns it back on against its own local fake server.
    """
    from tinyassets.connection_oauth import discovery

    monkeypatch.setattr(discovery, "DISCOVERY_ENABLED", False)


@pytest.fixture(autouse=True)
def _identity_fingerprint_key(monkeypatch):
    """Give tests an explicit dedicated status-fingerprint key."""
    monkeypatch.setenv(
        "TINYASSETS_IDENTITY_FINGERPRINT_KEY",
        "pytest-only-identity-fingerprint-key-32-bytes",
    )
    monkeypatch.setenv("TINYASSETS_IDENTITY_FINGERPRINT_VERSION", "v1")


#: Who the suite runs as. A direct call into an API in a test stands in for an
#: authenticated request, so the suite binds a NAMED operator the way the
#: transport would -- rather than leaving the ContextVar empty, which would
#: make every such call refuse and say nothing about the code under test.
TEST_OPERATOR = Identity(
    user_id="dev-tests",
    username="dev-tests",
    display_name="dev-tests",
    capabilities=["read", "write", "submit_request", "list", "costly", "admin"],
)


@pytest.fixture(autouse=True)
def _signed_in_operator(request):
    """Bind a named operator for every test, and unbind afterwards.

    There is no anonymous principal (founder, 2026-09-02): with nothing bound,
    ``current_identity()`` raises. A test that wants NOBODY says so --
    ``auth_middleware(None)`` -- and asserts the refusal; a test that wants a
    different subject sets its own provider. Neither is affected by this, and
    both used to be the only way a test got an identity at all.

    The operator is DISTINCT PER TEST. A shared one leaked across tests that
    share a data directory: an authenticated caller with no explicit scope
    resolves their bound home, so a home auto-birthed by one test became the
    resolved scope of the next, which had set up a differently-named universe
    and got somebody else's serial id.
    """
    from tinyassets.auth import middleware as _mw

    local_operator_process = _mw._local_operator_process
    _mw._local_operator_process = False
    operator = Identity(
        user_id=f"test-operator::{request.node.nodeid}",
        username="test-operator",
        display_name="test-operator",
        capabilities=list(TEST_OPERATOR.capabilities),
    )
    token = _mw._current_identity.set(operator)
    try:
        yield operator
    finally:
        try:
            _mw._current_identity.reset(token)
        except ValueError:
            # A test that set the identity inside another context (or in a
            # different task) leaves the token unresettable here; clearing is
            # the equivalent end state.
            _mw._current_identity.set(None)
        _mw._local_operator_process = local_operator_process


@pytest.fixture
def founder_home(tmp_path, monkeypatch, _signed_in_operator):
    """An isolated data dir where the signed-in operator HAS a home universe.

    The state a founder is in after first contact, and the one an omitted
    scope resolves to. Without it an authenticated caller with no bound home
    gets the first-contact card from ``get_status`` and the designated public
    universe from ``_request_universe`` -- both correct, and neither what a
    test asserting "the status of my universe" means.

    Returns the universe directory.
    """
    from tinyassets.daemon_server import (
        claim_founder_home,
        ensure_universe_registered,
        grant_universe_access,
    )

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    uid = "test-universe"
    udir = tmp_path / uid
    udir.mkdir(parents=True, exist_ok=True)
    (udir / "dispatcher.json").write_text("{}", encoding="utf-8")
    # The canonical completed-seed marker. A home without it is "bound but
    # partial", which status reports as first contact rather than reading.
    (udir / "soul.md").write_text("# test universe\n", encoding="utf-8")
    ensure_universe_registered(
        tmp_path, universe_id=uid, universe_path=udir, display_name=uid,
    )
    grant_universe_access(
        tmp_path, universe_id=uid, actor_id=_signed_in_operator.user_id,
        permission="admin", granted_by="tests",
    )
    claim_founder_home(tmp_path, _signed_in_operator.user_id, uid)
    return udir


@pytest.fixture
def nobody():
    """Nobody bound, for a test that asserts a refusal.

    The autouse operator signs every test in, which is right: production has no
    anonymous principal, so a test running as nobody is testing a state that
    cannot happen. The exception is a test whose SUBJECT is the refusal -- it
    has to be able to reach the unauthenticated path, and "just do not log in"
    stopped meaning that the moment the default became signed-in.
    """
    from tinyassets.auth import middleware as _mw

    token = _mw._current_identity.set(None)
    bearer = _mw._current_bearer_present.set(False)
    yield
    for var, tok in ((_mw._current_identity, token), (_mw._current_bearer_present, bearer)):
        try:
            var.reset(tok)
        except ValueError:
            pass


@pytest.fixture
def signed_in():
    """Run a block as a named subject: ``signed_in("workos|abc")``."""
    from tinyassets.auth import middleware as _mw

    tokens = []

    def _bind(user_id: str, capabilities: Sequence[str] | None = None) -> Identity:
        identity = Identity(
            user_id=user_id,
            username=user_id,
            display_name=user_id,
            capabilities=list(capabilities or TEST_OPERATOR.capabilities),
        )
        tokens.append(_mw._current_identity.set(identity))
        return identity

    yield _bind
    for token in reversed(tokens):
        try:
            _mw._current_identity.reset(token)
        except ValueError:
            _mw._current_identity.set(None)


@pytest.fixture(autouse=True)
def _reset_runtime():
    """Clear runtime singletons before AND after every test to prevent leakage.

    The pre-test reset catches cases where a prior test's background thread
    (e.g. LangGraph executor) sets the global after the prior test's teardown.
    """
    from tinyassets import runtime_singletons as runtime

    runtime.reset()
    yield
    runtime.reset()


@pytest.fixture(autouse=True)
def _stop_workspace_sweepers_between_tests():
    """No test may leave a process-global periodic worker for the next test."""
    import sys

    yield
    runs = sys.modules.get("tinyassets.runs")
    if runs is not None:
        assert runs._stop_all_workspace_sweepers(timeout_s=2)


@pytest.fixture(autouse=True)
def _clear_shortlist_cache_between_tests():
    """No test may leave a warm catalogue or a refresh thread for the next one.

    ``SHORTLIST_CACHE`` is a process-global, and its refreshes run on a thread
    pool. Both leak: a warm entry would let a later test read a catalogue it
    never discovered, and an in-flight refresh scheduled here would run while
    the NEXT test has ``discover_native_models_sync`` monkeypatched -- calling
    that test's double from outside its own assertions. Same class as the
    workspace sweepers above.
    """
    import sys

    yield
    module = sys.modules.get("tinyassets.providers.shortlist_refresh")
    if module is not None:
        module.SHORTLIST_CACHE.shutdown(wait=True)


@pytest.fixture(autouse=True)
def _reset_git_enabled_probe():
    """Re-probe ``git_bridge.is_enabled`` for every test.

    The probe result is cached in a MODULE GLOBAL, so it leaks across the whole
    process: any earlier test that stubs ``shutil.which`` to None (e.g.
    test_api_status.py pinning endpoint_hint) latches ``False``, and every
    later git-touching test then silently no-ops with "git not enabled".
    A few files carried their own local version of this fixture; the cache is
    process-global, so its reset has to be too.

    Found 2026-08-03 by the `required-tests` gate itself: new tests arriving on
    main shifted collection order and turned 138 passing tests red at once,
    with no code change to any of them.
    """
    from tinyassets import git_bridge

    git_bridge.invalidate_cache()
    yield
    git_bridge.invalidate_cache()


@pytest.fixture(autouse=True)
def _restore_auth_provider():
    """Put back the process-global auth provider each test started with.

    ``set_provider`` replaces a module global and returns nothing, so a test
    that swaps it and "restores" from its return value leaves its provider for
    every later test. tests/test_mcp_sse_keepalive.py did exactly that: a probe
    provider that requires auth and grants only read stayed installed, and a
    later first-contact test was refused its home universe (no_home_universe)
    whenever shard packing put the two files together.
    """
    from tinyassets.auth import middleware

    saved = middleware._provider
    yield
    middleware._provider = saved


@pytest.fixture(autouse=True)
def _reset_provider_request_state():
    """Clear the process-global provider-request capability state per test.

    ``_PROVIDER_REQUESTS`` (a module dict) plus the ``_current_provider_request``
    / ``_current_provider_reserve`` ContextVars hold the one-shot authenticated
    dispatch capability. ContextVars are NOT reset between pytest tests, so a
    test that ``reserve``/``claim``s a capability and doesn't tear it down leaks
    a stale ``ProviderRequestCapability`` into the next test: e.g. converse then
    reads it via ``provider_request_capability()`` and resolves the serving
    binding under the wrong principal (0 matches -> "exactly one founder serving
    binding is required"). Same collection-order leak class as
    ``_reset_git_enabled_probe`` above; the reset has to be process-global too.
    Reuses the canonical fork-time reset so there is one definition of "clean".
    """
    from tinyassets.auth.middleware import _reset_provider_request_state_after_fork

    _reset_provider_request_state_after_fork()
    yield
    _reset_provider_request_state_after_fork()


@pytest.fixture(autouse=True)
def _isolate_storage_backend(monkeypatch):
    """Pin the storage backend to ``sqlite_only`` by default for every test.

    Phase 7 Rationale: the module-global :class:`SqliteCachedBackend`
    anchors to ``Path.cwd()`` on first use and, once cached, keeps
    writing to the real repo ``branches/`` / ``goals/`` / ``nodes/``
    directories even when later tests point ``TINYASSETS_DATA_DIR``
    at a tmp dir. That causes (a) pollution of the working tree and
    (b) spurious ``DirtyFileError`` as tests fight over the same
    slug paths.

    Tests that explicitly exercise the cached backend (git-enabled
    path, YAML serialization, commit granularity) override this by
    re-setting the env var via their own ``monkeypatch``. See
    ``tests/test_storage_phase7_backend.py`` and future ``test_phase7_h3_*``.
    """
    monkeypatch.setenv("TINYASSETS_STORAGE_BACKEND", "sqlite_only")
    from tinyassets import catalog as _catalog

    _catalog.invalidate_backend_cache()
    yield
    _catalog.invalidate_backend_cache()


@pytest.fixture(autouse=True)
def _code_nodes_use_the_plain_launcher(monkeypatch):
    """Change `sandboxed-code-node`: a source_code node runs in an OS sandbox
    (bwrap on Linux). The suite also runs on hosts without one, so every test
    injects the TESTS-ONLY plain subprocess launcher by dependency injection -
    production code has no switch to do this (Codex round 1, P0). Tests that
    exercise the real bwrap launcher construct it explicitly and skip when the
    host has no bwrap."""
    from tinyassets import node_sandbox

    monkeypatch.setattr(
        node_sandbox, "DEFAULT_LAUNCHER_FACTORY",
        lambda: node_sandbox.PlainSubprocessLauncher(),
    )


@pytest.fixture
def checkpointer():
    """Yield an in-memory SqliteSaver for testing.

    Uses the ``from_conn_string`` context manager pattern.
    """
    with SqliteSaver.from_conn_string(":memory:") as cp:
        yield cp


@pytest.fixture
def tmp_story_db():
    """Create a temp story.db path for world state, cleaned up after test."""
    fd, path = tempfile.mkstemp(suffix=".db", prefix="story_test_")
    os.close(fd)
    os.unlink(path)  # Remove so init_db creates fresh
    yield path
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


@pytest.fixture
def scene_input(tmp_story_db) -> dict[str, Any]:
    """Minimal valid input for the Scene graph."""
    return {
        "universe_id": "test-universe",
        "book_number": 1,
        "chapter_number": 1,
        "scene_number": 1,
        "orient_result": {},
        "retrieved_context": {},
        "recent_prose": "",
        "workflow_instructions": {},
        "memory_context": {},
        "search_context": {},
        "plan_output": None,
        "draft_output": None,
        "commit_result": None,
        "editorial_notes": None,
        "second_draft_used": False,
        "verdict": "",
        "extracted_facts": [],
        "extracted_promises": [],
        "style_observations": [],
        "quality_trace": [],
        "quality_debt": [],
        "_universe_path": "",
        "_db_path": tmp_story_db,
        "_kg_path": "",
    }


@pytest.fixture
def chapter_input() -> dict[str, Any]:
    """Minimal valid input for the Chapter graph."""
    return {
        "universe_id": "test-universe",
        "book_number": 1,
        "chapter_number": 1,
        "scenes_completed": 0,
        "scenes_target": 2,
        "chapter_summary": None,
        "consolidated_facts": [],
        "quality_trend": {},
        "chapter_arc": {},
        "style_rules_observed": [],
        "craft_cards_generated": [],
    }


@pytest.fixture
def book_input() -> dict[str, Any]:
    """Minimal valid input for the Book graph."""
    return {
        "universe_id": "test-universe",
        "book_number": 1,
        "chapters_completed": 0,
        "chapters_target": 1,
        "book_summary": None,
        "book_arc": {},
        "health": {"stuck_level": 0},
        "cross_book_promises_active": [],
        "quality_trace": [],
    }


@pytest.fixture
def universe_input() -> dict[str, Any]:
    """Minimal valid input for the Universe graph."""
    return {
        "universe_id": "test-universe",
        "universe_path": "/tmp/test-universe",
        "review_stage": "foundation",
        "active_series": None,
        "series_completed": [],
        "selected_target_id": None,
        "selected_intent": None,
        "alternate_target_ids": [],
        "current_task": None,
        "current_execution_id": None,
        "current_execution_ref": None,
        "last_review_artifact_ref": None,
        "work_targets_ref": "work_targets.json",
        "hard_priorities_ref": "hard_priorities.json",
        "timeline_ref": None,
        "soft_conflicts": [],
        "world_state_version": 0,
        "canon_facts_count": 0,
        "total_words": 0,
        "total_chapters": 0,
        "health": {},
        "task_queue": ["write"],
        "universal_style_rules": [],
        "cross_series_facts": [],
        "quality_trace": [],
    }


# There is no anonymous principal (founder, 2026-09-02). The dev auth provider
# names its local operator through UNIVERSE_SERVER_DEV_USER and refuses to
# start without it; the suite runs as this named operator unless a test sets
# its own. A test that wants NO identity calls auth_middleware(None) and
# asserts the refusal, never a stand-in.
import os as _os

_os.environ.setdefault("UNIVERSE_SERVER_DEV_USER", "dev-tests")

# pystray picks its tray backend at import and, off Windows/macOS, opens an X
# display to do it -- so on a headless Linux runner `import tinyassets_tray`
# died at COLLECTION and the tray tests never ran in CI at all (they sat in
# known-failing-tests.txt as collection errors for two months). Its own dummy
# backend is the headless choice; a real desktop keeps whatever it has.
if not sys.platform.startswith(("win", "darwin")) and not _os.environ.get("DISPLAY"):
    _os.environ.setdefault("PYSTRAY_BACKEND", "dummy")
