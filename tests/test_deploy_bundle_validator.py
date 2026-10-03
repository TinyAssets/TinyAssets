"""The bundle validator, run against the shape REAL Docker Compose emits.

Why this file exists
--------------------
PR #2685's first production deploy refused a correct bundle
(`deploy_result=bundle_invalid`, "daemon.env_file is []") while
`tests/test_deploy_bundle_transaction.py` was 60/60 green. That suite drives the
validator through a fake `docker compose config`, and the fake modelled
`env_file` as a passthrough key. Compose v5 -- the droplet runs v5.1.3 --
resolves every `env_file` into `environment` and drops the key, so the suite was
asserting the fake's behaviour rather than Compose's. #2696 fixed both sides:
the validator reads `env_file` from a second `--no-interpolate` render, and the
fake reproduces the drop.

That fake is now correct. But a fake can only ever encode what its author
believed, which is precisely how this reached production. So this module is an
independent check that does not use the fake at all: it runs the validator's own
Python -- extracted from `deploy/deploy_fail_safe.sh`, never a copy -- against
JSON captured from a real

    docker compose --env-file /etc/tinyassets/env \\
        -f /opt/tinyassets/compose.yml config --format json

on the droplet (2026-08-30), plus the `--no-interpolate` companion render the
validator now also consumes.

It needs no bash, no docker and no WSL, so unlike the transaction suite it runs
on Windows as well as Linux CI. If the validator is changed to want a shape
production does not produce, this goes red everywhere.

Maintaining the capture
-----------------------
Re-measure on the droplet and reconcile `_render()` / `_render_no_interpolate()`
whenever Compose is upgraded or the compose file gains a directive these assert.
A capture that has silently drifted from production re-creates exactly the false
confidence this file exists to prevent -- it is worse than having no capture,
because it looks like evidence.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "deploy" / "deploy_fail_safe.sh"
REAL_COMPOSE = REPO / "deploy" / "compose.yml"

RUNTIME = "/opt/tinyassets"
ENV_FILE = "/etc/tinyassets/env"
IMAGE = "ghcr.io/tinyassets/tinyassets-daemon@sha256:" + "b" * 64


def _script_int(name: str) -> int:
    """One `NAME=<int>` assignment read out of the deploy script."""
    match = re.search(rf"^{name}=(\d+)$", SCRIPT.read_text(encoding="utf-8"), re.M)
    assert match, f"{name} is no longer a plain integer assignment in {SCRIPT.name}"
    return int(match.group(1))


MAX_STOP_GRACE_S = _script_int("MAX_DAEMON_STOP_GRACE_S")


def _validator_source() -> str:
    """The validator as it ships, lifted out of the shell heredoc."""
    text = SCRIPT.read_text(encoding="utf-8")
    match = re.search(r"<<'PY'\n(.*?)\nPY\n", text, re.S)
    assert match, "could not extract the embedded validator from deploy_fail_safe.sh"
    return match.group(1)


def _render() -> dict:
    """`docker compose config --format json`, as measured on the droplet.

    The details that matter, all of which the original fake got wrong:
      * `services.daemon` has NO `env_file` key -- Compose v5 resolves it away
      * `environment` therefore carries the merged result (TINYASSETS_DATA_DIR)
      * `mem_limit` is the STRING '4294967296', not an int and not '4g'
      * a named volume carries `volume: {}` and no `read_only`
    """
    return {
        "services": {
            "daemon": {
                "cap_drop": ["ALL"],
                "command": ["python", "-m", "tinyassets.universe_server"],
                "container_name": "tinyassets-daemon",
                "entrypoint": ["/usr/bin/tini", "--"],
                "environment": {
                    "HOME": "/app",
                    "TINYASSETS_DATA_DIR": "/data",
                    "CODEX_HOME": "/data/.codex",
                    "CLAUDE_CONFIG_DIR": "/data/.claude",
                    "TINYASSETS_IMAGE": IMAGE,
                },
                "healthcheck": {
                    "test": ["CMD", "python", "/app/scripts/mcp_public_canary.py"],
                    "interval": "30s",
                },
                "image": IMAGE,
                "labels": {"org.tinyassets.component": "daemon"},
                "logging": {
                    "driver": "fluentd",
                    "options": {
                        "fluentd-address": "127.0.0.1:24224",
                        "fluentd-async": "true",
                        "tag": "{{.Name}}",
                    },
                },
                "mem_limit": "4294967296",
                "memswap_limit": "4294967296",
                "networks": {"default": None},
                "ports": [{"mode": "ingress", "target": 8001, "published": "8001"}],
                "restart": "unless-stopped",
                "security_opt": ["seccomp=unconfined"],
                "volumes": [
                    {
                        "type": "volume",
                        "source": "tinyassets-data",
                        "target": "/data",
                        "volume": {},
                    }
                ],
            },
            "cloudflared": {
                "container_name": "tinyassets-tunnel",
                "image": "cloudflare/cloudflared:2026.3.0@sha256:" + "6" * 64,
                "restart": "unless-stopped",
                "logging": {
                    "driver": "fluentd",
                    "options": {
                        "fluentd-address": "127.0.0.1:24224",
                        "fluentd-async": "true",
                        "tag": "{{.Name}}",
                    },
                },
            },
            "logs": {
                "container_name": "tinyassets-logs",
                "image": "timberio/vector:0.40.0-alpine@sha256:" + "7" * 64,
                "restart": "unless-stopped",
                # Measured 2026-09-26 against Compose v5.1.4 rather than on the
                # droplet: this change INTRODUCES the directive, so production
                # cannot yet have rendered it. Reproduce with
                #   docker compose -f deploy/compose.yml config --format json
                # and read services.logs.logging. The rest of this capture
                # remains the 2026-08-30 droplet measurement.
                "logging": {"driver": "journald", "options": {"tag": "tinyassets-logs"}},
                "volumes": [
                    {
                        "type": "bind",
                        "source": f"{RUNTIME}/deploy/{name}",
                        "target": f"/etc/vector/{name}",
                        "read_only": True,
                    }
                    for name in (
                        "vector.yaml",
                        "vector-betterstack.yaml",
                        "vector-entrypoint.sh",
                    )
                ],
            },
        }
    }


def _render_no_interpolate() -> dict:
    """`config --format json --no-interpolate`, the companion render.

    Compose keeps `env_file` here, as `{path, required}` mappings, and leaves
    `${...}` unexpanded. The validator reads only `env_file` from this one.
    """
    return {
        "services": {
            "daemon": {
                "container_name": "tinyassets-daemon",
                "image": "${TINYASSETS_IMAGE:?Set TINYASSETS_IMAGE ...}",
                "env_file": [
                    {"path": ENV_FILE, "required": True},
                    {"path": "/etc/tinyassets/agent-interchange.env", "required": True},
                    {"path": "/etc/tinyassets/request-idempotency.env", "required": True},
                ],
            },
            "cloudflared": {"container_name": "tinyassets-tunnel"},
            "logs": {"container_name": "tinyassets-logs"},
        }
    }


def _source() -> str:
    return REAL_COMPOSE.read_text(encoding="utf-8")


def _validate(
    tmp_path: Path,
    config: dict,
    source: str,
    uninterpolated: dict | None = None,
) -> subprocess.CompletedProcess:
    if uninterpolated is None:
        uninterpolated = _render_no_interpolate()
    (tmp_path / "validator.py").write_text(_validator_source(), encoding="utf-8")
    (tmp_path / "config.json").write_text(json.dumps(config), encoding="utf-8")
    (tmp_path / "compose.yml").write_text(source, encoding="utf-8")
    (tmp_path / "raw.json").write_text(json.dumps(uninterpolated), encoding="utf-8")
    return subprocess.run(
        [
            sys.executable,
            str(tmp_path / "validator.py"),
            str(tmp_path / "config.json"),
            str(tmp_path / "compose.yml"),
            str(tmp_path / "raw.json"),
        ],
        capture_output=True,
        text=True,
        env={
            "RUNTIME_DIR": RUNTIME,
            "EXPECT_IMAGE": IMAGE,
            "ENV_FILE": ENV_FILE,
            # Read from the script, never a literal here: the validator reads it
            # with `os.environ[...]` on purpose, so a shell that forgets to export
            # it fails loudly, and a test that hard-coded the number would keep
            # passing after the deploy script changed it.
            "MAX_DAEMON_STOP_GRACE_S": str(MAX_STOP_GRACE_S),
            "SYSTEMROOT": "C:/Windows",  # cpython needs this on Windows
            "PATH": "",
        },
    )


# ---------------------------------------------------------------------------
# the regression this file was written for
# ---------------------------------------------------------------------------


def test_the_real_rendering_is_accepted(tmp_path: Path):
    """The exact shape the droplet produced, with the shipped compose.yml.

    This is the case that failed in production while the mocked suite was green.
    """
    result = _validate(tmp_path, _render(), _source())
    assert result.returncode == 0, (
        "the validator refused a rendering production actually produces:\n"
        f"{result.stderr}"
    )


def test_a_rendering_without_env_file_is_not_treated_as_missing_secrets(
    tmp_path: Path,
):
    """The production refusal, isolated: no `env_file` key in the interpolated
    render is not the same as no env_file configured."""
    config = _render()
    assert "env_file" not in config["services"]["daemon"], (
        "precondition: Compose v5 emits no env_file key in the default render"
    )
    result = _validate(tmp_path, config, _source())
    assert result.returncode == 0, result.stderr
    assert "env_file" not in result.stderr, result.stderr


# ---------------------------------------------------------------------------
# env_file, now read from the uninterpolated render — it must still bite
# ---------------------------------------------------------------------------


def test_losing_the_production_env_file_is_still_refused(tmp_path: Path):
    raw = _render_no_interpolate()
    raw["services"]["daemon"]["env_file"] = [
        {"path": "/etc/tinyassets/agent-interchange.env", "required": True}
    ]
    result = _validate(tmp_path, _render(), _source(), uninterpolated=raw)
    assert result.returncode == 1
    assert "env_file" in result.stderr


def test_no_env_file_in_either_render_is_refused(tmp_path: Path):
    raw = _render_no_interpolate()
    del raw["services"]["daemon"]["env_file"]
    result = _validate(tmp_path, _render(), _source(), uninterpolated=raw)
    assert result.returncode == 1
    assert "env_file" in result.stderr


def test_a_plain_string_env_file_list_is_accepted(tmp_path: Path):
    """Older Compose emits plain strings, not `{path, required}` mappings."""
    raw = _render_no_interpolate()
    raw["services"]["daemon"]["env_file"] = [ENV_FILE]
    result = _validate(tmp_path, _render(), _source(), uninterpolated=raw)
    assert result.returncode == 0, result.stderr


def test_env_file_falls_back_to_the_interpolated_render(tmp_path: Path):
    """The documented fallback for an older Compose that still carries it."""
    raw = _render_no_interpolate()
    del raw["services"]["daemon"]["env_file"]
    config = _render()
    config["services"]["daemon"]["env_file"] = [ENV_FILE]
    result = _validate(tmp_path, config, _source(), uninterpolated=raw)
    assert result.returncode == 0, result.stderr


def test_environment_is_still_checked_on_the_interpolated_render(tmp_path: Path):
    """env_file says what is wired; environment says what took effect."""
    config = _render()
    del config["services"]["daemon"]["environment"]["TINYASSETS_DATA_DIR"]
    result = _validate(tmp_path, config, _source())
    assert result.returncode == 1
    assert "TINYASSETS_DATA_DIR" in result.stderr


# ---------------------------------------------------------------------------
# mem_limit: every shape Compose may emit
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", [4294967296, "4294967296", "4g", "4096m"])
def test_every_mem_limit_shape_compose_may_emit_is_accepted(tmp_path: Path, value):
    config = _render()
    config["services"]["daemon"]["mem_limit"] = value
    result = _validate(tmp_path, config, _source())
    assert result.returncode == 0, f"{value!r} rejected:\n{result.stderr}"


@pytest.mark.parametrize("value", [0, "0", "", None, "not-a-size", True])
def test_a_missing_or_zero_mem_limit_is_still_refused(tmp_path: Path, value):
    config = _render()
    config["services"]["daemon"]["mem_limit"] = value
    result = _validate(tmp_path, config, _source())
    assert result.returncode == 1, f"{value!r} accepted"
    assert "mem_limit" in result.stderr


# ---------------------------------------------------------------------------
# the rest of the contract, against the real shape rather than the fake's
# ---------------------------------------------------------------------------


def test_a_named_volume_shape_satisfies_the_data_mount_check(tmp_path: Path):
    """`{'type':'volume','source':...,'target':'/data','volume':{}}` — no
    `read_only` key, which an over-strict check could trip on."""
    config = _render()
    assert config["services"]["daemon"]["volumes"][0]["volume"] == {}
    assert "read_only" not in config["services"]["daemon"]["volumes"][0]
    assert _validate(tmp_path, config, _source()).returncode == 0


def test_losing_the_data_volume_is_refused(tmp_path: Path):
    config = _render()
    config["services"]["daemon"]["volumes"] = []
    result = _validate(tmp_path, config, _source())
    assert result.returncode == 1
    assert "/data" in result.stderr


def test_losing_the_healthcheck_is_refused(tmp_path: Path):
    config = _render()
    del config["services"]["daemon"]["healthcheck"]
    result = _validate(tmp_path, config, _source())
    assert result.returncode == 1
    assert "healthcheck" in result.stderr


def test_an_unpinned_sidecar_image_is_refused(tmp_path: Path):
    config = _render()
    config["services"]["logs"]["image"] = "timberio/vector:latest"
    result = _validate(tmp_path, config, _source())
    assert result.returncode == 1
    assert "digest-pinned" in result.stderr


def test_a_writable_vector_mount_is_refused(tmp_path: Path):
    config = _render()
    config["services"]["logs"]["volumes"][0]["read_only"] = False
    result = _validate(tmp_path, config, _source())
    assert result.returncode == 1
    assert "read-only" in result.stderr


def test_an_extra_default_service_is_refused(tmp_path: Path):
    config = _render()
    config["services"]["surprise"] = {"image": "busybox"}
    result = _validate(tmp_path, config, _source())
    assert result.returncode == 1
    assert "service set" in result.stderr


def test_a_literal_daemon_image_is_refused_from_the_source(tmp_path: Path):
    """The source scan still bites against the real rendering."""
    source = _source().replace(
        "image: ${TINYASSETS_IMAGE:?Set TINYASSETS_IMAGE to an immutable "
        "ghcr.io/tinyassets/tinyassets-daemon@sha256:<digest> ref}",
        f"image: {IMAGE}",
        1,
    )
    result = _validate(tmp_path, _render(), source)
    assert result.returncode == 1
    assert "interpolate" in result.stderr


# ---------------------------------------------------------------------------
# where the logs come to rest (2026-09-26)
# ---------------------------------------------------------------------------


def test_a_logs_service_without_the_journald_driver_is_refused(tmp_path: Path):
    """json-file is Docker's DEFAULT, so this is what drift looks like: not a
    wrong value, an absent one. The sidecar's stdout is the only host-side copy
    of every container's output, and a container-scoped driver is deleted when
    this script force-recreates the service."""
    config = _render()
    del config["services"]["logs"]["logging"]
    result = _validate(tmp_path, config, _source())
    assert result.returncode == 1
    assert "logs.logging.driver is None" in result.stderr


def test_a_logs_service_on_json_file_is_refused(tmp_path: Path):
    config = _render()
    config["services"]["logs"]["logging"] = {
        "driver": "json-file",
        "options": {"max-size": "10m", "max-file": "3"},
    }
    result = _validate(tmp_path, config, _source())
    assert result.returncode == 1
    assert "expected 'journald'" in result.stderr


def test_a_logs_service_without_a_journal_tag_is_refused(tmp_path: Path):
    """Without the tag journald records a truncated container id that changes on
    every recreate, so the query meant to read ACROSS recreates cannot be
    written -- the driver alone does not buy the property."""
    config = _render()
    config["services"]["logs"]["logging"] = {"driver": "journald"}
    result = _validate(tmp_path, config, _source())
    assert result.returncode == 1
    assert "logs.logging.options.tag" in result.stderr


def test_a_forwarding_service_that_stops_forwarding_is_refused(tmp_path: Path):
    """Nothing else here constrains the daemon's own driver, so losing it would
    mean the sidecar receives nothing and the journal holds nothing -- with every
    other check still green."""
    for service in ("daemon", "cloudflared"):
        config = _render()
        config["services"][service]["logging"] = {"driver": "local"}
        result = _validate(tmp_path, config, _source())
        assert result.returncode == 1, service
        assert f"{service}.logging.driver is 'local'" in result.stderr


def test_the_logs_sidecar_may_not_forward_to_its_own_listener(tmp_path: Path):
    """A `logs` container on the fluent anchor would ship its own stdout into the
    listener that produced it."""
    config = _render()
    config["services"]["logs"]["logging"] = {
        "driver": "fluentd",
        "options": {"fluentd-address": "127.0.0.1:24224", "tag": "tinyassets-logs"},
    }
    result = _validate(tmp_path, config, _source())
    assert result.returncode == 1
    assert "expected 'journald'" in result.stderr


# ---------------------------------------------------------------------------
# the drain bound (2026-09-26, inverted 2026-10-01)
#
# `stop_grace_period` is how long a stopping daemon drains with its listener
# already closed, so it is public 502 time. On 2026-10-01 a 180s value held
# production down for 3m16s behind one long turn. It is a CEILING now. The key
# is still required, so the bound stays written down rather than silently left
# to docker's 10s default. Read from the SOURCE, not either render: compose
# normalizes durations and this check must not depend on which form this
# version emits.
# ---------------------------------------------------------------------------


def _without_grace(source: str) -> str:
    stripped = re.sub(r"^\s*stop_grace_period:.*\n", "", source, count=1, flags=re.M)
    assert stripped != source, "the shipped compose.yml no longer declares it here"
    return stripped


def test_losing_the_stop_grace_period_is_refused(tmp_path: Path):
    result = _validate(tmp_path, _render(), _without_grace(_source()))
    assert result.returncode == 1
    assert "stop_grace_period" in result.stderr
    assert "10s default" in result.stderr, (
        "the refusal must say what absence MEANS, not just that a key is missing")


@pytest.mark.parametrize("value", ["21s", "180s", "3m", "20001ms", "0m21s", "1h"])
def test_a_grace_above_the_ceiling_is_refused(tmp_path: Path, value: str):
    """Every form has to stay under the ceiling, not just the one the file uses."""
    source = re.sub(
        r"^(\s*)stop_grace_period:.*$", rf"\g<1>stop_grace_period: {value}",
        _source(), count=1, flags=re.M,
    )
    result = _validate(tmp_path, _render(), source)
    assert result.returncode == 1
    assert "must be at most" in result.stderr


@pytest.mark.parametrize(
    "value", ["20s", "0m20s", "20.0s", "20000ms", "10s", "1s", "500ms"],
)
def test_every_equivalent_duration_compose_accepts_is_accepted(tmp_path: Path, value: str):
    """Go duration syntax, because that is what compose documents.

    The first version took `(\\d+)(s|m)?` and refused `3m0s`, `180.0s` and
    `180000ms` -- all the same bound as the then-shipped `180s`, all valid
    compose (Codex on #4039, P2). A gate that blocks deploys, INCLUDING a rollback, must
    not refuse the next maintainer for writing an equivalent value.

    The oracle for WHICH forms compose accepts is
    `docs/audits/2026-09-26-pr4039-compose-repro.py` -- it runs `docker compose
    config` over each one. Re-run it before widening or narrowing this list; the
    first version of this test asserted from a guess about compose and was wrong
    about a bare integer (see the test below).
    """
    source = re.sub(
        r"^(\s*)stop_grace_period:.*$", rf"\g<1>stop_grace_period: {value}",
        _source(), count=1, flags=re.M,
    )
    result = _validate(tmp_path, _render(), source)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("value", ["later", "-180s", "180 s", "3minutes"])
def test_a_duration_this_check_cannot_read_is_refused_not_assumed(
    tmp_path: Path, value: str,
):
    """Refuse rather than guess: a bound this check cannot READ is one it cannot
    enforce, and silently accepting it is how the key came to mean 10 seconds.
    """
    source = re.sub(
        r"^(\s*)stop_grace_period:.*$", rf"\g<1>stop_grace_period: {value}",
        _source(), count=1, flags=re.M,
    )
    result = _validate(tmp_path, _render(), source)
    assert result.returncode == 1
    assert "not a duration this check can read" in result.stderr


def test_a_bare_integer_is_refused_here_and_by_compose_itself(tmp_path: Path):
    """`stop_grace_period: 180` is NOT valid compose -- it wants a duration.

    The first version of this suite listed a bare integer as an accepted form,
    which was simply wrong about compose (Codex checked v5.1.4). The real deploy
    never reaches this arm: `docker compose config` fails first and
    `validate_bundle` returns before the python runs. Asserted anyway, because a
    check that would have ACCEPTED an invalid file is a check that is not reading
    what it thinks it is.
    """
    source = re.sub(
        r"^(\s*)stop_grace_period:.*$", r"\g<1>stop_grace_period: 180",
        _source(), count=1, flags=re.M,
    )
    result = _validate(tmp_path, _render(), source)
    assert result.returncode == 1
    assert "not a duration this check can read" in result.stderr


def test_a_grace_buried_under_another_mapping_does_not_count(tmp_path: Path):
    """Codex reproduced exit 0 for this, which is the whole finding.

    `daemon_block_lines` returns every DESCENDANT of the daemon service, so a
    text-only match was satisfied by a key nested under `environment:` while
    docker had no service stop grace at all. A service property has to be a
    direct child to mean anything.
    """
    source = _without_grace(_source()).replace(
        "    environment:",
        "    environment:\n      stop_grace_period: 180s",
        1,
    )
    result = _validate(tmp_path, _render(), source)
    assert result.returncode == 1, (
        "a stop_grace_period under `environment:` is an env var, not a stop grace")
    assert "direct `stop_grace_period:`" in result.stderr


def test_a_second_grace_declaration_is_refused(tmp_path: Path):
    """Two keys in one mapping: YAML keeps the last, so a check reading the first
    would enforce a bound the daemon does not have."""
    source = re.sub(
        r"^(\s*)(stop_grace_period:.*)$", r"\g<1>\g<2>\n\g<1>stop_grace_period: 10s",
        _source(), count=1, flags=re.M,
    )
    result = _validate(tmp_path, _render(), source)
    assert result.returncode == 1
    assert "exactly one" in result.stderr


def test_another_services_grace_does_not_satisfy_the_daemons(tmp_path: Path):
    """The block walker is anchored on services.daemon; a sidecar's key must not
    stand in for it."""
    source = _without_grace(_source()).replace(
        "    container_name: tinyassets-tunnel",
        "    container_name: tinyassets-tunnel\n    stop_grace_period: 300s",
        1,
    )
    result = _validate(tmp_path, _render(), source)
    assert result.returncode == 1
    assert "stop_grace_period" in result.stderr
