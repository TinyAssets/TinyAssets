"""Reading the main process's cached provenance observation back.

OpenSpec change ``cloud-only-runtime-admission``, tasks 4/5 (record-only slice).

The gap these cover: the startup observation was only *logged*, so nothing
proved the main serving process had cached a verdict at all — and the hosted
preflight's metadata read happens in a different, short-lived process, so it is
not evidence about this one. The readback rides the existing authenticated
``/mcp/pulse`` for the canary principal.

Fixture-only. No real metadata service, no network, no live surface: every probe
here is an injected reader, an injected observation object, or a stubbed pulse
response.

What each test is defending:

* a health ``GET`` must never become the thing that resolves provenance;
* a cached verdict is stable, and unobserved/failed/inherited is an explicit
  ``unknown`` that is never smoothed into ``CLOUD``;
* the field is canary-only and widens no auth rule;
* the reporter prints allowlisted typed values and leaks no identifier;
* ``--report-provenance`` changes no exit code of the Hard Rule 14 gate.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from tinyassets import platform_runtime_provenance as prov
from tinyassets.auth import middleware as mw
from tinyassets.auth.provider import DEV_USER_ENV, DevAuthProvider

REPO_ROOT = Path(__file__).resolve().parent.parent

_CANARY_TOKEN = "c" * 40
_USER_TOKEN = "u" * 40

#: A plausible droplet id. Every leak assertion below greps the rendered output
#: for this exact string — the sanitized shape must never carry one.
_SECRET_INSTANCE_ID = "412903887"


def _cloud() -> prov.RuntimeProvenance:
    return prov.RuntimeProvenance(
        verdict=prov.CLOUD,
        reason="instance_match",
        metadata_reachable=True,
        expected_identity_prepared=True,
    )


def _counting_resolver(calls: list[int]):
    def resolve() -> prov.RuntimeProvenance:
        calls.append(1)
        return _cloud()

    return resolve


# --------------------------------------------------------------------------
# the peek itself: non-mutating, never resolves
# --------------------------------------------------------------------------


def test_peek_on_an_unobserved_process_resolves_nothing() -> None:
    calls: list[int] = []
    observation = prov.ProcessProvenanceObservation(_counting_resolver(calls))

    assert observation.peek() is None
    assert observation.resolved is False
    assert calls == []


def test_peek_never_initializes_the_cache_for_a_later_observer() -> None:
    """A peek must not leave the object in a state the resolver skips."""
    calls: list[int] = []
    observation = prov.ProcessProvenanceObservation(_counting_resolver(calls))

    observation.peek()
    observation.peek()
    assert calls == []

    assert observation.observe().verdict == prov.CLOUD
    assert calls == [1]


def test_cached_verdict_is_stable_across_startup_and_two_reads() -> None:
    calls: list[int] = []
    observation = prov.ProcessProvenanceObservation(_counting_resolver(calls))

    startup = observation.observe()
    first = observation.peek()
    second = observation.peek()

    assert first is startup and second is startup
    assert calls == [1], "a read must never re-resolve"


def test_peek_refuses_a_parent_observation_after_a_pid_change(monkeypatch) -> None:
    calls: list[int] = []
    monkeypatch.setattr(os, "getpid", lambda: 101)
    observation = prov.ProcessProvenanceObservation(_counting_resolver(calls))
    observation.observe()

    monkeypatch.setattr(os, "getpid", lambda: 202)
    assert observation.peek() is None, "an inherited result is not this process's"
    assert calls == [1], "refusing an inherited result must not resolve either"

    # ...and the refusal is non-mutating: a read does not decide what a later
    # observe() has to redo.
    monkeypatch.setattr(os, "getpid", lambda: 101)
    assert observation.peek() is not None
    assert calls == [1]


def test_unobserved_state_is_explicit_unknown_and_not_cloud() -> None:
    fields = prov.sanitized_peek_fields(None)

    assert fields["verdict"] == prov.UNKNOWN
    assert fields["verdict"] != prov.CLOUD
    assert fields["observed"] is False
    assert fields["reason"] == "not_observed"
    assert fields["enforced"] is True
    assert fields["mode"] == "application_admission"


def test_observed_fields_are_sanitized_and_carry_no_identifier() -> None:
    fields = prov.sanitized_peek_fields(_cloud())

    assert fields["verdict"] == prov.CLOUD
    assert fields["observed"] is True
    assert fields["enforced"] is True
    assert set(fields) == {
        "verdict", "reason", "observed", "metadata_reachable",
        "expected_identity_prepared", "enforced", "mode",
    }
    rendered = json.dumps(fields)
    assert _SECRET_INSTANCE_ID not in rendered
    assert "169.254" not in rendered


def test_a_dead_reader_that_records_nothing_refuses_instead_of_raising(monkeypatch) -> None:
    """`finally: finished.set()` also runs for a BaseException in the reader.

    The result list is then empty and indexing it would turn a startup
    observation into an IndexError.

    Capture the intentional thread failure and wait for its hook, so it cannot
    leak a warning into the following test after finished.set() wakes the caller.
    """
    failure_reported = threading.Event()
    failures = []

    def record_failure(args):
        failures.append(args.exc_value)
        failure_reported.set()

    monkeypatch.setattr(threading, "excepthook", record_failure)

    class DyingOpener:
        def open(self, _request, timeout):
            raise BaseException("reader died")  # noqa: TRY002 - the point

    result = prov.read_metadata_instance_id(opener=DyingOpener(), timeout=0.2)
    assert result.ok is False
    assert result.reason == "metadata_probe_failed"
    assert failure_reported.wait(1)
    assert len(failures) == 1 and str(failures[0]) == "reader died"


# --------------------------------------------------------------------------
# the pulse readback: canary-only, resolves nothing
# --------------------------------------------------------------------------


@pytest.fixture
def client(monkeypatch, tmp_path):
    from starlette.testclient import TestClient

    from tinyassets.universe_server import create_streamable_http_app

    mw.set_provider(DevAuthProvider(user_id="dev-tests"))
    mw.auth_middleware(None)
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv(DEV_USER_ENV, "operator-app")
    (tmp_path / ".router-credentials.json").write_text(
        json.dumps({"TINYASSETS_WIKI_CANARY_TOKEN": _CANARY_TOKEN})
    )
    # Serving startup and origin ingress now ADMIT (change tasks 7/8), so this
    # fixture must state the runtime it is testing against instead of letting
    # the lifespan perform a real link-local metadata read and refuse. Injected
    # module-locally through the existing observation seam — never an env flag,
    # and it confers no production authority.
    _admitted = prov.ProcessProvenanceObservation(
        resolver=lambda: prov.RuntimeProvenance(
            prov.CLOUD, "instance_match", True, True
        )
    )
    _admitted.observe()
    monkeypatch.setattr(prov, "_PROCESS_OBSERVATION", _admitted)
    try:
        with TestClient(create_streamable_http_app()) as test_client:
            yield test_client
    finally:
        mw.set_provider(DevAuthProvider(user_id="dev-tests"))
        mw.auth_middleware(None)


def _canary_pulse(client):
    return client.get("/mcp/pulse", headers={"Authorization": f"Bearer {_CANARY_TOKEN}"})


def test_pulse_readback_calls_no_resolver_and_opens_no_socket(client, monkeypatch):
    resolver_calls: list[int] = []
    reader_calls: list[int] = []

    def forbidden_resolver(**_kwargs):
        resolver_calls.append(1)
        raise AssertionError("a health GET must never resolve provenance")

    def forbidden_reader(**_kwargs):
        reader_calls.append(1)
        raise AssertionError("a health GET must never read metadata")

    monkeypatch.setattr(prov, "resolve_platform_runtime_provenance", forbidden_resolver)
    monkeypatch.setattr(prov, "read_metadata_instance_id", forbidden_reader)
    monkeypatch.setattr(
        prov, "_PROCESS_OBSERVATION", prov.ProcessProvenanceObservation(forbidden_resolver),
    )

    response = _canary_pulse(client)

    # Updated for the origin backstop (change task 7): an unobserved process no
    # longer *serves* an unknown readback — it refuses at the origin before the
    # handler. The property this test exists for is unchanged and still asserted:
    # the request resolved nothing and opened no socket on its way to refusal.
    assert response.status_code == 503, response.status_code
    assert prov.PLATFORM_NOT_CLOUD_REASON in response.text
    assert resolver_calls == [] and reader_calls == []
    assert "platform_runtime_provenance" not in response.text


def test_pulse_reports_the_cached_verdict_without_re_resolving(client, monkeypatch):
    calls: list[int] = []
    observation = prov.ProcessProvenanceObservation(_counting_resolver(calls))
    observation.observe()
    monkeypatch.setattr(prov, "_PROCESS_OBSERVATION", observation)

    first = _canary_pulse(client).json()["platform_runtime_provenance"]
    second = _canary_pulse(client).json()["platform_runtime_provenance"]

    assert first == second
    assert first["verdict"] == prov.CLOUD and first["observed"] is True
    assert first["enforced"] is True, "application admission, not custody proof"
    assert first["mode"] == "application_admission"
    assert calls == [1], "startup resolved once; two reads resolved nothing"


def test_the_provenance_field_is_canary_only(client, monkeypatch):
    observation = prov.ProcessProvenanceObservation(_counting_resolver([]))
    observation.observe()
    monkeypatch.setattr(prov, "_PROCESS_OBSERVATION", observation)

    # An ordinary authenticated principal keeps exactly the fields it had.
    user = client.get("/mcp/pulse", headers={"Authorization": f"Bearer {_USER_TOKEN}"})
    assert user.status_code == 200
    assert set(user.json()) == {"git_sha", "image_tag", "deployed_at", "uptime_seconds"}
    assert "platform_runtime_provenance" not in user.text

    # Unauthenticated is still rejected before the endpoint runs.
    assert client.get("/mcp/pulse").status_code == 401

    # The canary, and only the canary, sees it.
    assert "platform_runtime_provenance" in _canary_pulse(client).json()


def test_pulse_readback_leaks_no_identifier(client, monkeypatch):
    observation = prov.ProcessProvenanceObservation(_counting_resolver([]))
    observation.observe()
    monkeypatch.setattr(prov, "_PROCESS_OBSERVATION", observation)

    body = _canary_pulse(client).text
    assert _SECRET_INSTANCE_ID not in body
    assert "169.254" not in body
    assert "platform-expected-instance" not in body


# --------------------------------------------------------------------------
# the reporter: allowlisted, typed, and exit-semantics-preserving
# --------------------------------------------------------------------------


def load_gate():
    path = REPO_ROOT / "scripts" / "deployed_sha.py"
    spec = importlib.util.spec_from_file_location("deployed_sha_readback", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def repo(tmp_path_factory):
    root = tmp_path_factory.mktemp("provenance-readback-repo")

    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True, check=True,
        ).stdout.strip()

    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "T")
    (root / "a.txt").write_text("one", encoding="utf-8")
    git("add", ".")
    git("commit", "-q", "-m", "first")
    parent = git("rev-parse", "HEAD")
    (root / "a.txt").write_text("two", encoding="utf-8")
    git("add", ".")
    git("commit", "-q", "-m", "second")
    return {"root": root, "head": git("rev-parse", "HEAD"), "parent": parent}


def _receipt(sha, **extra):
    state = {"git_sha": sha, "image_tag": f"ghcr.io/x/tinyassets-daemon:{sha[:12]}"}
    state.update(extra)
    return state


@pytest.mark.parametrize("mode,enforced", [
    ("observation_only", False), ("application_admission", True),
])
def test_reporter_projects_only_allowlisted_typed_fields(mode, enforced):
    mod = load_gate()

    result = mod.provenance_report({
        "platform_runtime_provenance": {
            "verdict": "cloud",
            "reason": "instance_match",
            "observed": True,
            "metadata_reachable": True,
            "expected_identity_prepared": True,
            "enforced": enforced,
            "mode": mode,
            # Everything below is NOT on the allowlist and must be dropped.
            "instance_id": _SECRET_INSTANCE_ID,
            "expected_instance_id": _SECRET_INSTANCE_ID,
            "metadata_url": "http://169.254.169.254/metadata/v1/id",
            "state_path": "/data/platform-expected-instance.json",
        }
    })

    assert result["verdict"] == "cloud"
    assert result["observed"] is True and result["enforced"] is enforced
    assert result["mode"] == mode
    assert result["reported"] is True
    rendered = json.dumps(result)
    assert _SECRET_INSTANCE_ID not in rendered
    assert "169.254" not in rendered and "/data/" not in rendered
    assert set(result) == {
        "verdict", "reason", "mode", "observed", "metadata_reachable",
        "expected_identity_prepared", "enforced", "reported",
    }


def test_reporter_reports_a_missing_field_as_unknown_not_a_pass():
    mod = load_gate()

    result = mod.provenance_report(_receipt("a" * 40))

    assert result["reported"] is False
    assert result["verdict"] == "unknown"
    assert result["observed"] == "unknown"
    assert result["verdict"] != "cloud"


@pytest.mark.parametrize(
    "malformed",
    [
        "not-an-object",
        {"platform_runtime_provenance": "cloud"},
        {"platform_runtime_provenance": ["cloud"]},
        {"platform_runtime_provenance": None},
    ],
)
def test_reporter_survives_a_malformed_payload(malformed):
    mod = load_gate()

    result = mod.provenance_report(malformed)

    assert result["verdict"] == "unknown"
    assert result["reported"] is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        # A snake_case SHAPE check passes all of these. Only a known protocol
        # VALUE may print, or the reason field becomes a leak channel.
        ("reason", f"instance_{_SECRET_INSTANCE_ID}"),
        ("reason", "token_sensitive_value"),
        ("verdict", "unexpected_claim"),
        # "don't fake enforcement": a mode this reporter does not know must not
        # print as though enforcement were live.
        ("mode", "enforcement_enabled"),
        # Python's `$` matches BEFORE a trailing newline, so a `^...$` shape
        # check lets a log-injecting newline through.
        ("reason", "instance_match\n"),
    ],
)
def test_only_known_protocol_values_print_not_token_shapes(field, value):
    mod = load_gate()

    result = mod.provenance_report({"platform_runtime_provenance": {field: value}})

    assert result[field] == "unknown"
    assert value not in str(result.values())
    assert _SECRET_INSTANCE_ID not in json.dumps(result)


def test_reporter_value_allowlist_covers_every_reason_the_module_emits():
    """The allowlist must not silently fall behind the module it reports on."""
    mod = load_gate()
    source = (REPO_ROOT / "tinyassets" / "platform_runtime_provenance.py").read_text(
        encoding="utf-8"
    )
    # Harvest the CONSTRUCTION sites only. A bare string search also sweeps up
    # boolean field names like `metadata_reachable`, which are not reasons.
    emitted = set(
        re.findall(
            r'(?:MetadataRead|ExpectedInstanceRead)\([^,]+,\s*"([a-z0-9_]+)"', source
        )
    ) | set(re.findall(r'reason="([a-z0-9_]+)"', source))
    # "reachable" and "prepared" describe a successful intermediate read; the
    # resolver copies a reason only on the failure branches, so neither can ever
    # land on a verdict and neither is printable.
    emitted -= {"reachable", "prepared"}
    missing = emitted - mod.PROVENANCE_REASONS
    assert not missing, f"reporter allowlist is behind the module: {sorted(missing)}"
    assert mod.PROVENANCE_VERDICTS == {prov.CLOUD, prov.NOT_CLOUD, prov.UNKNOWN}


def test_reporter_rejects_wrong_types_and_unexpected_tokens():
    mod = load_gate()

    result = mod.provenance_report({
        "platform_runtime_provenance": {
            "verdict": {"nested": "cloud"},
            # A free-text reason could carry anything; only a short snake_case
            # token is printable.
            "reason": f"resolved against droplet {_SECRET_INSTANCE_ID}",
            "observed": 1,            # truthy int is not a bool
            "metadata_reachable": "yes",
            "expected_identity_prepared": None,
            "enforced": "false",      # a truthy string must not become True
            "mode": "OBSERVATION ONLY",
        }
    })

    assert result["reported"] is True, "the field was present, just unusable"
    for name in ("verdict", "reason", "mode", "observed", "metadata_reachable",
                 "expected_identity_prepared", "enforced"):
        assert result[name] == "unknown", name
    assert _SECRET_INSTANCE_ID not in json.dumps(result)


def test_report_without_the_flag_adds_no_provenance_key(monkeypatch, repo):
    mod = load_gate()
    monkeypatch.setattr(mod, "REPO_ROOT", repo["root"])
    monkeypatch.setattr(
        mod, "live_release_state",
        lambda url, timeout: _receipt(repo["head"], platform_runtime_provenance={
            "verdict": "cloud", "observed": True,
        }),
    )

    info = mod.report("https://example/mcp", 2.0)

    assert "platform_runtime_provenance" not in info
    assert info["proves"] == "receipt"


def test_report_provenance_makes_no_second_call(monkeypatch, repo):
    mod = load_gate()
    monkeypatch.setattr(mod, "REPO_ROOT", repo["root"])
    fetches: list[str] = []

    def one_shot(url, timeout):
        fetches.append(url)
        return _receipt(repo["head"], platform_runtime_provenance={
            "verdict": "cloud", "reason": "instance_match", "observed": True,
            "enforced": False, "mode": "observation_only",
        })

    monkeypatch.setattr(mod, "live_release_state", one_shot)

    info = mod.report("https://example/mcp", 2.0, include_provenance=True)

    assert fetches == ["https://example/mcp"], "one fetch, reused"
    assert info["platform_runtime_provenance"]["verdict"] == "cloud"


@pytest.mark.parametrize("with_provenance", [False, True])
def test_assert_contains_exit_semantics_are_unchanged(
    monkeypatch, capsys, repo, with_provenance
):
    """The record-only diagnostic must not move pass / fail / cannot-tell."""
    mod = load_gate()
    monkeypatch.setattr(mod, "REPO_ROOT", repo["root"])
    argv = ["--url", "https://example/mcp", "--assert-contains", repo["head"]]
    if with_provenance:
        argv.append("--report-provenance")

    # 0: production contains the commit.
    monkeypatch.setattr(
        mod, "live_release_state",
        lambda url, timeout: _receipt(repo["head"], platform_runtime_provenance={
            "verdict": "unknown", "reason": "not_observed", "observed": False,
        }),
    )
    assert mod.main(argv) == 0

    # 1: production serves an older commit that does not contain the target.
    monkeypatch.setattr(
        mod, "live_release_state", lambda url, timeout: _receipt(repo["parent"]),
    )
    assert mod.main(argv) == 1

    # 2: production serves a sha this checkout has never seen.
    other = "b" * 40
    monkeypatch.setattr(mod, "live_release_state", lambda url, timeout: _receipt(other))
    assert mod.main(argv) == 2, "an unknown-to-git deployed sha is cannot-determine"

    # 2: the receipt cannot be corroborated.
    monkeypatch.setattr(
        mod, "live_release_state",
        lambda url, timeout: {"git_sha": repo["head"]},
    )
    assert mod.main(argv) == 2
    capsys.readouterr()


def test_unknown_provenance_is_not_printed_as_a_pass(monkeypatch, capsys, repo):
    mod = load_gate()
    monkeypatch.setattr(mod, "REPO_ROOT", repo["root"])
    monkeypatch.setattr(
        mod, "live_release_state", lambda url, timeout: _receipt(repo["head"]),
    )

    code = mod.main([
        "--url", "https://example/mcp", "--report-provenance",
        "--assert-contains", repo["head"],
    ])
    out = capsys.readouterr().out

    assert code == 0, "the receipt assertion still decides the exit code"
    assert "verdict=unknown" in out
    assert "not binary freshness" in out
    assert "not all workers" in out
    assert _SECRET_INSTANCE_ID not in out
