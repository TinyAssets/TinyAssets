"""Tests for Row K log aggregation sidecar (deploy/compose.yml + deploy/vector.yaml)."""

from __future__ import annotations

import os
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPOSE = REPO_ROOT / "deploy" / "compose.yml"
VECTOR_YAML = REPO_ROOT / "deploy" / "vector.yaml"
VECTOR_BETTERSTACK_YAML = REPO_ROOT / "deploy" / "vector-betterstack.yaml"
VECTOR_ENTRYPOINT = REPO_ROOT / "deploy" / "vector-entrypoint.sh"
SHIP_LOGS = REPO_ROOT / "deploy" / "ship-logs.sh"
JOURNALD_DROPIN = REPO_ROOT / "deploy" / "journald-tinyassets.conf"
INSTALLER = REPO_ROOT / "deploy" / "install-host-uptime-services.sh"
RUNBOOK = REPO_ROOT / "docs" / "ops" / "log-aggregation-runbook.md"


# ---------------------------------------------------------------------------
# compose.yml — sidecar service assertions
# ---------------------------------------------------------------------------


def _load_compose() -> dict:
    yaml = pytest.importorskip("yaml")
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def test_logs_service_defined():
    data = _load_compose()
    assert "logs" in data["services"], "compose.yml must have a 'logs' sidecar service"


def test_logs_service_uses_vector_image():
    data = _load_compose()
    image = data["services"]["logs"]["image"]
    assert image.startswith("timberio/vector:"), f"unexpected image: {image}"


def test_logs_service_restart_policy():
    data = _load_compose()
    restart = data["services"]["logs"].get("restart")
    assert restart == "unless-stopped", f"restart policy should be unless-stopped, got: {restart}"


def test_logs_service_has_no_docker_socket_or_container_control():
    data = _load_compose()
    volumes = data["services"]["logs"].get("volumes", [])
    socket_mounts = [v for v in volumes if "/var/run/docker.sock" in str(v)]
    assert not socket_mounts, "logging sidecar must not receive Docker control access"


def test_runtime_containers_forward_logs_without_docker_socket():
    data = _load_compose()
    services = data["services"]
    # Derived, not listed: the four `worker*` services were deleted 2026-08-29
    # with the host-run fleet (nothing runs outside a user's universe --
    # ADR-009), and deriving means a NEW long-running container inherits the
    # forwarding requirement instead of silently escaping this test.
    forwarding_services = [
        name for name, service in services.items()
        if name != "logs" and service.get("logging") is not None
    ]
    assert set(forwarding_services) == {"daemon", "cloudflared", "slack-agent"}, (
        f"unexpected forwarding service set: {sorted(forwarding_services)}"
    )
    for name in forwarding_services:
        logging = services[name].get("logging") or {}
        assert logging.get("driver") == "fluentd", name
        options = logging.get("options") or {}
        assert options.get("fluentd-address") == "127.0.0.1:24224", name
        assert str(options.get("fluentd-async")).lower() == "true", name

    ports = services["logs"].get("ports") or []
    assert "127.0.0.1:24224:24224" in ports


def test_sidecars_receive_only_their_required_secret():
    services = _load_compose()["services"]
    expected = {
        "cloudflared": {"CLOUDFLARE_TUNNEL_TOKEN"},
        "logs": {"BETTERSTACK_SOURCE_TOKEN"},
    }
    for name, allowed in expected.items():
        service = services[name]
        assert not (service.get("env_file") or []), name
        environment = service.get("environment") or {}
        assert set(environment) == allowed, name
        assert all("${" in str(value) for value in environment.values()), name


def test_logs_service_mounts_vector_config():
    data = _load_compose()
    volumes = data["services"]["logs"].get("volumes", [])
    config_mounts = [v for v in volumes if "vector.yaml" in str(v)]
    assert config_mounts, "logs service must mount vector.yaml config"


def test_logs_service_depends_on_daemon():
    data = _load_compose()
    deps = data["services"]["logs"].get("depends_on", [])
    if isinstance(deps, dict):
        dep_names = list(deps.keys())
    else:
        dep_names = list(deps)
    assert "daemon" in dep_names, "logs service must depend on daemon"


# ---------------------------------------------------------------------------
# vector.yaml — source / transform / sink assertions
# ---------------------------------------------------------------------------


def _load_vector() -> dict:
    yaml = pytest.importorskip("yaml")
    return yaml.safe_load(VECTOR_YAML.read_text(encoding="utf-8"))


def test_vector_fluent_source_has_no_docker_api_dependency():
    data = _load_vector()
    sources = data.get("sources", {})
    assert all(source.get("type") != "docker_logs" for source in sources.values())
    fluent = next((v for v in sources.values() if v.get("type") == "fluent"), None)
    assert fluent is not None
    assert fluent.get("address") == "0.0.0.0:24224"
    assert fluent.get("mode") == "tcp"


def test_vector_classifies_forwarded_runtime_tags():
    data = _load_vector()
    transform = data["transforms"]["enriched"]
    source = transform.get("source", "")
    assert "tinyassets-daemon" in source
    assert "tinyassets-tunnel" in source
    # The host-run worker fleet was deleted 2026-08-29; a `worker` role that no
    # container can carry would be a stale classification, not coverage.
    assert "tinyassets-worker" not in source


def test_vector_has_stdout_sink():
    data = _load_vector()
    sinks = data.get("sinks", {})
    console_sinks = [v for v in sinks.values() if v.get("type") == "console"]
    assert console_sinks, "vector.yaml must have a console/stdout sink (always-on fallback)"


def test_vector_base_has_no_betterstack_sink():
    """Base vector.yaml must NOT contain the betterstack sink — it lives in the
    separate vector-betterstack.yaml fragment to silence 401 errors when the
    token is unset."""
    data = _load_vector()
    sinks = data.get("sinks", {})
    http_sinks = [v for v in sinks.values() if v.get("type") == "http"]
    assert not http_sinks, (
        "base vector.yaml must not contain an http sink — betterstack belongs "
        "in vector-betterstack.yaml (loaded conditionally by vector-entrypoint.sh)"
    )


def test_vector_betterstack_fragment_exists():
    assert VECTOR_BETTERSTACK_YAML.exists(), (
        "deploy/vector-betterstack.yaml must exist (conditional betterstack sink)"
    )


def test_vector_betterstack_fragment_has_http_sink():
    yaml = pytest.importorskip("yaml")
    data = yaml.safe_load(VECTOR_BETTERSTACK_YAML.read_text(encoding="utf-8"))
    sinks = data.get("sinks", {})
    http_sinks = [v for v in sinks.values() if v.get("type") == "http"]
    assert http_sinks, "vector-betterstack.yaml must have an HTTP sink"


def test_vector_betterstack_fragment_uses_token_env():
    yaml = pytest.importorskip("yaml")
    data = yaml.safe_load(VECTOR_BETTERSTACK_YAML.read_text(encoding="utf-8"))
    sinks = data.get("sinks", {})
    http_sinks = [v for v in sinks.values() if v.get("type") == "http"]
    assert http_sinks
    auth_header = http_sinks[0].get("request", {}).get("headers", {}).get("Authorization", "")
    assert "BETTERSTACK_SOURCE_TOKEN" in auth_header


def test_vector_entrypoint_exists():
    assert VECTOR_ENTRYPOINT.exists(), "deploy/vector-entrypoint.sh must exist"


def test_vector_entrypoint_conditional_betterstack():
    text = VECTOR_ENTRYPOINT.read_text(encoding="utf-8")
    assert "BETTERSTACK_SOURCE_TOKEN" in text
    assert "vector-betterstack.yaml" in text


def test_vector_entrypoint_exec_vector():
    text = VECTOR_ENTRYPOINT.read_text(encoding="utf-8")
    assert "exec vector" in text


def test_compose_mounts_entrypoint():
    data = _load_compose()
    volumes = data["services"]["logs"].get("volumes", [])
    entrypoint_mounts = [v for v in volumes if "vector-entrypoint.sh" in str(v)]
    assert entrypoint_mounts, "compose must mount vector-entrypoint.sh"


def test_compose_mounts_betterstack_fragment():
    data = _load_compose()
    volumes = data["services"]["logs"].get("volumes", [])
    bs_mounts = [v for v in volumes if "vector-betterstack.yaml" in str(v)]
    assert bs_mounts, "compose must mount vector-betterstack.yaml"


def test_vector_yaml_parses_cleanly():
    yaml = pytest.importorskip("yaml")
    # Should not raise
    data = yaml.safe_load(VECTOR_YAML.read_text(encoding="utf-8"))
    assert isinstance(data, dict)


# ---------------------------------------------------------------------------
# ship-logs.sh — basic sanity
# ---------------------------------------------------------------------------


_BASH_AVAILABLE = sys.platform != "win32"


def test_ship_logs_script_exists():
    assert SHIP_LOGS.exists(), "deploy/ship-logs.sh must exist"


def test_ship_logs_default_covers_the_production_containers():
    """The default set is exactly what compose runs: every name here is
    REQUIRED (a missing one aborts the archive), so a retired container in
    the default would make the hourly shipper exit 1 forever."""
    text = SHIP_LOGS.read_text(encoding="utf-8")
    default_line = next(
        line for line in text.splitlines() if line.startswith("LOG_CONTAINERS=")
    )
    for container in ("tinyassets-daemon", "tinyassets-tunnel"):
        assert container in default_line
    assert "tinyassets-worker" not in default_line, (
        "the host-run worker fleet was deleted 2026-08-29; a required container "
        "that never exists makes ship-logs.sh fail on every run"
    )


def test_ship_logs_requires_a_complete_readable_fleet_archive():
    text = SHIP_LOGS.read_text(encoding="utf-8")
    collect = text.split("# Collect Docker container logs", 1)[1].split(
        "# Archive", 1
    )[0]
    assert "docker ps" not in collect
    assert "{{.State.Status}}" in collect
    assert "{{.Id}}" in collect
    assert "fleet-manifest.tsv" in text
    assert "docker logs" in collect
    assert "|| true" not in collect
    assert 'docker logs "${container_id}"' in collect
    assert 'current_id="$(docker inspect' in collect


def test_log_runbook_uses_current_production_identities():
    text = RUNBOOK.read_text(encoding="utf-8")
    for stale in (
        "docker-compose@workflow",
        "docker-compose@tinyassets",
        '.service = "workflow"',
        "workflow-logs-",
        "docker logs workflow-logs",
    ):
        assert stale not in text
    assert "journalctl -u tinyassets-daemon" in text
    assert '.service = "tinyassets"' in text
    assert "tinyassets-logs-" in text
    assert "docker logs tinyassets-logs" in text
    assert "/opt/tinyassets-host-uptime/current/deploy/ship-logs.sh" in text
    assert "/opt/tinyassets/deploy/ship-logs.sh" not in text


@pytest.mark.skipif(not _BASH_AVAILABLE, reason="bash not available on Windows")
def test_ship_logs_dry_run_exits_0(tmp_path):
    if not SHIP_LOGS.exists():
        pytest.skip("ship-logs.sh not yet created")
    env = {
        "DRY_RUN": "1",
        "LOG_DEST": "s3://test-bucket/logs",
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path),
    }
    result = subprocess.run(
        ["bash", str(SHIP_LOGS)],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"DRY_RUN=1 should exit 0; got {result.returncode}\n{result.stderr}"
    )


@pytest.mark.skipif(not _BASH_AVAILABLE, reason="bash not available on Windows")
def test_ship_logs_dry_run_prints_indicator(tmp_path):
    if not SHIP_LOGS.exists():
        pytest.skip("ship-logs.sh not yet created")
    env = {
        "DRY_RUN": "1",
        "LOG_DEST": "s3://test-bucket/logs",
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path),
    }
    result = subprocess.run(
        ["bash", str(SHIP_LOGS)],
        env=env,
        capture_output=True,
        text=True,
    )
    combined = result.stdout + result.stderr
    assert "dry" in combined.lower(), "DRY_RUN=1 should print a dry-run indicator"


@pytest.mark.skipif(not _BASH_AVAILABLE, reason="bash not available on Windows")
def test_ship_logs_missing_log_dest_exits_1(tmp_path):
    if not SHIP_LOGS.exists():
        pytest.skip("ship-logs.sh not yet created")
    env = {
        "LOG_DEST": "",
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path),
    }
    result = subprocess.run(
        ["bash", str(SHIP_LOGS)],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0, "missing LOG_DEST should exit non-zero"


@pytest.mark.skipif(not _BASH_AVAILABLE, reason="bash not available on Windows")
@pytest.mark.parametrize(
    "failure", [None, "missing", "unreadable", "recreated", "recreated-earlier"]
)
def test_ship_logs_archives_stopped_members_and_fails_closed(tmp_path, failure):
    bin_dir = tmp_path / "bin"
    capture_dir = tmp_path / "capture"
    bin_dir.mkdir()
    capture_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "case \"$1\" in\n"
        "  inspect)\n"
        "    format=$3\n"
        "    name=$4\n"
        "    if [ \"${SHIP_LOG_FAILURE-}\" = missing ] "
        "&& [ \"$name\" = worker-b ]; then exit 1; fi\n"
        "    id=$(cat \"${SHIP_LOG_STATE:?}/$name.id\")\n"
        "    status=$(cat \"${SHIP_LOG_STATE:?}/$name.status\")\n"
        "    case \"$format\" in\n"
        "      '{{.Id}} {{.State.Status}}')\n"
        "        printf '%s %s\\n' \"$id\" \"$status\"\n"
        "        if [ \"${SHIP_LOG_FAILURE-}\" = recreated ] "
        "&& [ \"$name\" = worker-b ]; then printf '%064x' 99 > \"$SHIP_LOG_STATE/$name.id\"; fi\n"
        "        if [ \"${SHIP_LOG_FAILURE-}\" = recreated-earlier ] "
        "&& [ \"$name\" = worker-b ]; then "
        "printf '%064x' 98 > \"$SHIP_LOG_STATE/worker-a.id\"; fi\n"
        "        ;;\n"
        "      '{{.Id}}') printf '%s\\n' \"$id\" ;;\n"
        "      *) exit 91 ;;\n"
        "    esac\n"
        "    ;;\n"
        "  logs)\n"
        "    id=$2\n"
        "    if [ \"${SHIP_LOG_FAILURE-}\" = unreadable ] "
        "&& [ \"$id\" = \"$(cat \"$SHIP_LOG_STATE/worker-b.id\")\" ]; then exit 1; fi\n"
        "    printf 'logs for %s\\n' \"$id\"\n"
        "    ;;\n"
        "  *) exit 90 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    worker_ids = {
        "worker-a": f"{1:064x}",
        "worker-b": f"{2:064x}",
    }
    for name, container_id in worker_ids.items():
        (state_dir / f"{name}.id").write_text(container_id, encoding="utf-8")
        (state_dir / f"{name}.status").write_text(
            "exited" if name == "worker-b" else "running", encoding="utf-8"
        )
    rclone = bin_dir / "rclone"
    rclone.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "if [ \"$1\" = copyto ]; then\n"
        "  cp \"$2\" \"${SHIP_LOG_CAPTURE:?}/archive.tar.gz\"\n"
        "  touch \"${SHIP_LOG_CAPTURE:?}/uploaded\"\n"
        "fi\n",
        encoding="utf-8",
    )
    rclone.chmod(0o755)
    env = os.environ.copy()
    env.update(
        {
            "PATH": str(bin_dir) + os.pathsep + env.get("PATH", ""),
            "LOG_DEST": "fake:logs",
            "LOG_CONTAINERS": "worker-a worker-b",
            "LOG_DIR": str(tmp_path / "scratch"),
            "SHIP_LOG_CAPTURE": str(capture_dir),
            "SHIP_LOG_FAILURE": failure or "",
            "SHIP_LOG_STATE": str(state_dir),
        }
    )

    result = subprocess.run(
        ["bash", str(SHIP_LOGS)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    if failure:
        assert result.returncode != 0
        assert not (capture_dir / "uploaded").exists()
        return

    assert result.returncode == 0, result.stderr
    assert (capture_dir / "uploaded").exists()
    with tarfile.open(capture_dir / "archive.tar.gz", "r:gz") as archive:
        assert set(archive.getnames()) == {
            "fleet-manifest.tsv",
            "worker-a.log",
            "worker-b.log",
        }
        manifest = archive.extractfile("fleet-manifest.tsv")
        assert manifest is not None
        contents = manifest.read().decode("utf-8")
    assert f"worker-a\t{worker_ids['worker-a']}\trunning\tworker-a.log" in contents
    assert f"worker-b\t{worker_ids['worker-b']}\texited\tworker-b.log" in contents


# ---------------------------------------------------------------------------
# Where the forwarded lines come to rest -- the journal, not a container
# ---------------------------------------------------------------------------
#
# Regression cover for the 2026-09-26 finding
# (docs/concerns/2026-09-26-daemon-logs-not-shipped.md). The `logs` container is
# the one place every forwarded line exists on this host (Vector's console sink
# re-emits them), so ITS logging driver decides whether a deploy erases the
# evidence. It used to be Docker's default json-file, which lives in the
# container's own directory and dies with it.


def test_logs_service_output_lands_in_the_journal():
    logging = _load_compose()["services"]["logs"].get("logging") or {}
    assert logging.get("driver") == "journald", (
        "the logs sidecar re-emits every forwarded line on its stdout; with a "
        "container-scoped driver (json-file is Docker's default) that copy is "
        "deleted when the container is recreated, which every deploy does"
    )


def test_logs_service_carries_a_stable_journal_tag():
    """Without an explicit tag, Docker's journald driver uses a truncated
    container id, which changes on every recreate — so the query that is
    supposed to read ACROSS recreates would need a different value per
    generation."""
    options = (_load_compose()["services"]["logs"].get("logging") or {}).get("options") or {}
    assert options.get("tag") == "tinyassets-logs"


def test_logs_service_does_not_forward_to_its_own_listener():
    """A `logs` container using the fluent anchor would ship its own stdout into
    the listener that produced it."""
    logging = _load_compose()["services"]["logs"].get("logging") or {}
    assert logging.get("driver") != "fluentd"
    assert "fluentd-address" not in (logging.get("options") or {})


def test_journald_dropin_bounds_retention_in_bytes_and_time():
    """Pointing a chatty container at journald is only safe with caps, and the
    caps are what decide how much history survives."""
    text = JOURNALD_DROPIN.read_text(encoding="utf-8")
    assert "[Journal]" in text
    # Persistent, or the journal is a tmpfs that a reboot empties.
    assert "Storage=persistent" in text
    settings = dict(
        line.split("=", 1)
        for line in text.splitlines()
        if "=" in line and not line.lstrip().startswith("#")
    )
    assert settings["SystemMaxUse"] == "1G"
    assert settings["MaxRetentionSec"] == "14day"
    assert settings["SystemKeepFree"] == "2G"
    # Rate limiting drops messages to protect the journal, and a dropped line
    # during an incident is the evidence this change exists to keep -- so the
    # ceiling is generous. But it must stay FINITE: disabling it outright left no
    # bound on write/compression throughput during a storm, and the justification
    # for doing so ("the Docker driver gates volume") named a limit that is not
    # configured anywhere (cross-family review,
    # output/codex-log-durability-review.md §7).
    assert settings["RateLimitBurst"] != "0", (
        "an unlimited burst trades an outage risk for evidence; keep a ceiling"
    )
    assert int(settings["RateLimitBurst"]) >= 10_000, (
        "the ceiling must be far above normal volume or it becomes the drop"
    )
    assert settings["RateLimitIntervalSec"] != "0"


def test_the_dropin_does_not_promise_retention_it_cannot_deliver():
    """`MaxRetentionSec` is a maximum AGE, not a minimum guarantee: whichever of
    the age and byte bounds is reached first wins, so 1 GiB can mean hours. The
    comment used to claim the opposite, which is the kind of false reassurance
    that gets a window trusted past what it holds."""
    text = JOURNALD_DROPIN.read_text(encoding="utf-8")
    assert "maximum age" in text.lower()
    assert "not a minimum" in text.lower()
    assert "whichever runs out first" in text.lower()
    # And it must name the knob, since editing the box is reverted by the installer.
    assert "SystemMaxUse is the knob" in text


def test_installer_owns_the_journald_dropin():
    text = INSTALLER.read_text(encoding="utf-8")
    assert "deploy/journald-tinyassets.conf" in text
    # Shipped by the manifest, or the file never reaches the droplet: the install
    # workflow builds its bundle from `git archive` over the
    # TINYASSETS_PRINT_MANIFEST output, so a file missing from the manifest is
    # one the installer then refuses on.
    manifest_block = text.split('if [[ "${PRINT_MANIFEST}" == "1" ]]; then', 1)[1]
    manifest_block = manifest_block.split("exit 0", 1)[0]
    assert "JOURNALD_DROPIN_SOURCE" in manifest_block
    assert "restart systemd-journald" in text


def test_runbook_leads_with_the_query_that_survives_a_deploy():
    """The runbook is read mid-incident. `docker logs` is scoped to the current
    container, so a responder who reaches for it after a deploy finds nothing --
    which is how the 2026-09-26 evidence was declared lost."""
    text = RUNBOOK.read_text(encoding="utf-8")
    assert "journalctl CONTAINER_NAME=tinyassets-logs" in text
    assert "--output=short-iso-precise" in text
    assert "deploy/journald-tinyassets.conf" in text
    # The correction is part of the content: a reader who still believes compose
    # stdout reaches journald will not understand why the driver had to change.
    assert "detaches" in text


def test_the_journald_dropin_is_pinned_to_lf():
    """`git archive` ships this file to /etc/systemd/journald.conf.d/ verbatim,
    and it was authored on Windows. `.gitattributes` already pins `*.service`
    and `*.timer` for exactly this reason; `*.conf` was missing, so the first
    build of this change handed the droplet a CRLF drop-in. systemd happens to
    strip `\r` as whitespace, so it parsed -- which is why nothing would have
    failed loudly, and why this needs a test rather than a reader's attention.
    """
    attributes = (REPO_ROOT / ".gitattributes").read_text(encoding="utf-8")
    rules = [
        line.split()
        for line in attributes.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    conf = [rule for rule in rules if rule[0] == "*.conf"]
    assert conf, "*.conf is not pinned in .gitattributes"
    assert "eol=lf" in conf[0], conf[0]
    assert JOURNALD_DROPIN.suffix == ".conf", (
        "the drop-in must keep the .conf suffix the pinned rule matches"
    )


def test_the_fluentd_drop_gap_has_a_concern_file():
    """The journal is durable; everything upstream of the sidecar is not.

    A runbook line is guidance, not a tracked item — it has no home to be deleted
    from when the gap is closed. `docs/concerns/` is that home, and the file's
    front-matter row is what makes the gap visible in `scripts/concerns_index.py`
    to a reader who never opens the runbook.
    """
    concern = REPO_ROOT / "docs" / "concerns" / (
        "2026-09-26-fluentd-driver-drops-while-vector-is-down.md"
    )
    assert concern.exists(), (
        "the fluentd-drop gap must be a tracked concern, not only a runbook line"
    )
    head = concern.read_text(encoding="utf-8")[:2000]
    assert head.startswith("---\n") and "\nseverity:" in head, (
        "concern file has no front-matter, so scripts/concerns_index.py cannot list it"
    )

    text = concern.read_text(encoding="utf-8")
    # The blocker is the specific reason this is not fixed in the same change, and
    # it is the part a future reader would otherwise re-derive.
    assert "journalctl" in text and "alpine" in text.lower()
    # And it must say what closing it looks like, or it is a complaint.
    assert "journald" in text
    assert "deploy_fail_safe.sh" in text

    runbook = RUNBOOK.read_text(encoding="utf-8")
    assert concern.name in runbook, (
        "the runbook must point at the concern file, so the gap is findable from "
        "the doc someone reads mid-incident"
    )


def test_the_installer_cannot_report_an_unapplied_journald_policy_as_converged():
    """journald reads its config at START, so bytes on disk are not policy in
    effect.

    The first version installed the drop-in, tolerated a failed restart, and had a
    gate comparing only bytes — so every later install said "already current"
    while journald ran the old policy, forever (cross-family review,
    output/codex-log-durability-review.md §2). The applied-stamp is what makes
    "installed but not applied" a state the transaction repairs.
    """
    text = INSTALLER.read_text(encoding="utf-8")
    assert "JOURNALD_STAMP" in text
    assert "journald_applied()" in text

    gate = text.split("current_release_is_exact() {", 1)[1]
    gate = gate.split("\nif current_release_is_exact", 1)[0]
    assert "journald_applied || return 1" in gate, (
        "the gate exits before the first mutation, so a property it does not "
        "check is one the installer never converges"
    )

    # The stamp must be written only AFTER a successful restart, and dropped when
    # the restart fails — otherwise it asserts something unfalsifiable.
    apply_block = text.split('|| ! journald_applied; then', 1)[1]
    apply_block = apply_block.split("\nTIMERS_PAUSED=0", 1)[0]
    restart_at = apply_block.index("restart systemd-journald")
    write_at = apply_block.index("${JOURNALD_STAMP}.new.")
    assert restart_at < write_at, "the stamp is written before the restart is known"
    assert 'rm -f -- "${JOURNALD_STAMP}"' in apply_block, (
        "a failed restart must drop any stale stamp, or the next install inherits "
        "a claim that this policy is live"
    )
    # It must not live where systemd would try to parse it.
    assert '${RUNTIME_ROOT}/.journald-applied' in text
