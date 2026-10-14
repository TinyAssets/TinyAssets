"""The account storage gate on the writers that lost their own caps.

account-storage-quota 3.2 (founder 2026-09-30): project memory, the daemon wiki /
daemon memory, and the app UI library had per-feature caps that
`remove-non-usage-limits` deleted (#4134), leaving them unbounded. They are now
bounded by the ONE per-account pool: refused visibly at the quota, never before;
reads never refused; nothing written on a refusal.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tinyassets import storage_accounting as sa
from tinyassets.daemon_server import grant_universe_ownership, initialize_author_server

A = "workos|alice"
B = "workos|bob"
KIB = 1024


@pytest.fixture
def base(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(root))
    monkeypatch.setenv("TINYASSETS_FREE_STORAGE_GIB", str(100 * KIB / 1024**3))
    initialize_author_server(root)
    return root


def _account_with_bytes(base: Path, owner: str, uid: str, used: int) -> Path:
    grant_universe_ownership(base, universe_id=uid, owner_id=owner)
    udir = base / uid
    udir.mkdir(exist_ok=True)
    (udir / "filler.bin").write_bytes(b"x" * used)
    return udir


# --------------------------------------------------------------------------- #
# Project memory
# --------------------------------------------------------------------------- #


class TestProjectMemory:
    """Owner-box quota refusals preserve canonical values and append-only history.

    Provider refusal is injected here; actual OS quota enforcement belongs to C6.
    The former writer-account ledger path has been retired from this consumer.
    """

    @pytest.fixture
    def project(self, tmp_path):
        from tests.support.project_snapshot import records
        from tinyassets.boxes.local import LocalBoxProvider
        from tinyassets.okf.bundle import KnowledgeBundle

        provider = LocalBoxProvider(
            boxes_root=tmp_path / "boxes",
            state_dir=tmp_path / "state",
            owner_of={"a": A, "b": B}.get,
            allow_unisolated=True,
        )
        bundle = KnowledgeBundle(provider, provider.bind("a", account_id=A))
        yield records(bundle, tmp_path, namespaces=("p",))
        provider.close()

    def _set(self, project, value, **kw):
        from tinyassets.memory.project import project_memory_set

        return project_memory_set(project, project_id="p", key="k", value=value, actor=A, **kw)

    def test_under_the_quota_it_writes(self, project):
        assert self._set(project, "hello")["status"] == "ok"

    def test_at_the_quota_it_is_refused_and_nothing_is_committed(self, project, monkeypatch):
        from tinyassets.boxes.provider import BoxError
        from tinyassets.memory.project import project_memory_get

        before = project.snapshot()

        def refuse(*args, **kwargs):
            raise BoxError("owner storage quota exceeded")

        monkeypatch.setattr(project.bundle.provider, "write", refuse)
        with pytest.raises(BoxError, match="quota"):
            self._set(project, "too large")
        assert project_memory_get(project, project_id="p", key="k") is None
        assert project.snapshot().manifest == before.manifest

    def test_writer_identity_cannot_grant_another_project(self, project):
        from tinyassets.memory.project import project_memory_set
        from tinyassets.okf.bundle import InvalidBundle

        with pytest.raises(InvalidBundle, match="binding"):
            project_memory_set(project, project_id="foreign", key="k", value="x", actor=B)

    def test_host_path_is_not_a_canonical_authority(self, tmp_path):
        from tinyassets.memory.project import project_memory_set
        from tinyassets.okf.bundle import InvalidBundle

        with pytest.raises(InvalidBundle, match="admitted"):
            project_memory_set(tmp_path, project_id="p", key="k", value="x", actor="host")
        assert not (tmp_path / ".project_memory.db").exists()

    def test_a_conflict_never_attempts_provider_writes(self, project, monkeypatch):
        assert self._set(project, "v1")["status"] == "ok"

        def unexpected(*args, **kwargs):
            raise AssertionError("version conflict attempted write")

        monkeypatch.setattr(project.bundle.provider, "write", unexpected)
        assert self._set(project, "v2", expected_version=99)["conflict"] is True

    def test_reads_remain_available_after_write_quota_refusal(self, project, monkeypatch):
        from tinyassets.boxes.provider import BoxError
        from tinyassets.memory.project import project_memory_get, project_memory_list

        self._set(project, "kept")

        def refuse(*args, **kwargs):
            raise BoxError("owner storage quota exceeded")

        monkeypatch.setattr(project.bundle.provider, "write", refuse)
        with pytest.raises(BoxError):
            self._set(project, "refused")
        assert project_memory_get(project, project_id="p", key="k")["value"] == "kept"
        assert len(project_memory_list(project, project_id="p")) == 1

    def test_current_and_history_bytes_live_in_the_owner_box(self, project, tmp_path):
        self._set(project, "y" * (20 * KIB))
        rows = project.rows(project.snapshot())
        assert len(next(iter(rows["project_memory"].values()))["value"]) > 20 * KIB
        assert len(next(iter(rows["project_memory_history"].values()))["value"]) > 20 * KIB
        assert not (tmp_path / ".project_memory.db").exists()

    def test_committed_response_loss_reuses_one_history_version(self, project, monkeypatch):
        from tinyassets.boxes.provider import BoxError
        from tinyassets.okf.bundle import INDEX

        real = project.bundle.provider.write

        def lose(handle, op, path, data, **kwargs):
            result = real(handle, op, path, data, **kwargs)
            if path == INDEX:
                raise BoxError("lost commit reply")
            return result

        monkeypatch.setattr(project.bundle.provider, "write", lose)
        with pytest.raises(BoxError):
            self._set(project, "value", operation_id="stable-request")
        assert self._set(project, "value", operation_id="stable-request")["version"] == 1
        assert len(project.rows(project.snapshot())["project_memory_history"]) == 1

    def test_request_id_reuse_cannot_change_committed_content(self, project):
        from tinyassets.okf.bundle import InvalidBundle

        self._set(project, "original", operation_id="stable")
        with pytest.raises(InvalidBundle, match="different content"):
            self._set(project, "changed", operation_id="stable")


# --------------------------------------------------------------------------- #
# App UI library
# --------------------------------------------------------------------------- #


class TestUiLibrary:
    def _save(self, base, owner, library, revision=0):
        from tinyassets.app_ui_store import save_app_ui

        return save_app_ui(
            base,
            owner_user_id=owner,
            universe_id="u-a",
            expected_revision=revision,
            changes={"ui_library": library},
        )

    def test_under_the_quota_it_saves(self, base):
        _account_with_bytes(base, A, "u-a", 1 * KIB)
        assert self._save(base, A, [])["revision"] == 1

    def test_at_the_quota_it_is_refused_and_nothing_is_saved(self, base):
        from tinyassets.app_ui_store import get_app_ui

        _account_with_bytes(base, A, "u-a", 95 * KIB)
        big = [{"ui_id": "ui", "html": "x" * (10 * KIB)}]
        with pytest.raises(sa.StorageRefused):
            self._save(base, A, big)

        assert get_app_ui(base, owner_user_id=A, universe_id="u-a")["revision"] == 0

    def test_a_conflict_releases_its_reservation(self, base):
        from tinyassets.commons_catalog import AgentConflictError

        _account_with_bytes(base, A, "u-a", 1 * KIB)
        with pytest.raises(AgentConflictError):
            self._save(base, A, [{"ui_id": "ui", "html": "x" * (60 * KIB)}], revision=7)
        assert self._save(base, A, [{"ui_id": "ui", "html": "x" * (60 * KIB)}])["revision"] == 1

    def test_a_collaborator_with_no_universe_still_has_a_pool(self, base):
        """gpt-6-astra PR #4158: a collaborator who owns nothing was exempt, so
        their saves into someone else's universe escaped every quota."""
        _account_with_bytes(base, A, "u-a", 1 * KIB)
        with pytest.raises(sa.StorageRefused):
            self._save(base, B, [{"ui_id": "ui", "html": "x" * (110 * KIB)}])

    def test_the_api_returns_the_refusal_record(self, base, monkeypatch):
        from tinyassets.api import app_ui

        _account_with_bytes(base, A, "u-a", 95 * KIB)
        monkeypatch.setattr(app_ui, "_binding_universe", lambda uid: "u-a")
        monkeypatch.setattr(app_ui, "_binding_access", lambda uid, write: None)
        monkeypatch.setattr(app_ui, "_authenticated_actor", lambda: A)
        monkeypatch.setattr(app_ui, "_base_path", lambda: base)

        out = app_ui.write_app_ui(
            universe_id="u-a",
            payload={"ui_library": [{"ui_id": "ui", "html": "x" * (10 * KIB)}]},
        )

        assert out["failure_class"] == sa.FAILURE_QUOTA
        assert "Upgrade" in out["error"]


# --------------------------------------------------------------------------- #
# Daemon memory (the daemon wiki's caps and eviction are gone)
# --------------------------------------------------------------------------- #


def _daemon(base: Path, owner: str) -> str:
    from tinyassets.daemon_registry import create_daemon

    return create_daemon(
        base,
        display_name="Test Ada",
        created_by=owner,
        soul_mode="soul",
        soul_text="Test Ada is a careful test daemon.",
    )["daemon_id"]


def _capture(base: Path, daemon_id: str, content: str):
    from tinyassets.daemon_brain import capture_daemon_memory

    return capture_daemon_memory(
        base,
        daemon_id=daemon_id,
        content=content,
        memory_kind="failure_mode",
        source_type="manual",
        source_id="pytest",
        reliability="host_observed",
        temporal_bounds={"valid_from": "2026-09-30"},
        language_type="policy",
    )


class TestDaemonMemory:
    def test_capture_is_refused_at_the_owners_quota(self, base):
        _account_with_bytes(base, A, "u-a", 95 * KIB)
        daemon = _daemon(base, A)
        with pytest.raises(sa.StorageRefused):
            _capture(base, daemon, "x" * (10 * KIB))

    def test_capture_under_the_quota_writes(self, base):
        _account_with_bytes(base, A, "u-a", 1 * KIB)
        daemon = _daemon(base, A)
        assert _capture(base, daemon, "remember this")["content"] == "remember this"

    def test_a_platform_daemon_is_not_gated(self, base):
        _account_with_bytes(base, A, "u-a", 95 * KIB)
        daemon = _daemon(base, "host")
        assert _capture(base, daemon, "x" * (10 * KIB))

    def test_promotion_is_refused_at_the_quota(self, base):
        from tinyassets.daemon_brain import promote_daemon_memory_to_wiki

        _account_with_bytes(base, A, "u-a", 1 * KIB)
        daemon = _daemon(base, A)
        entry = _capture(base, daemon, "y" * (30 * KIB))
        (base / "u-a" / "more.bin").write_bytes(b"x" * (60 * KIB))
        sa.measure(base, "u-a", "universe_files")

        with pytest.raises(sa.StorageRefused):
            promote_daemon_memory_to_wiki(
                base,
                daemon_id=daemon,
                entry_ids=[entry["entry_id"]],
                summary="s",
            )

    def test_every_persisted_field_is_reserved_not_just_content(self, base):
        """A tiny content with a huge source path must not slip the gate."""
        from tinyassets.daemon_brain import capture_daemon_memory

        _account_with_bytes(base, A, "u-a", 90 * KIB)
        daemon = _daemon(base, A)
        with pytest.raises(sa.StorageRefused):
            capture_daemon_memory(
                base,
                daemon_id=daemon,
                content="tiny",
                memory_kind="failure_mode",
                source_type="manual",
                source_id="pytest",
                reliability="host_observed",
                source_path="p" * (20 * KIB),
                language_type="policy",
            )

    def test_promotion_metadata_is_reserved_and_measured(self, base):
        from tinyassets.daemon_brain import promote_daemon_memory_to_wiki

        _account_with_bytes(base, A, "u-a", 1 * KIB)
        daemon = _daemon(base, A)
        entry = _capture(base, daemon, "short")
        promote_daemon_memory_to_wiki(
            base,
            daemon_id=daemon,
            entry_ids=[entry["entry_id"]],
            summary="s",
            metadata={"blob": "m" * (30 * KIB)},
        )
        sa.measure(base, A, "daemon_memory")
        measured = dict(((s, st), b) for s, st, b in sa.usage(base, A).breakdown)
        assert measured[(A, "daemon_memory")] >= 30 * KIB

    def test_the_mcp_action_returns_the_refusal(self, base, monkeypatch):
        from tinyassets.api import universe as api_universe

        _account_with_bytes(base, A, "u-a", 95 * KIB)
        daemon = _daemon(base, A)
        monkeypatch.setattr(api_universe, "_base_path", lambda: base)
        monkeypatch.setattr(api_universe, "_request_universe", lambda uid: "u-a")

        out = json.loads(
            api_universe._action_daemon_memory_capture(
                universe_id="u-a",
                daemon_id=daemon,
                inputs_json=json.dumps(
                    {
                        "content": "x" * (10 * KIB),
                        "reliability": "host_observed",
                        "language_type": "policy",
                    }
                ),
            )
        )

        assert out["failure_class"] == sa.FAILURE_QUOTA, out
