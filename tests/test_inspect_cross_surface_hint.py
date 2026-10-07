"""Tests for task #30: universe inspect cross_surface_hint field."""
import json
from pathlib import Path
from unittest.mock import patch


def _call_inspect(universe_id="test-u"):
    from tinyassets.api.universe import _action_inspect_universe

    fake_udir = Path("/fake") / universe_id

    with (
        # _action_inspect_universe lives in tinyassets.api.universe and
        # imports these symbols directly (not via re-export). Patch at the
        # consumer site (api.universe) for it to take effect.
        patch("tinyassets.api.universe._request_universe", return_value=universe_id),
        patch("tinyassets.api.universe._universe_dir", return_value=fake_udir),
        patch.object(Path, "is_dir", return_value=True),
        patch("tinyassets.api.visibility.visibility_permits", return_value=True),
        # inspect also requires an ownership row to name the universe (a
        # directory on disk is not one, 2026-09-02). This file's subject is the
        # cross_surface_hint FIELD, and every other precondition here is already
        # mocked, so the ownership answer is mocked alongside them.
        patch(
            "tinyassets.api.universe._owned_universe_id",
            return_value=universe_id,
        ),
        patch("tinyassets.api.visibility.declared_level_name", return_value="public"),
        patch("tinyassets.api.universe._read_json", return_value=None),
        patch("tinyassets.api.universe._read_text", return_value=""),
        patch("tinyassets.api.universe._daemon_liveness", return_value={
            "phase": "idle", "phase_human": "Idle", "is_paused": False,
            "has_premise": False, "has_soul": False, "has_work": False,
            "last_activity_at": "",
            "staleness": "fresh", "word_count": 0, "word_count_sample": "",
            "accept_rate": 0, "accept_rate_sample": "",
        }),
        patch("tinyassets.api.universe._list_output_tree", return_value=[]),
        patch("tinyassets.api.universe._base_path", return_value=Path("/fake")),
    ):
        return json.loads(_action_inspect_universe(universe_id=universe_id))


class TestInspectCrossSurfaceHint:
    def test_cross_surface_hint_present(self):
        """inspect response always includes cross_surface_hint."""
        result = _call_inspect()
        assert "cross_surface_hint" in result

    def test_cross_surface_hint_has_note(self):
        """cross_surface_hint.note is a non-empty string."""
        result = _call_inspect()
        hint = result["cross_surface_hint"]
        assert isinstance(hint.get("note"), str)
        assert hint["note"]

    def test_cross_surface_hint_has_four_paths(self):
        """cross_surface_hint.paths has exactly 4 entries."""
        result = _call_inspect()
        paths = result["cross_surface_hint"]["paths"]
        assert isinstance(paths, list)
        assert len(paths) == 4

    def test_cross_surface_hint_path_actions(self):
        """All four hints route through advertised handles."""
        result = _call_inspect()
        actions = {p["action"] for p in result["cross_surface_hint"]["paths"]}
        assert 'read_graph target="branch" branch_id="<known id>"' in actions
        assert 'read_graph target="goals"' in actions
        assert 'read_page query="<terms>"' in actions
        assert 'read_graph target="graphs"' in actions

    def test_cross_surface_hint_paths_have_purpose(self):
        """Every path entry has a non-empty purpose field."""
        result = _call_inspect()
        for p in result["cross_surface_hint"]["paths"]:
            assert p.get("purpose"), f"Missing purpose on path: {p}"

    def test_existing_fields_still_present(self):
        """cross_surface_hint addition does not remove universe_id or daemon."""
        result = _call_inspect()
        assert "universe_id" in result
        assert "daemon" in result
