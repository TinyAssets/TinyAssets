"""The shortlist comes from the provider, and effort rides validated authority.

Founder, 2026-10-03: "the desktop app should auto grab latest short list from
provider" and "effort level controls should also be avalible". The symptom was a
model menu showing ``claude-code · opus`` while the current flagship read "model
access opt-in required" -- because ClaudeProvider declared credential custody
but no metadata protocol, so enumeration returned None and the picker fell back
to the reviewed static list, where every row is an offer to GRANT.

Real owned custody/member/serving boundaries via the native fixture; the
executor is synthetic except where a test is explicitly marked as reading the
installed CLI.
"""

import asyncio
import json
import os
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401
from tests.test_native_discovery_integration import install_discovery
from tests.test_native_model_authority import _call, native  # noqa: F401
from tests.test_native_model_discovery import (
    metadata_transport_processes,  # noqa: F401 - a fixture, requested by name below
)
from tinyassets.exceptions import ProviderAuthorityHeldError
from tinyassets.providers.claude_provider import ClaudeProvider
from tinyassets.providers.model_options import model_options_document
from tinyassets.providers.model_policy import ModelRef
from tinyassets.providers.model_preferences import ModelEffort, ModelPreferences
from tinyassets.providers.native_catalogue import NativeCatalogue, NativeModel
from tinyassets.providers.native_jsonrpc_discovery import (
    NativeControlProtocol,
    read_native_catalogue,
)
from tinyassets.providers.served_model_plan import (
    PUBLIC_LISTED_BASIS,
    prepare_owned_model_plan,
)
from tinyassets.storage.model_preferences import ModelPreferenceStore

pytestmark = pytest.mark.usefixtures("cloud_runtime")

#: The shape the installed Claude Code CLI actually answers `list_models` with,
#: captured from 2.1.288 on 2026-10-02. Alias rows, a `default` marker, no
#: reported modalities, and per-model effort -- including a model with none.
CLI_ROWS = [
    {"value": "default", "resolvedModel": "claude-opus-5-5",
     "displayName": "Default (recommended)", "supportsEffort": True,
     "supportedEffortLevels": ["low", "medium", "high", "xhigh", "max"]},
    {"value": "opus", "resolvedModel": "claude-opus-5-5", "displayName": "Opus 5.5",
     "supportsEffort": True,
     "supportedEffortLevels": ["low", "medium", "high", "xhigh", "max"]},
    {"value": "haiku", "resolvedModel": "claude-haiku-4-5-20251001",
     "displayName": "Haiku 4.5"},
    {"value": "claude-opus-4-6", "resolvedModel": "claude-opus-4-6",
     "displayName": "Opus 4.6", "supportsEffort": True,
     "supportedEffortLevels": ["low", "medium", "high", "max"]},
]


def effortful(ids, *, levels=("low", "medium", "high", "xhigh", "max"), age=0, hidden=()):
    return NativeCatalogue(
        tuple(NativeModel(name, frozenset({"text"}), name in hidden, bool(levels),
                          tuple(levels))
              for name in ids),
        ids[0] if ids else None, datetime.now(timezone.utc) - timedelta(seconds=age),
    )


def save_effort(native, ref, level):  # noqa: F811
    store = ModelPreferenceStore(native.base)
    current = store.get("owner-1", native.universe.name)
    store.save(
        "owner-1", native.universe.name, expected_generation=current.generation,
        policy=ModelPreferences("explicit", ref, (), (ModelEffort(ref, level),)),
    )


def warm_shortlist(native):  # noqa: F811
    """Run the discovery a DISPLAY read no longer runs for itself.

    #4368 moved enumeration off the picker's request path: a display read now
    serves the warm per-source catalogue and never discovers inline, so the
    refresh that used to happen *during* ``prepare_owned_model_plan`` has to
    happen before it. Same pattern as
    ``test_native_discovery_integration.test_new_account_model_reaches_picker``.

    Only the display-read tests need this. The invocation tests below go through
    ``_call``, and execution still discovers inline -- it is about to launch and
    will not build a plan from a catalogue it has not confirmed.
    """
    from tinyassets.providers.shortlist_refresh import SHORTLIST_CACHE

    SHORTLIST_CACHE.refresh_now(
        base=native.base, owner="owner-1",
        universe_id=native.universe.name, provider="codex",
    )


# --------------------------------------------------------------------------
# Ask 1: the provider's own shortlist, as NORMAL choices.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("native", ["discovered"], indirect=True)
def test_advertised_model_is_a_normal_choice_not_an_opt_in_offer(native, monkeypatch):  # noqa: F811
    """The founder's exact symptom, pinned on both sides of the split.

    An id the EXECUTOR advertised is selectable now. The same id arriving only
    from the reviewed static list is an offer to grant and says so. Asserting
    just the first half would pass with the bug, because the static list also
    makes the row appear -- only with the wrong status.
    """
    async def discover():
        return effortful(["claude-opus-5-5"])
    install_discovery(native, monkeypatch, discover)
    warm_shortlist(native)
    prepared = prepare_owned_model_plan(
        base=native.base, universe=native.universe, owner="owner-1", agent=native.agent,
        allow_empty=True,
    )
    document = model_options_document(prepared.catalog, prepared.plan, prepared.ineligible)
    row = next(r for r in document["options"]
               if r["reference"]["model_id"] == "claude-opus-5-5")

    assert row["availability_basis"] == "executor_enumerated"
    assert row["in_candidate_catalog"] is True
    assert row["reasons"] == [], "an advertised model must carry no refusal reason"
    assert all(reason["reason"] != "model_access_optin_required"
               for reason in row["reasons"])
    # The split that makes the assertion above meaningful: a reviewed-list row
    # for a DIFFERENT id is still gated, so this is not vacuously true.
    listed = [r for r in document["options"]
              if r["availability_basis"] == PUBLIC_LISTED_BASIS]
    for offer in listed:
        assert offer["in_candidate_catalog"] is False
        assert any(reason["reason"] == "model_access_optin_required"
                   for reason in offer["reasons"])


@pytest.mark.parametrize("native", ["discovered"], indirect=True)
def test_a_row_the_executor_disabled_never_becomes_a_choice(native, monkeypatch):  # noqa: F811
    """Advertised-and-unselectable must not be offered; it would fail at launch."""
    async def discover():
        return effortful(["usable-model", "withdrawn-model"], hidden=("withdrawn-model",))
    install_discovery(native, monkeypatch, discover)
    warm_shortlist(native)
    prepared = prepare_owned_model_plan(
        base=native.base, universe=native.universe, owner="owner-1", agent=native.agent,
        allow_empty=True,
    )
    enumerated = {m.model_id for m in prepared.catalog.connections[0].models
                  if m.availability_basis == "executor_enumerated"}
    assert "usable-model" in enumerated
    assert "withdrawn-model" not in enumerated


# --------------------------------------------------------------------------
# Ask 2: effort, advertised per model and carried to the provider.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("native", ["discovered"], indirect=True)
def test_only_a_model_that_advertises_effort_offers_the_control(native, monkeypatch):  # noqa: F811
    """No control where the provider reports none, and only the levels it named.

    A live Claude Code catalogue reports effort on Opus and NOT on Haiku, and
    4.6 drops a level 5.x carries, so this cannot be a per-provider flag.
    """
    async def discover():
        return NativeCatalogue(
            (NativeModel("rich-model", frozenset({"text"}), False, True,
                         ("low", "medium", "high", "xhigh", "max")),
             NativeModel("narrow-model", frozenset({"text"}), False, True,
                         ("low", "medium", "high", "max")),
             NativeModel("plain-model", frozenset({"text"}))),
            "rich-model", datetime.now(timezone.utc),
        )
    install_discovery(native, monkeypatch, discover)
    warm_shortlist(native)
    prepared = prepare_owned_model_plan(
        base=native.base, universe=native.universe, owner="owner-1", agent=native.agent,
        allow_empty=True,
    )
    document = model_options_document(prepared.catalog, prepared.plan, prepared.ineligible)
    levels = {row["reference"]["model_id"]: row.get("effort_levels")
              for row in document["options"]}
    assert levels["rich-model"] == ["low", "medium", "high", "xhigh", "max"]
    assert levels["narrow-model"] == ["low", "medium", "high", "max"]
    assert levels["plain-model"] == [], "no advertised levels means no control"


@pytest.mark.parametrize("native", ["discovered"], indirect=True)
def test_saved_effort_reaches_the_provider_invocation(native, monkeypatch):  # noqa: F811
    """End to end through the REAL authority chain, not a stubbed selection."""
    async def discover():
        return effortful(["new-account-model"])
    _seen, configs = install_discovery(native, monkeypatch, discover)
    ref = ModelRef("codex", "new-account-model")
    save_effort(native, ref, "xhigh")

    context = replace(native.context, model_selection=ref)
    assert _call(native, context).provider == "codex"
    assert configs[0].native_model_id == "new-account-model"
    assert configs[0].reasoning_effort == "xhigh"


@pytest.mark.parametrize("native", ["discovered"], indirect=True)
def test_unset_effort_leaves_the_executor_default(native, monkeypatch):  # noqa: F811
    """Absent choice is empty, never a level this platform picked.

    Asserted against a config that ALREADY carries a level, so this pins the
    security property rather than restating ModelConfig's default: an
    enumerated selection speaks for effort absolutely, and "the owner saved
    nothing" must clear an inherited value rather than let it through.
    """
    from tinyassets.providers.base import ModelConfig

    async def discover():
        return effortful(["new-account-model"])
    _seen, configs = install_discovery(native, monkeypatch, discover)
    context = replace(native.context,
                      model_selection=ModelRef("codex", "new-account-model"))
    asyncio.run(native.router.call(
        "writer", "hello", "system", operation="converse", universe_context=context,
        config=ModelConfig(reasoning_effort="max"),
    ))
    assert configs and configs[0].reasoning_effort == ""


@pytest.mark.parametrize("native", ["discovered"], indirect=True)
def test_effort_outside_the_advertised_levels_is_refused_not_dropped(native, monkeypatch):  # noqa: F811
    """Running at the default while the owner saved `max` would be a silent lie."""
    async def discover():
        return effortful(["new-account-model"], levels=("low", "medium", "high"))
    _seen, configs = install_discovery(native, monkeypatch, discover)
    ref = ModelRef("codex", "new-account-model")
    save_effort(native, ref, "max")

    context = replace(native.context, model_selection=ref)
    with pytest.raises(ProviderAuthorityHeldError):
        _call(native, context)
    assert not configs, "no turn may run at a level the model did not advertise"


@pytest.mark.parametrize("native", ["discovered"], indirect=True)
def test_caller_config_cannot_raise_a_served_turns_effort(native, monkeypatch):  # noqa: F811
    """Effort rides validated authority for the same reason the model id does."""
    async def discover():
        return effortful(["new-account-model"])
    _seen, configs = install_discovery(native, monkeypatch, discover)
    ref = ModelRef("codex", "new-account-model")
    save_effort(native, ref, "low")

    context = replace(
        native.context, model_selection=ref,
        config=replace(native.context.config, preferred_writer="claude-code"),
    )
    assert _call(native, context).provider == "codex"
    # The owner's saved level wins over anything an ordinary caller supplied.
    assert configs[0].reasoning_effort == "low"


def test_declared_model_cannot_attest_an_effort_level():
    """Without a catalogue there are no advertised levels to validate against."""
    from tinyassets.providers.native_model_selection import NativeSelection

    assert NativeSelection("codex", "typed-id").effort == ""
    with pytest.raises(ValueError, match="cannot attest an effort level"):
        NativeSelection("codex", "typed-id", effort="high")


@pytest.mark.parametrize("level", ["high", ""])
def test_enumerated_effort_evidence_round_trips_by_version(level):
    """A stored version-2 evidence row must keep parsing after effort exists."""
    from tinyassets.providers.native_model_selection import NativeSelection

    now = datetime.now(timezone.utc)
    selection = NativeSelection(
        "codex", "model", "model", "executor_enumerated",
        now.isoformat(), now.isoformat(), "digest", level,
    )
    document = selection.to_dict()
    assert document["version"] == (3 if level else 2)
    assert ("effort" in document) is bool(level)
    assert NativeSelection.from_dict(document) == selection


# --------------------------------------------------------------------------
# The control envelope, against a real child process.
# --------------------------------------------------------------------------

#: The REAL registration, not a copy of it. A hand-mirrored protocol here
#: would drift from the shipped one silently -- and it did: this was a local
#: copy, so it lacked `unsupported_error_marker` and the feature-detection
#: tests below passed against a protocol production does not use.
CLAUDE_PROTOCOL = ClaudeProvider.native_discovery_protocol


def control_peer(reply, *, noise=()):
    """A child that speaks the control envelope and rejects inference."""
    return f'''
import json, sys
noise = {list(noise)!r}
request = json.loads(sys.stdin.readline())
assert request["type"] == "control_request", request
assert request["request"]["subtype"] == "list_models", request
assert "prompt" not in request, "metadata transport must not carry inference"
for line in noise:
    print(json.dumps(line), flush=True)
print(json.dumps({{"type": "control_response", "response": {{
    "subtype": "success", "request_id": request["request_id"],
    "response": {reply!r},
}}}}), flush=True)
'''


def metadata_snapshot(universe):
    """The one launch-credential snapshot the metadata jail will bind.

    `read_native_catalogue` refuses without its owning command center and an
    exact snapshot under it (`provider_jail.metadata_view`), so a transport
    test has to stand one up or it never reaches the decoder -- a negative
    case would then pass on the confinement refusal instead of the behaviour
    it names.
    """
    snapshot = universe / ".runtime" / "provider-launch-credentials" / "metadata-test"
    snapshot.mkdir(parents=True, exist_ok=True)
    return str(snapshot)


#: Every case below that spawns a real metadata child. The transport requires
#: confinement, so without this seam each one refuses on "no OS sandbox on this
#: host" wherever bubblewrap is absent -- which is most CI runners and every
#: Windows box. `metadata_transport_processes` (tests/test_native_model_discovery.py)
#: substitutes `confine_launch` with one that still asserts the launch scope
#: and view bind to the same command center, so the protocol, the decoder and
#: the owned-process family are all real.
#:
#: It drops more than the isolation, so do not read it as "only the sandbox is
#: stubbed": the bwrap argv, the launch disk budget and bwrap's own
#: cwd/environment setup go with it, and the child runs from `/` rather than the
#: snapshot. Those live where they belong -- tests/test_native_metadata_jail.py
#: runs the real jail under `linux-jail-proof`, and
#: tests/test_native_metadata_confinement.py proves a missing, redirected or
#: foreign snapshot refuses before any process is created.
#:
#: The NEGATIVE cases need it most: they assert
#: `ProviderError("native model discovery unavailable")`, which the confinement
#: refusal also raises, so without the seam they pass on a jail that never ran
#: instead of the refusal they name.
real_metadata_child = pytest.mark.usefixtures("metadata_transport_processes")


def run_control(tmp_path, reply, *, noise=(), protocol=CLAUDE_PROTOCOL):
    return asyncio.run(read_native_catalogue(
        [sys.executable, "-u", "-c", control_peer(reply, noise=noise)],
        env=os.environ.copy(), cwd=metadata_snapshot(tmp_path),
        universe_dir=tmp_path, protocol=protocol, timeout=10,
    ))


@real_metadata_child
def test_control_envelope_reads_alias_rows_and_per_model_effort(tmp_path):
    """The real CLI shape: alias rows, a default marker, unreported modalities."""
    result = run_control(tmp_path, {"models": CLI_ROWS})

    # `default` and `opus` resolve to ONE model, so one choice is offered.
    assert [m.model_id for m in result.models] == [
        "claude-opus-5-5", "claude-haiku-4-5-20251001", "claude-opus-4-6",
    ]
    assert result.default_model_id == "claude-opus-5-5"
    by_id = {m.model_id: m for m in result.models}
    assert by_id["claude-opus-5-5"].effort_levels == (
        "low", "medium", "high", "xhigh", "max")
    assert by_id["claude-opus-4-6"].effort_levels == ("low", "medium", "high", "max")
    assert by_id["claude-haiku-4-5-20251001"].supports_effort is False
    assert by_id["claude-haiku-4-5-20251001"].effort_levels == ()
    # Unreported modalities take the transport's own text floor, so an
    # enumerated row does not read as a model that accepts nothing.
    assert all(m.input_modalities == frozenset({"text"}) for m in result.models)


@real_metadata_child
def test_control_envelope_skips_unrelated_stream_traffic(tmp_path):
    """These streams carry session/system lines before the answer."""
    result = run_control(tmp_path, {"models": [CLI_ROWS[1]]}, noise=(
        {"type": "system", "subtype": "init", "session_id": "s"},
        {"type": "system", "subtype": "hook_started", "hook_name": "x"},
        {"type": "assistant", "message": {"content": []}},
    ))
    assert [m.model_id for m in result.models] == ["claude-opus-5-5"]


@pytest.mark.parametrize("reply", [
    {"models": [{**CLI_ROWS[1], "supportedEffortLevels": []}]},
    {"models": [{**CLI_ROWS[1], "supportedEffortLevels": "high"}]},
    {"models": [{**CLI_ROWS[1], "supportedEffortLevels": [7]}]},
    # Claimed support with no levels at all: a control with unknown values.
    {"models": [{"value": "x", "resolvedModel": "x", "supportsEffort": True}]},
    {"models": [{**CLI_ROWS[1], "supportsEffort": "yes"}]},
    {"models": [{**CLI_ROWS[1], "inputModalities": "text"}]},
    {"models": [{"value": "x", "resolvedModel": "bad\nid"}]},
    # One id with two different capability claims is a protocol fault.
    {"models": [CLI_ROWS[1], {**CLI_ROWS[1], "supportedEffortLevels": ["low"]}]},
    {"models": {}},
    {},
])
@real_metadata_child
def test_malformed_control_catalogue_refuses(tmp_path, reply):
    from tinyassets.exceptions import ProviderError

    with pytest.raises(ProviderError, match="^native model discovery unavailable$"):
        run_control(tmp_path, reply)


@real_metadata_child
def test_control_error_subtype_is_not_an_empty_catalogue(tmp_path):
    """An upstream refusal must not read as "this account has no models".

    The error envelope carries an otherwise VALID empty-catalogue payload on
    purpose. Without it the refusal also satisfies the decoder's
    `response`-is-a-dict check, so the case passed even with subtype validation
    removed and proved nothing (Codex review 2026-10-04, DISAGREE_EVIDENCE).
    The subtype is now the only thing left to refuse on.
    """
    from tinyassets.exceptions import ProviderError

    script = '''
import json, sys
request = json.loads(sys.stdin.readline())
print(json.dumps({"type": "control_response", "response": {
    "subtype": "error", "request_id": request["request_id"], "error": "nope",
    "response": {"models": []},
}}), flush=True)
'''
    with pytest.raises(ProviderError, match="^native model discovery unavailable$"):
        asyncio.run(read_native_catalogue(
            [sys.executable, "-u", "-c", script], env=os.environ.copy(),
            cwd=metadata_snapshot(tmp_path), universe_dir=tmp_path,
            protocol=CLAUDE_PROTOCOL, timeout=10,
        ))


@real_metadata_child
def test_control_response_for_another_request_is_refused(tmp_path):
    from tinyassets.exceptions import ProviderError

    script = '''
import json, sys
json.loads(sys.stdin.readline())
print(json.dumps({"type": "control_response", "response": {
    "subtype": "success", "request_id": "someone-elses-request",
    "response": {"models": []},
}}), flush=True)
'''
    with pytest.raises(ProviderError, match="^native model discovery unavailable$"):
        asyncio.run(read_native_catalogue(
            [sys.executable, "-u", "-c", script], env=os.environ.copy(),
            cwd=metadata_snapshot(tmp_path), universe_dir=tmp_path,
            protocol=CLAUDE_PROTOCOL, timeout=10,
        ))


def test_control_protocol_registration_is_validated():
    """A handshake belongs to JSON-RPC; this envelope answers immediately."""
    base = dict(
        list_method="list_models", items_key="models", model_key="resolvedModel",
        default_key="value", modalities_key="inputModalities", hidden_key="disabled",
    )
    with pytest.raises(ValueError, match="takes no handshake"):
        NativeControlProtocol(**base, initialize_method="initialize")
    # Support without a levels field would admit a control of unknown values.
    with pytest.raises(ValueError, match="must name its levels field"):
        NativeControlProtocol(**base, effort_key="supportsEffort")
    with pytest.raises(ValueError):
        NativeControlProtocol(**base, cursor_key="nextCursor")


# --------------------------------------------------------------------------
# Claude Code's registration, and the installed CLI itself.
# --------------------------------------------------------------------------


def test_claude_registers_discovery_keyed_on_the_resolved_execution_id():
    """Keyed on `resolvedModel`, because an alias means different things.

    `--model opus` resolved to claude-opus-4-8 under the pinned CLI and to
    claude-opus-5-5 under a newer one. A stored preference must mean one model.
    """
    from tinyassets.providers.claude_provider import ClaudeProvider

    protocol = ClaudeProvider.native_discovery_protocol
    assert type(protocol) is NativeControlProtocol
    assert protocol.list_method == "list_models"
    assert protocol.model_key == "resolvedModel"
    assert protocol.effort_key == "supportsEffort"
    assert ClaudeProvider.native_credential_service == "claude"
    assert "--input-format" in ClaudeProvider.native_metadata_arguments
    # Never --bare: it forces API-key auth and would fail a subscription read.
    assert "--bare" not in ClaudeProvider.native_metadata_arguments


def test_claude_reports_enumeration_support_on_the_read_surface(monkeypatch):
    """The fact a client reads to know a shortlist exists at all.

    This is the assertion that was false before: `_native_enumeration` returned
    "unknown" for claude-code, which is why the picker had nothing but the
    reviewed static list to show.
    """
    from tinyassets.api.model_options import _native_enumeration
    from tinyassets.providers.claude_provider import ClaudeProvider
    from tinyassets.providers.router import ProviderRouter

    router = ProviderRouter({"claude-code": ClaudeProvider()})
    monkeypatch.setattr("tinyassets.providers.call.get_provider_router", lambda: router)
    assert _native_enumeration("claude-code") == "supported"


@pytest.mark.parametrize("effort,expected", [
    ("xhigh", ["--effort", "xhigh"]),
    ("MAX", ["--effort", "max"]),
    ("", []),
    (None, []),
])
def test_claude_effort_flag_is_the_cli_setting_not_a_prompt_hint(effort, expected):
    from tinyassets.providers.claude_provider import _effort_args

    assert _effort_args(effort) == expected


def test_claude_argv_carries_the_effort_from_its_model_config(monkeypatch):
    """The flag must land in argv, not merely in a config object."""
    import tinyassets.providers.claude_provider as module
    from tinyassets.providers.base import ModelConfig

    captured = {}

    async def fake_spawn(cmd, **kwargs):
        captured["cmd"] = cmd
        raise RuntimeError("stop before inference")

    monkeypatch.setattr(module, "aspawn_owned", fake_spawn)
    monkeypatch.setattr(module, "_resolve_claude_cmd", lambda: (["claude"], False))
    config = ModelConfig(native_model_id="claude-opus-5-5", reasoning_effort="xhigh")
    with pytest.raises(RuntimeError, match="stop before inference"):
        asyncio.run(module.ClaudeProvider().complete("hi", "", config))

    cmd = captured["cmd"]
    assert "--effort" in cmd
    assert cmd[cmd.index("--effort") + 1] == "xhigh"
    assert cmd[cmd.index("--model") + 1] == "claude-opus-5-5"


@pytest.mark.skipif(
    not os.environ.get("TINYASSETS_LIVE_CLI_DISCOVERY"),
    reason="spawns the installed Claude Code CLI, so it is version-dependent and "
           "not hermetic; owner=claude-code "
           "runs-in=a host with the Claude Code CLI installed and "
           "TINYASSETS_LIVE_CLI_DISCOVERY=1 (not CI)",
)
@real_metadata_child
def test_installed_cli_advertises_a_shortlist_with_effort(tmp_path):
    """The claim this whole change rests on, against the real binary.

    Opt-in because it spawns the installed CLI and depends on its version.
    Verified by hand on 2.1.288, 2026-10-02: 12 rows in, 11 models out,
    default claude-opus-5-5, effort on every model except haiku.
    """
    import shutil

    from tinyassets.providers.claude_provider import (
        _METADATA_ARGUMENTS,
        ClaudeProvider,
        _resolve_claude_cmd,
    )

    if shutil.which("claude") is None:
        pytest.skip("claude CLI is not installed")
    base_cmd, use_shell = _resolve_claude_cmd()
    assert not use_shell, "metadata discovery requires a direct executable"
    catalogue = asyncio.run(read_native_catalogue(
        [*base_cmd, *_METADATA_ARGUMENTS],
        protocol=ClaudeProvider.native_discovery_protocol,
        env=os.environ.copy(), cwd=metadata_snapshot(tmp_path),
        universe_dir=tmp_path,
        spawn_kwargs=ClaudeProvider.native_process_options(), timeout=60,
    ))
    assert catalogue.models, "the CLI advertised no models"
    assert catalogue.default_model_id
    assert any(m.supports_effort and m.effort_levels for m in catalogue.models)
    # Resolved execution ids, never the aliases.
    assert all(m.model_id not in {"default", "opus", "sonnet", "haiku", "fable"}
               for m in catalogue.models)
    # Deduped: one row per execution id.
    assert len({m.model_id for m in catalogue.models}) == len(catalogue.models)
    print(json.dumps({
        "default": catalogue.default_model_id,
        "models": [[m.model_id, list(m.effort_levels)] for m in catalogue.models],
    }, indent=1))


@pytest.mark.parametrize("native", ["explicit"], indirect=True)
def test_a_declared_native_selection_does_not_clobber_a_node_effort(native, monkeypatch):  # noqa: F811
    """A workflow node's own effort must survive the served authority path.

    A node declares effort for itself (`api/branches.py`, reaching the provider
    as ModelConfig.reasoning_effort). A DECLARED native selection carries no
    level -- it has no advertised list to validate one against -- so overwriting
    the config from it unconditionally would silently downgrade every node that
    runs on a native source with an explicit model id.
    """
    configs = []
    monkeypatch.setattr("tinyassets.providers.call.get_provider_router", lambda: native.router)
    complete = native.provider.complete

    async def record(prompt, system, config, **kwargs):
        configs.append(config)
        return await complete(prompt, system, config, **kwargs)

    monkeypatch.setattr(native.provider, "complete", record)
    context = replace(native.context,
                      model_selection=ModelRef("codex", "future-native-model"))
    from tinyassets.providers.base import ModelConfig

    asyncio.run(native.router.call(
        "writer", "hello", "system", operation="converse", universe_context=context,
        config=ModelConfig(reasoning_effort="minimal"),
    ))
    assert configs, "the provider was never invoked"
    assert configs[0].native_model_id == "future-native-model"
    assert configs[0].reasoning_effort == "minimal", (
        "a declared selection with no level erased the node's own effort"
    )


# --------------------------------------------------------------------------
# Codex round 1 findings (ADAPT), each pinned.
# --------------------------------------------------------------------------


def test_codex_registers_its_own_effort_shape():
    """Codex advertises effort too, in a different shape -- finding 3.

    Live `model/list` from the installed Codex app-server reports
    `supportedReasoningEfforts` as OBJECTS with no boolean support gate, and
    includes `ultra`, a level Claude Code does not offer. Registering only
    Claude's shape left every Codex row with no levels, so the founder's
    explicit ask (codex `model_reasoning_effort`) had no control at all.
    """
    from tinyassets.providers.codex_provider import CodexProvider

    protocol = CodexProvider.native_discovery_protocol
    assert protocol.effort_levels_key == "supportedReasoningEfforts"
    assert protocol.effort_level_key == "reasoningEffort"
    assert protocol.effort_key is None, "Codex implies support by listing levels"
    # The real row shape, captured from the installed app-server.
    supports, levels = protocol.row_effort({
        "model": "gpt-6.1-sol",
        "supportedReasoningEfforts": [
            {"reasoningEffort": "low", "description": "Fast responses"},
            {"reasoningEffort": "xhigh", "description": "Extra high"},
            {"reasoningEffort": "ultra", "description": "Maximum"},
        ],
        "defaultReasoningEffort": "low",
    })
    assert supports is True
    assert levels == ("low", "xhigh", "ultra")


def test_a_source_with_no_boolean_gate_and_no_levels_has_no_control():
    """Absence is a truthful "no control", not a fault, without a gate."""
    from tinyassets.providers.codex_provider import CodexProvider

    protocol = CodexProvider.native_discovery_protocol
    assert protocol.row_effort({"model": "m"}) == (False, ())
    assert protocol.row_effort({"model": "m", "supportedReasoningEfforts": []}) == (False, ())


@pytest.mark.parametrize("row", [
    {"model": "m", "supportedReasoningEfforts": ["low"]},          # not objects
    {"model": "m", "supportedReasoningEfforts": [{"other": "low"}]},
    {"model": "m", "supportedReasoningEfforts": [{"reasoningEffort": 7}]},
    {"model": "m", "supportedReasoningEfforts": "low"},
])
def test_malformed_codex_effort_entries_refuse(row):
    from tinyassets.providers.codex_provider import CodexProvider

    with pytest.raises(ValueError):
        CodexProvider.native_discovery_protocol.row_effort(row)


def test_a_gated_source_claiming_support_without_levels_still_refuses():
    """Claude's shape keeps the stricter rule: a claim must name its levels."""
    assert CLAUDE_PROTOCOL.effort_key == "supportsEffort"
    with pytest.raises(ValueError):
        CLAUDE_PROTOCOL.row_effort({"resolvedModel": "m", "supportsEffort": True})
    with pytest.raises(ValueError):
        CLAUDE_PROTOCOL.row_effort({"resolvedModel": "m", "supportsEffort": True,
                                    "supportedEffortLevels": []})
    assert CLAUDE_PROTOCOL.row_effort({"resolvedModel": "m"}) == (False, ())


@pytest.mark.parametrize("native", ["discovered"], indirect=True)
def test_a_withdrawn_id_does_not_return_as_a_learned_candidate(native, monkeypatch):  # noqa: F811
    """Finding 4: filtering enumeration alone let the id back in elsewhere.

    The candidate sources dedupe against the rows that were KEPT, so a hidden
    id was absent from the choices and then reappeared as a reviewed-list or
    own-history candidate. A source saying "not this one" outranks our own
    record of it.
    """
    async def discover():
        return effortful(["usable-model", "withdrawn-model"], hidden=("withdrawn-model",))
    install_discovery(native, monkeypatch, discover)
    # The reviewed list vouches for the withdrawn id AND for an unrelated one,
    # so this cannot pass by the list contributing nothing at all.
    monkeypatch.setattr(
        "tinyassets.providers.public_model_lists.newest_listed_cached",
        lambda kind: ("withdrawn-model", "some-other-id"),
    )
    warm_shortlist(native)
    prepared = prepare_owned_model_plan(
        base=native.base, universe=native.universe, owner="owner-1", agent=native.agent,
        allow_empty=True,
    )
    offered = {m.model_id for m in prepared.catalog.connections[0].models}
    assert "usable-model" in offered
    assert "some-other-id" in offered, "the reviewed list must still contribute"
    assert "withdrawn-model" not in offered, (
        "a withdrawn id came back through a candidate source"
    )


def test_select_refuses_a_withdrawn_model_at_launch():
    """Hiding a choice and refusing to run it are two separate guarantees."""
    from pathlib import Path

    from tinyassets.credential_vault import LLMCredentialCustodyReference
    from tinyassets.providers.native_discovery import NativeDiscoverySnapshot

    custody = LLMCredentialCustodyReference(
        "ref", "owner-1", "home", "codex", 1, "digest", "record-digest")
    now = datetime.now(timezone.utc)
    universe = Path.cwd().resolve()
    snapshot = NativeDiscoverySnapshot(
        "codex", "owner-1", universe, custody, now, now,
        NativeCatalogue((NativeModel("withdrawn", frozenset({"text"}), True),), None, now),
    )
    from tinyassets.provider_assignment_manifest import ModelAccess

    with pytest.raises(PermissionError, match="does not match current model authority"):
        snapshot.select(provider="codex", owner="owner-1", universe=universe,
                        custody=custody, model_id="withdrawn",
                        access=ModelAccess("discovered"))


# --------------------------------------------------------------------------
# Feature detection: production runs an older CLI than `list_models` needs.
# --------------------------------------------------------------------------


@real_metadata_child
def test_an_older_cli_reads_as_unsupported_not_broken(tmp_path):
    """The path production takes today, until the CLI pin moves (#4351).

    2.1.183 predates `list_models`. It answers an explicit "Unsupported
    control request subtype" in 0.6s (measured 2026-10-02), which is a
    truthful "enumeration is unknown here" -- NOT a fault. Reading it as a
    failure would tell the owner their source is broken when it is merely old.

    Detected by ASKING. A version floor would be the static provider-release
    table this repo refuses, and it would also go stale on every CLI release.
    """
    from tinyassets.providers.native_jsonrpc_discovery import NativeMetadataUnsupported

    script = '''
import json, sys
request = json.loads(sys.stdin.readline())
print(json.dumps({"type": "control_response", "response": {
    "subtype": "error", "request_id": request["request_id"],
    "error": "Unsupported control request subtype: list_models",
}}), flush=True)
'''
    with pytest.raises(NativeMetadataUnsupported):
        asyncio.run(read_native_catalogue(
            [sys.executable, "-u", "-c", script], env=os.environ.copy(),
            cwd=metadata_snapshot(tmp_path), universe_dir=tmp_path,
            protocol=CLAUDE_PROTOCOL, timeout=10,
        ))


def test_unsupported_enumeration_becomes_the_unknown_contract(monkeypatch, tmp_path):
    """`enumerate_models` returns None for it, like an executor with no protocol.

    That is what makes the source report `native_enumeration_unsupported` and
    keep its own default usable, which is the pre-change behaviour exactly.
    """
    from tinyassets.providers import base as base_module
    from tinyassets.providers.claude_provider import ClaudeProvider
    from tinyassets.providers.native_jsonrpc_discovery import NativeMetadataUnsupported

    async def refuse(*args, **kwargs):
        raise NativeMetadataUnsupported("native model enumeration unsupported")

    monkeypatch.setattr(base_module, "read_native_catalogue", refuse, raising=False)
    monkeypatch.setattr(
        "tinyassets.providers.native_jsonrpc_discovery.read_native_catalogue", refuse,
    )
    snapshot = tmp_path / "snap"
    snapshot.mkdir()
    assert asyncio.run(ClaudeProvider().enumerate_models(
        universe_dir=tmp_path, credential_snapshot_dir=snapshot,
    )) is None


@real_metadata_child
def test_the_unsupported_answer_survives_the_real_transport(monkeypatch, tmp_path):
    """Through the REAL transport, not a stub, into `enumerate_models`.

    The test above replaces `read_native_catalogue`, so it cannot see the
    transport widening the answer. It did: the sanitizing handler catches
    `ProviderError`, which `NativeMetadataUnsupported` subclasses, so an
    executor's truthful "I do not implement this" came back as a generic
    failure and the honest unknown was lost (Codex review 2026-10-04,
    DISAGREE_EVIDENCE). Only a real child answering the real decoder proves it.
    """
    from tinyassets.providers.claude_provider import ClaudeProvider

    script = '''
import json, sys
request = json.loads(sys.stdin.readline())
print(json.dumps({"type": "control_response", "response": {
    "subtype": "error", "request_id": request["request_id"],
    "error": "Unsupported control request subtype: list_models",
}}), flush=True)
'''
    monkeypatch.setattr(
        ClaudeProvider, "native_command_resolver",
        staticmethod(lambda: ([sys.executable, "-u", "-c", script], False)))
    monkeypatch.setattr(ClaudeProvider, "native_metadata_arguments", ())
    snapshot = metadata_snapshot(tmp_path)
    assert asyncio.run(ClaudeProvider().enumerate_models(
        universe_dir=tmp_path, credential_snapshot_dir=Path(snapshot),
    )) is None


def test_a_real_failure_is_still_a_failure_not_an_unknown():
    """Only the EXPLICIT unsupported answer is downgraded.

    A generic error must stay an error: reading every failure as "unsupported"
    would hide a genuinely broken source behind a benign-looking reason.
    """
    from tinyassets.providers.native_jsonrpc_discovery import NativeMetadataUnsupported

    for text in ("internal error", "not signed in", "rate limited"):
        with pytest.raises(ValueError):
            CLAUDE_PROTOCOL.decode_response({
                "type": "control_response",
                "response": {"subtype": "error", "request_id": "t", "error": text},
            }, "t")
    # ...and the marker is matched case-insensitively on the real wording.
    with pytest.raises(NativeMetadataUnsupported):
        CLAUDE_PROTOCOL.decode_response({
            "type": "control_response",
            "response": {"subtype": "error", "request_id": "t",
                         "error": "UNSUPPORTED CONTROL REQUEST subtype: list_models"},
        }, "t")


def test_claude_declares_the_unsupported_marker():
    from tinyassets.providers.claude_provider import ClaudeProvider

    assert (ClaudeProvider.native_discovery_protocol.unsupported_error_marker
            == "unsupported control request")


def test_a_protocol_without_a_marker_treats_every_error_as_an_error():
    """Opt-in: codex's JSON-RPC envelope has no such concept and keeps none."""
    from tinyassets.providers.codex_provider import CodexProvider

    assert getattr(
        CodexProvider.native_discovery_protocol, "unsupported_error_marker", None,
    ) is None
