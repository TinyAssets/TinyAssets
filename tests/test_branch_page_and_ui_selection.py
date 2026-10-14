"""An oversized branch pages its nodes; a selection read is not the library."""
from __future__ import annotations

import json

from tinyassets.engine_read_views import project_branch


def _branch(nodes: list[dict]) -> str:
    return json.dumps({
        "branch_def_id": "score",
        "name": "Score",
        "edges": [{"from": "n0", "to": "END"}],
        "node_defs": nodes,
    })


def test_a_branch_that_fits_is_the_original_text() -> None:
    payload = _branch([{"node_id": "n0", "source_code": "return 1"}])
    assert project_branch(
        payload, offset=0, budget=24_576, render_text=lambda text: text,
    ) == payload


def test_an_oversized_branch_pages_every_node_once() -> None:
    nodes = [{"node_id": f"n{i}", "source_code": "x" * 4_000} for i in range(4)]
    payload = _branch(nodes)
    seen: list[str] = []
    offset = 0
    pages = 0
    while True:
        page = json.loads(project_branch(
            payload, offset=offset, budget=6_000, render_text=lambda text: text,
            scope=' graph_id="u-1"',
        ))
        pages += 1
        assert page["edges"] == [{"from": "n0", "to": "END"}]
        assert page["node_total"] == 4
        assert page["offset"] == offset
        seen.extend(node["node_id"] for node in page["node_defs"])
        if page["complete"]:
            assert page["next"] is None and page["next_offset"] is None
            break
        assert page["next"] == (
            'read_graph target="branch" branch_id="score" graph_id="u-1" '
            f'output_offset={page["next_offset"]}'
        )
        offset = page["next_offset"]
        assert pages < 8
    assert pages > 1
    assert seen == ["n0", "n1", "n2", "n3"]


def test_one_node_bigger_than_the_page_is_clipped_and_the_cursor_continues() -> None:
    payload = _branch([
        {"node_id": "big", "source_code": "x" * 20_000},
        {"node_id": "after", "source_code": "y"},
    ])
    first = json.loads(project_branch(
        payload, offset=0, budget=4_000, render_text=lambda text: text,
    ))
    assert first["node_defs"][0]["node_id"] == "big"
    assert first["node_defs"][0]["clipped_chars"]["source_code"] == 20_000
    assert first["complete"] is False
    second = json.loads(project_branch(
        payload, offset=first["next_offset"], budget=4_000, render_text=lambda text: text,
    ))
    assert [node["node_id"] for node in second["node_defs"]] == ["after"]
    assert second["node_defs"][0]["source_code"] == "y"
    assert second["complete"] is True


def test_an_offset_past_the_nodes_is_an_error() -> None:
    payload = _branch([{"node_id": "n0"}])
    err = json.loads(project_branch(
        payload, offset=2, budget=24_576, render_text=lambda text: text,
    ))
    assert err == {"error": "output_offset is past the branch", "node_total": 1}
    refused = json.loads(project_branch(
        payload, offset=-1, budget=24_576, render_text=lambda text: text,
    ))
    assert refused["error"] == "output_offset must be a non-negative integer"


def test_a_read_error_is_not_paged() -> None:
    payload = json.dumps({"error": "Branch 'missing' not found."})
    assert project_branch(
        payload, offset=0, budget=8, render_text=lambda text: text,
    ) == payload


def test_the_envelope_is_the_text_the_page_is_fit_to() -> None:
    payload = _branch([{"node_id": f"n{i}", "source_code": "x" * 500} for i in range(3)])

    def wrap(text: str) -> str:
        return json.dumps({"untrusted": True, "content": json.loads(text)})

    full = len(wrap(payload).encode("utf-8"))
    text = project_branch(
        payload, offset=0, budget=full - 1, render_text=wrap,
    )
    wrapped = json.loads(text)
    assert len(text.encode("utf-8")) <= full - 1
    assert wrapped["untrusted"] is True
    assert wrapped["content"]["node_total"] == 3
    assert len(wrapped["content"]["node_defs"]) < 3
    assert wrapped["content"]["next_offset"] is not None


def test_ui_selection_is_the_choice_and_not_the_library(monkeypatch) -> None:
    from tinyassets.api import app_ui

    library = [{"ui_id": "office", "markup": "m" * 20_000}]
    document = {
        "universe_id": "u-alice",
        "ui_library": library,
        "ui_selection": {"ui_id": "office"},
        "revision": 4,
        "updated_at": 1.5,
    }
    monkeypatch.setattr(app_ui, "_binding_universe", lambda universe_id: "u-alice")
    monkeypatch.setattr(app_ui, "_binding_access", lambda uid, write=False: None)
    monkeypatch.setattr(app_ui, "_authenticated_actor", lambda: "alice")
    monkeypatch.setattr(app_ui, "get_app_ui", lambda *args, **kwargs: document)

    choice = app_ui.read_app_ui(universe_id="u-alice", ui_id="ui_selection")["app_ui"]
    assert choice == {
        "universe_id": "u-alice",
        "revision": 4,
        "updated_at": 1.5,
        "ui_selection": {"ui_id": "office"},
    }
    index = app_ui.read_app_ui(universe_id="u-alice", ui_id="index")["app_ui"]
    assert "ui_library" not in index
    assert index["uis"][0]["ui_id"] == "office"
    assert "markup" not in index["uis"][0]
