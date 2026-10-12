"""Focused tests for the record-only platform runtime provenance observation.

OpenSpec change `cloud-only-runtime-admission`, task 4.

No test here touches a real network, a real metadata service or a real cloud API:
the metadata client is driven through an injected opener, and the resolver
through injected readers. There is no environment-variable switch to test,
because the module deliberately has none.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from tinyassets import platform_runtime_provenance as prov

MODULE_SOURCE = Path(prov.__file__).read_text(encoding="utf-8")


# --- injected fakes -------------------------------------------------------


class _FakeResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self, size: int) -> bytes:
        return self._body[:size]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeOpener:
    """Records what it was asked to open, so we can assert the exact target."""

    def __init__(self, body: bytes | None = None, error: BaseException | None = None):
        self._body = body
        self._error = error
        self.calls: list[tuple[str, float]] = []

    def open(self, request, timeout):  # noqa: D102
        self.calls.append((request.full_url, timeout))
        if self._error is not None:
            raise self._error
        return _FakeResponse(self._body or b"")


def _expected(instance_id: str) -> prov.ExpectedInstanceRead:
    return prov.ExpectedInstanceRead(instance_id, "prepared")


def _metadata(instance_id: str) -> prov.MetadataRead:
    return prov.MetadataRead(instance_id, "reachable")


def _write_state(root: Path, payload: object) -> Path:
    path = root / prov.EXPECTED_INSTANCE_FILENAME
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _good_state(instance_id: str = "512345678") -> dict[str, object]:
    return {
        "schema": prov.EXPECTED_INSTANCE_SCHEMA,
        "version": prov.EXPECTED_INSTANCE_VERSION,
        "expected_instance_id": instance_id,
        "recorded_at": "2026-09-22T06:00:00.000000Z",
    }


# --- the bounded metadata client -----------------------------------------


def test_metadata_client_reads_only_the_literal_link_local_address() -> None:
    opener = _FakeOpener(b"512345678")
    read = prov.read_metadata_instance_id(opener=opener)
    assert read.ok and read.instance_id == "512345678"
    assert opener.calls == [(prov.METADATA_URL, prov.METADATA_TIMEOUT_SECONDS)]
    assert prov.METADATA_URL == "http://169.254.169.254/metadata/v1/id"


def test_metadata_budget_is_subsecond() -> None:
    assert 0 < prov.METADATA_TIMEOUT_SECONDS < 1.0


def test_metadata_timeout_is_a_distinct_refusal() -> None:
    read = prov.read_metadata_instance_id(
        opener=_FakeOpener(error=urllib.error.URLError(TimeoutError("timed out")))
    )
    assert (read.instance_id, read.reason) == (None, "metadata_timeout")
    bare = prov.read_metadata_instance_id(opener=_FakeOpener(error=TimeoutError()))
    assert bare.reason == "metadata_timeout"


def test_metadata_unreachable_refuses() -> None:
    read = prov.read_metadata_instance_id(
        opener=_FakeOpener(error=urllib.error.URLError(ConnectionRefusedError()))
    )
    assert (read.instance_id, read.reason) == (None, "metadata_unreachable")


def test_metadata_redirect_is_refused_not_followed() -> None:
    redirect = urllib.error.HTTPError(
        prov.METADATA_URL, 302, "Found", {}, None  # type: ignore[arg-type]
    )
    read = prov.read_metadata_instance_id(opener=_FakeOpener(error=redirect))
    assert (read.instance_id, read.reason) == (None, "metadata_redirect_refused")


def test_metadata_oversize_body_is_never_parsed_as_a_prefix() -> None:
    # A truncated prefix of an overlong body is itself a well-formed id, so the
    # only safe answer is refusal.
    body = b"5" * (prov.MAX_METADATA_BODY_BYTES + 1)
    read = prov.read_metadata_instance_id(opener=_FakeOpener(body))
    assert (read.instance_id, read.reason) == (None, "metadata_body_too_large")


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        (b"", "metadata_empty_id"),
        (b"   \n", "metadata_empty_id"),
        (b"not-a-droplet-id", "metadata_malformed_instance_id"),
        (b"512345678abc", "metadata_malformed_instance_id"),
        (b"-1", "metadata_malformed_instance_id"),
    ],
)
def test_metadata_malformed_bodies_refuse(body: bytes, reason: str) -> None:
    read = prov.read_metadata_instance_id(opener=_FakeOpener(body))
    assert (read.instance_id, read.reason) == (None, reason)


def test_opener_inherits_no_proxy(monkeypatch) -> None:
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")

    def proxying(opener: urllib.request.OpenerDirector) -> list[urllib.request.ProxyHandler]:
        return [
            h
            for h in opener.handlers
            if isinstance(h, urllib.request.ProxyHandler) and h.proxies
        ]

    # Control: a default opener under these env vars DOES install a proxy, so
    # this assertion can actually go red.
    assert proxying(urllib.request.build_opener()), (
        "the control case must show an inherited proxy, otherwise the real "
        "assertion below is vacuous"
    )
    # An inherited http_proxy would send the probe to a proxy and report the
    # proxy's answer as this machine's own identity.
    assert proxying(prov.build_metadata_opener()) == []


def test_opener_follows_no_redirect() -> None:
    opener = prov.build_metadata_opener()
    redirect_handlers = [
        h
        for h in opener.handlers
        if isinstance(h, urllib.request.HTTPRedirectHandler)
    ]
    assert redirect_handlers
    assert all(
        h.redirect_request(None, None, 302, "Found", {}, "http://example.invalid/")
        is None
        for h in redirect_handlers
    )


def test_proxy_environment_cannot_retarget_the_probe(monkeypatch) -> None:
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    opener = _FakeOpener(b"512345678")
    prov.read_metadata_instance_id(opener=opener)
    assert opener.calls[0][0] == prov.METADATA_URL


# --- deploy-recorded expected identity -----------------------------------


def test_expected_identity_missing_refuses(tmp_path: Path) -> None:
    read = prov.read_expected_instance_id(data_root=tmp_path)
    assert (read.instance_id, read.reason) == (None, "expected_identity_missing")


def test_expected_identity_reads_from_the_canonical_data_root(
    tmp_path: Path, monkeypatch
) -> None:
    # `data_dir()` is the single CWD-independent resolver; the module must not
    # re-implement its precedence.
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    _write_state(tmp_path, _good_state("777000111"))
    assert prov.expected_instance_state_path() == (
        tmp_path / prov.EXPECTED_INSTANCE_FILENAME
    )
    read = prov.read_expected_instance_id()
    assert (read.instance_id, read.reason) == ("777000111", "prepared")


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        ({"schema": "something_else", "version": 1, "expected_instance_id": "1"},
         "expected_identity_schema_unknown"),
        ({"schema": prov.EXPECTED_INSTANCE_SCHEMA, "version": 99,
          "expected_instance_id": "1"},
         "expected_identity_version_unsupported"),
        ({"schema": prov.EXPECTED_INSTANCE_SCHEMA, "version": 1,
          "expected_instance_id": "droplet-1"},
         "expected_identity_malformed"),
        ({"schema": prov.EXPECTED_INSTANCE_SCHEMA, "version": 1,
          "expected_instance_id": 512345678},
         "expected_identity_malformed"),
        ({"schema": prov.EXPECTED_INSTANCE_SCHEMA, "version": 1},
         "expected_identity_malformed"),
        (["not", "an", "object"], "expected_identity_malformed"),
    ],
)
def test_expected_identity_malformed_state_refuses(
    tmp_path: Path, payload: object, reason: str
) -> None:
    _write_state(tmp_path, payload)
    read = prov.read_expected_instance_id(data_root=tmp_path)
    assert (read.instance_id, read.reason) == (None, reason)


def test_expected_identity_unreadable_state_refuses(tmp_path: Path) -> None:
    (tmp_path / prov.EXPECTED_INSTANCE_FILENAME).write_text("{not json", encoding="utf-8")
    read = prov.read_expected_instance_id(data_root=tmp_path)
    assert (read.instance_id, read.reason) == (None, "expected_identity_state_unreadable")


def test_expected_identity_oversize_state_refuses(tmp_path: Path) -> None:
    payload = _good_state()
    payload["padding"] = "x" * (prov.MAX_EXPECTED_STATE_BYTES + 1)
    _write_state(tmp_path, payload)
    read = prov.read_expected_instance_id(data_root=tmp_path)
    assert (read.instance_id, read.reason) == (
        None,
        "expected_identity_state_too_large",
    )


# --- the resolver ---------------------------------------------------------


def test_match_resolves_cloud() -> None:
    result = prov.resolve_platform_runtime_provenance(
        metadata_reader=lambda: _metadata("512345678"),
        expected_reader=lambda: _expected("512345678"),
    )
    assert result.verdict == prov.CLOUD
    assert result.reason == "instance_match"
    assert result.is_cloud is True
    assert result.metadata_reachable and result.expected_identity_prepared
    # Application guards are enabled; this does not establish cloud custody.
    assert result.enforced is True


def test_comparison_is_strict_integer_not_string() -> None:
    same = prov.resolve_platform_runtime_provenance(
        metadata_reader=lambda: _metadata("0512345678"),
        expected_reader=lambda: _expected("512345678"),
    )
    assert same.verdict == prov.CLOUD
    prefix = prov.resolve_platform_runtime_provenance(
        metadata_reader=lambda: _metadata("51234567"),
        expected_reader=lambda: _expected("512345678"),
    )
    assert (prefix.verdict, prefix.reason) == (prov.NOT_CLOUD, "instance_mismatch")


def test_mismatch_resolves_not_cloud() -> None:
    result = prov.resolve_platform_runtime_provenance(
        metadata_reader=lambda: _metadata("512345678"),
        expected_reader=lambda: _expected("999888777"),
    )
    assert (result.verdict, result.reason) == (prov.NOT_CLOUD, "instance_mismatch")
    assert result.is_cloud is False


def test_unreachable_metadata_resolves_not_cloud_even_when_expected_is_prepared() -> None:
    result = prov.resolve_platform_runtime_provenance(
        metadata_reader=lambda: prov.MetadataRead(None, "metadata_unreachable"),
        expected_reader=lambda: _expected("512345678"),
    )
    assert (result.verdict, result.reason) == (prov.NOT_CLOUD, "metadata_unreachable")
    assert result.metadata_reachable is False
    assert result.expected_identity_prepared is True


def test_unprepared_expected_identity_resolves_not_cloud() -> None:
    result = prov.resolve_platform_runtime_provenance(
        metadata_reader=lambda: _metadata("512345678"),
        expected_reader=lambda: prov.ExpectedInstanceRead(
            None, "expected_identity_missing"
        ),
    )
    assert (result.verdict, result.reason) == (
        prov.NOT_CLOUD,
        "expected_identity_missing",
    )
    assert result.metadata_reachable is True
    assert result.expected_identity_prepared is False


def test_resolver_has_no_default_cloud_fallback(tmp_path: Path, monkeypatch) -> None:
    # Nothing prepared, nothing reachable: the honest answer is not-cloud, and
    # there is no env var, hostname or label that changes it.
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TINYASSETS_CLOUD", "1")
    monkeypatch.setenv("TINYASSETS_EXECUTOR_CLASS", "cloud")
    result = prov.resolve_platform_runtime_provenance(
        metadata_reader=lambda: prov.MetadataRead(None, "metadata_unreachable"),
    )
    assert result.verdict == prov.NOT_CLOUD


# --- once-per-process immutability ---------------------------------------


def test_observation_resolves_once_per_process_and_is_immutable() -> None:
    calls: list[int] = []

    def resolver() -> prov.RuntimeProvenance:
        calls.append(1)
        return prov.RuntimeProvenance(prov.CLOUD, "instance_match", True, True)

    observation = prov.ProcessProvenanceObservation(resolver)
    assert observation.resolved is False
    first = observation.observe()
    second = observation.observe()
    third = observation.observe()
    assert len(calls) == 1
    assert first is second is third
    assert observation.resolved is True
    with pytest.raises(Exception):
        first.verdict = prov.NOT_CLOUD  # type: ignore[misc]  # frozen dataclass


def test_cached_refusal_never_silently_upgrades() -> None:
    answers = [
        prov.RuntimeProvenance(prov.NOT_CLOUD, "metadata_unreachable", False, True),
        prov.RuntimeProvenance(prov.CLOUD, "instance_match", True, True),
    ]

    def resolver() -> prov.RuntimeProvenance:
        return answers.pop(0)

    observation = prov.ProcessProvenanceObservation(resolver)
    assert observation.observe().verdict == prov.NOT_CLOUD
    # Metadata coming back later does not promote this process.
    assert observation.observe().verdict == prov.NOT_CLOUD
    assert observation.observe().reason == "metadata_unreachable"
    assert len(answers) == 1


def test_a_new_process_resolves_independently() -> None:
    answers = [
        prov.RuntimeProvenance(prov.NOT_CLOUD, "metadata_unreachable", False, True),
        prov.RuntimeProvenance(prov.CLOUD, "instance_match", True, True),
    ]

    def resolver() -> prov.RuntimeProvenance:
        return answers.pop(0)

    refused = prov.ProcessProvenanceObservation(resolver)
    assert refused.observe().verdict == prov.NOT_CLOUD
    # A new process is a new object: an explicit restart resolves anew rather
    # than inheriting the previous incarnation's evidence in either direction.
    restarted = prov.ProcessProvenanceObservation(resolver)
    assert restarted.observe().verdict == prov.CLOUD


def test_module_singleton_is_shared_and_lazy() -> None:
    assert isinstance(prov._PROCESS_OBSERVATION, prov.ProcessProvenanceObservation)
    assert prov.observe_platform_runtime_provenance.__doc__


# --- sanitization ---------------------------------------------------------


def test_sanitized_fields_carry_no_identifier() -> None:
    fields = prov.sanitized_observation_fields(
        prov.RuntimeProvenance(prov.CLOUD, "instance_match", True, True)
    )
    assert fields == {
        "verdict": "cloud",
        "reason": "instance_match",
        "metadata_reachable": True,
        "expected_identity_prepared": True,
        "enforced": True,
        "mode": "application_admission",
    }
    rendered = json.dumps(fields)
    assert not re.search(r"\d{5,}", rendered)


def test_verdict_object_never_stores_an_instance_id() -> None:
    result = prov.resolve_platform_runtime_provenance(
        metadata_reader=lambda: _metadata("512345678"),
        expected_reader=lambda: _expected("512345678"),
    )
    assert "512345678" not in repr(result)
    assert not any(
        isinstance(getattr(result, f), str) and getattr(result, f).isdigit()
        for f in ("verdict", "reason")
    )


# --- structural pins: admission sites, no lock-held I/O, no bypass --------


def _code_only(source: str) -> str:
    """Drop comment lines: the prose around an admission site legitimately
    discusses refusal and record-only history, and matching that would be a
    prose test rather than a pin on the code."""
    return "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("#")
    )


def test_module_opens_no_database_transaction() -> None:
    # The metadata read must never be able to run under the SQLite write lock.
    for forbidden in ("sqlite3", "BEGIN IMMEDIATE", "with_connection", "commit()"):
        assert forbidden not in MODULE_SOURCE, forbidden


def test_module_has_no_environment_bypass() -> None:
    # No tests-only switch and no cloud assertion from env/hostname/UUID.
    for forbidden in ("os.environ", "getenv", "socket.gethostname", "uuid"):
        assert forbidden not in MODULE_SOURCE, forbidden


def test_serving_startup_refuses_rather_than_merely_recording() -> None:
    """The record-only contract is gone: startup is an admission site now.

    Updated for tasks 7/8 (it previously pinned "exactly one call site, and it
    changes no admission"). Both serving entry points must refuse: ``main`` for
    every transport it serves, and the HTTP app's own lifespan, because that app
    is constructible without ``main``.
    """
    repo = Path(__file__).resolve().parents[1]
    source = (repo / "tinyassets" / "universe_server.py").read_text(encoding="utf-8")

    # Code only: the surrounding comment prose legitimately discusses record-only
    # history, and matching that would be a prose test.
    code = _code_only(source)
    assert code.count("require_process_cloud_admission(") == 2, (
        "serving startup must admit in main() AND in the HTTP app's lifespan"
    )
    assert "SystemExit(PLATFORM_NOT_CLOUD_EXIT_CODE)" in code
    assert prov.PLATFORM_NOT_CLOUD_REASON == "platform_not_cloud"

    # No env/hostname escape hatch at the admission site, and no swallow that
    # would let an unadmitted process carry on serving.
    for forbidden in ("TINYASSETS_ALLOW_NOT_CLOUD", "getenv(\"TINYASSETS_CLOUD"):
        assert forbidden not in source, forbidden


def test_the_boot_refusal_exit_code_is_nonzero() -> None:
    from tinyassets.universe_server import PLATFORM_NOT_CLOUD_EXIT_CODE

    assert isinstance(PLATFORM_NOT_CLOUD_EXIT_CODE, int)
    assert PLATFORM_NOT_CLOUD_EXIT_CODE != 0


def test_the_origin_backstop_reads_only_the_cache() -> None:
    """Site (D): a request may never resolve, probe or open storage."""
    repo = Path(__file__).resolve().parents[1]
    source = (repo / "tinyassets" / "origin_admission.py").read_text(encoding="utf-8")
    # Strip the module docstring as well as the comments: it explains *why* the
    # backstop opens no socket, and a pin that matched its prose would fail on
    # the explanation rather than on the code.
    code = _code_only(source.split('"""', 2)[-1])

    assert "cached_process_is_cloud_admitted" in code
    for forbidden in (
        "resolve_platform_runtime_provenance",
        "require_process_cloud_admission",
        "observe_platform_runtime_provenance",
        "read_metadata_instance_id",
        "import socket",
        "socket.socket",
        "urllib",
        "requests.get",
        "os.environ",
        "getenv",
        "gethostname",
    ):
        assert forbidden not in code, forbidden

    # The refusal body is one stable sanitized token, never an identifier.
    from tinyassets.origin_admission import _REFUSAL_BODY, PLATFORM_NOT_CLOUD_STATUS

    assert PLATFORM_NOT_CLOUD_STATUS == 503
    assert _REFUSAL_BODY == b'{"error":"platform_not_cloud"}'


def test_admission_sites_consume_the_one_resolver() -> None:
    """Flipped from the record-only pin: these paths MUST consume provenance.

    Its predecessor asserted the opposite ("...do_not_consume_provenance_yet")
    and was true only for the record-only slice. It is updated rather than
    skipped: a skipped pin would keep asserting the contract the code moved past.
    """
    repo = Path(__file__).resolve().parents[1]
    for module in (
        "branch_tasks_v2.py",
        "daemon_registry.py",
        "foreground_run_provider.py",
        "background_served_provider.py",
        "universe_server.py",
        "origin_admission.py",
    ):
        text = (repo / "tinyassets" / module).read_text(encoding="utf-8")
        assert "platform_runtime_provenance" in text, module

    # One resolver, not a second implementation per site.
    resolvers = sorted(
        path.relative_to(repo).as_posix()
        for path in (repo / "tinyassets").rglob("*.py")
        if "def resolve_platform_runtime_provenance" in path.read_text(encoding="utf-8")
    )
    assert resolvers == ["tinyassets/platform_runtime_provenance.py"], resolvers
