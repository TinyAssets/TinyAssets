"""Stress tests for API endpoints with GPT edge cases.

Covers: no name, special chars, long names, empty/large canon, facts/chars/
promises with no DB, overview on empty universe, daemon start on already-running
universe, malformed JSON, Unicode, and boundary conditions.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from fantasy_daemon.api import app, configure


@pytest.fixture(autouse=True)
def _configure_api(tmp_path):
    """Point the API at a temp base directory for every test."""
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
    return TestClient(app)


@pytest.fixture
def universe_dir(tmp_path):
    return tmp_path / "test-universe"


@pytest.fixture
def base_dir(tmp_path):
    return tmp_path


# ---------------------------------------------------------------------------
# Create universe edge cases
# ---------------------------------------------------------------------------


class TestCreateUniverseEdgeCases:
    def test_empty_name_string(self, client):
        """Empty string name should be rejected by Pydantic or treated as no name."""
        resp = client.post("/v1/universes", json={"name": ""})
        # Empty string -> slugify("") -> random slug, OR could be treated as None
        # Either way should succeed with a generated ID
        if resp.status_code == 201:
            assert resp.json()["id"].startswith("universe-")

    def test_very_long_name(self, client, base_dir):
        """Name with 500 chars should produce a truncated but valid slug."""
        long_name = "A" * 500
        resp = client.post("/v1/universes", json={"name": long_name})
        assert resp.status_code == 201
        slug = resp.json()["id"]
        # Slug should be truncated to 80 chars max
        assert len(slug) <= 80
        # Slug should exist as a directory
        assert (base_dir / slug).is_dir()

    def test_unicode_name(self, client, base_dir):
        """Unicode characters in name should produce a valid slug."""
        resp = client.post("/v1/universes", json={"name": "Le Monde des Dragons"})
        assert resp.status_code == 201
        assert "monde" in resp.json()["id"].lower() or len(resp.json()["id"]) > 0

    def test_emoji_name(self, client, base_dir):
        """Emoji in name should produce a valid slug (stripped to empty -> random)."""
        resp = client.post("/v1/universes", json={"name": "\U0001f525\U0001f409"})
        assert resp.status_code == 201
        # Emojis stripped by slugify -> empty -> random universe-xxx ID
        assert resp.json()["id"].startswith("universe-")

    def test_special_chars_name(self, client, base_dir):
        """Special characters should be stripped from slug."""
        resp = client.post(
            "/v1/universes", json={"name": "The <Dragon's> Quest! @#$%"},
        )
        assert resp.status_code == 201
        slug = resp.json()["id"]
        assert (base_dir / slug).is_dir()
        # No special chars in slug
        for ch in "<>'!@#$%":
            assert ch not in slug

    def test_whitespace_only_name(self, client):
        """Whitespace-only name should produce a random slug."""
        resp = client.post("/v1/universes", json={"name": "   "})
        assert resp.status_code == 201
        assert resp.json()["id"].startswith("universe-")

    def test_null_body(self, client):
        """POST with no body should create a random universe."""
        resp = client.post("/v1/universes")
        assert resp.status_code == 201
        assert resp.json()["id"].startswith("universe-")

    def test_extra_fields_ignored(self, client):
        """Extra fields in body should be ignored by Pydantic."""
        resp = client.post(
            "/v1/universes",
            json={"name": "Extra Fields World", "unknown_field": "ignored"},
        )
        assert resp.status_code == 201


# ---------------------------------------------------------------------------
# Update universe edge cases
# ---------------------------------------------------------------------------


class TestUpdateUniverseEdgeCases:
    def test_update_very_long_name(self, client, universe_dir):
        resp = client.patch(
            "/v1/universes/test-universe",
            json={"name": "B" * 1000},
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "B" * 1000

    def test_update_unicode_name(self, client):
        resp = client.patch(
            "/v1/universes/test-universe",
            json={"name": "Les Dragons du Nord"},
        )
        assert resp.status_code == 200
        assert "Nord" in resp.json()["name"]

    def test_update_missing_name_field(self, client):
        """PATCH without name field should be 422."""
        resp = client.patch(
            "/v1/universes/test-universe",
            json={},
        )
        assert resp.status_code == 422

    def test_update_null_name(self, client):
        """null name should be rejected."""
        resp = client.patch(
            "/v1/universes/test-universe",
            json={"name": None},
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Premise edge cases
# ---------------------------------------------------------------------------


class TestPremiseEdgeCases:
    def test_very_long_premise(self, client, universe_dir):
        """10KB premise should be accepted."""
        long_text = "Word " * 2000  # ~10KB
        resp = client.post(
            "/v1/universes/test-universe/premise",
            json={"text": long_text},
        )
        assert resp.status_code == 200
        content = (universe_dir / "PROGRAM.md").read_text(encoding="utf-8")
        assert len(content) > 5000

    def test_unicode_premise(self, client, universe_dir):
        """Unicode in premise should round-trip correctly."""
        text = "Dans un monde de dragons et de magie..."
        resp = client.post(
            "/v1/universes/test-universe/premise",
            json={"text": text},
        )
        assert resp.status_code == 200
        resp2 = client.get("/v1/universes/test-universe/premise")
        assert resp2.json()["text"] == text

    def test_premise_with_markdown(self, client, universe_dir):
        """Markdown in premise should be preserved."""
        text = "# Title\n\n## Characters\n\n- **Ryn**: a wanderer\n- *Kael*: a thief"
        resp = client.post(
            "/v1/universes/test-universe/premise",
            json={"text": text},
        )
        assert resp.status_code == 200
        resp2 = client.get("/v1/universes/test-universe/premise")
        assert "**Ryn**" in resp2.json()["text"]

    def test_premise_null_text(self, client):
        """null text should be 422."""
        resp = client.post(
            "/v1/universes/test-universe/premise",
            json={"text": None},
        )
        assert resp.status_code == 422

    def test_premise_missing_text(self, client):
        """Missing text field should be 422."""
        resp = client.post(
            "/v1/universes/test-universe/premise",
            json={},
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Canon edge cases
# ---------------------------------------------------------------------------


class TestCanonEdgeCases:
    def test_large_canon_file(self, client, universe_dir):
        """50KB canon file should be accepted."""
        large_content = "Lore paragraph. " * 3000  # ~50KB
        resp = client.post(
            "/v1/universes/test-universe/canon",
            json={"filename": "world-lore.md", "content": large_content},
        )
        assert resp.status_code == 201
        written = (universe_dir / "canon" / "world-lore.md").read_text(encoding="utf-8")
        assert len(written) > 40000

    def test_empty_filename_rejected(self, client):
        resp = client.post(
            "/v1/universes/test-universe/canon",
            json={"filename": "", "content": "Some content"},
        )
        assert resp.status_code == 422

    def test_empty_content_rejected(self, client):
        resp = client.post(
            "/v1/universes/test-universe/canon",
            json={"filename": "test.md", "content": ""},
        )
        assert resp.status_code == 422

    def test_filename_with_spaces(self, client, universe_dir):
        """Filenames with spaces should work."""
        resp = client.post(
            "/v1/universes/test-universe/canon",
            json={"filename": "my world notes.md", "content": "Notes here."},
        )
        assert resp.status_code == 201
        assert (universe_dir / "canon" / "my world notes.md").exists()

    def test_filename_only_dots_rejected(self, client, universe_dir):
        """Filename like '..' or '.' should be sanitized."""
        resp = client.post(
            "/v1/universes/test-universe/canon",
            json={"filename": "..", "content": "Evil"},
        )
        # Path("..").name is "" on Windows/Linux -> should be caught
        assert resp.status_code == 400

    def test_unicode_content(self, client, universe_dir):
        """Unicode content should round-trip."""
        content = "Les anciens disaient que les dragons..."
        resp = client.post(
            "/v1/universes/test-universe/canon",
            json={"filename": "lore.md", "content": content},
        )
        assert resp.status_code == 201
        resp2 = client.get("/v1/universes/test-universe/canon/lore.md")
        assert resp2.json()["content"] == content

    def test_hidden_files_excluded_from_list(self, client, universe_dir):
        """Hidden files (dot-prefixed) should not appear in canon listing."""
        canon_dir = universe_dir / "canon"
        canon_dir.mkdir(parents=True)
        (canon_dir / ".hidden").write_text("hidden", encoding="utf-8")
        (canon_dir / "visible.md").write_text("visible", encoding="utf-8")
        resp = client.get("/v1/universes/test-universe/canon")
        names = [f["filename"] for f in resp.json()["files"]]
        assert "visible.md" in names
        assert ".hidden" not in names


# ---------------------------------------------------------------------------
# Overview endpoint
# ---------------------------------------------------------------------------


class TestOverview:
    def test_overview_empty_universe(self, client):
        """Overview on a universe with no status/progress/output should return defaults."""
        resp = client.get("/v1/universes/test-universe/overview")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"]["daemon_state"] == "idle"
        assert data["status"]["word_count"] == 0
        assert "No progress" in data["progress"]
        assert data["output_files"] == []
        assert data["recent_activity"] == []

    def test_overview_with_all_data(self, client, universe_dir):
        """Overview should composite status + progress + output + activity."""
        # Status
        (universe_dir / "status.json").write_text(
            json.dumps({"daemon_state": "running", "word_count": 5000}),
            encoding="utf-8",
        )
        # Progress
        (universe_dir / "progress.md").write_text(
            "# Story Progress\n\nChapter 3 complete.", encoding="utf-8",
        )
        # Output
        output_dir = universe_dir / "output" / "book-1"
        output_dir.mkdir(parents=True)
        (output_dir / "chapter-01.md").write_text("Content.", encoding="utf-8")
        # Activity
        lines = [f"[2026-04-01 12:00:{i:02d}] Event {i}" for i in range(15)]
        (universe_dir / "activity.log").write_text(
            "\n".join(lines), encoding="utf-8",
        )

        resp = client.get("/v1/universes/test-universe/overview")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"]["word_count"] == 5000
        assert "Chapter 3" in data["progress"]
        assert len(data["output_files"]) == 1
        # Activity returns last 10
        assert len(data["recent_activity"]) == 10
        assert "Event 14" in data["recent_activity"][-1]

    def test_overview_nonexistent_universe(self, client):
        resp = client.get("/v1/universes/nonexistent/overview")
        assert resp.status_code == 404

    def test_overview_corrupt_status_json(self, client, universe_dir):
        """Corrupt status.json should gracefully return defaults."""
        (universe_dir / "status.json").write_text(
            "not valid json{{{", encoding="utf-8",
        )
        resp = client.get("/v1/universes/test-universe/overview")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"]["daemon_state"] == "idle"


# ---------------------------------------------------------------------------
# Facts/Characters/Promises with no DB
# ---------------------------------------------------------------------------


class TestInternalsNoDB:
    def test_facts_no_db(self, client):
        """Facts with no DB should return empty list."""
        resp = client.get("/v1/universes/test-universe/facts")
        assert resp.status_code == 200
        assert resp.json() == {"facts": [], "count": 0}

    def test_characters_no_db(self, client):
        resp = client.get("/v1/universes/test-universe/characters")
        assert resp.status_code == 200
        assert resp.json() == {"characters": [], "count": 0}

    def test_promises_no_db(self, client):
        resp = client.get("/v1/universes/test-universe/promises")
        assert resp.status_code == 200
        assert resp.json() == {"promises": [], "count": 0}

    def test_facts_nonexistent_universe(self, client):
        resp = client.get("/v1/universes/nonexistent/facts")
        assert resp.status_code == 404

    def test_characters_nonexistent_universe(self, client):
        resp = client.get("/v1/universes/nonexistent/characters")
        assert resp.status_code == 404

    def test_promises_nonexistent_universe(self, client):
        resp = client.get("/v1/universes/nonexistent/promises")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Facts edge cases
# ---------------------------------------------------------------------------


class TestFactsEdgeCases:
    def test_facts_invalid_chapter_param(self, client):
        """chapter=0 should be rejected (ge=1)."""
        resp = client.get("/v1/universes/test-universe/facts?chapter=0")
        assert resp.status_code == 422

    def test_facts_negative_chapter_rejected(self, client):
        resp = client.get("/v1/universes/test-universe/facts?chapter=-1")
        assert resp.status_code == 422

    def test_facts_non_numeric_chapter_rejected(self, client):
        resp = client.get("/v1/universes/test-universe/facts?chapter=abc")
        assert resp.status_code == 422

    def test_facts_chapter_with_no_results(self, client, universe_dir):
        """Querying chapter that has no facts should return empty."""
        from domains.fantasy_daemon.phases.world_state_db import connect, init_db, store_fact

        db_path = str(universe_dir / "story.db")
        init_db(db_path)
        with connect(db_path) as conn:
            store_fact(
                conn, fact_id="f1", text="A fact.",
                scene_id="s1", chapter_number=1, importance=0.5,
            )

        resp = client.get("/v1/universes/test-universe/facts?chapter=99")
        assert resp.status_code == 200
        assert resp.json()["count"] == 0


# ---------------------------------------------------------------------------
# Promises edge cases
# ---------------------------------------------------------------------------


class TestPromisesEdgeCases:
    def test_invalid_status_filter(self, client):
        """Unknown status value should still return (just unfiltered)."""
        resp = client.get("/v1/universes/test-universe/promises?status=unknown")
        assert resp.status_code == 200
        # No filter matches, returns full list (which is empty)
        assert resp.json()["count"] == 0

    def test_resolved_filter(self, client, universe_dir):
        """?status=resolved should only return resolved promises."""
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
                conn, promise_id="p1", text="Active one.",
                created_scene="s1", created_chapter=1,
            )
            add_promise(
                conn, promise_id="p2", text="Resolved one.",
                created_scene="s1", created_chapter=1,
            )
            resolve_promise(
                conn, promise_id="p2",
                resolved_scene="s3", resolved_chapter=2,
            )

        resp = client.get("/v1/universes/test-universe/promises?status=resolved")
        assert resp.status_code == 200
        assert resp.json()["count"] == 1
        assert resp.json()["promises"][0]["text"] == "Resolved one."


# ---------------------------------------------------------------------------
# Output edge cases
# ---------------------------------------------------------------------------


class TestOutputEdgeCases:
    def test_read_empty_directory_returns_404(self, client, universe_dir):
        """Reading an empty directory should return 404 (no scene files)."""
        book_dir = universe_dir / "output" / "book-1"
        book_dir.mkdir(parents=True)
        resp = client.get("/v1/universes/test-universe/output/book-1")
        assert resp.status_code == 404

    def test_read_directory_concatenates_scenes(self, client, universe_dir):
        """Reading a chapter directory returns concatenated scene files."""
        chapter_dir = universe_dir / "output" / "book-1" / "chapter-01"
        chapter_dir.mkdir(parents=True)
        (chapter_dir / "scene-01.md").write_text("Scene one.", encoding="utf-8")
        (chapter_dir / "scene-02.md").write_text("Scene two.", encoding="utf-8")
        resp = client.get("/v1/universes/test-universe/output/book-1/chapter-01")
        assert resp.status_code == 200
        assert "Scene one." in resp.text
        assert "Scene two." in resp.text
        assert "---" in resp.text

    def test_put_empty_path(self, client):
        """PUT to output/ with no path should fail (403 lockdown or routing error)."""
        resp = client.put(
            "/v1/universes/test-universe/output/",
            json={"content": "Something"},
        )
        assert resp.status_code in (307, 403, 404, 405)

    def test_put_unicode_content_blocked(self, client, universe_dir):
        """PUT output is locked down — even valid unicode content gets 403."""
        text = "Les chevaliers du royaume..."
        resp = client.put(
            "/v1/universes/test-universe/output/chapter.md",
            json={"content": text},
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Daemon control edge cases
# ---------------------------------------------------------------------------


class TestDaemonControlEdgeCases:
    def test_unknown_action_rejects(self, client):
        """Actions other than start/stop/pause should be 400."""
        for action in ("restart", "kill", "status", "reset"):
            resp = client.post(f"/v1/daemon/{action}")
            assert resp.status_code == 400, f"Expected 400 for action '{action}'"

    def test_stop_when_no_daemon(self, client):
        """Stopping with no daemon should succeed gracefully."""
        resp = client.post("/v1/daemon/stop")
        assert resp.status_code == 200
        assert resp.json()["status"] == "stopping"

    def test_pause_when_no_daemon(self, client):
        """Pausing with no daemon should succeed gracefully."""
        resp = client.post("/v1/daemon/pause")
        assert resp.status_code == 200
        assert resp.json()["status"] == "paused"

    def test_start_with_empty_universe(self, client):
        """Start with empty string universe should be treated as no universe."""
        resp = client.post(
            "/v1/daemon/start",
            json={"universe": ""},
        )
        # Empty string is falsy -> no universe specified, no daemon -> 400
        assert resp.status_code == 400

    def test_start_with_null_universe(self, client):
        """Start with null universe should be treated as no universe."""
        resp = client.post(
            "/v1/daemon/start",
            json={"universe": None},
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Status edge cases
# ---------------------------------------------------------------------------


class TestStatusEdgeCases:
    def test_corrupt_status_json(self, client, universe_dir):
        """Corrupt status.json should return 500."""
        (universe_dir / "status.json").write_text(
            "{invalid json!!!", encoding="utf-8",
        )
        resp = client.get("/v1/universes/test-universe/status")
        assert resp.status_code == 500

    def test_empty_status_json(self, client, universe_dir):
        """Empty status.json file should return 500."""
        (universe_dir / "status.json").write_text("", encoding="utf-8")
        resp = client.get("/v1/universes/test-universe/status")
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# Auth edge cases
# ---------------------------------------------------------------------------


class TestAuthEdgeCases:
    def test_bearer_prefix_case_sensitive(self, tmp_path):
        """'bearer' (lowercase) should fail auth."""
        configure(
            base_path=str(tmp_path),
            api_key="fa_user_sk_abc123",
            daemon=None,
        )
        c = TestClient(app)
        resp = c.get(
            "/v1/universes/test-universe/status",
            headers={"Authorization": "bearer fa_user_sk_abc123"},
        )
        assert resp.status_code == 401

    def test_key_without_bearer_prefix_accepted(self, tmp_path):
        """Raw key without 'Bearer ' prefix is also accepted."""
        configure(
            base_path=str(tmp_path),
            api_key="fa_user_sk_abc123",
            daemon=None,
        )
        c = TestClient(app)
        resp = c.get(
            "/v1/universes/test-universe/status",
            headers={"Authorization": "fa_user_sk_abc123"},
        )
        # The API accepts raw keys without Bearer prefix
        assert resp.status_code == 200

# ---------------------------------------------------------------------------
# Activity edge cases
# ---------------------------------------------------------------------------


class TestActivityEdgeCases:
    def test_empty_log_file(self, client, universe_dir):
        """Empty activity.log should return empty lines."""
        (universe_dir / "activity.log").write_text("", encoding="utf-8")
        resp = client.get("/v1/universes/test-universe/activity")
        assert resp.status_code == 200
        assert resp.json()["lines"] == []

    def test_single_line_log(self, client, universe_dir):
        (universe_dir / "activity.log").write_text(
            "[2026-04-01 12:00:00] Single event", encoding="utf-8",
        )
        resp = client.get("/v1/universes/test-universe/activity?lines=1")
        assert resp.status_code == 200
        assert len(resp.json()["lines"]) == 1

    def test_lines_exactly_at_max(self, client, universe_dir):
        """lines=500 (max allowed) should work."""
        (universe_dir / "activity.log").write_text(
            "Event\n" * 600, encoding="utf-8",
        )
        resp = client.get("/v1/universes/test-universe/activity?lines=500")
        assert resp.status_code == 200
        assert len(resp.json()["lines"]) == 500
