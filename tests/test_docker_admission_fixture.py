"""Keep image refusal and simulated protocol acceptance separate."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/docker_admitted_process.py"


def test_protocol_fixture_uses_image_runtime_and_blocks_metadata(monkeypatch):
    spec = importlib.util.spec_from_file_location("docker_admission_fixture", FIXTURE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    provenance = module.provenance
    monkeypatch.setattr(provenance, "__file__", "/app/tinyassets/provenance.py")
    # Restore all globals changed by the fixture after the test.
    for name in (
        "read_metadata_instance_id", "_read_metadata_instance_id",
        "build_metadata_opener", "_PROCESS_OBSERVATION",
    ):
        monkeypatch.setattr(provenance, name, getattr(provenance, name))
    calls = []
    monkeypatch.setattr(module.runpy, "run_module", lambda *a, **k: calls.append((a, k)))
    module.main()
    observed = provenance.observe_platform_runtime_provenance()
    assert observed.is_cloud
    assert observed.reason == "fixture_simulated_admitted_process"
    for name in (
        "read_metadata_instance_id", "_read_metadata_instance_id", "build_metadata_opener",
    ):
        with pytest.raises(AssertionError, match="must not read cloud metadata"):
            getattr(provenance, name)()
    assert calls == [(("tinyassets.universe_server",), {"run_name": "__main__"})]


def test_fixture_refuses_runtime_outside_image(monkeypatch):
    spec = importlib.util.spec_from_file_location("docker_admission_fixture", FIXTURE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.provenance, "__file__", "/checkout/tinyassets/provenance.py")
    with pytest.raises(RuntimeError, match="built image"):
        module.main()


def test_docker_ci_preserves_unmodified_refusal_and_authenticated_protocol():
    workflow = yaml.safe_load((ROOT / ".github/workflows/docker-build.yml").read_text())
    steps = workflow["jobs"]["build-smoke"]["steps"]
    commands = {step.get("name"): step.get("run", "") for step in steps}
    refusal = commands["Unmodified image refuses unadmitted startup"]
    assert "--network none" in refusal
    assert "--mount" not in refusal
    assert "-p " not in refusal
    assert 'test "$EXIT_CODE" = 78' in refusal
    # Owner split: PID1 refuses an unmigrated volume before the app loads.
    assert "startup refused: no readable layout marker" in refusal
    assert "--cap-add SETPCAP" in refusal and "--user 0:0" in refusal
    assert "timeout 60 docker wait" in refusal
    positive = commands["Start isolated protocol fixture with simulated admission"]
    assert "127.0.0.1:8001:8001" in positive
    assert "docker_admitted_process.py,dst=/ci/admitted_process.py,readonly" in positive
    assert "tinyassets-daemon:ci python /ci/admitted_process.py" in positive
    assert "TINYASSETS_WIKI_CANARY_TOKEN" in positive
    assert "UNIVERSE_SERVER_URL=http://localhost:8001/mcp" in positive
    assert "python tests/fixtures/docker_protocol_probe.py" in commands["MCP initialize smoke"]
    assert "tests/" in (ROOT / ".dockerignore").read_text().splitlines()
    assert "docker_admitted_process" not in (ROOT / "Dockerfile").read_text()
    assert "--entrypoint" not in positive  # Preserve the actual entrypoint.


@pytest.mark.parametrize("exit_code", [0, 6])
def test_fixture_client_keeps_all_checks_and_propagates_failure(exit_code):
    spec = importlib.util.spec_from_file_location(
        "docker_protocol_probe", ROOT / "tests/fixtures/docker_protocol_probe.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    original = ("https://production-issuer.invalid",)
    canary = SimpleNamespace(EXPECTED_AUTHORIZATION_SERVERS=original)

    def main(args):
        assert canary.EXPECTED_AUTHORIZATION_SERVERS == (module.URL,)
        assert args == [
            "--url", "http://localhost:8001/mcp", "--timeout", "15",
            "--assert-handles", "--verbose",
        ]
        return exit_code

    canary.main = main
    assert module.run(canary) == exit_code
    assert canary.EXPECTED_AUTHORIZATION_SERVERS is original


def test_fixture_config_matches_real_challenge_and_discovery(monkeypatch):
    from tinyassets.auth.middleware import _challenge_prm_url
    from tinyassets.auth.wellknown import protected_resource_metadata

    monkeypatch.delenv("WORKOS_MCP_RESOURCE", raising=False)
    monkeypatch.setenv("UNIVERSE_SERVER_AUTH", "dev")
    monkeypatch.setenv("UNIVERSE_SERVER_URL", "http://localhost:8001/mcp")
    assert _challenge_prm_url() == (
        "http://localhost:8001/mcp/.well-known/oauth-protected-resource"
    )
    document = protected_resource_metadata()
    assert document["resource"] == "http://localhost:8001/mcp"
    assert document["authorization_servers"] == ["http://localhost:8001/mcp"]
