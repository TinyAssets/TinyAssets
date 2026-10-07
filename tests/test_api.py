"""Tests for the FastAPI HTTP layer.

Uses FastAPI's TestClient (synchronous) pointed at a temp base directory.
Follows patterns from test_mcp_server.py: temp directories via tmp_path,
monkeypatch for env vars, class-based test organization.
"""

from __future__ import annotations

import hashlib
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from fantasy_daemon.api import (
    _extract_username,
    _slugify,
    app,
    configure,
)


@pytest.fixture(autouse=True)
def _configure_api(tmp_path):
    """Point the API at a temp base directory for every test.

    Creates a ``test-universe/`` subdirectory with ``universe.json``
    so existing per-universe tests keep working.
    """
    uni = tmp_path / "test-universe"
    uni.mkdir()
    meta = {
        "id": "test-universe",
        "name": "Test Universe",
        "created_at": "2026-04-01T00:00:00Z",
        "auto_name": True,
    }
    (uni / "universe.json").write_text(
        json.dumps(meta, indent=2) + "\n", encoding="utf-8",
    )
    configure(base_path=str(tmp_path), api_key="", daemon=None)
    yield
    configure(base_path="", api_key="", daemon=None)


@pytest.fixture
def client():
    """Return a TestClient bound to the FastAPI app."""
    return TestClient(app)


@pytest.fixture
def universe_dir(tmp_path):
    """Return the test-universe directory path."""
    return tmp_path / "test-universe"


@pytest.fixture
def base_dir(tmp_path):
    """Return the base directory path."""
    return tmp_path


@pytest.fixture
def authed_client(tmp_path):
    """Return a TestClient with API key auth enabled."""
    configure(
        base_path=str(tmp_path),
        api_key="fa_testuser_sk_abc123",
        daemon=None,
    )
    return TestClient(app)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


class TestHealth:
    def test_health_ok(self, client):
        resp = client.get("/v1/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["universes"] >= 1
        assert data["daemon"] == "not_attached"

    def test_health_with_daemon(self, client, tmp_path):
        mock_daemon = MagicMock()
        mock_daemon.daemon_state = "running"
        configure(
            base_path=str(tmp_path),
            daemon=mock_daemon,
        )
        resp = client.get("/v1/health")
        assert resp.json()["daemon"] == "running"


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


class TestAuth:
    def test_no_key_allows_access(self, client):
        resp = client.get("/v1/universes/test-universe/status")
        assert resp.status_code == 200

    def test_valid_key_allows_access(self, authed_client):
        resp = authed_client.get(
            "/v1/universes/test-universe/status",
            headers={"Authorization": "Bearer fa_testuser_sk_abc123"},
        )
        assert resp.status_code == 200

    def test_invalid_key_rejected(self, authed_client):
        resp = authed_client.get(
            "/v1/universes/test-universe/status",
            headers={"Authorization": "Bearer wrong_key"},
        )
        assert resp.status_code == 401

    def test_missing_key_rejected(self, authed_client):
        resp = authed_client.get("/v1/universes/test-universe/status")
        assert resp.status_code == 401

    def test_extract_username(self):
        assert _extract_username("fa_jonathan_sk_abc123") == "jonathan"
        assert _extract_username("fa_user99_sk_xyz") == "user99"
        assert _extract_username("bad_key") == "anonymous"
        assert _extract_username("") == "anonymous"


# ---------------------------------------------------------------------------
# Universe ID validation
# ---------------------------------------------------------------------------


class TestUniverseValidation:
    def test_wrong_universe_returns_404(self, client):
        resp = client.get("/v1/universes/nonexistent/status")
        assert resp.status_code == 404
        assert "nonexistent" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Universe discovery & management
# ---------------------------------------------------------------------------


class TestListUniverses:
    def test_returns_universes(self, client):
        resp = client.get("/v1/universes")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["universes"]) >= 1
        ids = [u["id"] for u in data["universes"]]
        assert "test-universe" in ids

    def test_includes_metadata(self, client):
        resp = client.get("/v1/universes")
        uni = resp.json()["universes"][0]
        assert "name" in uni
        assert "created_at" in uni
        assert "auto_name" in uni
        assert "has_premise" in uni

    def test_includes_word_count_from_status(self, client, universe_dir):
        status = {"word_count": 4200, "daemon_state": "running"}
        (universe_dir / "status.json").write_text(
            json.dumps(status), encoding="utf-8",
        )
        resp = client.get("/v1/universes")
        uni = [u for u in resp.json()["universes"] if u["id"] == "test-universe"][0]
        assert uni["word_count"] == 4200
        assert uni["daemon_state"] == "running"

    def test_has_premise_flag(self, client, universe_dir):
        resp = client.get("/v1/universes")
        uni = [u for u in resp.json()["universes"] if u["id"] == "test-universe"][0]
        assert uni["has_premise"] is False

        (universe_dir / "PROGRAM.md").write_text("A story.", encoding="utf-8")
        resp = client.get("/v1/universes")
        uni = [u for u in resp.json()["universes"] if u["id"] == "test-universe"][0]
        assert uni["has_premise"] is True

    def test_empty_when_unconfigured(self, client):
        configure(base_path="", api_key="", daemon=None)
        # /v1/universes calls _base() which raises 503
        resp = client.get("/v1/universes")
        assert resp.status_code == 503

    def test_multiple_universes(self, client, base_dir):
        # Create a second universe
        second = base_dir / "second-world"
        second.mkdir()
        meta = {"id": "second-world", "name": "Second World",
                "created_at": "2026-04-01T01:00:00Z", "auto_name": False}
        (second / "universe.json").write_text(json.dumps(meta), encoding="utf-8")

        resp = client.get("/v1/universes")
        ids = [u["id"] for u in resp.json()["universes"]]
        assert "test-universe" in ids
        assert "second-world" in ids

    def test_hidden_dirs_excluded(self, client, base_dir):
        (base_dir / ".hidden").mkdir()
        resp = client.get("/v1/universes")
        ids = [u["id"] for u in resp.json()["universes"]]
        assert ".hidden" not in ids


class TestCreateUniverse:
    def test_create_with_name(self, client, base_dir):
        resp = client.post(
            "/v1/universes",
            json={"name": "The Dragon's Whisper"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "The Dragon's Whisper"
        slug = data["id"]
        assert "dragon" in slug

        # Directory and files created
        udir = base_dir / slug
        assert udir.is_dir()
        assert (udir / "canon").is_dir()
        assert (udir / "output").is_dir()
        assert (udir / "universe.json").exists()

        # Metadata correct
        meta = json.loads((udir / "universe.json").read_text(encoding="utf-8"))
        assert meta["name"] == "The Dragon's Whisper"
        assert meta["auto_name"] is False

    def test_create_without_name(self, client, base_dir):
        resp = client.post("/v1/universes")
        assert resp.status_code == 201
        data = resp.json()
        assert data["id"].startswith("universe-")
        assert len(data["id"]) > len("universe-")

        udir = base_dir / data["id"]
        meta = json.loads((udir / "universe.json").read_text(encoding="utf-8"))
        assert meta["auto_name"] is True

    def test_create_duplicate_rejected(self, client):
        resp = client.post(
            "/v1/universes",
            json={"name": "Test Universe"},
        )
        # "test-universe" slug already exists from fixture
        assert resp.status_code == 409

    def test_create_appears_in_list(self, client):
        client.post("/v1/universes", json={"name": "Brand New World"})
        resp = client.get("/v1/universes")
        ids = [u["id"] for u in resp.json()["universes"]]
        assert "brand-new-world" in ids


class TestUpdateUniverse:
    def test_update_name(self, client, universe_dir):
        resp = client.patch(
            "/v1/universes/test-universe",
            json={"name": "Renamed Universe"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Renamed Universe"
        assert data["auto_name"] is False

        # Persisted to disk
        meta = json.loads(
            (universe_dir / "universe.json").read_text(encoding="utf-8"),
        )
        assert meta["name"] == "Renamed Universe"
        assert meta["auto_name"] is False

    def test_update_nonexistent(self, client):
        resp = client.patch(
            "/v1/universes/nonexistent",
            json={"name": "New Name"},
        )
        assert resp.status_code == 404

    def test_update_empty_name_rejected(self, client):
        resp = client.patch(
            "/v1/universes/test-universe",
            json={"name": ""},
        )
        assert resp.status_code == 422


class TestDeleteUniverse:
    def test_delete_removes_directory(self, client, universe_dir):
        assert universe_dir.is_dir()
        resp = client.delete("/v1/universes/test-universe")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "test-universe"
        assert data["deleted"] is True
        assert not universe_dir.exists()

    def test_delete_nonexistent_returns_404(self, client):
        resp = client.delete("/v1/universes/nonexistent")
        assert resp.status_code == 404

    def test_delete_removes_from_list(self, client, base_dir):
        # Create a universe to delete
        client.post("/v1/universes", json={"name": "Doomed World"})
        resp = client.get("/v1/universes")
        ids = [u["id"] for u in resp.json()["universes"]]
        assert "doomed-world" in ids

        client.delete("/v1/universes/doomed-world")
        resp = client.get("/v1/universes")
        ids = [u["id"] for u in resp.json()["universes"]]
        assert "doomed-world" not in ids

    def test_delete_stops_daemon_on_active_universe(self, client, tmp_path):
        mock_daemon = MagicMock()
        mock_daemon._universe_id = "test-universe"
        mock_daemon._stop_event = MagicMock()
        mock_daemon._paused = MagicMock()
        configure(
            base_path=str(tmp_path),
            daemon=mock_daemon,
        )
        resp = client.delete("/v1/universes/test-universe")
        assert resp.status_code == 200
        mock_daemon._stop_event.set.assert_called_once()

    def test_delete_clears_active_universe_marker(self, client, base_dir):
        # Set the active universe marker
        marker = base_dir / ".active_universe"
        marker.write_text("test-universe", encoding="utf-8")

        resp = client.delete("/v1/universes/test-universe")
        assert resp.status_code == 200
        assert not marker.exists()

    def test_delete_preserves_other_universes(self, client, base_dir):
        # Create a second universe
        client.post("/v1/universes", json={"name": "Safe World"})
        client.delete("/v1/universes/test-universe")

        resp = client.get("/v1/universes")
        ids = [u["id"] for u in resp.json()["universes"]]
        assert "safe-world" in ids
        assert "test-universe" not in ids


# ---------------------------------------------------------------------------
# Backward compat / migration
# ---------------------------------------------------------------------------


class TestMigration:
    def test_auto_creates_universe_json(self, client, base_dir):
        """A directory without universe.json should be auto-migrated."""
        legacy = base_dir / "legacy-world"
        legacy.mkdir()
        # No universe.json -- just a bare directory

        resp = client.get("/v1/universes/legacy-world/status")
        assert resp.status_code == 200

        # universe.json should now exist
        meta_path = legacy / "universe.json"
        assert meta_path.exists()
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        assert meta["id"] == "legacy-world"
        assert meta["auto_name"] is True

    def test_migrated_universe_in_list(self, client, base_dir):
        legacy = base_dir / "old-saga"
        legacy.mkdir()

        resp = client.get("/v1/universes")
        ids = [u["id"] for u in resp.json()["universes"]]
        assert "old-saga" in ids


# ---------------------------------------------------------------------------
# Slugify
# ---------------------------------------------------------------------------


class TestSlugify:
    def test_basic(self):
        assert _slugify("The Dragon's Whisper") == "the-dragon-s-whisper"

    def test_spaces_and_special(self):
        assert _slugify("Hello World!") == "hello-world"

    def test_already_slug(self):
        assert _slugify("my-universe") == "my-universe"

    def test_empty_string_gets_random(self):
        slug = _slugify("")
        assert slug.startswith("universe-")


# ---------------------------------------------------------------------------
# Premise
# ---------------------------------------------------------------------------


class TestPremise:
    def test_get_premise(self, client, universe_dir):
        (universe_dir / "PROGRAM.md").write_text(
            "A dark fantasy about dragons.", encoding="utf-8",
        )
        resp = client.get("/v1/universes/test-universe/premise")
        assert resp.status_code == 200
        assert "dragons" in resp.json()["text"]
        assert resp.json()["source"] == "PROGRAM.md"

    def test_get_premise_falls_back_to_soul(self, client, universe_dir):
        from tinyassets.universe_soul import write_universe_soul

        write_universe_soul(
            universe_dir,
            purpose="A civic lab studies memory.",
            lineage="test",
        )
        resp = client.get("/v1/universes/test-universe/premise")
        assert resp.status_code == 200
        assert resp.json()["text"] == "A civic lab studies memory."
        assert resp.json()["source"] == "soul.md"

    def test_get_premise_missing(self, client):
        resp = client.get("/v1/universes/test-universe/premise")
        assert resp.status_code == 404

    def test_set_premise(self, client, universe_dir):
        resp = client.post(
            "/v1/universes/test-universe/premise",
            json={"text": "A wandering knight seeks redemption."},
        )
        assert resp.status_code == 200
        content = (universe_dir / "PROGRAM.md").read_text(encoding="utf-8")
        assert "wandering knight" in content
        soul = (universe_dir / "soul.md").read_text(encoding="utf-8")
        assert "wandering knight" in soul

    def test_set_premise_overwrites(self, client, universe_dir):
        client.post(
            "/v1/universes/test-universe/premise",
            json={"text": "Old premise"},
        )
        client.post(
            "/v1/universes/test-universe/premise",
            json={"text": "New premise"},
        )
        content = (universe_dir / "PROGRAM.md").read_text(encoding="utf-8")
        assert "New premise" in content
        assert "Old premise" not in content

    def test_set_premise_empty_rejected(self, client):
        resp = client.post(
            "/v1/universes/test-universe/premise",
            json={"text": ""},
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------


class TestNotesEndpoints:
    def test_post_note(self, client, universe_dir):
        resp = client.post(
            "/v1/universes/test-universe/notes",
            json={"text": "Focus on tension", "category": "direction"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "ok"
        assert data["note"]["source"] == "user"
        assert data["note"]["text"] == "Focus on tension"
        assert data["note"]["category"] == "direction"
        assert data["note"]["id"]

    def test_post_note_invalid_category(self, client):
        resp = client.post(
            "/v1/universes/test-universe/notes",
            json={"text": "test", "category": "invalid"},
        )
        assert resp.status_code == 422

    def test_get_notes_empty(self, client):
        resp = client.get("/v1/universes/test-universe/notes")
        assert resp.status_code == 200
        assert resp.json()["notes"] == []

    def test_get_notes_with_filter(self, client):
        client.post(
            "/v1/universes/test-universe/notes",
            json={"text": "A", "category": "direction"},
        )
        client.post(
            "/v1/universes/test-universe/notes",
            json={"text": "B", "category": "concern"},
        )
        resp = client.get("/v1/universes/test-universe/notes?category=concern")
        notes = resp.json()["notes"]
        assert len(notes) == 1
        assert notes[0]["text"] == "B"

    def test_post_then_get_roundtrip(self, client):
        client.post(
            "/v1/universes/test-universe/notes",
            json={"text": "First", "category": "direction"},
        )
        client.post(
            "/v1/universes/test-universe/notes",
            json={"text": "Second", "category": "protect"},
        )
        resp = client.get("/v1/universes/test-universe/notes")
        notes = resp.json()["notes"]
        assert len(notes) == 2

    def test_patch_note_status(self, client):
        resp = client.post(
            "/v1/universes/test-universe/notes",
            json={"text": "Mark me", "category": "direction"},
        )
        note_id = resp.json()["note"]["id"]

        resp = client.patch(
            f"/v1/universes/test-universe/notes/{note_id}",
            json={"status": "read"},
        )
        assert resp.status_code == 200

        resp = client.get("/v1/universes/test-universe/notes?status=read")
        assert len(resp.json()["notes"]) == 1

    def test_patch_note_invalid_status(self, client):
        resp = client.post(
            "/v1/universes/test-universe/notes",
            json={"text": "X", "category": "direction"},
        )
        note_id = resp.json()["note"]["id"]

        resp = client.patch(
            f"/v1/universes/test-universe/notes/{note_id}",
            json={"status": "invalid"},
        )
        assert resp.status_code == 422

    def test_patch_note_not_found(self, client):
        resp = client.patch(
            "/v1/universes/test-universe/notes/nonexistent",
            json={"status": "read"},
        )
        assert resp.status_code == 404

    def test_delete_note(self, client):
        resp = client.post(
            "/v1/universes/test-universe/notes",
            json={"text": "Delete me", "category": "observation"},
        )
        note_id = resp.json()["note"]["id"]

        resp = client.delete(
            f"/v1/universes/test-universe/notes/{note_id}",
        )
        assert resp.status_code == 200

        resp = client.get("/v1/universes/test-universe/notes")
        assert len(resp.json()["notes"]) == 0

    def test_delete_note_not_found(self, client):
        resp = client.delete(
            "/v1/universes/test-universe/notes/nonexistent",
        )
        assert resp.status_code == 404

    def test_post_note_with_concern_fields(self, client):
        resp = client.post(
            "/v1/universes/test-universe/notes",
            json={
                "text": "Wrong name used",
                "category": "error",
                "clearly_wrong": True,
                "quoted_passage": "Kael said softly",
            },
        )
        assert resp.status_code == 201
        note = resp.json()["note"]
        assert note["clearly_wrong"] is True
        assert note["quoted_passage"] == "Kael said softly"

    def test_post_note_with_line_anchor(self, client):
        resp = client.post(
            "/v1/universes/test-universe/notes",
            json={
                "text": "Tighten this fragment.",
                "category": "direction",
                "target": "output/chapter-1.md",
                "anchor": {
                    "start_line": 7,
                    "end_line": 8,
                    "start_column": 4,
                    "end_column": 18,
                },
            },
        )
        assert resp.status_code == 201
        note = resp.json()["note"]
        assert note["target"] == "output/chapter-1.md"
        assert note["anchor"] == {
            "start_line": 7,
            "end_line": 8,
            "start_column": 4,
            "end_column": 18,
        }


# ---------------------------------------------------------------------------
# TOMBSTONE — orphan TestStatus tests deleted 2026-04-16
# ---------------------------------------------------------------------------
# TestStatus.test_returns_status_json + test_missing_status_returns_defaults
# asserted ``last_process_score`` and ``process_failures`` keys on the
# /status response. Neither key exists in the API or status.json schema —
# `git log -S` returns zero add-commits for either. Same orphan family
# already tombstoned in tests/test_integration.py by commit d8a4757
# (TestDaemonController process-evaluation cleanup). Resurrection would
# require fresh design.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Activity
# ---------------------------------------------------------------------------


class TestActivity:
    def test_returns_tail(self, client, universe_dir):
        lines = [f"[2026-04-01 12:00:{i:02d}] Line {i}" for i in range(30)]
        (universe_dir / "activity.log").write_text(
            "\n".join(lines), encoding="utf-8",
        )
        resp = client.get("/v1/universes/test-universe/activity?lines=5")
        data = resp.json()
        assert len(data["lines"]) == 5
        assert "Line 29" in data["lines"][-1]

    def test_default_lines(self, client, universe_dir):
        lines = [f"Line {i}" for i in range(100)]
        (universe_dir / "activity.log").write_text(
            "\n".join(lines), encoding="utf-8",
        )
        resp = client.get("/v1/universes/test-universe/activity")
        data = resp.json()
        assert len(data["lines"]) == 50  # default

    def test_missing_log(self, client):
        resp = client.get("/v1/universes/test-universe/activity")
        assert resp.status_code == 200
        assert resp.json()["lines"] == []


# ---------------------------------------------------------------------------
# Progress
# ---------------------------------------------------------------------------


class TestProgress:
    def test_returns_progress(self, client, universe_dir):
        (universe_dir / "progress.md").write_text(
            "# Writing Progress\n\n8,500 words across 7 scenes.",
            encoding="utf-8",
        )
        resp = client.get("/v1/universes/test-universe/progress")
        assert resp.status_code == 200
        assert "8,500 words" in resp.json()["text"]

    def test_missing_progress(self, client):
        resp = client.get("/v1/universes/test-universe/progress")
        assert resp.status_code == 200
        assert "not" in resp.json()["text"].lower() or "No" in resp.json()["text"]


# ---------------------------------------------------------------------------
# Facts
# ---------------------------------------------------------------------------


class TestFacts:
    def test_returns_facts(self, client, universe_dir):
        """Should return facts stored in the world state DB."""
        from domains.fantasy_daemon.phases.world_state_db import connect, init_db, store_fact

        db_path = str(universe_dir / "story.db")
        init_db(db_path)
        with connect(db_path) as conn:
            store_fact(
                conn,
                fact_id="f1",
                text="Kael is a thief.",
                source_type="narrator_claim",
                language_type="literal",
                confidence=0.9,
                scene_id="s1",
                chapter_number=1,
                importance=0.7,
            )

        resp = client.get("/v1/universes/test-universe/facts")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["facts"][0]["text"] == "Kael is a thief."
        assert data["facts"][0]["source_type"] == "narrator_claim"

    def test_filter_by_chapter(self, client, universe_dir):
        """Should filter facts by chapter number."""
        from domains.fantasy_daemon.phases.world_state_db import connect, init_db, store_fact

        db_path = str(universe_dir / "story.db")
        init_db(db_path)
        with connect(db_path) as conn:
            store_fact(
                conn, fact_id="f1", text="Fact one.",
                scene_id="s1", chapter_number=1, importance=0.5,
            )
            store_fact(
                conn, fact_id="f2", text="Fact two.",
                scene_id="s2", chapter_number=2, importance=0.5,
            )

        resp = client.get("/v1/universes/test-universe/facts?chapter=1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["facts"][0]["fact_id"] == "f1"

    def test_no_db_returns_empty(self, client):
        """Without a DB file, should return empty facts list."""
        resp = client.get("/v1/universes/test-universe/facts")
        assert resp.status_code == 200
        assert resp.json()["facts"] == []
        assert resp.json()["count"] == 0


# ---------------------------------------------------------------------------
# Characters
# ---------------------------------------------------------------------------


class TestCharacters:
    def test_returns_characters(self, client, universe_dir):
        """Should return characters stored in the world state DB."""
        from domains.fantasy_daemon.phases.world_state_db import connect, init_db, upsert_character

        db_path = str(universe_dir / "story.db")
        init_db(db_path)
        with connect(db_path) as conn:
            upsert_character(
                conn,
                character_id="kael",
                name="Kael",
                location="market square",
                emotional_state="tense",
            )

        resp = client.get("/v1/universes/test-universe/characters")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["characters"][0]["name"] == "Kael"
        assert data["characters"][0]["location"] == "market square"

    def test_no_db_returns_empty(self, client):
        """Without a DB file, should return empty character list."""
        resp = client.get("/v1/universes/test-universe/characters")
        assert resp.status_code == 200
        assert resp.json()["characters"] == []
        assert resp.json()["count"] == 0


# ---------------------------------------------------------------------------
# Promises
# ---------------------------------------------------------------------------


class TestPromises:
    def test_returns_scene_promises(self, client, universe_dir):
        """Should return promises from the world state DB."""
        from domains.fantasy_daemon.phases.world_state_db import add_promise, connect, init_db

        db_path = str(universe_dir / "story.db")
        init_db(db_path)
        with connect(db_path) as conn:
            add_promise(
                conn,
                promise_id="p1",
                text="The dragon will return.",
                created_scene="s1",
                created_chapter=1,
                importance=0.8,
            )

        resp = client.get("/v1/universes/test-universe/promises")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] >= 1
        texts = [p["text"] for p in data["promises"]]
        assert "The dragon will return." in texts

    def test_filter_active(self, client, universe_dir):
        """?status=active should only return active promises."""
        from domains.fantasy_daemon.phases.world_state_db import (
            add_promise,
            connect,
            init_db,
            resolve_promise,
        )

        db_path = str(universe_dir / "story.db")
        init_db(db_path)
        with connect(db_path) as conn:
            add_promise(
                conn, promise_id="p1", text="Active.",
                created_scene="s1", created_chapter=1,
            )
            add_promise(
                conn, promise_id="p2", text="Resolved.",
                created_scene="s1", created_chapter=1,
            )
            resolve_promise(
                conn, promise_id="p2",
                resolved_scene="s3", resolved_chapter=2,
            )

        resp = client.get("/v1/universes/test-universe/promises?status=active")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["promises"][0]["text"] == "Active."

    def test_no_db_returns_empty(self, client):
        """Without a DB file, should return empty promises list."""
        resp = client.get("/v1/universes/test-universe/promises")
        assert resp.status_code == 200
        assert resp.json()["promises"] == []
        assert resp.json()["count"] == 0


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


class TestListOutput:
    def test_lists_files(self, client, universe_dir):
        book_dir = universe_dir / "output" / "book-1"
        book_dir.mkdir(parents=True)
        (book_dir / "chapter-01.md").write_text("Chapter one.", encoding="utf-8")
        (book_dir / "chapter-02.md").write_text("Chapter two content here.", encoding="utf-8")
        resp = client.get("/v1/universes/test-universe/output")
        assert resp.status_code == 200
        files = resp.json()["files"]
        assert len(files) == 2
        paths = [f["path"] for f in files]
        assert "book-1/chapter-01.md" in paths
        assert "book-1/chapter-02.md" in paths

    def test_includes_size(self, client, universe_dir):
        output_dir = universe_dir / "output"
        output_dir.mkdir(parents=True)
        content = "Exactly this text."
        (output_dir / "scene.md").write_text(content, encoding="utf-8")
        resp = client.get("/v1/universes/test-universe/output")
        files = resp.json()["files"]
        assert len(files) == 1
        assert files[0]["size"] == len(content.encode("utf-8"))

    def test_empty_when_no_output_dir(self, client):
        resp = client.get("/v1/universes/test-universe/output")
        assert resp.status_code == 200
        assert resp.json()["files"] == []

    def test_nested_directories(self, client, universe_dir):
        deep = universe_dir / "output" / "book-2" / "drafts"
        deep.mkdir(parents=True)
        (deep / "scene-03.md").write_text("Deep file.", encoding="utf-8")
        resp = client.get("/v1/universes/test-universe/output")
        files = resp.json()["files"]
        assert len(files) == 1
        assert files[0]["path"] == "book-2/drafts/scene-03.md"

    def test_excludes_directories(self, client, universe_dir):
        book_dir = universe_dir / "output" / "book-1"
        book_dir.mkdir(parents=True)
        (book_dir / "chapter-01.md").write_text("Content.", encoding="utf-8")
        resp = client.get("/v1/universes/test-universe/output")
        for f in resp.json()["files"]:
            assert not f["path"].endswith("/")
            assert "chapter" in f["path"]

    def test_wrong_universe_rejected(self, client):
        resp = client.get("/v1/universes/wrong/output")
        assert resp.status_code == 404

    def test_paths_use_forward_slashes(self, client, universe_dir):
        """Paths should use forward slashes regardless of OS."""
        book_dir = universe_dir / "output" / "book-1"
        book_dir.mkdir(parents=True)
        (book_dir / "ch01.md").write_text("x", encoding="utf-8")
        resp = client.get("/v1/universes/test-universe/output")
        for f in resp.json()["files"]:
            assert "\\" not in f["path"]


class TestOutput:
    def test_reads_chapter(self, client, universe_dir):
        chapter_dir = universe_dir / "output" / "book-1"
        chapter_dir.mkdir(parents=True)
        (chapter_dir / "chapter-03.md").write_text(
            "Ryn entered the forest.", encoding="utf-8",
        )
        resp = client.get("/v1/universes/test-universe/output/book-1/chapter-03.md")
        assert resp.status_code == 200
        assert "Ryn entered the forest" in resp.text

    def test_missing_file(self, client):
        resp = client.get("/v1/universes/test-universe/output/book-1/chapter-99.md")
        assert resp.status_code == 404

    def test_path_traversal_blocked(self, client, universe_dir):
        (universe_dir / "secret.txt").write_text("secret", encoding="utf-8")
        resp = client.get("/v1/universes/test-universe/output/../../secret.txt")
        # 400 (our check), 404 (file not found), or 405 (URL normalized away by Starlette)
        assert resp.status_code in (400, 404, 405)

    def test_returns_plain_text(self, client, universe_dir):
        output_dir = universe_dir / "output"
        output_dir.mkdir(parents=True)
        (output_dir / "notes.md").write_text("Some notes.", encoding="utf-8")
        resp = client.get("/v1/universes/test-universe/output/notes.md")
        assert "text/plain" in resp.headers["content-type"]


# ---------------------------------------------------------------------------
# Canon
# ---------------------------------------------------------------------------


class TestCanon:
    def test_writes_file(self, client, universe_dir):
        resp = client.post(
            "/v1/universes/test-universe/canon",
            json={"filename": "characters.md", "content": "# Characters\n\nRyn: a wanderer."},
        )
        assert resp.status_code == 201
        assert resp.json()["filename"] == "characters.md"
        content = (universe_dir / "canon" / "characters.md").read_text(encoding="utf-8")
        assert "Ryn: a wanderer" in content

    def test_creates_canon_dir(self, client, universe_dir):
        assert not (universe_dir / "canon").exists()
        client.post(
            "/v1/universes/test-universe/canon",
            json={"filename": "lore.md", "content": "World lore."},
        )
        assert (universe_dir / "canon").exists()

    def test_overwrites_existing(self, client, universe_dir):
        client.post(
            "/v1/universes/test-universe/canon",
            json={"filename": "notes.md", "content": "Old notes"},
        )
        client.post(
            "/v1/universes/test-universe/canon",
            json={"filename": "notes.md", "content": "New notes"},
        )
        content = (universe_dir / "canon" / "notes.md").read_text(encoding="utf-8")
        assert "New notes" in content
        assert "Old notes" not in content

    def test_path_traversal_sanitized(self, client, universe_dir):
        client.post(
            "/v1/universes/test-universe/canon",
            json={"filename": "../../evil.md", "content": "Malicious content"},
        )
        assert (universe_dir / "canon" / "evil.md").exists()
        assert not (universe_dir.parent / "evil.md").exists()


class TestListCanon:
    def test_lists_files(self, client, universe_dir):
        canon_dir = universe_dir / "canon"
        canon_dir.mkdir(parents=True)
        (canon_dir / "dragons.md").write_text("# Dragons\nFire-breathing.", encoding="utf-8")
        (canon_dir / "characters.md").write_text("# Characters\nRyn.", encoding="utf-8")
        resp = client.get("/v1/universes/test-universe/canon")
        assert resp.status_code == 200
        files = resp.json()["files"]
        assert len(files) == 2
        names = [f["filename"] for f in files]
        assert "dragons.md" in names
        assert "characters.md" in names

    def test_includes_size(self, client, universe_dir):
        canon_dir = universe_dir / "canon"
        canon_dir.mkdir(parents=True)
        content = "Lore about the ancient world."
        (canon_dir / "lore.md").write_text(content, encoding="utf-8")
        resp = client.get("/v1/universes/test-universe/canon")
        files = resp.json()["files"]
        assert len(files) == 1
        assert files[0]["size"] == len(content.encode("utf-8"))

    def test_empty_when_no_canon_dir(self, client):
        resp = client.get("/v1/universes/test-universe/canon")
        assert resp.status_code == 200
        assert resp.json()["files"] == []

    def test_excludes_directories(self, client, universe_dir):
        canon_dir = universe_dir / "canon"
        canon_dir.mkdir(parents=True)
        (canon_dir / "subdir").mkdir()
        (canon_dir / "file.md").write_text("Content.", encoding="utf-8")
        resp = client.get("/v1/universes/test-universe/canon")
        files = resp.json()["files"]
        assert len(files) == 1
        assert files[0]["filename"] == "file.md"

    def test_wrong_universe_rejected(self, client):
        resp = client.get("/v1/universes/wrong/canon")
        assert resp.status_code == 404


class TestListCanonSources:
    def test_empty_when_no_sources(self, client, universe_dir):
        resp = client.get("/v1/universes/test-universe/canon/sources")
        assert resp.status_code == 200
        data = resp.json()
        assert data["source_files"] == []
        assert data["source_count"] == 0
        assert data["pending_synthesis"] == 0
        assert data["synthesized_docs"] == []
        assert data["synthesized_count"] == 0

    def test_lists_source_files(self, client, universe_dir):
        sources = universe_dir / "canon" / "sources"
        sources.mkdir(parents=True)
        (sources / "worldbible.md").write_text("World bible content.", encoding="utf-8")
        (sources / "magic_system.md").write_text("Magic rules.", encoding="utf-8")

        resp = client.get("/v1/universes/test-universe/canon/sources")
        assert resp.status_code == 200
        data = resp.json()
        assert data["source_count"] == 2
        names = [f["filename"] for f in data["source_files"]]
        assert "worldbible.md" in names
        assert "magic_system.md" in names

    def test_source_files_have_metadata(self, client, universe_dir):
        sources = universe_dir / "canon" / "sources"
        sources.mkdir(parents=True)
        content = "A" * 500
        (sources / "lore.md").write_text(content, encoding="utf-8")

        resp = client.get("/v1/universes/test-universe/canon/sources")
        sf = resp.json()["source_files"][0]
        assert sf["filename"] == "lore.md"
        assert sf["size"] == len(content.encode("utf-8"))
        assert "modified_at" in sf

    def test_enriches_from_manifest(self, client, universe_dir):
        canon = universe_dir / "canon"
        sources = canon / "sources"
        sources.mkdir(parents=True)
        (sources / "worldbible.md").write_text("Content.", encoding="utf-8")

        manifest = {
            "worldbible.md": {
                "filename": "worldbible.md",
                "source_path": "sources/worldbible.md",
                "file_type": "text",
                "mime_type": "text/markdown",
                "byte_count": 8,
                "sha256": "abc123",
                "routed_to": "sources",
                "synthesized_docs": ["characters.md", "locations.md"],
                "ingested_at": "2026-04-01T00:00:00Z",
            }
        }
        (canon / ".manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8",
        )
        # Also create the synthesized docs so they show up
        (canon / "characters.md").write_text("# Characters", encoding="utf-8")
        (canon / "locations.md").write_text("# Locations", encoding="utf-8")

        resp = client.get("/v1/universes/test-universe/canon/sources")
        data = resp.json()

        # Source file enriched with synthesis status
        sf = data["source_files"][0]
        assert sf["file_type"] == "text"
        assert sf["synthesis_complete"] is True
        assert sf["synthesized_docs"] == ["characters.md", "locations.md"]

        # Synthesized docs listed
        assert data["synthesized_count"] == 2
        synth_names = [d["filename"] for d in data["synthesized_docs"]]
        assert "characters.md" in synth_names
        assert "locations.md" in synth_names
        assert data["synthesized_docs"][0]["source_file"] == "worldbible.md"

    def test_pending_synthesis_counted(self, client, universe_dir):
        signals = [
            {"type": "synthesize_source", "source_file": "bible.md"},
            {"type": "new_element", "topic": "character"},
            {"type": "synthesize_source", "source_file": "magic.md"},
        ]
        (universe_dir / "worldbuild_signals.json").write_text(
            json.dumps(signals), encoding="utf-8",
        )

        resp = client.get("/v1/universes/test-universe/canon/sources")
        assert resp.json()["pending_synthesis"] == 2

    def test_unsynthesized_source_marked_incomplete(self, client, universe_dir):
        canon = universe_dir / "canon"
        sources = canon / "sources"
        sources.mkdir(parents=True)
        (sources / "raw_notes.md").write_text("Raw notes.", encoding="utf-8")

        # Manifest exists but no synthesized_docs yet
        manifest = {
            "raw_notes.md": {
                "filename": "raw_notes.md",
                "source_path": "sources/raw_notes.md",
                "file_type": "text",
                "mime_type": "text/markdown",
                "byte_count": 10,
                "sha256": "def456",
                "routed_to": "sources",
                "synthesized_docs": [],
                "ingested_at": "2026-04-01T00:00:00Z",
            }
        }
        (canon / ".manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8",
        )

        resp = client.get("/v1/universes/test-universe/canon/sources")
        data = resp.json()
        sf = data["source_files"][0]
        assert sf["synthesis_complete"] is False
        assert sf["synthesized_docs"] == []

        # Gap state: no pending signals but synthesis incomplete
        # -> should re-emit signals and report pending > 0
        assert data["pending_synthesis"] == 1

        # Verify the signal was written to disk
        signals_file = universe_dir / "enrichment_signals.json"
        assert signals_file.exists()
        signals = json.loads(signals_file.read_text(encoding="utf-8"))
        synth_signals = [
            s for s in signals if s.get("type") == "synthesize_source"
        ]
        assert len(synth_signals) == 1
        assert synth_signals[0]["source_file"] == "raw_notes.md"

    def test_reemit_skips_already_queued(self, client, universe_dir):
        """Don't duplicate signals for sources already in the queue."""
        canon = universe_dir / "canon"
        sources = canon / "sources"
        sources.mkdir(parents=True)
        (sources / "lore.md").write_text("Lore.", encoding="utf-8")

        manifest = {
            "lore.md": {
                "filename": "lore.md",
                "source_path": "sources/lore.md",
                "file_type": "text",
                "mime_type": "text/markdown",
                "byte_count": 5,
                "sha256": "abc123",
                "routed_to": "sources",
                "synthesized_docs": [],
                "ingested_at": "2026-04-01T00:00:00Z",
            }
        }
        (canon / ".manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8",
        )

        # Pre-existing signal for this file
        (universe_dir / "worldbuild_signals.json").write_text(
            json.dumps([{
                "type": "synthesize_source",
                "source_file": "lore.md",
            }]),
            encoding="utf-8",
        )

        resp = client.get("/v1/universes/test-universe/canon/sources")
        # Should count the existing signal, not duplicate it
        assert resp.json()["pending_synthesis"] == 1

        # The API path migrates legacy reads forward to the canonical
        # enrichment_signals.json, so assert the EFFECTIVE (canonical-first)
        # state via the helper rather than rereading the legacy file the daemon
        # no longer writes — a legacy reread can pass spuriously while canonical
        # is missing or wrong.
        from tinyassets.enrichment_signals import load_enrichment_signals

        signals = load_enrichment_signals(universe_dir)
        synth = [
            s for s in signals if s.get("type") == "synthesize_source"
        ]
        assert len(synth) == 1  # No duplicate

    def test_hidden_files_excluded(self, client, universe_dir):
        sources = universe_dir / "canon" / "sources"
        sources.mkdir(parents=True)
        (sources / ".hidden").write_text("Hidden.", encoding="utf-8")
        (sources / "visible.md").write_text("Visible.", encoding="utf-8")

        resp = client.get("/v1/universes/test-universe/canon/sources")
        names = [f["filename"] for f in resp.json()["source_files"]]
        assert "visible.md" in names
        assert ".hidden" not in names


    def test_reemit_stops_after_max_retries(self, client, universe_dir):
        """After MAX_SYNTHESIS_RETRIES, sources are not re-emitted."""
        canon = universe_dir / "canon"
        sources = canon / "sources"
        sources.mkdir(parents=True)
        (sources / "corrupt.bin").write_text("binary junk", encoding="utf-8")

        manifest = {
            "corrupt.bin": {
                "filename": "corrupt.bin",
                "source_path": "sources/corrupt.bin",
                "file_type": "text",
                "mime_type": "application/octet-stream",
                "byte_count": 11,
                "sha256": "abc",
                "routed_to": "sources",
                "synthesized_docs": [],
                "ingested_at": "2026-04-01T00:00:00Z",
                "synthesis_attempts": 3,
                "synthesis_failed": True,
            }
        }
        (canon / ".manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8",
        )

        resp = client.get("/v1/universes/test-universe/canon/sources")
        data = resp.json()

        # Should NOT re-emit — max retries exceeded
        assert data["pending_synthesis"] == 0

        # Source should report synthesis_failed (set by worldbuild node)
        source = next(
            s for s in data["source_files"] if s["filename"] == "corrupt.bin"
        )
        assert source["synthesis_failed"] is True

    def test_reemit_does_not_modify_manifest(self, client, universe_dir):
        """API re-emit should not increment synthesis_attempts —
        only the worldbuild node tracks attempt counts."""
        canon = universe_dir / "canon"
        sources = canon / "sources"
        sources.mkdir(parents=True)
        (sources / "notes.md").write_text("Some notes.", encoding="utf-8")

        manifest = {
            "notes.md": {
                "filename": "notes.md",
                "source_path": "sources/notes.md",
                "file_type": "text",
                "mime_type": "text/markdown",
                "byte_count": 11,
                "sha256": "abc",
                "routed_to": "sources",
                "synthesized_docs": [],
                "ingested_at": "2026-04-01T00:00:00Z",
                "synthesis_attempts": 1,
            }
        }
        (canon / ".manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8",
        )

        resp = client.get("/v1/universes/test-universe/canon/sources")
        assert resp.json()["pending_synthesis"] == 1

        # Manifest should be UNCHANGED — API doesn't modify it
        updated = json.loads(
            (canon / ".manifest.json").read_text(encoding="utf-8")
        )
        assert updated["notes.md"]["synthesis_attempts"] == 1


class TestGetCanonFile:
    def test_reads_file(self, client, universe_dir):
        canon_dir = universe_dir / "canon"
        canon_dir.mkdir(parents=True)
        (canon_dir / "dragons.md").write_text(
            "# Dragons\n\nFire-breathers of the north.", encoding="utf-8",
        )
        resp = client.get("/v1/universes/test-universe/canon/dragons.md")
        assert resp.status_code == 200
        data = resp.json()
        assert data["filename"] == "dragons.md"
        assert "Fire-breathers" in data["content"]

    def test_missing_file(self, client, universe_dir):
        canon_dir = universe_dir / "canon"
        canon_dir.mkdir(parents=True)
        resp = client.get("/v1/universes/test-universe/canon/nonexistent.md")
        assert resp.status_code == 404

    def test_path_traversal_blocked(self, client, universe_dir):
        (universe_dir / "secret.txt").write_text("secret", encoding="utf-8")
        resp = client.get("/v1/universes/test-universe/canon/..%2Fsecret.txt")
        # 400 (our validation) or 404 (file not found after sanitization)
        assert resp.status_code in (400, 404)
        # Either way, the secret content must not leak
        if resp.status_code == 200:
            assert "secret" not in resp.json().get("content", "")

    def test_wrong_universe_rejected(self, client):
        resp = client.get("/v1/universes/wrong/canon/file.md")
        assert resp.status_code == 404

    def test_post_then_get_roundtrip(self, client):
        client.post(
            "/v1/universes/test-universe/canon",
            json={"filename": "magic.md", "content": "# Magic System\n\nRune-based."},
        )
        resp = client.get("/v1/universes/test-universe/canon/magic.md")
        assert resp.status_code == 200
        assert "Rune-based" in resp.json()["content"]

    def test_list_then_read_roundtrip(self, client, universe_dir):
        canon_dir = universe_dir / "canon"
        canon_dir.mkdir(parents=True)
        (canon_dir / "world.md").write_text("World info.", encoding="utf-8")
        (canon_dir / "chars.md").write_text("Character info.", encoding="utf-8")

        # List first
        resp = client.get("/v1/universes/test-universe/canon")
        files = resp.json()["files"]
        assert len(files) == 2

        # Read each one
        for f in files:
            resp = client.get(f"/v1/universes/test-universe/canon/{f['filename']}")
            assert resp.status_code == 200
            assert resp.json()["filename"] == f["filename"]
            assert len(resp.json()["content"]) > 0


# ---------------------------------------------------------------------------
# Daemon control
# ---------------------------------------------------------------------------


class TestDaemonControl:
    def test_pause_signals_daemon(self, client, tmp_path):
        mock_daemon = MagicMock()
        mock_daemon._universe_path = str(tmp_path / "test-universe")
        configure(
            base_path=str(tmp_path),
            daemon=mock_daemon,
        )
        resp = client.post("/v1/daemon/pause")
        assert resp.status_code == 200
        assert resp.json()["status"] == "paused"
        mock_daemon._paused.set.assert_called_once()

    def test_pause_reports_universe(self, client, tmp_path):
        mock_daemon = MagicMock()
        mock_daemon._universe_path = str(tmp_path / "test-universe")
        configure(base_path=str(tmp_path), daemon=mock_daemon)
        resp = client.post("/v1/daemon/pause")
        assert resp.json()["universe"] == "test-universe"

    def test_start_clears_daemon_pause(self, client, tmp_path):
        mock_daemon = MagicMock()
        mock_daemon._universe_path = str(tmp_path / "test-universe")
        configure(
            base_path=str(tmp_path),
            daemon=mock_daemon,
        )
        resp = client.post("/v1/daemon/start")
        assert resp.status_code == 200
        assert resp.json()["status"] == "running"
        mock_daemon._paused.clear.assert_called_once()

    def test_stop_signals_daemon(self, client, tmp_path):
        mock_daemon = MagicMock()
        mock_thread = MagicMock()
        mock_thread.is_alive.return_value = False
        configure(
            base_path=str(tmp_path),
            daemon=mock_daemon,
            daemon_thread=mock_thread,
        )
        resp = client.post("/v1/daemon/stop")
        assert resp.status_code == 200
        assert resp.json()["status"] == "stopping"
        mock_daemon._stop_event.set.assert_called_once()

    def test_unknown_action(self, client):
        resp = client.post("/v1/daemon/restart")
        assert resp.status_code == 400

    def test_start_without_body(self, client, tmp_path):
        """Start with no body and no daemon should return 400."""
        configure(base_path=str(tmp_path), daemon=None)
        resp = client.post("/v1/daemon/start")
        assert resp.status_code == 400

    def test_start_nonexistent_universe_rejected(self, client):
        resp = client.post(
            "/v1/daemon/start",
            json={"universe": "nonexistent"},
        )
        assert resp.status_code == 404


class TestDaemonSwitching:
    """Tests for switching the daemon between universes."""

    def test_start_with_universe_when_no_daemon(self, client, base_dir, monkeypatch):
        """Starting a universe when no daemon is running should start one."""
        # Retargeted to fantasy_daemon.api: the test reaches into private
        # globals (_daemon, _daemon_thread, _start_daemon_for). The
        # tinyassets.api shim (tinyassets/api/__init__.py) does a wildcard
        # re-export which does not forward writes to module-level names,
        # so ``api_mod._daemon = mock`` patches the shim's own binding
        # rather than the real module the running handlers read. Until
        # the engine/domain API split completes (docs/architecture.md
        # § Domains), tests that mutate private state must go to the
        # real module.
        from fantasy_daemon import api as api_mod

        # Track what _start_daemon_for receives
        started = []

        def fake_start(uid):
            mock = MagicMock()
            mock._universe_path = str(base_dir / uid)
            api_mod._daemon = mock
            api_mod._daemon_thread = MagicMock()
            started.append(uid)

        monkeypatch.setattr(api_mod, "_start_daemon_for", fake_start)
        configure(base_path=str(base_dir), daemon=None)

        resp = client.post(
            "/v1/daemon/start",
            json={"universe": "test-universe"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "running"
        assert resp.json()["universe"] == "test-universe"
        assert started == ["test-universe"]

    def test_switch_universe_stops_old_starts_new(self, client, base_dir, monkeypatch):
        """Switching universe should stop current daemon and start new one."""
        # See note on test_start_with_universe_when_no_daemon — retargeted to
        # the real module because the test mutates api_mod._daemon directly.
        from fantasy_daemon import api as api_mod

        # Create a second universe
        second = base_dir / "second-world"
        second.mkdir()
        meta = {"id": "second-world", "name": "Second", "created_at": "2026-04-01T00:00:00Z",
                "auto_name": False}
        (second / "universe.json").write_text(json.dumps(meta), encoding="utf-8")

        # Set up mock current daemon
        old_daemon = MagicMock()
        old_daemon._universe_path = str(base_dir / "test-universe")
        old_thread = MagicMock()
        old_thread.is_alive.return_value = False
        configure(
            base_path=str(base_dir),
            daemon=old_daemon,
            daemon_thread=old_thread,
        )

        # Track _start_daemon_for
        started = []

        def fake_start(uid):
            mock = MagicMock()
            mock._universe_path = str(base_dir / uid)
            api_mod._daemon = mock
            api_mod._daemon_thread = MagicMock()
            started.append(uid)

        monkeypatch.setattr(api_mod, "_start_daemon_for", fake_start)

        resp = client.post(
            "/v1/daemon/start",
            json={"universe": "second-world"},
        )
        assert resp.status_code == 200
        assert resp.json()["universe"] == "second-world"
        # Old daemon was stopped
        old_daemon._stop_event.set.assert_called_once()
        # New daemon was started
        assert started == ["second-world"]

    def test_start_same_universe_no_switch(self, client, base_dir):
        """Starting the same universe should not restart the daemon."""
        mock_daemon = MagicMock()
        mock_daemon._universe_path = str(base_dir / "test-universe")
        configure(base_path=str(base_dir), daemon=mock_daemon)

        resp = client.post(
            "/v1/daemon/start",
            json={"universe": "test-universe"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "running"
        # Should NOT have called _stop_event.set -- just resumed
        mock_daemon._stop_event.set.assert_not_called()
        mock_daemon._paused.clear.assert_called()

    def test_health_reports_active_universe(self, client, tmp_path):
        mock_daemon = MagicMock()
        mock_daemon.daemon_state = "running"
        mock_daemon._universe_path = str(tmp_path / "my-saga")
        configure(base_path=str(tmp_path), daemon=mock_daemon)

        resp = client.get("/v1/health")
        data = resp.json()
        assert data["active_universe"] == "my-saga"
        assert data["daemon"] == "running"

    def test_health_no_active_when_no_daemon(self, client):
        resp = client.get("/v1/health")
        data = resp.json()
        assert "active_universe" not in data


# ---------------------------------------------------------------------------
# Path traversal (is_relative_to)
# ---------------------------------------------------------------------------


class TestPathTraversalStrict:
    def test_output_prefix_attack(self, client, universe_dir):
        evil_dir = universe_dir / "output_evil"
        evil_dir.mkdir()
        (evil_dir / "data.md").write_text("stolen", encoding="utf-8")
        resp = client.get("/v1/universes/test-universe/output/../output_evil/data.md")
        assert resp.status_code in (400, 404)

    def test_universe_id_traversal_blocked(self, client):
        """Path traversal in universe ID must be rejected before any filesystem op."""
        for evil_uid in ["../../../Windows", "..%2F..%2Fetc", "foo/../../bar"]:
            resp = client.get(f"/v1/universes/{evil_uid}/status")
            assert resp.status_code in (400, 404, 422), f"traversal not blocked for {evil_uid}"

    def test_universe_delete_traversal_blocked(self, client):
        """DELETE with traversal uid must not reach shutil.rmtree."""
        resp = client.delete("/v1/universes/../../important-data")
        assert resp.status_code in (400, 404, 422)


# ---------------------------------------------------------------------------
# Unconfigured API
# ---------------------------------------------------------------------------


class TestUnconfigured:
    def test_base_raises_when_unconfigured(self, client):
        configure(base_path="", api_key="", daemon=None)
        resp = client.get("/v1/universes")
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Activity lines bounds
# ---------------------------------------------------------------------------


class TestActivityBounds:
    def test_lines_too_high_rejected(self, client):
        resp = client.get("/v1/universes/test-universe/activity?lines=9999")
        assert resp.status_code == 422

    def test_lines_zero_rejected(self, client):
        resp = client.get("/v1/universes/test-universe/activity?lines=0")
        assert resp.status_code == 422

    def test_lines_negative_rejected(self, client):
        resp = client.get("/v1/universes/test-universe/activity?lines=-1")
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Daemon _ready event
# ---------------------------------------------------------------------------


class TestDaemonReady:
    def test_daemon_state_initializing(self):
        from tinyassets.__main__ import DaemonController

        controller = DaemonController(universe_path="/tmp/test")
        assert not controller._ready.is_set()
        assert controller.daemon_state == "initializing"

        controller._ready.set()
        assert controller.daemon_state == "running"

    def test_daemon_state_paused_after_ready(self):
        from tinyassets.__main__ import DaemonController

        controller = DaemonController(universe_path="/tmp/test")
        controller._ready.set()
        controller._paused.set()
        assert controller.daemon_state == "paused"

    def test_health_reports_initializing(self, client, tmp_path):
        mock_daemon = MagicMock()
        mock_daemon.daemon_state = "initializing"
        configure(
            base_path=str(tmp_path),
            daemon=mock_daemon,
        )
        resp = client.get("/v1/health")
        assert resp.json()["daemon"] == "initializing"


# ---------------------------------------------------------------------------
# Output writing
# ---------------------------------------------------------------------------


class TestPutOutput:
    """Output editing is locked down — all PUT /output/ requests return 403."""

    def test_put_blocked(self, client, universe_dir):
        resp = client.put(
            "/v1/universes/test-universe/output/book-1/chapter-01.md",
            json={"content": "The story begins here."},
        )
        assert resp.status_code == 403
        assert not (universe_dir / "output" / "book-1" / "chapter-01.md").exists()

    def test_no_parent_dirs_created(self, client, universe_dir):
        assert not (universe_dir / "output" / "book-3").exists()
        resp = client.put(
            "/v1/universes/test-universe/output/book-3/chapter-02.md",
            json={"content": "Chapter content."},
        )
        assert resp.status_code == 403
        assert not (universe_dir / "output" / "book-3").exists()

    def test_existing_file_not_overwritten(self, client, universe_dir):
        output_dir = universe_dir / "output"
        output_dir.mkdir(parents=True)
        (output_dir / "scene.md").write_text("Old scene.", encoding="utf-8")

        resp = client.put(
            "/v1/universes/test-universe/output/scene.md",
            json={"content": "Rewritten scene."},
        )
        assert resp.status_code == 403
        content = (output_dir / "scene.md").read_text(encoding="utf-8")
        assert "Old scene." in content

    def test_path_traversal_blocked(self, client, universe_dir):
        resp = client.put(
            "/v1/universes/test-universe/output/../../evil.md",
            json={"content": "Malicious"},
        )
        assert resp.status_code in (400, 403, 404, 405)
        assert not (universe_dir / "evil.md").exists()

    def test_empty_content_blocked(self, client):
        resp = client.put(
            "/v1/universes/test-universe/output/test.md",
            json={"content": ""},
        )
        assert resp.status_code in (403, 422)

    def test_wrong_universe_blocked(self, client):
        resp = client.put(
            "/v1/universes/wrong/output/test.md",
            json={"content": "Content."},
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Serve CLI flag
# ---------------------------------------------------------------------------


class TestServeFlag:
    def test_serve_arg_exists(self):
        from tinyassets.__main__ import main

        assert callable(main)

    def test_api_module_importable(self):
        from fantasy_daemon.api import app as api_app

        assert api_app is not None


# ---------------------------------------------------------------------------
# GPT edge-case stress tests
# ---------------------------------------------------------------------------


class TestEdgeCaseUniverseCreation:
    """Edge cases a GPT might trigger when creating universes."""

    def test_create_universe_no_name(self, client, base_dir):
        """Empty body should auto-generate a name."""
        resp = client.post("/v1/universes", json={})
        assert resp.status_code == 201
        data = resp.json()
        assert data["id"]
        assert data["name"]
        assert data["has_soul"] is True
        assert (base_dir / data["id"] / "soul.md").exists()

    def test_create_universe_declares_loop_branch_in_soul(self, client, base_dir):
        resp = client.post(
            "/v1/universes",
            json={"name": "TinyAssets Lab", "branch_def_id": "workflow:review_loop"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["soul"]["loop_branch_def_id"] == "workflow:review_loop"
        soul_md = (base_dir / data["id"] / "soul.md").read_text(encoding="utf-8")
        assert "- Loop branch: workflow:review_loop" in soul_md

    def test_create_universe_empty_name(self, client):
        """Explicit empty string name should auto-generate."""
        resp = client.post("/v1/universes", json={"name": ""})
        assert resp.status_code == 201
        data = resp.json()
        assert data["id"]
        assert data["name"]

    def test_create_universe_special_characters(self, client):
        """Name with special characters should be slugified safely."""
        resp = client.post(
            "/v1/universes",
            json={"name": "The Dragon's Lair: A Tale of Fire & Ice!"},
        )
        assert resp.status_code == 201
        data = resp.json()
        # Slug should be URL-safe (no apostrophes, colons, etc.)
        assert "'" not in data["id"]
        assert ":" not in data["id"]
        assert " " not in data["id"]

    def test_create_universe_very_long_name(self, client):
        """Very long name should be truncated, not crash."""
        long_name = "A" * 500
        resp = client.post("/v1/universes", json={"name": long_name})
        assert resp.status_code == 201
        data = resp.json()
        assert len(data["id"]) <= 80  # _slugify truncates to 80

    def test_create_universe_unicode_name(self, client):
        """Unicode name should be handled gracefully."""
        resp = client.post(
            "/v1/universes",
            json={"name": "Throne of Stars"},
        )
        assert resp.status_code == 201

    def test_create_duplicate_universe(self, client):
        """Creating the same universe twice should return 409."""
        client.post("/v1/universes", json={"name": "duplicate-test"})
        resp = client.post("/v1/universes", json={"name": "duplicate-test"})
        assert resp.status_code == 409

    def test_create_universe_no_body(self, client):
        """POST with no body at all should still work."""
        resp = client.post("/v1/universes")
        assert resp.status_code == 201


class TestEdgeCaseCanon:
    """Edge cases for canon file uploads."""

    def test_canon_empty_content_rejected(self, client):
        """Empty content should be rejected by validation."""
        resp = client.post(
            "/v1/universes/test-universe/canon",
            json={"filename": "empty.md", "content": ""},
        )
        assert resp.status_code == 422

    def test_canon_large_content(self, client):
        """Large content (100KB) should not crash."""
        large = "The ancient texts speak of dragons.\n" * 3000
        resp = client.post(
            "/v1/universes/test-universe/canon",
            json={"filename": "tome.md", "content": large},
        )
        assert resp.status_code == 201

    def test_canon_path_traversal_filename(self, client):
        """Path traversal in filename should be sanitized."""
        resp = client.post(
            "/v1/universes/test-universe/canon",
            json={"filename": "../../etc/passwd", "content": "evil"},
        )
        assert resp.status_code == 201
        # Should use just the basename
        assert resp.json()["filename"] == "passwd"

    def test_canon_dot_filename(self, client):
        """Filename of '.' should be rejected."""
        resp = client.post(
            "/v1/universes/test-universe/canon",
            json={"filename": ".", "content": "evil"},
        )
        assert resp.status_code == 400

    def test_canon_dotdot_filename(self, client):
        """Filename of '..' should be rejected."""
        resp = client.post(
            "/v1/universes/test-universe/canon",
            json={"filename": "..", "content": "evil"},
        )
        assert resp.status_code == 400

    def test_canon_nonexistent_universe(self, client):
        """Canon on a non-existent universe should return 404."""
        resp = client.post(
            "/v1/universes/no-such-universe/canon",
            json={"filename": "test.md", "content": "text"},
        )
        assert resp.status_code == 404


class TestCanonUpload:
    """Tests for POST /v1/universes/{uid}/canon/upload (openaiFileIdRefs)."""

    def _mock_httpx_response(self, content: bytes, status_code: int = 200):
        """Build a fake httpx.Response."""
        resp = MagicMock()
        resp.content = content
        resp.status_code = status_code
        resp.raise_for_status = MagicMock()
        if status_code >= 400:
            import httpx
            resp.raise_for_status.side_effect = httpx.HTTPStatusError(
                "error", request=MagicMock(), response=resp,
            )
        return resp

    def _patch_httpx(self, responses: list):
        """Patch httpx.AsyncClient to return canned responses in order."""
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=responses)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        return patch("httpx.AsyncClient", return_value=mock_client)

    def test_single_file_byte_for_byte(self, client, universe_dir):
        """A single file upload should land in canon/ byte-for-byte."""
        content = b"The dragon circled the mountain, scales glinting like obsidian."
        resp_mock = self._mock_httpx_response(content)

        with self._patch_httpx([resp_mock]):
            resp = client.post(
                "/v1/universes/test-universe/canon/upload",
                json={
                    "openaiFileIdRefs": [{
                        "name": "dragon_lore.md",
                        "download_link": "https://files.example.com/abc123",
                    }],
                },
            )

        assert resp.status_code == 201
        data = resp.json()
        assert data["count"] == 1
        assert data["files"][0]["status"] == "ok"
        assert data["files"][0]["filename"] == "dragon_lore.md"
        assert data["files"][0]["byte_count"] == len(content)
        assert data["files"][0]["sha256"] == hashlib.sha256(content).hexdigest()

        # Verify the file landed in sources/ (user uploads go to sources/)
        source_file = universe_dir / "canon" / "sources" / "dragon_lore.md"
        assert source_file.exists()
        assert source_file.read_bytes() == content

    def test_multiple_files(self, client, universe_dir):
        """Multiple files should all land in canon/."""
        file_a = b"Chapter 1: The Beginning"
        file_b = b"Map data: coordinates and terrain"

        with self._patch_httpx([
            self._mock_httpx_response(file_a),
            self._mock_httpx_response(file_b),
        ]):
            resp = client.post(
                "/v1/universes/test-universe/canon/upload",
                json={
                    "openaiFileIdRefs": [
                        {"name": "chapter1.md", "download_link": "https://example.com/a"},
                        {"name": "map.txt", "download_link": "https://example.com/b"},
                    ],
                },
            )

        assert resp.status_code == 201
        data = resp.json()
        assert data["count"] == 2
        assert all(f["status"] == "ok" for f in data["files"])

        assert (universe_dir / "canon" / "sources" / "chapter1.md").read_bytes() == file_a
        assert (universe_dir / "canon" / "sources" / "map.txt").read_bytes() == file_b

    def test_binary_content_preserved(self, client, universe_dir):
        """Binary content (e.g., an image) should be preserved byte-for-byte."""
        binary = bytes(range(256)) * 10  # 2560 bytes of all byte values

        with self._patch_httpx([self._mock_httpx_response(binary)]):
            resp = client.post(
                "/v1/universes/test-universe/canon/upload",
                json={
                    "openaiFileIdRefs": [{
                        "name": "map.png",
                        "download_link": "https://example.com/img",
                    }],
                },
            )

        assert resp.status_code == 201
        assert (universe_dir / "canon" / "sources" / "map.png").read_bytes() == binary

    def test_reviewed_marker_created(self, client, universe_dir):
        """Upload should create a .filename.reviewed marker for user-tier provenance."""
        with self._patch_httpx([self._mock_httpx_response(b"content")]):
            client.post(
                "/v1/universes/test-universe/canon/upload",
                json={
                    "openaiFileIdRefs": [{
                        "name": "lore.md",
                        "download_link": "https://example.com/x",
                    }],
                },
            )

        marker = universe_dir / "canon" / "sources" / ".lore.md.reviewed"
        assert marker.exists()
        meta = json.loads(marker.read_text(encoding="utf-8"))
        assert meta["model"] == "user"
        assert "reviewed_at" in meta

    def test_no_file_refs_returns_400(self, client):
        """Missing openaiFileIdRefs should return 400."""
        resp = client.post(
            "/v1/universes/test-universe/canon/upload",
            json={},
        )
        assert resp.status_code == 400

    def test_empty_file_refs_returns_400(self, client):
        """Empty openaiFileIdRefs list should return 400."""
        resp = client.post(
            "/v1/universes/test-universe/canon/upload",
            json={"openaiFileIdRefs": []},
        )
        assert resp.status_code == 400

    def test_missing_download_link(self, client):
        """A ref with no download_link should report error but not crash."""
        resp = client.post(
            "/v1/universes/test-universe/canon/upload",
            json={
                "openaiFileIdRefs": [{"name": "orphan.md"}],
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["files"][0]["status"] == "error"
        assert "no download_link" in data["files"][0]["detail"]

    def test_download_failure_reported(self, client, universe_dir):
        """HTTP error on download should be reported, not crash the endpoint."""
        with self._patch_httpx([self._mock_httpx_response(b"", status_code=404)]):
            resp = client.post(
                "/v1/universes/test-universe/canon/upload",
                json={
                    "openaiFileIdRefs": [{
                        "name": "gone.md",
                        "download_link": "https://example.com/404",
                    }],
                },
            )

        assert resp.status_code == 201
        assert resp.json()["files"][0]["status"] == "error"
        # File should NOT have been written
        assert not (universe_dir / "canon" / "gone.md").exists()

    def test_path_traversal_in_filename(self, client, universe_dir):
        """Path traversal attempts in filenames should be sanitized."""
        with self._patch_httpx([self._mock_httpx_response(b"safe content")]):
            resp = client.post(
                "/v1/universes/test-universe/canon/upload",
                json={
                    "openaiFileIdRefs": [{
                        "name": "../../etc/passwd",
                        "download_link": "https://example.com/x",
                    }],
                },
            )

        assert resp.status_code == 201
        assert resp.json()["files"][0]["filename"] == "passwd"
        # Should land in sources/, not escape
        assert (universe_dir / "canon" / "sources" / "passwd").exists()

    def test_dot_filename_gets_default(self, client, universe_dir):
        """Filename of '.' should fall back to 'unnamed.md'."""
        with self._patch_httpx([self._mock_httpx_response(b"data")]):
            resp = client.post(
                "/v1/universes/test-universe/canon/upload",
                json={
                    "openaiFileIdRefs": [{
                        "name": ".",
                        "download_link": "https://example.com/x",
                    }],
                },
            )

        assert resp.status_code == 201
        assert resp.json()["files"][0]["filename"] == "unnamed.md"

    def test_nonexistent_universe_returns_404(self, client):
        """Upload to a non-existent universe should return 404."""
        resp = client.post(
            "/v1/universes/no-such-universe/canon/upload",
            json={
                "openaiFileIdRefs": [{
                    "name": "test.md",
                    "download_link": "https://example.com/x",
                }],
            },
        )
        assert resp.status_code == 404

    def test_partial_success(self, client, universe_dir):
        """Mix of good and bad refs: good files land, bad ones report errors."""
        good_content = b"The treaty was signed at dawn."

        with self._patch_httpx([
            self._mock_httpx_response(good_content),
            self._mock_httpx_response(b"", status_code=500),
        ]):
            resp = client.post(
                "/v1/universes/test-universe/canon/upload",
                json={
                    "openaiFileIdRefs": [
                        {"name": "treaty.md", "download_link": "https://example.com/a"},
                        {"name": "broken.md", "download_link": "https://example.com/b"},
                    ],
                },
            )

        assert resp.status_code == 201
        files = resp.json()["files"]
        assert files[0]["status"] == "ok"
        assert files[1]["status"] == "error"
        assert (universe_dir / "canon" / "sources" / "treaty.md").read_bytes() == good_content
        assert not (universe_dir / "canon" / "sources" / "broken.md").exists()


class TestCanonBatchUpload:
    """Tests for POST /v1/universes/{uid}/canon/batch (base64 batch)."""

    def test_single_file_small(self, client, universe_dir):
        """Small user upload should land in sources/ (provenance-based routing)."""
        import base64

        content = b"The dragon lore is ancient."
        b64 = base64.b64encode(content).decode()

        resp = client.post(
            "/v1/universes/test-universe/canon/batch",
            json={"files": [{"filename": "lore.md", "content": b64}]},
        )

        assert resp.status_code == 201
        data = resp.json()
        assert data["count"] == 1
        assert data["files"][0]["status"] == "ok"
        assert data["files"][0]["file_type"] == "text"
        assert data["files"][0]["routed_to"] == "sources"
        assert (universe_dir / "canon" / "sources" / "lore.md").read_bytes() == content

    def test_large_file_routes_to_sources(self, client, universe_dir):
        """Files >5KB should route to canon/sources/."""
        import base64

        content = b"x" * 6000  # Exceeds SIZE_THRESHOLD
        b64 = base64.b64encode(content).decode()

        resp = client.post(
            "/v1/universes/test-universe/canon/batch",
            json={"files": [{"filename": "big.txt", "content": b64}]},
        )

        assert resp.status_code == 201
        data = resp.json()["files"][0]
        assert data["routed_to"] == "sources"
        assert data["signal_emitted"] is True
        assert (universe_dir / "canon" / "sources" / "big.txt").exists()

    def test_multiple_files_mixed_types(self, client, universe_dir):
        """Multiple files of different types in one batch."""
        import base64

        md_content = b"# Characters\n\nElara the mage."
        png_header = b"\x89PNG\r\n\x1a\n" + b"\x00" * 50

        resp = client.post(
            "/v1/universes/test-universe/canon/batch",
            json={
                "files": [
                    {
                        "filename": "chars.md",
                        "content": base64.b64encode(md_content).decode(),
                    },
                    {
                        "filename": "map.png",
                        "content": base64.b64encode(png_header).decode(),
                    },
                ],
            },
        )

        assert resp.status_code == 201
        data = resp.json()
        assert data["count"] == 2
        assert data["files"][0]["file_type"] == "text"
        assert data["files"][1]["file_type"] == "image"

    def test_empty_files_rejected(self, client):
        """Empty files array should return 400."""
        resp = client.post(
            "/v1/universes/test-universe/canon/batch",
            json={"files": []},
        )
        assert resp.status_code == 400

    def test_missing_content_reports_error(self, client):
        """A file entry with no content should report error."""
        resp = client.post(
            "/v1/universes/test-universe/canon/batch",
            json={"files": [{"filename": "empty.md"}]},
        )
        assert resp.status_code == 201
        assert resp.json()["files"][0]["status"] == "error"
        assert "no content" in resp.json()["files"][0]["detail"]

    def test_invalid_base64_reports_error(self, client):
        """Invalid base64 should report error, not crash."""
        resp = client.post(
            "/v1/universes/test-universe/canon/batch",
            json={
                "files": [
                    {"filename": "bad.md", "content": "!!!not-base64!!!"},
                ],
            },
        )
        assert resp.status_code == 201
        assert resp.json()["files"][0]["status"] == "error"
        assert "base64" in resp.json()["files"][0]["detail"]

    def test_user_provenance_marker_created(self, client, universe_dir):
        """Batch upload should create user-tier provenance markers."""
        import base64

        content = b"Treaty details here."
        b64 = base64.b64encode(content).decode()

        client.post(
            "/v1/universes/test-universe/canon/batch",
            json={"files": [{"filename": "treaty.md", "content": b64}]},
        )

        marker = universe_dir / "canon" / "sources" / ".treaty.md.reviewed"
        assert marker.exists()
        meta = json.loads(marker.read_text(encoding="utf-8"))
        assert meta["model"] == "user"

    def test_upload_includes_ingestion_metadata(self, client, universe_dir):
        """Upload response should include file_type and sha256."""
        import base64

        content = b"Some text content"
        b64 = base64.b64encode(content).decode()

        resp = client.post(
            "/v1/universes/test-universe/canon/batch",
            json={"files": [{"filename": "notes.txt", "content": b64}]},
        )

        data = resp.json()["files"][0]
        assert "file_type" in data
        assert "sha256" in data
        assert "byte_count" in data
        assert data["sha256"] == hashlib.sha256(content).hexdigest()


class TestUploadIngestionPipeline:
    """Verify upload endpoint integrates with ingestion pipeline."""

    def _mock_httpx_response(self, content: bytes, status_code: int = 200):
        resp = MagicMock()
        resp.content = content
        resp.status_code = status_code
        resp.raise_for_status = MagicMock()
        if status_code >= 400:
            import httpx
            resp.raise_for_status.side_effect = httpx.HTTPStatusError(
                "error", request=MagicMock(), response=resp,
            )
        return resp

    def _patch_httpx(self, responses: list):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=responses)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        return patch("httpx.AsyncClient", return_value=mock_client)

    def test_upload_returns_file_type(self, client, universe_dir):
        """Upload response should now include file_type from ingestion."""
        content = b"# Magic System\n\nThe Lattice connects all."

        with self._patch_httpx([self._mock_httpx_response(content)]):
            resp = client.post(
                "/v1/universes/test-universe/canon/upload",
                json={
                    "openaiFileIdRefs": [{
                        "name": "magic.md",
                        "download_link": "https://example.com/x",
                    }],
                },
            )

        data = resp.json()["files"][0]
        assert data["file_type"] == "text"
        assert data["routed_to"] == "sources"

    def test_upload_large_file_routes_to_sources(self, client, universe_dir):
        """Large files uploaded via openaiFileIdRefs go to sources/."""
        content = b"x" * 6000  # Exceeds SIZE_THRESHOLD

        with self._patch_httpx([self._mock_httpx_response(content)]):
            resp = client.post(
                "/v1/universes/test-universe/canon/upload",
                json={
                    "openaiFileIdRefs": [{
                        "name": "big_doc.txt",
                        "download_link": "https://example.com/big",
                    }],
                },
            )

        data = resp.json()["files"][0]
        assert data["routed_to"] == "sources"
        assert data["signal_emitted"] is True
        assert (
            universe_dir / "canon" / "sources" / "big_doc.txt"
        ).exists()


class TestEdgeCaseDataEndpoints:
    """Edge cases for facts, characters, promises on empty/missing data."""

    def test_facts_no_db(self, client):
        """Facts on a universe with no DB should return empty, not crash."""
        resp = client.get("/v1/universes/test-universe/facts")
        assert resp.status_code == 200
        assert resp.json()["facts"] == []
        assert resp.json()["count"] == 0

    def test_characters_no_db(self, client):
        """Characters on a universe with no DB should return empty."""
        resp = client.get("/v1/universes/test-universe/characters")
        assert resp.status_code == 200
        assert resp.json()["characters"] == []

    def test_promises_no_db(self, client):
        """Promises on a universe with no DB should return empty."""
        resp = client.get("/v1/universes/test-universe/promises")
        assert resp.status_code == 200
        assert resp.json()["promises"] == []

    def test_facts_nonexistent_universe(self, client):
        """Facts on a non-existent universe should return 404."""
        resp = client.get("/v1/universes/no-such-universe/facts")
        assert resp.status_code == 404

    def test_characters_nonexistent_universe(self, client):
        """Characters on a non-existent universe should return 404."""
        resp = client.get("/v1/universes/no-such-universe/characters")
        assert resp.status_code == 404

    def test_promises_nonexistent_universe(self, client):
        """Promises on a non-existent universe should return 404."""
        resp = client.get("/v1/universes/no-such-universe/promises")
        assert resp.status_code == 404


class TestEdgeCaseOverview:
    """Edge cases for the overview composite endpoint."""

    def test_overview_empty_universe(self, client):
        """Overview on a universe with no content should return defaults."""
        resp = client.get("/v1/universes/test-universe/overview")
        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data
        assert "progress" in data
        assert "output_files" in data
        assert isinstance(data["output_files"], list)
        assert data["status"]["daemon_state"] == "idle"

    def test_overview_nonexistent_universe(self, client):
        """Overview on a non-existent universe should return 404."""
        resp = client.get("/v1/universes/no-such-universe/overview")
        assert resp.status_code == 404


class TestEdgeCaseDaemon:
    """Edge cases for daemon control the GPT might trigger."""

    def test_stop_when_not_running(self, client, tmp_path):
        """Stopping when no daemon is running should not crash."""
        configure(base_path=str(tmp_path), daemon=None)
        resp = client.post("/v1/daemon/stop")
        # Should handle gracefully — either 200 or 400, not 500
        assert resp.status_code < 500

    def test_start_with_empty_universe_string(self, client, tmp_path):
        """Starting with empty universe string should not crash."""
        configure(base_path=str(tmp_path), daemon=None)
        resp = client.post("/v1/daemon/start", json={"universe": ""})
        assert resp.status_code < 500

    def test_start_nonexistent_universe(self, client, tmp_path):
        """Starting a non-existent universe should return 404."""
        configure(base_path=str(tmp_path), daemon=None)
        resp = client.post(
            "/v1/daemon/start", json={"universe": "does-not-exist"},
        )
        assert resp.status_code == 404

    def test_pause_when_not_running(self, client, tmp_path):
        """Pausing when no daemon is running should not crash."""
        configure(base_path=str(tmp_path), daemon=None)
        resp = client.post("/v1/daemon/pause")
        assert resp.status_code < 500


class TestEdgeCasePremise:
    """Edge cases for premise endpoints."""

    def test_get_premise_when_none_set(self, client):
        """Getting premise before it's set should return 404."""
        resp = client.get("/v1/universes/test-universe/premise")
        assert resp.status_code == 404

    def test_set_empty_premise(self, client):
        """Setting an empty premise should be handled."""
        resp = client.post(
            "/v1/universes/test-universe/premise",
            json={"text": ""},
        )
        # Should either succeed or return 400, not 500
        assert resp.status_code < 500

    def test_set_very_long_premise(self, client):
        """Setting a very long premise should not crash."""
        long_premise = "In a world where " + "magic flows through everything " * 500
        resp = client.post(
            "/v1/universes/test-universe/premise",
            json={"text": long_premise},
        )
        assert resp.status_code == 200


# =====================================================================
# Status is_active field
# =====================================================================


class TestStatusIsActive:
    def test_status_returns_is_active_false_when_no_daemon(self, client):
        """Status should include is_active=False when no daemon is running."""
        resp = client.get("/v1/universes/test-universe/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_active"] is False
        assert data["daemon_state"] == "idle"

    def test_status_idle_when_daemon_on_different_universe(self, client, tmp_path):
        """Status should show idle when daemon is on a different universe."""
        # Retargeted to fantasy_daemon.api — test mutates api_mod._daemon and
        # the handler reads from the real module; the shim's wildcard
        # re-export doesn't forward writes to module-level names.
        import fantasy_daemon.api as api_mod

        # Write a status.json that claims the daemon is running
        uni_dir = tmp_path / "test-universe"
        status = {
            "daemon_state": "running",
            "current_phase": "draft",
            "word_count": 5000,
        }
        (uni_dir / "status.json").write_text(
            json.dumps(status), encoding="utf-8",
        )

        # Mock daemon on a different universe
        mock_daemon = MagicMock()
        mock_daemon._universe_id = "water-world"
        old_daemon = api_mod._daemon
        api_mod._daemon = mock_daemon
        try:
            resp = client.get("/v1/universes/test-universe/status")
            data = resp.json()
            assert data["is_active"] is False
            assert data["daemon_state"] == "idle"
            assert data["word_count"] == 5000  # data preserved
        finally:
            api_mod._daemon = old_daemon

    def test_status_active_when_daemon_on_same_universe(self, client, tmp_path):
        """Status should show is_active=True when daemon matches."""
        # See note on test_status_idle_when_daemon_on_different_universe.
        import fantasy_daemon.api as api_mod

        uni_dir = tmp_path / "test-universe"
        status = {
            "daemon_state": "running",
            "current_phase": "draft",
            "word_count": 5000,
        }
        (uni_dir / "status.json").write_text(
            json.dumps(status), encoding="utf-8",
        )

        mock_daemon = MagicMock()
        mock_daemon._universe_id = "test-universe"
        old_daemon = api_mod._daemon
        api_mod._daemon = mock_daemon
        try:
            resp = client.get("/v1/universes/test-universe/status")
            data = resp.json()
            assert data["is_active"] is True
            assert data["daemon_state"] == "running"
        finally:
            api_mod._daemon = old_daemon

    def test_overview_includes_is_active(self, client):
        """Overview readiness should include is_active field."""
        resp = client.get("/v1/universes/test-universe/overview")
        assert resp.status_code == 200
        data = resp.json()
        assert "is_active" in data["readiness"]
        assert "is_active" in data["status"]
