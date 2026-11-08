"""The handles canary across the cutover (scripts/cutover_handles_canary.py), offline.

Each side's handle set is data (scripts/canary_handles.json): the old side's is the old
canary's own record, the new side's the doors' tool table (held by
platform/doors/guards/handles.test.ts). The canary tells the sides apart by the
authorization server the public resource document names, holds the side it is asked for,
and changes nothing. handles-canary.yml runs it by hand, read-only, in production.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "scripts"))

import cutover_handles_canary as canary  # noqa: E402
import mcp_public_canary as public  # noqa: E402

pytestmark = pytest.mark.verifies(
    "live-mcp-connector-surface:Public Canary And Canonical Review Surface",
    "clean-sheet-platform:Each caller is served exactly its own tool profile",
)

URL = "https://tinyassets.io/mcp"
METADATA = "https://tinyassets.io/.well-known/oauth-protected-resource/mcp"
KEY = "ta_key_0123456789abcdef_" + "s" * 43
RECORD = json.loads((_REPO / "scripts" / "canary_handles.json").read_text(encoding="utf-8"))
OLD = set(RECORD["old"]["handles"])
NEW = set(RECORD["new"]["handles"])


def test_the_old_side_is_the_old_canarys_own_record():
    assert public.CANONICAL_HANDLES == frozenset(OLD)
    assert [RECORD["old"]["authorization_server"]] == list(public.EXPECTED_AUTHORIZATION_SERVERS)
    assert RECORD["new"]["authorization_server"] == "https://tinyassets.io"
    assert len(OLD) == len(RECORD["old"]["handles"])
    assert len(NEW) == len(RECORD["new"]["handles"])


class Doors:
    """A fake of whichever side answers: its resource document, its challenge and its API."""

    def __init__(self, monkeypatch, side="new", served=NEW, refuse=None):
        self.gets: list[tuple[str, str]] = []
        server = RECORD[side]["authorization_server"]
        document = {"resource": URL, "authorization_servers": [server]}
        monkeypatch.setattr(public, "_get_json", lambda url, timeout: (
            document if url == METADATA else pytest.fail(f"unexpected document {url}")))

        def post(url, payload, timeout, session_id=None, accepted_http_statuses=frozenset(),
                 bearer=None):
            assert bearer is None and payload["method"] == "initialize"
            return 401, {"www-authenticate": f'Bearer resource_metadata="{METADATA}"'}, b"{}"

        monkeypatch.setattr(public, "_post", post)

        def get(url, key, timeout):
            name = url.rsplit("/", 1)[1]
            self.gets.append((url, key))
            if refuse is not None:
                return refuse
            return 405 if name in served else 404

        monkeypatch.setattr(canary, "_get_status", get)


def test_auto_takes_the_side_the_resource_document_names(monkeypatch):
    for side in ("old", "new"):
        Doors(monkeypatch, side=side)
        assert canary.serving_side(URL, 1.0) == side


def test_neither_sides_server_stops_it(monkeypatch, capsys):
    monkeypatch.setattr(public, "_get_json", lambda url, timeout: {
        "resource": URL, "authorization_servers": ["https://elsewhere.test"]})
    assert canary.run("auto", URL, 1.0, False) == 9
    assert "neither side's authorization server" in capsys.readouterr().err


def test_the_new_side_holds_every_handle_served_and_the_old_ones_not(monkeypatch, capsys):
    monkeypatch.setenv(canary.PROBE_KEY_ENV, KEY)
    doors = Doors(monkeypatch)
    assert canary.run("new", URL, 1.0, True) == 0
    asked = {url.rsplit("/", 1)[1] for url, _key in doors.gets}
    # Every new handle, every old-only one and an unknown name: one GET each, on /api/tools.
    assert asked == NEW | (OLD - NEW) | {"no_such_tool"}
    assert all(url.startswith("https://tinyassets.io/api/tools/") for url, _key in doors.gets)
    out = capsys.readouterr()
    assert f"{len(NEW)} handles served" in out.out
    assert KEY not in out.out + out.err


@pytest.mark.parametrize("served, named", [
    (NEW - {"file_read"}, "missing=['file_read (HTTP 404)']"),
    (NEW | {"read_graph"}, "extra=['read_graph (HTTP 405)']"),
])
def test_a_drifted_handle_set_fails_naming_it(monkeypatch, capsys, served, named):
    monkeypatch.setenv(canary.PROBE_KEY_ENV, KEY)
    Doors(monkeypatch, served=served)
    assert canary.run("new", URL, 1.0, False) == 4
    assert named in capsys.readouterr().err


@pytest.mark.parametrize("status, said", [
    (401, "is not a live API key"), (503, "the control record must admit it")])
def test_a_refused_probe_key_fails_loudly(monkeypatch, capsys, status, said):
    monkeypatch.setenv(canary.PROBE_KEY_ENV, KEY)
    Doors(monkeypatch, refuse=status)
    assert canary.run("new", URL, 1.0, False) == 6
    assert said in capsys.readouterr().err


def test_no_probe_key_is_a_failure_never_a_pass(monkeypatch, capsys):
    monkeypatch.delenv(canary.PROBE_KEY_ENV, raising=False)
    doors = Doors(monkeypatch)
    assert canary.run("new", URL, 1.0, True) == 7
    assert doors.gets == []
    assert "handle set is unchecked" in capsys.readouterr().err


def test_the_new_side_needs_the_doors_challenge(monkeypatch, capsys):
    monkeypatch.setenv(canary.PROBE_KEY_ENV, KEY)
    Doors(monkeypatch)
    monkeypatch.setattr(public, "_post", lambda *a, **k: (401, {
        "www-authenticate": 'Bearer resource_metadata="https://elsewhere.test/x"'}, b"{}"))
    assert canary.run("new", URL, 1.0, False) == 6
    assert "did not challenge" in capsys.readouterr().err


def test_an_explicit_side_must_be_the_one_serving(monkeypatch, capsys):
    Doors(monkeypatch, side="old")
    assert canary.run("new", URL, 1.0, False) == 8
    assert "asked for the new side, but the old side serves" in capsys.readouterr().err


def test_the_old_side_is_the_old_stacks_full_canary(monkeypatch):
    Doors(monkeypatch, side="old")
    calls: list[list[str]] = []

    def old_main(argv):
        calls.append(argv)
        raise SystemExit(4)

    monkeypatch.setattr(public, "main", old_main)
    assert canary.run("old", URL, 1.0, True) == 4
    assert calls == [["--url", URL, "--assert-handles", "--verbose"]]
    monkeypatch.setattr(public, "main", lambda argv: 0)
    assert canary.run("auto", URL, 1.0, False) == 0


# --- handles-canary.yml: by hand, read-only, in production ---------------------------

_WORKFLOW = _REPO / ".github" / "workflows" / "handles-canary.yml"


def test_the_workflow_runs_the_canary_by_hand_and_nothing_else():
    data = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    trigger = data.get("on", data.get(True))
    assert list(trigger) == ["workflow_dispatch"]
    side = trigger["workflow_dispatch"]["inputs"]["side"]
    assert side["options"] == ["auto", "old", "new"]
    assert data["permissions"] == {"contents": "read"}
    (job,) = data["jobs"].values()
    assert job["environment"] == "production"
    uses = [step["uses"] for step in job["steps"] if "uses" in step]
    assert [use.split("@")[0] for use in uses] == ["actions/checkout", "actions/setup-python"]
    runs = [step for step in job["steps"] if "run" in step]
    assert [step["run"] for step in runs] == [
        'python scripts/cutover_handles_canary.py --side "${SIDE}" --verbose']
    assert runs[0]["env"] == {
        "SIDE": "${{ inputs.side }}",
        "TINYASSETS_WIKI_CANARY_TOKEN": "${{ secrets.TINYASSETS_WIKI_CANARY_TOKEN }}",
        "TA_PROBE_KEY": "${{ secrets.TA_PROBE_KEY }}",
    }
