"""The handles canary across the cutover (scripts/cutover_handles_canary.py), offline.

Each side's handle set is data (scripts/canary_handles.json): the old side's is the old
canary's own record, the new side's the doors' tool table (held by
platform/doors/guards/handles.test.ts). The canary tells the sides apart by the
authorization server the public resource document names, holds the side it is asked for,
and changes nothing. handles-canary.yml runs it by hand, read-only, in production.
"""

from __future__ import annotations

import json
import os
import subprocess
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


def test_the_old_canary_runs_with_only_the_images_two_files(tmp_path):
    # The daemon image copies mcp_public_canary.py and _canary_common.py alone (Dockerfile),
    # and its healthcheck runs the first: it must import and run with nothing beside it.
    for name in ("mcp_public_canary.py", "_canary_common.py"):
        (tmp_path / name).write_bytes((_REPO / "scripts" / name).read_bytes())
    for argv in (["-c", "import sys; sys.path.insert(0, sys.argv[1]); "
                  "import mcp_public_canary as c; print(len(c.CANONICAL_HANDLES))", str(tmp_path)],
                 ["mcp_public_canary.py", "--help"]):
        ran = subprocess.run([sys.executable, "-I", *argv], cwd=tmp_path, capture_output=True,
                             text=True, env={"PATH": os.environ.get("PATH", "")}, timeout=60)
        assert (argv[0], ran.returncode, ran.stderr) == (argv[0], 0, "")
    assert ran.stdout.startswith("usage:")


def test_the_old_side_is_the_old_canarys_own_record():
    # Two sources: the old canary's own literal and the JSON, so dropping a handle from
    # either fails.
    assert public.CANONICAL_HANDLES == frozenset(OLD)
    assert [RECORD["old"]["authorization_server"]] == list(public.EXPECTED_AUTHORIZATION_SERVERS)
    assert RECORD["new"]["authorization_server"] == "https://tinyassets.io"
    assert len(OLD) == len(RECORD["old"]["handles"])
    assert len(NEW) == len(RECORD["new"]["handles"])


#: Each side's anonymous 401 body: the old daemon's, and the doors' empty one.
BODIES = {"old": json.dumps({"error": "authentication_required",
                             "error_description": "Sign in to use TinyAssets."}).encode(),
          "new": b""}


class Doors:
    """A fake of whichever side answers: its resource document, its challenge and its API.
    ``answers`` is the side ``/mcp`` itself answers as, the document's by default."""

    def __init__(self, monkeypatch, side="new", served=NEW, refuse=None, answers=None):
        self.gets: list[tuple[str, str]] = []
        server = RECORD[side]["authorization_server"]
        document = {"resource": URL, "authorization_servers": [server]}
        monkeypatch.setattr(public, "_get_json", lambda url, timeout: (
            document if url == METADATA else pytest.fail(f"unexpected document {url}")))
        body = BODIES[answers or side]

        def post(url, payload, timeout, session_id=None, accepted_http_statuses=frozenset(),
                 bearer=None):
            assert bearer is None and payload["method"] == "initialize"
            return 401, {"www-authenticate": f'Bearer resource_metadata="{METADATA}"'}, body

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
    Doors(monkeypatch)
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
        "www-authenticate": 'Bearer resource_metadata="https://elsewhere.test/x"'}, b""))
    assert canary.run("new", URL, 1.0, False) == 6
    assert "did not challenge" in capsys.readouterr().err


@pytest.mark.parametrize("answers, document", [("new", "old"), ("old", "new")])
def test_a_split_mid_handover_is_its_own_failure(monkeypatch, capsys, answers, document):
    # /mcp moved, its resource document not yet (or the reverse, a handback partway).
    Doors(monkeypatch, side=document, answers=answers)
    for side in ("auto", "old", "new"):
        assert canary.run(side, URL, 1.0, False) == 10
    assert (f"split: {URL} answers as the {answers} side, but the resource document is the "
            f"{document} side's") in capsys.readouterr().err


def test_an_anonymous_401_neither_side_sends_stops_it(monkeypatch, capsys):
    Doors(monkeypatch)
    monkeypatch.setattr(public, "_post", lambda *a, **k: (401, {}, b'{"error": "other"}'))
    assert canary.run("auto", URL, 1.0, False) == 9
    assert "anonymous 401 is neither side's" in capsys.readouterr().err


def test_unkeyed_routes_passes_with_no_key_and_a_closed_record(monkeypatch, capsys):
    # What the deploy kit's routes runs: no probe key can exist yet, and the dark control
    # record refuses every /api call (503), so only the unkeyed checks run, and pass.
    monkeypatch.delenv(canary.PROBE_KEY_ENV, raising=False)
    doors = Doors(monkeypatch, refuse=503)
    assert canary.main(["--side", "new", "--unkeyed"]) == 0
    assert doors.gets == []
    assert "Its handle set is owed after verify" in capsys.readouterr().out
    # It still holds the side and the challenge: the old stack answering fails it.
    Doors(monkeypatch, side="old")
    assert canary.main(["--side", "new", "--unkeyed"]) == 8
    with pytest.raises(SystemExit):
        canary.main(["--side", "old", "--unkeyed"])


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
    assert calls == [["--url", URL, "--timeout", "1.0", "--assert-handles", "--verbose"]]
    monkeypatch.setattr(public, "main", lambda argv: 0)
    assert canary.run("auto", URL, 1.0, False) == 0


# --- timeouts: the canary's own, one retry for a timeout, never for an answer --------


def test_the_canary_has_its_own_timeout_and_hands_it_to_the_old_canary(monkeypatch):
    # The old stack's anonymous answers took close to 10 s from GitHub's runners and
    # crossed mcp_public_canary.py's own 10 s inside the deploy kit's probe.
    assert canary.DEFAULT_TIMEOUT == 30.0 > public.DEFAULT_TIMEOUT
    asked: list[tuple] = []
    monkeypatch.setattr(canary, "run", lambda *args: asked.append(args) or 0)
    assert canary.main(["--side", "auto"]) == 0
    assert asked == [("auto", public.DEFAULT_URL, 30.0, False, False)]


def _timeout(url):
    try:
        raise TimeoutError("The read operation timed out")
    except TimeoutError as exc:
        raise public.CanaryError(2, f"timeout after 30.0s: {url}") from exc


class Slow:
    """The old side, whose anonymous initialize times out its first ``slow`` times."""

    def __init__(self, monkeypatch, slow):
        Doors(monkeypatch, side="old")
        self.posts = 0
        answer = public._post

        def post(url, payload, timeout, **kwargs):
            self.posts += 1
            if self.posts <= slow:
                _timeout(url)
            return answer(url, payload, timeout, **kwargs)

        monkeypatch.setattr(public, "_post", post)
        # The old stack's own canary, sending through the same sender.
        monkeypatch.setattr(public, "main", lambda argv: public._post(
            URL, public._INIT_PAYLOAD, 30.0, accepted_http_statuses=frozenset({401}))[0] - 401)


def test_a_slow_first_answer_is_sent_once_more_and_passes(monkeypatch, capsys):
    slow = Slow(monkeypatch, slow=1)
    assert canary.run("auto", URL, 30.0, True) == 0
    # The side's read once, retried once, then the old canary's own request.
    assert slow.posts == 3
    err = capsys.readouterr().err
    assert f"POST {URL} initialize (anonymous) timed out after" in err
    assert "sending it once more" in err
    assert f"POST {URL} initialize (anonymous): " in err


def test_the_old_canarys_own_slow_request_is_sent_once_more(monkeypatch):
    slow = Slow(monkeypatch, slow=0)
    answer = public._post
    timed_out = []

    def post(url, payload, timeout, **kwargs):
        if slow.posts == 1 and not timed_out:
            timed_out.append(url)
            _timeout(url)
        return answer(url, payload, timeout, **kwargs)

    monkeypatch.setattr(public, "_post", post)
    assert canary.run("old", URL, 30.0, False) == 0
    assert (timed_out, slow.posts) == ([URL], 2)


def test_two_timeouts_fail_naming_the_request(monkeypatch, capsys):
    slow = Slow(monkeypatch, slow=2)
    assert canary.run("auto", URL, 30.0, False) == 2
    assert slow.posts == 2
    err = capsys.readouterr().err
    assert f"POST {URL} initialize (anonymous) timed out after " in err
    assert f"s on its one retry: timeout after 30.0s: {URL}" in err


def _refused(url):
    raise public.CanaryError(2, f"HTTP 502 from {url}: Bad Gateway")


@pytest.mark.parametrize("answer, code", [
    (_refused, 2),                          # a refusal
    (lambda url: (200, {}, b"{}"), 9),      # a wrong status
])
def test_a_wrong_answer_is_never_sent_again(monkeypatch, capsys, answer, code):
    Doors(monkeypatch, side="old")
    posts: list[str] = []
    monkeypatch.setattr(public, "_post", lambda url, *a, **k: posts.append(url) or answer(url))
    assert canary.run("auto", URL, 30.0, False) == code
    assert posts == [URL]
    assert "once more" not in capsys.readouterr().err


def test_a_doors_timeout_is_not_sent_again(monkeypatch, capsys):
    monkeypatch.setenv(canary.PROBE_KEY_ENV, KEY)
    doors = Doors(monkeypatch)
    monkeypatch.setattr(canary, "_get_status", lambda url, key, timeout: (
        doors.gets.append((url, key)), _timeout(url)))
    assert canary.run("new", URL, 30.0, False) == 2
    assert len(doors.gets) == 1
    assert "timed out after" in capsys.readouterr().err


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
