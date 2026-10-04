"""A read says which stored UI the app will refuse, and how to fix it.

Companion to ``tests/test_app_ui_one_bad_component.py``: the app stops hiding
the good UIs, and the SERVER read tells the agent which one it broke and why.
The agent cannot run the app's JavaScript, so without this it sees a library
that looks fine and a person who says nothing works.

The split this respects: the write path owns what the server's own stores
depend on (``_check_component`` -- bounds, assets, libraries, script_type) and
deliberately does not police the rendering contract. So ``app_ui_renderability``
checks exactly what a STORED entry can still fail, and it only reports: no
write starts refusing because of it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tinyassets.custom_agents import (
    APP_UI_COMPONENT_FIELDS,
    APP_UI_FORMAT_VERSION,
    APP_UI_KIND,
    APP_UI_MAX_NAME,
    APP_UI_OPTIONAL_COMPONENT_FIELDS,
    app_ui_index,
    app_ui_renderability,
    get_app_ui,
    save_app_ui,
)

ALICE = "alice"
HOME = "u-alice"
#: The founder's actual value: a timestamp used as a cache-buster.
CACHE_BUSTER = 1791005187


def _bundle(**over) -> dict:
    return {"kind": APP_UI_KIND, "version": APP_UI_FORMAT_VERSION, "ui_id": "office",
            "name": "Office", "markup": "<div>Lobby</div>", "style": "", "script": "",
            **over}


def test_a_good_component_is_renderable() -> None:
    assert app_ui_renderability(_bundle()) == {}
    # Every optional field present is still fine.
    assert app_ui_renderability(_bundle(libraries=["three"], script_type="module",
                                        assets={})) == {}


def test_the_founders_timestamp_version_names_itself_and_the_fix() -> None:
    refusal = app_ui_renderability(_bundle(version=CACHE_BUSTER))
    assert str(CACHE_BUSTER) in refusal["reason"]
    assert "not supported" in refusal["reason"]
    # The hint has to say the thing the agent got wrong, or it will do it again.
    assert "FORMAT version" in refusal["hint"]
    assert "cache-buster" in refusal["hint"]
    assert "revision moves" in refusal["hint"], "and what to do instead"
    assert "sha256" in refusal["hint"]


@pytest.mark.parametrize(("entry", "expected"), [
    ("not an object", "not an object"),
    ({}, "missing"),
    ({**_bundle(), "build_id": "7"}, "does not render: build_id"),
    ({**_bundle(), "kind": "something.else"}, f"not a {APP_UI_KIND}"),
    (_bundle(version="1"), "not supported"),
    (_bundle(version=True), "not supported"),
    (_bundle(ui_id="Office Tower"), "ui_id must be"),
    (_bundle(ui_id=""), "ui_id must be"),
    (_bundle(name="  "), "name must be"),
    (_bundle(name="x" * (APP_UI_MAX_NAME + 1)), "name must be"),
    (_bundle(markup=None), "markup must be a string"),
    (_bundle(script=42), "script must be a string"),
])
def test_each_way_a_stored_entry_can_be_unrenderable(entry, expected) -> None:
    refusal = app_ui_renderability(entry)
    assert refusal, f"{entry!r} should not be renderable"
    assert expected in refusal["reason"], refusal
    assert refusal["hint"], "every refusal carries a fix the agent can act on"


def test_a_spare_field_is_refused_so_a_build_id_is_not_the_answer() -> None:
    """Why the guidance cannot say "use a build id instead".

    There is no field to put one in: anything outside the component contract is
    refused by the app, so inventing a cache-buster field reproduces this P1 by
    another route.
    """
    refusal = app_ui_renderability({**_bundle(), "build_id": "7"})
    assert "build_id" in refusal["reason"]
    assert "build_id" not in str(APP_UI_COMPONENT_FIELDS + APP_UI_OPTIONAL_COMPONENT_FIELDS)


def test_the_index_marks_each_ui_and_only_the_broken_one(tmp_path) -> None:
    """The whole point: a library with one bad entry still reads as a library."""
    library = [_bundle(ui_id="office", name="Office building"),
               _bundle(ui_id="furry-house", name="Furry House", version=CACHE_BUSTER),
               _bundle(ui_id="village", name="Village")]
    saved = save_app_ui(tmp_path, owner_user_id=ALICE, universe_id=HOME,
                        expected_revision=0, changes={"ui_library": library})
    # The write really does accept it -- which is how the row got this way.
    assert saved["ui_library"] == library

    index = app_ui_index(saved)
    by_id = {entry["ui_id"]: entry for entry in index["uis"]}
    assert len(by_id) == 3, "every UI is still listed"
    assert by_id["office"]["renderable"] is True
    assert by_id["village"]["renderable"] is True
    assert "reason" not in by_id["office"] and "fix" not in by_id["office"]

    bad = by_id["furry-house"]
    assert bad["renderable"] is False
    assert str(CACHE_BUSTER) in bad["reason"]
    assert "FORMAT version" in bad["fix"]
    assert bad["name"] == "Furry House", "named, so the person can be told which"


def test_a_non_dict_entry_does_not_crash_the_index() -> None:
    assert app_ui_index({"ui_library": ["nonsense", None]})["uis"] == []


def test_the_python_contract_matches_the_app_that_enforces_it() -> None:
    """These constants are a mirror; the app is the authority.

    A drift here is worse than no check: the read would tell the agent a UI is
    fine while the app refuses it, or name a field the app accepts.
    """
    source = Path("tinyassets/onboarding/app_ui.js").read_text(encoding="utf-8")
    assert f'KIND:"{APP_UI_KIND}"' in source
    assert f"VERSION:{APP_UI_FORMAT_VERSION}," in source

    def listed(name: str) -> tuple[str, ...]:
        found = re.search(name + r":\[(.*?)\]", source)
        assert found, name
        return tuple(sorted(re.findall(r'"([a-z_]+)"', found.group(1))))

    assert listed("FIELDS") == tuple(sorted(APP_UI_COMPONENT_FIELDS))
    assert listed("OPTIONAL") == tuple(sorted(APP_UI_OPTIONAL_COMPONENT_FIELDS))
    assert f"MAX_NAME:{APP_UI_MAX_NAME}," in source
    assert "ID_RE:/^[a-z0-9][a-z0-9-]{0,63}$/" in source


def test_replace_ui_refuses_the_founders_component_with_the_fix_text(tmp_path) -> None:
    """Addendum 2: the write accepted it and answered "saved".

    The founder's agent installed through replace_ui, got revision 50 -> 51 back,
    and told them the UI was intact -- while the app refused to render it. The
    write and the read now share one definition.
    """
    from tinyassets.custom_agents import AgentValidationError, change_app_ui_entry

    save_app_ui(tmp_path, owner_user_id=ALICE, universe_id=HOME,
                expected_revision=0, changes={"ui_library": [_bundle(ui_id="furry-house")]})
    with pytest.raises(AgentValidationError) as refused:
        change_app_ui_entry(tmp_path, owner_user_id=ALICE, universe_id=HOME,
                            operation="replace_ui",
                            payload={"component": _bundle(ui_id="furry-house",
                                                           version=CACHE_BUSTER)})
    detail = str(refused.value)
    assert str(CACHE_BUSTER) in detail
    assert "FORMAT version" in detail, "the agent is told what to change"
    assert "cache-buster" in detail

    # And the stored row is untouched: a refusal is not a partial write.
    document = get_app_ui(tmp_path, owner_user_id=ALICE, universe_id=HOME)
    assert document["ui_library"] == [_bundle(ui_id="furry-house")]
    assert document["revision"] == 1, "no revision was burned"


def test_add_ui_refuses_it_too_and_names_the_operation(tmp_path) -> None:
    from tinyassets.custom_agents import AgentValidationError, change_app_ui_entry

    with pytest.raises(AgentValidationError, match="FORMAT version"):
        change_app_ui_entry(tmp_path, owner_user_id=ALICE, universe_id=HOME,
                            operation="add_ui",
                            payload={"component": _bundle(version=CACHE_BUSTER)})


def test_save_still_accepts_a_library_holding_an_unrenderable_entry(tmp_path) -> None:
    """The write refusal must NOT reach `save`, or the app cannot heal a row.

    `save` writes the whole library, and the app carries entries it cannot
    render through that write so they are not destroyed. If `save` refused
    them, a row that already holds a bad entry could never be written again --
    turning a hidden UI into an unfixable one.
    """
    library = [_bundle(ui_id="office"), _bundle(ui_id="furry-house", version=CACHE_BUSTER)]
    saved = save_app_ui(tmp_path, owner_user_id=ALICE, universe_id=HOME,
                        expected_revision=0, changes={"ui_library": library})
    assert saved["ui_library"] == library


def test_edit_ui_may_not_break_a_working_ui_but_may_edit_a_broken_one(tmp_path) -> None:
    """An edit is refused for a fault it INTRODUCES, never one it inherited."""
    from tinyassets.custom_agents import AgentValidationError, change_app_ui_entry

    save_app_ui(tmp_path, owner_user_id=ALICE, universe_id=HOME, expected_revision=0,
                changes={"ui_library": [_bundle(ui_id="office"),
                                        _bundle(ui_id="broken", version=CACHE_BUSTER)]})
    # Breaking a working one: refused.
    with pytest.raises(AgentValidationError, match="name must be"):
        change_app_ui_entry(tmp_path, owner_user_id=ALICE, universe_id=HOME,
                            operation="edit_ui",
                            payload={"ui_id": "office", "set": {"name": "   "}})
    # Editing one that was ALREADY unrenderable: allowed, so the agent is not
    # locked out of a row it has to repair.
    outcome = change_app_ui_entry(tmp_path, owner_user_id=ALICE, universe_id=HOME,
                                  operation="edit_ui",
                                  payload={"ui_id": "broken", "set": {"style": "p{}"}})
    assert outcome["ui_id"] == "broken"


def test_the_python_mirror_agrees_with_the_app_on_adversarial_shapes() -> None:
    """Differential: run app_ui.js's own parseBundle and compare verdicts.

    The constant-spelling test below cannot see BEHAVIOURAL drift, and two real
    ones were found that way (Codex, 2026-10-03): Python's ``$`` also matches
    before a trailing newline, and ``len()`` counts code points where
    JavaScript counts UTF-16 units. A mirror that says "renderable" about a UI
    the app refuses is worse than no report, so the equivalence is measured.
    """
    import json
    import shutil
    import subprocess
    import tempfile

    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed, so the real controller cannot be run here")

    cases = [
        _bundle(),
        _bundle(version=CACHE_BUSTER),
        _bundle(version="1"),
        _bundle(version=True),
        _bundle(version=1.0),
        {**_bundle(), "kind": "other"},
        {**_bundle(), "build_id": "7"},
        _bundle(ui_id="x" * 64),
        _bundle(ui_id="x" * 64 + "\n"),      # Python's `$` used to allow this
        _bundle(ui_id="x" * 65),
        _bundle(ui_id="Office"),
        _bundle(ui_id=""),
        _bundle(name="n" * APP_UI_MAX_NAME),
        _bundle(name="n" * (APP_UI_MAX_NAME + 1)),
        _bundle(name="\U0001f600" * 61),     # 61 code points, 122 UTF-16 units
        _bundle(name="\U0001f600" * 60),
        _bundle(name=" "),
        _bundle(markup=None),
        _bundle(script=42),
        _bundle(style=["x"]),
        {k: v for k, v in _bundle().items() if k != "script"},
        _bundle(script_type="esm"),
        _bundle(script_type="module"),
        _bundle(libraries="three"),
        _bundle(libraries=["three"]),
        _bundle(libraries=["three", "three"]),
        _bundle(libraries=["nosuchlib"]),
        _bundle(assets=[]),
        _bundle(assets={"img/a.png": {"sha256": "0" * 64, "size": 1,
                                      "media_type": "image/png"}}),
        _bundle(assets={"img/a.png": {"sha256": "nothex", "size": 1,
                                      "media_type": "image/png"}}),
        _bundle(assets={"../escape.png": {"sha256": "0" * 64, "size": 1,
                                          "media_type": "image/png"}}),
    ]
    controller = Path("tinyassets/onboarding/app_ui.js").read_text(encoding="utf-8")
    # Via files, not argv: the cases plus the controller are far past Windows'
    # command-line limit (WinError 206).
    work = Path(tempfile.mkdtemp())
    (work / "cases.json").write_text(json.dumps(cases), encoding="utf-8")
    (work / "probe.js").write_text(
        "const $=()=>({});const document={createElement:()=>({})};\n"
        "const window={addEventListener(){},removeEventListener(){}};\n"
        + controller
        + "const cases=require('fs').readFileSync(process.argv[2],'utf8');\n"
          "console.log(JSON.stringify(JSON.parse(cases).map(c=>AppUI.parseBundle(c).ok)));\n",
        encoding="utf-8")
    out = subprocess.run([node, str(work / "probe.js"), str(work / "cases.json")],
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stdout + out.stderr
    app_says = json.loads(out.stdout)
    assert len(app_says) == len(cases)

    disagreements = [
        (index, case, app, not python)
        for index, (case, app) in enumerate(zip(cases, app_says))
        for python in [bool(app_ui_renderability(case))]
        if app is not (not python)
    ]
    assert not disagreements, (
        "the Python mirror and the app disagree about these components "
        f"(index, case, app_ok, python_ok): {disagreements}")


def test_the_interfaces_chapter_says_version_is_the_format_version() -> None:
    """The agent-facing guidance, so the next agent does not repeat it."""
    from tinyassets.engine_mcp_server import _WRITE_GRAPH_INTERFACES_CHAPTER as chapter

    flat = " ".join(chapter.split())
    assert "FORMAT version" in flat
    assert "not a revision, a build number or a cache-buster" in flat
    assert "renderable" in flat, "and how to find out which UI is refused"
    # It must not send the agent after a field that does not exist. There IS no
    # build id in this repo, so the only allowed mention is the one that says
    # there is nowhere to put one -- naming the move it should not make.
    for sentence in re.split(r"(?<=[.:])\s", flat):
        if "build id" in sentence:
            assert "nowhere to put" in sentence, sentence
    assert "nowhere to put a build id" in flat, (
        "the guidance pre-empts inventing a cache-buster field")
