"""A serving universe is never told it has no serving provider.

App thread, 2026-09-02. The founder's universe had a ready provider
assignment and a serving binding (chat and runs worked), and `get_status`
kept saying:

    consumer_pump: no_serving_runtime -- "This universe has no serving
    provider selected ... Choose one (registering a provider is not
    selecting it)."

Tiny planned around it ("cloud automation is not fully ready ... I do not
have the surface that flips the universe onto one"), and the app's heal
re-fired on every page load because it keys on that reason. The reason was
wrong: the consumer records `no_serving_runtime` whenever the fleet-era
executor selects no daemon, which since the fleet retirement is every
universe, serving or not.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from types import SimpleNamespace

import pytest

import tinyassets.provider_serving_binding  # noqa: F401 - bind its real imports BEFORE any patch below
from tinyassets.consumer_reason_actions import consumer_next_action
from tinyassets.control_plane.tick import AssignedQueueConsumer


def _consumer(tmp_path):
    """Only `base_path` is read by `_no_runtime_reason`; no executor needed."""
    return SimpleNamespace(
        base_path=tmp_path,
        _no_runtime_reason=lambda universe_id: AssignedQueueConsumer._no_runtime_reason(
            SimpleNamespace(base_path=tmp_path), universe_id,
        ),
    )


def _ready(monkeypatch) -> None:
    monkeypatch.setattr(
        "tinyassets.provider_assignment.load_provider_assignment",
        lambda base, universe_id: SimpleNamespace(
            state="ready", provider="codex", owner_user_id="alice",
        ),
    )


def _bindings(base, *statuses: str, created_by: str = "alice") -> None:
    """Exercise the real owner/status-filtered query, not a retired import."""
    from tests.test_open_serving_bind import _agent_definition
    from tinyassets.commons_catalog import (
        _agent_connect,
        create_binding,
        publish_definition,
        set_binding_serving_in_transaction,
    )

    definition = publish_definition(base, author_id=created_by, payload=_agent_definition())
    for index, status in enumerate(statuses):
        binding = create_binding(
            base, universe_id="u-1", created_by=created_by,
            definition_id=definition["agent_definition_id"],
            payload={"schema_version": 1, "name": f"Agent {index}", "role": "writer"},
        )
        if status == "serving":
            with _agent_connect(base) as conn:
                conn.execute("BEGIN IMMEDIATE")
                set_binding_serving_in_transaction(
                    conn, universe_id="u-1", binding_id=binding["agent_binding_id"],
                    expected_revision=binding["revision"], owner_user_id=created_by,
                    enabled=True,
                )
                conn.commit()


def test_a_SERVING_universe_is_not_told_to_choose_a_provider(tmp_path, monkeypatch):
    _ready(monkeypatch)
    _bindings(tmp_path, "configured", "serving")
    # Serving: nothing to report (the fleet-era "legacy_control_tasks_parked"
    # retired with its pump; the consumer records an `ok:` row status hides).
    assert _consumer(tmp_path)._no_runtime_reason("u-1") == ""


def test_TWO_serving_bindings_are_not_serving_because_admission_refuses_them(tmp_path, monkeypatch):
    """Codex round 2: admission requires exactly one serving binding for the
    owner; the status must use the same predicate, not "any serving"."""
    _ready(monkeypatch)
    _bindings(tmp_path, "serving", "serving")
    assert _consumer(tmp_path)._no_runtime_reason("u-1") == "no_serving_runtime"


def test_a_serving_binding_created_by_someone_else_does_not_count(tmp_path, monkeypatch):
    _ready(monkeypatch)
    _bindings(tmp_path, "serving", created_by="mallory")
    assert _consumer(tmp_path)._no_runtime_reason("u-1") == "no_serving_runtime"


def test_a_ready_assignment_with_serving_DISABLED_is_still_not_serving(tmp_path, monkeypatch):
    """Codex: disabling serving flips the binding to `configured` and leaves
    the assignment ready; runs then fail provider_not_bound, so the honest
    reason is still the unserved one and the app heal must still fire."""
    _ready(monkeypatch)
    _bindings(tmp_path, "configured")
    assert _consumer(tmp_path)._no_runtime_reason("u-1") == "no_serving_runtime"


@pytest.mark.parametrize("assignment", [
    None,
    SimpleNamespace(state="pending", provider="codex", owner_user_id="alice"),
])
def test_a_universe_without_a_ready_assignment_still_hears_no_serving_runtime(
    tmp_path, monkeypatch, assignment,
):
    monkeypatch.setattr(
        "tinyassets.provider_assignment.load_provider_assignment",
        lambda base, universe_id: assignment,
    )
    _bindings(tmp_path, "serving")
    assert _consumer(tmp_path)._no_runtime_reason("u-1") == "no_serving_runtime"


def test_an_unreadable_assignment_is_reported_as_not_serving_and_logged(
    tmp_path, monkeypatch, caplog,
):
    import logging

    def _boom(base, universe_id):
        raise RuntimeError("database is locked")

    monkeypatch.setattr("tinyassets.provider_assignment.load_provider_assignment", _boom)
    with caplog.at_level(logging.ERROR):
        assert _consumer(tmp_path)._no_runtime_reason("u-1") == "no_serving_runtime"
    assert "could not establish whether u-1 is serving" in caplog.text


def test_the_new_reason_says_the_universe_IS_serving_in_words_a_user_can_use():
    """No 'fleet', no 'executor' (founder, 2026-09-02: the fleet was old
    spaghetti; users build and run whatever graphs they want, when they want).
    And it must not claim automations do not fire: this loop submits due
    automations, and schedules and subscriptions fire through the run path
    (Codex on the first cut); the founder's own automation completed on
    2026-08-31 (run c78d10128b5a42d0)."""
    action = consumer_next_action("legacy_control_tasks_parked")
    lowered = action.lower()
    assert "is serving" in lowered
    assert "automations and schedules fire" in lowered
    assert "subscription" not in lowered, (
        "non-Source subscriptions are stored but never fire; do not claim "
        "them (docs/concerns/2026-09-02-non-source-event-subscriptions-"
        "never-fire.md)"
    )
    assert "no serving provider selected" not in lowered
    assert "choose one" not in lowered
    assert "do not fire" not in lowered
    assert "fleet" not in lowered and "executor" not in lowered


# The wiring is pinned by tests/test_automations.py
# ::test_a_poll_beats_for_a_serving_universe_with_no_runtime_at_all, which
# drives the real poll against a universe with a READY assignment and now
# expects `legacy_control_tasks_parked`.


# ------------------------------------------------ the app heal keys on the truth


_NODE = shutil.which("node")


@pytest.mark.skipif(_NODE is None, reason="node is not installed")
def test_the_app_heal_does_not_fire_on_the_new_reason():
    """The heal must keep firing for `no_serving_runtime` and stay quiet for a
    universe that is serving but has no background executor."""
    from tinyassets.onboarding import render_app_html

    html, _csp = render_app_html()

    def fn(name: str) -> str:
        start = html.index(f"function {name}(")
        if html[max(0, start - 6):start] == "async ":
            start -= 6
        i = html.index("{", start)
        depth = 0
        for j in range(i, len(html)):
            if html[j] == "{":
                depth += 1
            elif html[j] == "}":
                depth -= 1
                if depth == 0:
                    return html[start:j + 1]
        raise AssertionError(name)

    script = "\n".join([
        "const posts = [];",
        "globalThis.fetch = async (url, opts) => { posts.push(JSON.parse(opts.body));"
        " return {json: async () => ({serving: {status: 'serving'}})}; };",
        "globalThis.authHeaders = () => ({});",
        "globalThis.appendMessage = () => {};",
        "let servingHealAttempted = false;",
        # `serviceLabel` reads a module-level table, so lift it too or the whole
        # bundle throws ReferenceError before a single assertion runs.
        re.search(r"const SERVICE_LABELS=\{.*?\};", html, re.S).group(0),
        fn("serveOn"), fn("serviceLabel"), fn("unservedCandidates"), fn("healServing"),
        "const status = (reason) => ({active_host: {llm_endpoint_bound: 'codex'},"
        " supervisor_liveness: {epoch2_operational: {consumer_pump: [{reason}]},"
        " provider_auth: {writers: {codex: {status: 'ok'}}}}});",
        "(async () => {",
        "  await healServing(status(process.argv[process.argv.length - 1]));",
        "  console.log(JSON.stringify({posts, attempted: servingHealAttempted}));",
        "})();",
    ])
    quiet = json.loads(subprocess.run(
        [_NODE, "-e", script, "--", "legacy_control_tasks_parked"],
        capture_output=True, text=True, timeout=30, check=True,
    ).stdout.strip().splitlines()[-1])
    loud = json.loads(subprocess.run(
        [_NODE, "-e", script, "--", "no_serving_runtime"],
        capture_output=True, text=True, timeout=30, check=True,
    ).stdout.strip().splitlines()[-1])
    assert quiet == {"posts": [], "attempted": False}, quiet
    assert loud["posts"] == [{"service": "codex"}], loud
