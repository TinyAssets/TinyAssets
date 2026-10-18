"""Tests for the DR-drill-blocking invariants fixed in Task #12.

Two invariants, both structural (grep-shape), both guarded here so a
future refactor can't silently reintroduce the exit-5 drill abort class
that ate DR drill #3:

  (a) `deploy/backup-restore.sh` must NOT attempt to start the daemon.
      The drill's dedicated "Start compose on drill Droplet" step owns
      that with full retry + probe logic. Coupling caused the restore
      script to exit 5 when cloudflared couldn't initialize without a
      real CLOUDFLARE_TUNNEL_TOKEN on the drill droplet — the daemon
      itself was perfectly capable of starting.

  (b) `.github/workflows/dr-drill.yml` "Start compose" step must scope
      to `docker compose up -d daemon` (not a bare `up -d`). Bringing
      up all services on a drill droplet fails hard on cloudflared +
      vector because those require real secrets that don't exist on a
      fresh drill provision.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_RESTORE = _REPO / "deploy" / "backup-restore.sh"
_DR_DRILL = _REPO / ".github" / "workflows" / "dr-drill.yml"


# ---- (a) restore script: no start-daemon coupling ------------------------


def test_restore_script_exists():
    assert _RESTORE.is_file()


def _restore_code() -> str:
    """Return the script body *excluding* comment lines AND `log "..."`
    message strings. Both of these legitimately reference the
    historical behavior ('docker start failed', 'try: systemctl
    restart') as operator guidance without actually invoking those
    commands. We only want to catch real shell-command invocations.
    """
    text = _RESTORE.read_text(encoding="utf-8")
    cleaned: list[str] = []
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        # Strip lines that are log-message strings — they contain
        # narrative text (operator guidance) not shell invocations.
        if stripped.startswith('log "') or stripped.startswith("log '"):
            continue
        cleaned.append(line)
    return "\n".join(cleaned)


def test_restore_does_not_call_docker_start():
    """`docker start tinyassets-daemon` in the active code path was the
    2026-04-22 drill blocker. Must stay out."""
    code = _restore_code()
    assert "docker start tinyassets-daemon" not in code, (
        "backup-restore.sh must not `docker start tinyassets-daemon` — "
        "the drill's own Start step owns that with retry + probe logic"
    )


def test_restore_does_not_call_systemctl_start():
    """`systemctl start tinyassets-daemon` / `restart` from restore would
    trip the ExecStartPre ENV-UNREADABLE guard on a fresh drill droplet
    (Task #3). Keep it out of the restore path."""
    code = _restore_code()
    # Tolerant regex — catches `systemctl start`, `systemctl restart`,
    # and any `systemctl reload-or-restart` variants.
    assert not re.search(r"systemctl\s+(start|restart|reload-or-restart)\s+tinyassets-daemon",
                         code), (
        "backup-restore.sh must not systemctl-(re)start tinyassets-daemon"
    )


def test_restore_stops_every_running_volume_consumer_before_swap():
    """The pre-swap stop must cover every running container mounting the volume."""
    code = _restore_code()
    assert 'docker ps -q --filter "volume=${BACKUP_VOLUME}"' in code, (
        "restore must discover every running container mounting the target volume"
    )
    assert 'docker stop "${containers[@]}"' in code, (
        "restore must stop all discovered running consumers before the swap"
    )


def test_restore_uses_docker_inspected_mountpoint_not_derived_path():
    code = _restore_code()
    assert "docker volume inspect --format" in code
    assert '/var/lib/docker/volumes/${BACKUP_VOLUME}/_data' not in code


def test_restore_exits_zero_after_extract():
    """The last `exit` in the active code path must be 0, indicating
    the split-responsibility contract (restore done, caller starts)."""
    code = _restore_code()
    # Walk through all `exit N` statements; the final one in normal flow
    # should be `exit 0` (success exit after extract). The earlier exits
    # are error-path (exit 1/2/3/4).
    exits = re.findall(r"^\s*exit\s+(\d+)\s*$", code, flags=re.MULTILINE)
    assert exits, "restore script should have explicit exit statements"
    # The LAST exit in the script is the success path.
    assert exits[-1] == "0", (
        f"restore script's final exit must be 0 (success after extract); "
        f"found exit sequence: {exits}"
    )


# ---- (b) DR-drill workflow: daemon-scoped compose-up --------------------


try:
    import yaml as _yaml
    _YAML_AVAILABLE = True
except ImportError:
    _YAML_AVAILABLE = False


pytestmark_yaml = pytest.mark.skipif(
    not _YAML_AVAILABLE, reason="pyyaml not installed",
)


def _drill_yaml() -> dict:
    return _yaml.safe_load(_DR_DRILL.read_text(encoding="utf-8"))


def _start_compose_step() -> dict:
    for step in _drill_yaml()["jobs"]["drill"]["steps"]:
        if step.get("name") == "Start compose on drill Droplet":
            return step
    raise AssertionError("'Start compose on drill Droplet' step missing")


@pytestmark_yaml
def test_drill_start_compose_scopes_to_daemon_service():
    """The drill's compose-up must explicitly target the `daemon` service.
    A bare `compose up -d` would also try to start cloudflared + vector,
    both of which fail on a fresh drill droplet missing their real
    secrets, causing the step to abort before the probe ever runs.
    """
    run = _start_compose_step()["run"]
    assert "-f /opt/tinyassets/deploy/compose.yml up -d daemon" in run, (
        "drill's compose-up must scope to `daemon` service only; a bare "
        "`up -d` would also try to start cloudflared + vector and fail"
    )


@pytestmark_yaml
def test_drill_start_compose_does_not_start_cloudflared():
    """Belt + suspenders: confirm cloudflared isn't explicitly listed."""
    run = _start_compose_step()["run"]
    # The step must not start cloudflared. Any `up -d <...> cloudflared`
    # pattern is wrong. Negative check — this fires if someone later
    # edits the step to add cloudflared.
    assert "cloudflared" not in run, (
        "drill must not start the cloudflared service (no real tunnel "
        "token on drill droplet); probe hits daemon port directly"
    )


def test_drill_probe_still_hits_daemon_port():
    """Sanity: the probe still targets port 8001 (whether via external
    IP, SSH tunnel, or curl-over-ssh). Protects against regressions
    where someone routes the probe through cloudflared or a different
    port — drill mode stops working if the probe doesn't go to 8001."""
    text = _DR_DRILL.read_text(encoding="utf-8")
    assert ":8001" in text, (
        "drill probe must target daemon port 8001; regressions here "
        "break the whole drill end-to-end"
    )


@pytestmark_yaml
def test_drill_probe_uses_ssh_tunnel_not_external_http():
    """DR drill #4 (2026-04-22) surfaced: compose.yml binds daemon on
    `127.0.0.1:8001:8001` (loopback only), so external HTTP to
    `${DRILL_IP}:8001/mcp` gets connection-refused regardless of
    daemon health. Task #13 fix: probe via SSH port-forward so the
    probe hits the droplet's loopback.

    This test pins the fix shape so someone doesn't accidentally
    'fix by changing compose.yml to 0.0.0.0:8001' — that would work
    for the drill but unnecessarily exposes the prod daemon port on
    the public IP. The correct fix is probe-side (SSH tunnel), not
    compose-side (binding change).
    """
    for step in _drill_yaml()["jobs"]["drill"]["steps"]:
        if step.get("name") == "Probe drill Droplet directly":
            run = step.get("run", "")
            break
    else:
        raise AssertionError("'Probe drill Droplet directly' step missing")

    # The probe must use SSH port-forward (`ssh -L ... -f -N`) or
    # an inline `ssh ... curl ...` invocation — NOT a bare external
    # HTTP probe to ${DRILL_IP}:8001.
    has_port_forward = "-L 8001:" in run
    has_ssh_curl = "ssh " in run and "curl" in run and "127.0.0.1:8001" in run
    assert has_port_forward or has_ssh_curl, (
        "drill probe must use SSH tunnel or ssh-curl to reach the daemon's "
        "loopback binding; external ${DRILL_IP}:8001 is connection-refused"
    )

    # Negative check: the probe URL, if it's localhost-based, must be
    # `localhost` or `127.0.0.1` — not the external drill IP.
    # Catches the "revert the fix by pointing at DRILL_IP again" class.
    if "mcp_probe.py" in run and "--url" in run:
        # Extract the URL argument. It should be localhost/127.0.0.1.
        assert "http://localhost:8001" in run or "http://127.0.0.1:8001" in run, (
            "mcp_probe.py invocation must point at localhost/127.0.0.1 "
            "(SSH-forwarded port), not the external drill IP"
        )


@pytestmark_yaml
def test_drill_does_not_require_0_0_0_0_binding():
    """Regression guard: the drill must NOT depend on compose.yml being
    edited to bind daemon on 0.0.0.0 instead of 127.0.0.1. If someone
    'fixes' the drill by changing the compose binding, that'd expose
    prod's daemon port on the public IP unnecessarily.

    This check walks every drill step and confirms none of them sed/edit
    the compose.yml port binding. The drill's responsibility is to work
    around the loopback binding (via SSH tunnel), not to change it.
    """
    wf = _drill_yaml()
    for step in wf["jobs"]["drill"]["steps"]:
        run = str(step.get("run", ""))
        # Catch any sed/awk edit that changes the port mapping.
        assert "0.0.0.0:8001" not in run, (
            f"drill step {step.get('name')!r} attempts to rebind daemon "
            f"on 0.0.0.0:8001 — prod-unsafe. Use SSH tunnel instead."
        )
        # Catch any sed rewrite of the existing loopback binding.
        assert "127.0.0.1:8001:8001" not in run or "compose.yml" not in run, (
            "drill must not sed/edit compose.yml's port binding"
        )
