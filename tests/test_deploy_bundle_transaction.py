"""`deploy/deploy_fail_safe.sh` runtime-bundle transaction, against a fake docker.

PR #2685 first restored the dropped compose/vector/unit sync as a workflow step
that INSTALLED. A cross-family review (Codex, 2026-08-29) refuted that shape:
config mutation sat outside the fail-safe transaction, so a failure in any later
step -- the auth-volume step, the deploy_fail_safe.sh scp, the host-mutation
lock, the candidate-image preflight -- stranded the new config under the old
image, and neither rollback path restored it. The workflow now only stages, into
a per-run ``/tmp/tinyassets-bundle-<run id>-<attempt>``, and this script owns:

    claim -> validate -> snapshot -> install -> converge -> restore

These tests drive the real script with a fake ``docker`` and ``systemctl`` on
PATH and assert on the FILES it left behind, not on its log lines.

Why a real script run and not a source grep: the thing that failed in production
was ordering (install before the transaction), and only an execution can show
that production files are still the old bytes when a bundle is refused.

Windows is skipped -- the script drives ``install(1)``, ``flock``, ``sha256sum``
and POSIX ownership -- but every test runs on Linux CI, which is the oracle.

Which test is the discriminator for which guarantee
---------------------------------------------------
Round 2 noted the mutation exercise left no durable artifact, so the map lives
here. Each row was verified by flipping that guarantee in ``deploy_fail_safe.sh``
and confirming the named test goes red (38/38 red, 2026-08-30):

===============================================  ==================================================
guarantee in deploy_fail_safe.sh                 discriminating test
===============================================  ==================================================
validation refuses a wrong bundle                ``invalid_bundle_is_refused`` (13 cases)
a partial stage is refused                       ``partial_bundle_is_refused``
the bundle is installed at all, at 0644/0755     ``valid_bundle_snapshots_installs``
an absent stage is image-only                    ``absent_bundle_is_an_image_only``
a vector change forces a logs recreate           ``changed_vector_inputs_force_recreate``
snapshot dirs cannot collide within one second   ``back_to_back_deploys_get_distinct``
``--restore-bundle`` actually restores           ``restore_bundle_reinstalls``
a pointer that will not advance is fatal         ``pointer_write_failure``
the pointer is renamed with ``mv -fT``           ``pointer_write_failure``
a failed ``--restore-bundle`` is fatal           ``restore_of_an_incomplete_snapshot``
a failed internal restore is fatal               ``logs_that_stays_dead_through_the_rollback``
a non-zero ``compose up`` is never accepted      ``failed_converge_rolls_back``
an image-only failure leaves the bundle alone    ``image_only_failed_deploy_leaves_an_older``
the dirty marker is written, and blocks          ``dirty_marker_blocks_the_next_normal_deploy``
the stage is claimed into a private copy         ``valid_bundle_snapshots_installs``
sidecar images are pinned and expected           ``invalid_bundle_is_refused``
the daemon keeps env_file / /data / healthcheck  ``invalid_bundle_is_refused``
the parser accepts Compose v5's mem_limit STRING  ``valid_bundle_snapshots_installs``
env_file is read from the uninterpolated render   ``valid_bundle_snapshots_installs``
the SOURCE interpolates ``${TINYASSETS_IMAGE}``  ``invalid_bundle_is_refused``
restore uses the manifest, not the contract      ``restore_puts_back_the_recorded_mode``
a missing manifest row fails the restore         ``restore_of_an_incomplete_snapshot``
retention never deletes the pointed snapshot     ``retention_keeps_the_pointed_snapshot_when_...``
``accept()`` re-checks logs last                 ``logs_that_exits_after_being_seen_running``
a rollback with logs down is not ``rolled_back`` ``logs_that_stays_dead_through_the_rollback``
the manifest lookup matches the key EXACTLY      ``a_missing_root_compose_row``
an unclearable marker is terminal                ``marker_that_will_not_clear_is_terminal``
an empty marker blocks, and does not fall back   ``an_empty_marker_blocks_a_normal_deploy``,
                                                 ``an_empty_marker_makes_restore_bundle_refuse``
comments are stripped before the source scan     ``variable_in_a_trailing_comment``
the source scan anchors at top-level services:   ``an_earlier_extension_block_named_daemon``
the private work copy is removed on exit         ``the_private_work_copy_is_removed_on_exit``
the CLAIMED copy is what gets installed          ``the_private_copy_is_what_gets_installed``
the rollback's own converge failure is fatal     ``rollback_converge_failure_is_rollback_failed``
===============================================  ==================================================

Three mutations SURVIVED a first pass, and none was a weak test to wave away:

* a bare second-resolution snapshot stamp -- a real bug; the name shape is now
  asserted, because whether two deploys share a second is not reproducible
* a discarded ``restart_stack`` status -- invisible until the fake could
  simulate a compose run that brings the daemon up and still exits non-zero
* the ROLLBACK's own ``restart_stack`` -- its failure reached
  ``rollback_failed`` by a second route (the logs recreate failing too), so the
  test now pre-seeds the vector inputs to rule that route out

Where a test needs a precondition like that to stay discriminating, it asserts
the precondition rather than relying on it.

The fake is not the oracle. #2685 shipped 60/60 green and was refused by the
first production deploy: the fake modelled `env_file` as a passthrough key, and
Compose v5 resolves it into `environment` and drops it. #2696 fixed that, and
this commit closed the two divergences it left -- `mem_limit` rendered as an int
where Compose emits the string '4294967296', and a named volume missing its
`volume: {}` shape. Both were load-bearing: against the int-emitting fake, a
validator mutated to reject numeric strings still passes 60/60.

A corrected fake is still only what its author believed, so the validator is
ALSO pinned against a capture of the real rendering in
``tests/test_deploy_bundle_validator.py``, which needs no docker and runs on
Windows too."""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "deploy" / "deploy_fail_safe.sh"
REAL_COMPOSE = REPO / "deploy" / "compose.yml"
REAL_ENV_HELPER = REPO / "deploy" / "install-tinyassets-env.sh"

_BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(
    os.name == "nt" or _BASH is None,
    reason=(
        "deploy_fail_safe.sh drives install(1)/flock/sha256sum and POSIX "
        "ownership; Linux CI is the oracle for this suite"
    ),
)

OLD_IMAGE = "ghcr.io/tinyassets/tinyassets-daemon@sha256:" + "a" * 64
NEW_IMAGE = "ghcr.io/tinyassets/tinyassets-daemon@sha256:" + "b" * 64
OTHER_IMAGE = "ghcr.io/tinyassets/tinyassets-daemon@sha256:" + "c" * 64
BUNDLE_KEEP = 5  # must track BUNDLE_KEEP in deploy/deploy_fail_safe.sh


# ---------------------------------------------------------------------------
# fake docker
# ---------------------------------------------------------------------------
# It reads the STAGED compose file for `config`, so an invalid fixture produces
# invalid JSON by construction rather than by a canned blob -- a canned blob
# would encode the behaviour under test and could never go red.

FAKE_DOCKER = r'''#!/usr/bin/env python3
"""Minimal docker stand-in: pull / run / inspect / image inspect / compose."""
import hashlib
import json
import os
import re
import sys


STATE_PATH = os.environ["FAKE_DOCKER_STATE"]
CALL_LOG = os.environ["FAKE_DOCKER_CALLS"]


def load_state():
    with open(STATE_PATH, encoding="utf-8") as handle:
        return json.load(handle)


def save_state(state):
    with open(STATE_PATH, "w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2, sort_keys=True)


def record(argv):
    with open(CALL_LOG, "a", encoding="utf-8") as handle:
        handle.write(" ".join(argv) + "\n")


def image_id(ref):
    return "sha256:" + hashlib.sha256(ref.encode("utf-8")).hexdigest()


def read_env_file(path):
    values = {}
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                values[key.strip()] = value
    except OSError:
        pass
    return values


_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(:\?[^}]*|:-[^}]*)?\}")


def interpolate(text, values):
    def sub(match):
        name, modifier = match.group(1), match.group(2) or ""
        value = values.get(name)
        if value:
            return value
        if modifier.startswith(":-"):
            return modifier[2:]
        if modifier.startswith(":?"):
            sys.stderr.write("required variable %s is missing\n" % name)
            raise SystemExit(1)
        return ""

    return _VAR.sub(sub, text)


def to_bytes(value):
    """Byte count as a STRING, which is what Compose v5 emits.

    Measured on the droplet 2026-08-30: `mem_limit: 4g` renders as
    '4294967296'. Returning an int here left the suite exercising a shape
    production does not produce -- the same class of divergence that made
    #2685 green in CI and refused on the box.
    """
    if isinstance(value, int):
        return str(value)
    text = str(value).strip().lower()
    for suffix, factor in (("g", 1024 ** 3), ("m", 1024 ** 2), ("k", 1024)):
        if text.endswith(suffix):
            return str(int(float(text[:-1]) * factor))
    try:
        return str(int(text))
    except ValueError:
        return value


def normalise_volumes(volumes):
    out = []
    for volume in volumes or []:
        if isinstance(volume, dict):
            out.append(volume)
            continue
        parts = str(volume).split(":")
        if len(parts) < 2:
            continue
        source, target = parts[0], parts[1]
        options = parts[2].split(",") if len(parts) > 2 else []
        if source.startswith("/"):
            out.append(
                {
                    "type": "bind",
                    "source": source,
                    "target": target,
                    "read_only": "ro" in options,
                }
            )
        else:
            # A named volume carries `volume: {}` and NO `read_only` key unless
            # it is actually read-only -- measured on the droplet 2026-08-30.
            entry = {
                "type": "volume",
                "source": source,
                "target": target,
                "volume": {},
            }
            if "ro" in options:
                entry["read_only"] = True
            out.append(entry)
    return out


def inline_env_files(service):
    """Read the env files into `environment`, as the real CLI does.

    Dropping the key alone left `environment` unfaithful, so a check added
    against a value that reaches the daemon THROUGH an env file would pass here
    and fail on the box. Service-level `environment` wins, as in Compose.
    """
    paths = service.get("env_file") or []
    if isinstance(paths, (str, dict)):
        paths = [paths]
    merged = {}
    for entry in paths:
        path = entry if isinstance(entry, str) else (entry or {}).get("path", "")
        required = True if isinstance(entry, str) else (entry or {}).get("required", True)
        if path and required and not os.path.isfile(path):
            # The real CLI refuses a missing env_file. Skipping it here is what
            # would let a deploy that never rendered daemon.env pass this suite.
            raise RuntimeError("env file %s not found" % path)
        if path:
            merged.update(read_env_file(path))
    merged.update(service.get("environment") or {})
    if merged:
        service["environment"] = merged
    return service


def compose_config(compose_file, env_values, interpolate_values=True):
    """Mirror the one Compose v5.1.3 behaviour the validator depends on: the
    default render inlines every `env_file` into `environment` and then DROPS
    the key, as the real CLI does (a missing file is skipped here, where the
    real CLI errors -- the one remaining divergence); `--no-interpolate` keeps
    `env_file`, normalised to {path, required} mappings, and leaves `${VAR}`
    unresolved. The earlier fake kept `env_file` verbatim, which is why #2685
    was green in CI and refused in production ("daemon.env_file is []",
    2026-08-30 00:34Z)."""
    # Only compose config needs YAML; inspect/pull/run are frequent cheap calls.
    import yaml

    with open(compose_file, encoding="utf-8") as handle:
        raw = handle.read()
    # Shell environment wins over the env file, exactly as compose interpolates.
    values = dict(env_values)
    values.update({k: v for k, v in os.environ.items() if v})
    data = yaml.safe_load(interpolate(raw, values) if interpolate_values else raw) or {}
    active = set(filter(None, os.environ.get("COMPOSE_PROFILES", "").split(",")))
    services = {}
    for name, service in (data.get("services") or {}).items():
        service = dict(service or {})
        profiles = set(service.pop("profiles", []) or [])
        if profiles and not (profiles & active):
            continue
        if "mem_limit" in service:
            service["mem_limit"] = to_bytes(service["mem_limit"])
        if "volumes" in service:
            service["volumes"] = normalise_volumes(service["volumes"])
        env_files = service.pop("env_file", None)
        if interpolate_values:
            # Inlined into environment, then the key is dropped -- both halves,
            # so `environment` is what the daemon would actually receive.
            service["env_file"] = env_files
            service = inline_env_files(service)
            service.pop("env_file", None)
        elif env_files is not None:
            if isinstance(env_files, str):
                env_files = [env_files]
            service["env_file"] = [
                entry if isinstance(entry, dict) else {"path": entry, "required": True}
                for entry in env_files
            ]
        services[name] = service
    data["services"] = services
    return data


def main(argv):
    record(argv)
    state = load_state()

    if argv[:1] == ["pull"]:
        if not state.get("pull_ok", True):
            sys.stderr.write("fake docker: pull refused\n")
            return 1
        state.setdefault("images", {})[argv[-1]] = image_id(argv[-1])
        save_state(state)
        return 0

    if argv[:1] == ["run"]:
        if "KNOWN_LAYOUTS" in argv[-1]:
            if not state.get("layout_probe_ok", True):
                return 1
            # Declarations belong to the requested image, not the running one.
            target = argv[argv.index("--entrypoint") + 2]
            declaration = state.get("known_layouts", {}).get(target, [1])
            print(state.get("layout_probe_output", json.dumps(declaration)))
        return 0 if state.get("preflight_ok", True) else 1

    if argv[:2] == ["volume", "inspect"]:
        # The data volume's host directory: the layout guard reads its marker.
        print(os.environ.get("FAKE_DOCKER_VOLUME_DIR") or os.path.dirname(STATE_PATH))
        return 0
    if argv[:2] == ["image", "inspect"]:
        fmt = argv[argv.index("-f") + 1] if "-f" in argv else ""
        target = argv[-1]
        images = state.get("images", {})
        if "{{.Id}}" in fmt:
            if target not in images:
                sys.stderr.write("Error: No such image: %s\n" % target)
                return 1
            print(images[target])
            return 0
        if "RepoDigests" in fmt:
            for ref, ident in images.items():
                if target in (ref, ident):
                    print(ref)
                    return 0
            sys.stderr.write("Error: No such image: %s\n" % target)
            return 1
        return 1

    if argv[:1] == ["inspect"]:
        fmt = argv[argv.index("-f") + 1] if "-f" in argv else ""
        name = argv[-1]
        container = (state.get("containers") or {}).get(name)
        if container is None:
            sys.stderr.write("Error: No such object: %s\n" % name)
            return 1
        # A container can be seen `running` and then die. logs_running() returns
        # on the first sighting and accept() only re-checks at the very end, so
        # the suite needs to be able to reproduce that window.
        if name == "tinyassets-logs" and state.get("logs_dies_after") is not None:
            seen = state.get("logs_inspects", 0) + 1
            state["logs_inspects"] = seen
            if seen > state["logs_dies_after"]:
                container = dict(container)
                container["status"] = "exited"
            save_state(state)
        if ".State.Health" in fmt:
            print(container.get("health") or container.get("status", "running"))
        elif "{{.State.Status}}" in fmt:
            print(container.get("status", "running"))
        elif "{{.Image}}" in fmt:
            print(container.get("image_id", ""))
        elif "{{.Config.Image}}" in fmt:
            print(container.get("image_ref", ""))
        elif ".Config.Env" in fmt:
            print(json.dumps(container.get("config_env") or []))
        else:
            print("")
        return 0

    if argv[:1] == ["top"]:
        container = (state.get("containers") or {}).get(argv[1])
        if container is None:
            sys.stderr.write("Error: No such container: %s\n" % argv[1])
            return 1
        print("PID")
        for pid in container.get("pids") or []:
            print(pid)
        return 0

    if argv[:1] == ["compose"]:
        env_file = argv[argv.index("--env-file") + 1] if "--env-file" in argv else ""
        compose_file = argv[argv.index("-f") + 1] if "-f" in argv else ""
        env_values = read_env_file(env_file)
        if "config" in argv:
            try:
                config = compose_config(
                    compose_file, env_values, interpolate_values="--no-interpolate" not in argv,
                )
            except SystemExit:
                raise
            except Exception as exc:  # noqa: BLE001 - mirrors compose's own exit 1
                sys.stderr.write("fake docker compose config: %s\n" % exc)
                return 1
            print(json.dumps(config, indent=2, sort_keys=True))
            # Injection point for the private-copy guarantee: `config` is the
            # last thing validation does, so writing here changes the STAGE
            # between validation and install. If the script installed from the
            # stage rather than its own copy, this poison would go live.
            for path, content in (state.get("mutate_stage_after_config") or {}).items():
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write(content)
            return 0
        if "up" in argv:
            ref = env_values.get("TINYASSETS_IMAGE", "")
            partial = ref in (state.get("up_partial_images") or [])
            if not partial and (
                not state.get("up_ok", True)
                or ref in (state.get("up_fail_images") or [])
            ):
                sys.stderr.write("fake docker: compose up refused for %s\n" % ref)
                return 1
            # A candidate that starts migrating the data while it boots.
            marker = (state.get("mark_migrating_on_up") or {}).get(ref)
            if marker:
                with open(marker, "w", encoding="utf-8") as handle:
                    handle.write('{"layout": 1, "state": "migrating"}')
            health = state.get("daemon_health", "healthy")
            # Per-image health: a rollback test needs the CANDIDATE to stay
            # unhealthy while the previous image comes back healthy.
            if ref in (state.get("unhealthy_images") or []):
                health = state.get("unhealthy_health", "starting")
            # The environment the created container holds: Config.Env is the
            # compose render's `environment` (env files inlined), and the
            # daemon process holds that plus whatever the image or entrypoint
            # adds (`extra_process_env`). Written as a procfs environ file so
            # the script reads it the way it reads the real one.
            rendered = compose_config(compose_file, env_values, interpolate_values=True)
            daemon_env = dict(
                ((rendered.get("services") or {}).get("daemon") or {}).get("environment") or {}
            )
            daemon_env.update(state.get("extra_config_env") or {})
            process_env = dict(daemon_env)
            process_env.update(state.get("extra_process_env") or {})
            pid = 4242
            environ = os.path.join(os.environ["PROC_ROOT"], str(pid), "environ")
            os.makedirs(os.path.dirname(environ), exist_ok=True)
            if state.get("environ_unreadable"):
                if os.path.exists(environ):
                    os.remove(environ)
            else:
                with open(environ, "w", encoding="utf-8") as handle:
                    handle.write("".join("%s=%s\0" % item for item in process_env.items()))
            # More processes: "unreadable" leaves a live pid whose environ
            # cannot be read; "gone" lists a pid that exited before the read.
            pids = [pid]
            for extra_pid, kind in (state.get("extra_pids") or {}).items():
                pids.append(int(extra_pid))
                extra_dir = os.path.join(os.environ["PROC_ROOT"], str(extra_pid))
                if kind == "unreadable":
                    os.makedirs(os.path.join(extra_dir, "environ"), exist_ok=True)
            containers = state.setdefault("containers", {})
            containers["tinyassets-daemon"] = {
                "status": "running",
                "health": health,
                "image_id": image_id(ref),
                "image_ref": ref,
                "config_env": ["%s=%s" % item for item in daemon_env.items()],
                "pids": pids,
            }
            containers.setdefault(
                "tinyassets-tunnel",
                {"status": state.get("tunnel_status", "running")},
            )["status"] = state.get("tunnel_status", "running")
            logs_status = state.get("logs_status", "running")
            if "--force-recreate" in argv:
                # A list lets the forward recreate fail and the ROLLBACK
                # recreate succeed, which is the difference between
                # `rolled_back` and `rollback_failed`.
                queued = state.get("logs_recreate_statuses")
                if queued:
                    logs_status = queued.pop(0)
                    state["logs_recreate_statuses"] = queued
                else:
                    logs_status = state.get("logs_recreate_status", logs_status)
            containers.setdefault("tinyassets-logs", {})["status"] = logs_status
            state.setdefault("images", {}).setdefault(ref, image_id(ref))
            save_state(state)
            if partial:
                # The dangerous shape: compose brought the daemon up and then
                # failed on something else. Everything accept() looks at is
                # green, so only the RETURN CODE says the converge was bad.
                sys.stderr.write("fake docker: compose up partially failed\n")
                return 1
            return 0
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
'''

# Refuses to remove one chosen path, so "the marker could not be cleared" is
# reachable without root: making the state dir unwritable would break the
# snapshot long before the clear.
FAKE_RM = r'''#!/bin/sh
match="${FAKE_RM_FAIL_MATCH:-}"
if [ -n "$match" ]; then
  for arg in "$@"; do
    if [ "$arg" = "$match" ]; then
      echo "fake rm: refusing to remove $arg" >&2
      exit 1
    fi
  done
fi
exec /bin/rm "$@"
'''

FAKE_SYSTEMCTL = r'''#!/bin/sh
printf 'systemctl %s\n' "$*" >> "$FAKE_SYSTEMCTL_CALLS"
for failing in ${FAKE_SYSTEMCTL_FAIL:-}; do
  if [ "$1" = "$failing" ]; then
    echo "fake systemctl: $1 refused" >&2
    exit 1
  fi
done
exit 0
'''

# Records every install(1) invocation, then delegates to the real one. The
# runtime assertions can compare CONTENT and MODE but not owner (an
# unprivileged test cannot chown to another uid), so the -o/-g the restore
# passes is checked here instead.
#
# It can also refuse ONCE for a chosen destination. That is how a part-way
# install is provoked without permissions games: making a directory unwritable
# breaks the RESTORE too, so it could never test "install failed, restore
# succeeded" — which is the path that has to leave the pointer alone.
FAKE_INSTALL = r'''#!/bin/sh
printf '%s\n' "$*" >> "$FAKE_INSTALL_CALLS"
match="${FAKE_INSTALL_FAIL_MATCH:-}"
if [ -n "$match" ] && [ ! -f "${FAKE_INSTALL_FAIL_STATE}" ]; then
  case "$*" in
    *"$match"*)
      : > "${FAKE_INSTALL_FAIL_STATE}"
      echo "fake install: refusing once for $match" >&2
      exit 1
      ;;
  esac
fi
exec /usr/bin/install "$@"
'''

# Writes KEY=value into ENV_FILE the way install-tinyassets-env.sh does. The
# real helper chowns to root:tinyassets and needs privilege; the transaction
# under test is the bundle, not the env write.
FAKE_ENV_HELPER = r'''#!/usr/bin/env bash
set -euo pipefail
# The daemon-env rendering is the REAL helper's code, run unprivileged: it is
# part of what this suite proves, not scaffolding around it.
real() { TINYASSETS_ENV_OWNER= TINYASSETS_ENV_READ_USER= bash "$REAL_ENV_HELPER" "$@"; }
case "${1:-}" in
  daemon-forbidden-names|render-daemon-env) real "$@"; exit $? ;;
esac
if [ "${FAKE_ENV_HELPER_FAIL:-0}" = "1" ]; then
  echo "fake env helper: refusing" >&2
  exit 3
fi
action="$1"; key="$2"
value="$(cat)"
tmp="$(mktemp)"
grep -v -E "^${key}=" "$ENV_FILE" > "$tmp" || true
printf '%s=%s\n' "$key" "$value" >> "$tmp"
cat "$tmp" > "$ENV_FILE"
rm -f "$tmp"
# As the real helper does: a write to the source re-renders the daemon's copy,
# unless the caller turned rendering off with an empty TINYASSETS_DAEMON_ENV_FILE.
if [ "${TINYASSETS_DAEMON_ENV_FILE-unset}" != "" ]; then
  TINYASSETS_DAEMON_ENV_SOURCE="$ENV_FILE" TINYASSETS_DAEMON_ENV_FILE="$DAEMON_ENV_FILE" \
    TINYASSETS_ENV_FILE="$DAEMON_ENV_FILE" real render-daemon-env >/dev/null
fi
'''


# ---------------------------------------------------------------------------
# the fake droplet
# ---------------------------------------------------------------------------

OLD_VECTOR = "# live vector.yaml (pre-deploy)\nsources: {}\n"
OLD_BETTERSTACK = "# live vector-betterstack.yaml (pre-deploy)\nsinks: {}\n"
OLD_ENTRYPOINT = "#!/bin/sh\n# live vector-entrypoint.sh (pre-deploy)\n"
OLD_UNIT = "[Unit]\nDescription=live unit (pre-deploy)\n"
OLD_COMPOSE = "# live compose.yml (pre-deploy)\nservices: {}\n"

NEW_VECTOR = "# staged vector.yaml\nsources: {}\ntransforms: {}\n"
NEW_BETTERSTACK = "# staged vector-betterstack.yaml\nsinks: {}\n"
NEW_ENTRYPOINT = "#!/bin/sh\n# staged vector-entrypoint.sh\n"
NEW_UNIT = "[Unit]\nDescription=staged unit\n"


class Box:
    """A temp-directory stand-in for the droplet's filesystem."""

    def __init__(self, root: Path):
        self.root = root
        self.runtime = root / "opt" / "tinyassets"
        self.unit_file = root / "etc" / "systemd" / "system" / "tinyassets-daemon.service"
        self.env_file = root / "etc" / "tinyassets" / "env"
        self.stage = root / "tmp" / "tinyassets-bundle"
        self.state_dir = root / "var" / "lib" / "tinyassets-deploy"
        self.bin = root / "bin"
        self.docker_state = root / "docker-state.json"
        self.docker_calls = root / "docker-calls.log"
        self.systemctl_calls = root / "systemctl-calls.log"
        self.install_calls = root / "install-calls.log"
        self.env_helper = root / "tmp" / "install-tinyassets-env.sh"
        self.lock = root / "host-mutation.lock"
        self.daemon_env_file = self.env_file.parent / "daemon.env"
        self.proc = root / "proc"
        self.helper_install_path = root / "usr" / "local" / "sbin" / "tinyassets-env"

    # -- live state ------------------------------------------------------
    @property
    def pointer(self) -> Path:
        return self.state_dir / "bundle-previous"

    @property
    def dirty(self) -> Path:
        return self.state_dir / "bundle-dirty"

    @property
    def snapshots(self) -> Path:
        return self.state_dir / "bundle-snapshots"

    def work_dirs(self) -> list[Path]:
        """Leftover private bundle copies under the state directory."""
        return sorted(self.state_dir.glob(".bundle-work.*"))

    def install_calls_text(self) -> str:
        return (
            self.install_calls.read_text(encoding="utf-8")
            if self.install_calls.exists()
            else ""
        )

    def modes(self) -> dict[str, int]:
        """The mode of every production file the bundle owns."""
        return {
            "compose": stat.S_IMODE((self.runtime / "compose.yml").stat().st_mode),
            "deploy_compose": stat.S_IMODE(
                (self.runtime / "deploy" / "compose.yml").stat().st_mode
            ),
            "vector": stat.S_IMODE(
                (self.runtime / "deploy" / "vector.yaml").stat().st_mode
            ),
            "betterstack": stat.S_IMODE(
                (self.runtime / "deploy" / "vector-betterstack.yaml").stat().st_mode
            ),
            "entrypoint": stat.S_IMODE(
                (self.runtime / "deploy" / "vector-entrypoint.sh").stat().st_mode
            ),
            "unit": stat.S_IMODE(self.unit_file.stat().st_mode),
        }

    def live(self) -> dict[str, str]:
        """Every production file the bundle owns, by destination."""
        return {
            "compose": (self.runtime / "compose.yml").read_text(encoding="utf-8"),
            "deploy_compose": (self.runtime / "deploy" / "compose.yml").read_text(
                encoding="utf-8"
            ),
            "vector": (self.runtime / "deploy" / "vector.yaml").read_text(
                encoding="utf-8"
            ),
            "betterstack": (
                self.runtime / "deploy" / "vector-betterstack.yaml"
            ).read_text(encoding="utf-8"),
            "entrypoint": (self.runtime / "deploy" / "vector-entrypoint.sh").read_text(
                encoding="utf-8"
            ),
            "unit": self.unit_file.read_text(encoding="utf-8"),
        }

    def docker_state_json(self) -> dict:
        return json.loads(self.docker_state.read_text(encoding="utf-8"))

    def set_docker_state(self, **changes) -> None:
        state = self.docker_state_json()
        state.update(changes)
        self.docker_state.write_text(json.dumps(state, indent=2), encoding="utf-8")

    def docker_calls_text(self) -> str:
        return (
            self.docker_calls.read_text(encoding="utf-8")
            if self.docker_calls.exists()
            else ""
        )

    def env_image(self) -> str:
        for line in self.env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("TINYASSETS_IMAGE="):
                return line.split("=", 1)[1]
        return ""

    # -- staging ---------------------------------------------------------
    def stage_bundle(self, compose_text: str | None = None) -> None:
        self.stage.mkdir(parents=True, exist_ok=True)
        (self.stage / "compose.yml").write_text(
            compose_text if compose_text is not None else self.valid_compose(),
            encoding="utf-8",
        )
        (self.stage / "vector.yaml").write_text(NEW_VECTOR, encoding="utf-8")
        (self.stage / "vector-betterstack.yaml").write_text(
            NEW_BETTERSTACK, encoding="utf-8"
        )
        (self.stage / "vector-entrypoint.sh").write_text(NEW_ENTRYPOINT, encoding="utf-8")
        (self.stage / "tinyassets-daemon.service").write_text(NEW_UNIT, encoding="utf-8")

    def repoint(self, compose_text: str) -> str:
        """Rewrite the production absolute paths onto this box's temp roots."""
        return compose_text.replace("/opt/tinyassets", str(self.runtime)).replace(
            "/etc/tinyassets", str(self.env_file.parent)
        )

    def valid_compose(self) -> str:
        """The REAL deploy/compose.yml, repointed at this box's temp roots.

        Using the shipped file rather than a fixture means dropping `mem_limit`,
        a container_name, the daemon's env_file or a vector mount from
        production compose.yml turns this suite red.
        """
        return self.repoint(REAL_COMPOSE.read_text(encoding="utf-8"))

    # -- invocation ------------------------------------------------------
    def env(self, **overrides) -> dict[str, str]:
        env = {
            **os.environ,
            "PATH": f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}",
            "ENV_FILE": str(self.env_file),
            "DAEMON_ENV_FILE": str(self.daemon_env_file),
            "PROC_ROOT": str(self.proc),
            "REAL_ENV_HELPER": str(REAL_ENV_HELPER),
            "HELPER_INSTALL_PATH": str(self.helper_install_path),
            "HELPER_INSTALL_OWNER": str(os.getuid()),
            "HELPER_INSTALL_GROUP": str(os.getgid()),
            "ENV_HELPER": str(self.env_helper),
            "RUNTIME_DIR": str(self.runtime),
            "UNIT_FILE": str(self.unit_file),
            "BUNDLE_DIR": str(self.stage),
            "BUNDLE_STATE_DIR": str(self.state_dir),
            "LOCK_FILE": str(self.lock),
            # Numeric ids: `install -o` needs privilege for any other owner, and
            # the tinyassets:tinyassets / root:root production contract is
            # asserted against the script's defaults in
            # test_install_ownership_defaults_match_the_unit_contract.
            "RUNTIME_OWNER": str(os.getuid()),
            "RUNTIME_GROUP": str(os.getgid()),
            "UNIT_OWNER": str(os.getuid()),
            "UNIT_GROUP": str(os.getgid()),
            "HEALTH_TIMEOUT": "5",
            "FAKE_DOCKER_STATE": str(self.docker_state),
            "FAKE_DOCKER_CALLS": str(self.docker_calls),
            "FAKE_SYSTEMCTL_CALLS": str(self.systemctl_calls),
            "FAKE_SYSTEMCTL_FAIL": "",
            "FAKE_INSTALL_CALLS": str(self.install_calls),
            "FAKE_INSTALL_FAIL_MATCH": "",
            "FAKE_INSTALL_FAIL_STATE": str(self.root / "install-refused.flag"),
            "FAKE_RM_FAIL_MATCH": "",
        }
        env.update(overrides)
        return env

    def run(self, *args: str, **overrides) -> subprocess.CompletedProcess:
        return subprocess.run(
            [_BASH, str(SCRIPT), *args],
            capture_output=True,
            text=True,
            env=self.env(**overrides),
        )


@pytest.fixture
def box(tmp_path: Path) -> Box:
    fake = Box(tmp_path)
    (fake.runtime / "deploy").mkdir(parents=True)
    fake.unit_file.parent.mkdir(parents=True)
    fake.env_file.parent.mkdir(parents=True)
    fake.stage.parent.mkdir(parents=True, exist_ok=True)
    fake.state_dir.mkdir(parents=True)
    fake.bin.mkdir()

    (fake.runtime / "compose.yml").write_text(OLD_COMPOSE, encoding="utf-8")
    (fake.runtime / "deploy" / "compose.yml").write_text(OLD_COMPOSE, encoding="utf-8")
    (fake.runtime / "deploy" / "vector.yaml").write_text(OLD_VECTOR, encoding="utf-8")
    (fake.runtime / "deploy" / "vector-betterstack.yaml").write_text(
        OLD_BETTERSTACK, encoding="utf-8"
    )
    (fake.runtime / "deploy" / "vector-entrypoint.sh").write_text(
        OLD_ENTRYPOINT, encoding="utf-8"
    )
    fake.unit_file.write_text(OLD_UNIT, encoding="utf-8")
    fake.env_file.write_text(f"TINYASSETS_IMAGE={OLD_IMAGE}\n", encoding="utf-8")
    # Every env file the compose lists, so the fake inlines the same set the
    # real CLI would rather than silently skipping the absent ones. Real Compose
    # errors on a missing env_file, so an absent one is not a shape production
    # can be in.
    for extra in (
        "agent-interchange.env",
        "request-idempotency.env",
        "app-ingress.env",
    ):
        (fake.env_file.parent / extra).write_text("", encoding="utf-8")

    fake_bins = [("docker", FAKE_DOCKER), ("systemctl", FAKE_SYSTEMCTL)]
    # Only shadow install(1)/rm(1) where the real ones are where the wrappers
    # expect them.
    if Path("/usr/bin/install").exists():
        fake_bins.append(("install", FAKE_INSTALL))
    if Path("/bin/rm").exists():
        fake_bins.append(("rm", FAKE_RM))
    for name, body in fake_bins:
        path = fake.bin / name
        path.write_text(body, encoding="utf-8", newline="\n")
        path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    fake.helper_install_path.parent.mkdir(parents=True)
    fake.env_helper.write_text(FAKE_ENV_HELPER, encoding="utf-8", newline="\n")
    fake.env_helper.chmod(0o755)

    import hashlib

    fake.docker_state.write_text(
        json.dumps(
            {
                "pull_ok": True,
                "preflight_ok": True,
                "up_ok": True,
                "daemon_health": "healthy",
                "tunnel_status": "running",
                "logs_status": "running",
                "images": {
                    OLD_IMAGE: "sha256:"
                    + hashlib.sha256(OLD_IMAGE.encode("utf-8")).hexdigest()
                },
                "containers": {
                    "tinyassets-daemon": {
                        "status": "running",
                        "health": "healthy",
                        "image_id": "sha256:"
                        + hashlib.sha256(OLD_IMAGE.encode("utf-8")).hexdigest(),
                        "image_ref": OLD_IMAGE,
                    },
                    "tinyassets-tunnel": {"status": "running"},
                    "tinyassets-logs": {"status": "running"},
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return fake


def _result(completed: subprocess.CompletedProcess) -> str:
    for line in reversed(completed.stdout.splitlines()):
        if line.startswith("deploy_result="):
            return line.split("=", 1)[1]
    return ""


def _deployed_image(completed: subprocess.CompletedProcess) -> str:
    for line in reversed(completed.stdout.splitlines()):
        if line.startswith("deployed_image="):
            return line.split("=", 1)[1]
    return ""


# ---------------------------------------------------------------------------
# (0) the harness itself must be able to go red
# ---------------------------------------------------------------------------


def test_fake_docker_rejects_a_compose_file_it_cannot_parse(box: Box):
    """A canned-JSON fake could never fail; this one parses the staged file."""
    box.stage_bundle(compose_text="services: [this is not a mapping\n")
    completed = box.run(NEW_IMAGE)
    assert completed.returncode == 1
    assert _result(completed) == "bundle_invalid"


# ---------------------------------------------------------------------------
# (a) invalid bundle -> refused before any install, production untouched
# ---------------------------------------------------------------------------


def _mutate(compose_text: str, old: str, new: str) -> str:
    assert old in compose_text, f"fixture drift: {old!r} is no longer in compose.yml"
    return compose_text.replace(old, new, 1)


@pytest.mark.parametrize(
    "label,old,new",
    [
        ("no daemon memory limit", "\n    mem_limit: 4g\n", "\n"),
        (
            "wrong logs container name",
            "container_name: tinyassets-logs",
            "container_name: tinyassets-logging",
        ),
        (
            "daemon image pinned instead of interpolated",
            "image: ${TINYASSETS_IMAGE:?Set TINYASSETS_IMAGE to an immutable "
            "ghcr.io/tinyassets/tinyassets-daemon@sha256:<digest> ref}",
            "image: ghcr.io/tinyassets/tinyassets-daemon:latest",
        ),
        (
            "a vector mount dropped",
            "      - /opt/tinyassets/deploy/vector-betterstack.yaml:"
            "/etc/vector/vector-betterstack.yaml:ro\n",
            "",
        ),
        (
            "a vector mount is writable",
            "/etc/vector/vector-entrypoint.sh:ro",
            "/etc/vector/vector-entrypoint.sh",
        ),
        (
            "daemon restart policy weakened",
            "    container_name: tinyassets-daemon\n    restart: unless-stopped",
            "    container_name: tinyassets-daemon\n    restart: 'no'",
        ),
        # Round 2, §2: nothing constrained the sidecars at all, and a daemon with
        # no secrets, no data volume or no healthcheck converged "successfully"
        # and served nothing.
        (
            "arbitrary cloudflared image",
            "image: cloudflare/cloudflared:",
            "image: attacker/cloudflared-but-not-really:",
        ),
        (
            "unpinned vector image",
            "image: timberio/vector:0.40.0-alpine@sha256:"
            "7a81fdd62e056321055a9e4bdec4073d752ecf68f4c192e676b85001721523c2",
            "image: timberio/vector:latest",
        ),
        (
            "daemon loses its env_file",
            "    env_file:\n      - /etc/tinyassets/daemon.env\n",
            "    env_file:\n",
        ),
        (
            "daemon loads the host secret store again",
            "    env_file:\n      - /etc/tinyassets/daemon.env\n",
            "    env_file:\n      - /etc/tinyassets/env\n",
        ),
        (
            "daemon loses its /data volume",
            "    volumes:\n      - tinyassets-data:/data\n",
            "    volumes:\n      - tinyassets-data:/somewhere-else\n",
        ),
        (
            "daemon environment loses TINYASSETS_DATA_DIR",
            "      TINYASSETS_DATA_DIR: /data\n",
            "",
        ),
        (
            "daemon loses its healthcheck",
            "    healthcheck:\n      # Liveness from release facts as the canary "
            "service principal.",
            "    x-healthcheck-removed:\n      # Liveness from release facts as the "
            "canary service principal.",
        ),
        (
            "daemon image pinned via a different variable",
            "image: ${TINYASSETS_IMAGE:?Set TINYASSETS_IMAGE to an immutable "
            "ghcr.io/tinyassets/tinyassets-daemon@sha256:<digest> ref}",
            "image: ${SOME_OTHER_IMAGE:-"
            "ghcr.io/tinyassets/tinyassets-daemon@sha256:"
            "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb}",
        ),
    ],
)
def test_invalid_bundle_is_refused_before_any_install(box: Box, label, old, new):
    """Any production invariant miss refuses with production files untouched.

    `config --services` passed on "any syntactically valid file containing three
    skeletal services named daemon, cloudflared and logs" (Codex, 2026-08-29);
    each case here is such a file.
    """
    # Mutate against the ORIGINAL production paths, then repoint: the mount and
    # env_file cases are written in terms of /opt/tinyassets and /etc/tinyassets.
    raw = REAL_COMPOSE.read_text(encoding="utf-8")
    mutated = box.repoint(_mutate(raw, old, new))
    before = box.live()
    box.stage_bundle(compose_text=mutated)

    completed = box.run(NEW_IMAGE)

    assert completed.returncode == 1, f"{label}: expected a refusal\n{completed.stderr}"
    assert _result(completed) == "bundle_invalid", label
    assert box.live() == before, f"{label}: production files were mutated"
    assert not box.pointer.exists(), f"{label}: the bundle pointer advanced"
    assert box.env_image() == OLD_IMAGE, f"{label}: TINYASSETS_IMAGE was swapped"
    assert "compose up" not in box.docker_calls_text(), f"{label}: the stack converged"


_DAEMON_IMAGE_LINE = (
    "image: ${TINYASSETS_IMAGE:?Set TINYASSETS_IMAGE to an immutable "
    "ghcr.io/tinyassets/tinyassets-daemon@sha256:<digest> ref}"
)


def test_variable_in_a_trailing_comment_does_not_satisfy_the_source_check(box: Box):
    """`image: <literal> # ${TINYASSETS_IMAGE}` rendered to the candidate and
    passed the substring scan; comments must be stripped first."""
    before = box.live()
    literal = f"image: {NEW_IMAGE}  # ${{TINYASSETS_IMAGE}}"
    raw = REAL_COMPOSE.read_text(encoding="utf-8")
    assert _DAEMON_IMAGE_LINE in raw
    box.stage_bundle(compose_text=box.repoint(raw.replace(_DAEMON_IMAGE_LINE, literal, 1)))

    completed = box.run(NEW_IMAGE)

    assert completed.returncode == 1, completed.stderr
    assert _result(completed) == "bundle_invalid"
    assert box.live() == before


def test_an_earlier_extension_block_named_daemon_does_not_satisfy_the_check(box: Box):
    """The scan must anchor at top-level `services:` -> `daemon:`.

    An `x-` extension mapping with its own indented `daemon:` block was found
    first, so a real service using a literal image passed.
    """
    before = box.live()
    raw = REAL_COMPOSE.read_text(encoding="utf-8")
    assert _DAEMON_IMAGE_LINE in raw
    decoy = (
        "x-decoy:\n"
        "  daemon:\n"
        "    image: ${TINYASSETS_IMAGE}\n"
    )
    poisoned = decoy + raw.replace(_DAEMON_IMAGE_LINE, f"image: {NEW_IMAGE}", 1)
    box.stage_bundle(compose_text=box.repoint(poisoned))

    completed = box.run(NEW_IMAGE)

    assert completed.returncode == 1, completed.stderr
    assert _result(completed) == "bundle_invalid"
    assert box.live() == before


def test_the_decoy_fixture_is_otherwise_valid(box: Box):
    """Control: the decoy alone, with the real interpolated image, deploys.

    Without this the test above could pass because the fixture is broken for
    some unrelated reason.
    """
    raw = REAL_COMPOSE.read_text(encoding="utf-8")
    decoy = "x-decoy:\n  daemon:\n    image: ${TINYASSETS_IMAGE}\n"
    box.stage_bundle(compose_text=box.repoint(decoy + raw))

    completed = box.run(NEW_IMAGE)

    assert completed.returncode == 0, completed.stderr
    assert _result(completed) == "deployed"


def test_the_private_copy_is_what_gets_installed(box: Box):
    """Change the STAGE after validation; the validated bytes must still win.

    This is the race the claim exists for: the workflow populates the stage
    before the script's lock exists, so anything with write access to /tmp could
    swap the bytes between `docker compose config` and `install`.
    """
    poison = "# POISON: written to the stage after validation\nsources: {}\n"
    box.stage_bundle()
    box.set_docker_state(
        mutate_stage_after_config={str(box.stage / "vector.yaml"): poison}
    )

    completed = box.run(NEW_IMAGE)

    assert completed.returncode == 0, completed.stderr
    assert (box.stage / "vector.yaml").read_text(encoding="utf-8") == poison, (
        "precondition: the stage really was changed mid-run"
    )
    assert box.live()["vector"] == NEW_VECTOR, (
        "the installed file must be the validated copy, not the changed stage"
    )


def test_the_private_work_copy_is_removed_on_exit(box: Box):
    """It holds a whole bundle and lives under persistent state."""
    box.stage_bundle()
    assert box.run(NEW_IMAGE).returncode == 0
    assert box.work_dirs() == [], f"left behind: {box.work_dirs()}"

    # and on a refusal path, which exits long before the install
    box.stage_bundle(compose_text="services: {}\n")
    assert box.run(OTHER_IMAGE).returncode == 1
    assert box.work_dirs() == [], f"left behind on refusal: {box.work_dirs()}"


def test_rollback_converge_failure_is_rollback_failed(box: Box):
    """The rollback's OWN `restart_stack` can fail; that is not `rolled_back`.

    Vector inputs are pre-seeded to match the bundle so the rollback does NOT
    force-recreate logs: that call would also fail here, and would reach
    `rollback_failed` by a different route, leaving the test unable to say which
    return code the script acted on.
    """
    (box.runtime / "deploy" / "vector.yaml").write_text(NEW_VECTOR, encoding="utf-8")
    (box.runtime / "deploy" / "vector-betterstack.yaml").write_text(
        NEW_BETTERSTACK, encoding="utf-8"
    )
    (box.runtime / "deploy" / "vector-entrypoint.sh").write_text(
        NEW_ENTRYPOINT, encoding="utf-8"
    )
    box.stage_bundle()
    box.set_docker_state(unhealthy_images=[NEW_IMAGE], up_fail_images=[OLD_IMAGE])

    completed = box.run(NEW_IMAGE)

    assert "--force-recreate" not in box.docker_calls_text(), (
        "precondition: no logs recreate, so only restart_stack can fail"
    )
    assert completed.returncode == 3, completed.stderr
    assert _result(completed) == "rollback_failed"
    assert _deployed_image(completed) == ""


def test_partial_bundle_is_refused_rather_than_half_installed(box: Box):
    """Half a bundle is the 2026-08 502; an incomplete stage dir must refuse."""
    box.stage_bundle()
    (box.stage / "vector-betterstack.yaml").unlink()
    before = box.live()

    completed = box.run(NEW_IMAGE)

    assert completed.returncode == 1
    assert _result(completed) == "bundle_invalid"
    assert box.live() == before
    assert "incomplete" in completed.stderr


# ---------------------------------------------------------------------------
# (b) valid bundle -> snapshot written, files installed, pointer advanced
# ---------------------------------------------------------------------------


def test_valid_bundle_snapshots_installs_and_advances_the_pointer(box: Box):
    before = box.live()
    box.stage_bundle()

    completed = box.run(NEW_IMAGE)

    assert completed.returncode == 0, completed.stderr
    assert _result(completed) == "deployed"
    assert _deployed_image(completed) == NEW_IMAGE

    # installed
    after = box.live()
    assert after["vector"] == NEW_VECTOR
    assert after["betterstack"] == NEW_BETTERSTACK
    assert after["entrypoint"] == NEW_ENTRYPOINT
    assert after["unit"] == NEW_UNIT
    assert after["compose"] == box.valid_compose()
    assert after["deploy_compose"] == box.valid_compose()

    # modes: 0644 for config, 0755 for the entrypoint script
    def mode(path: Path) -> int:
        return stat.S_IMODE(path.stat().st_mode)

    assert mode(box.runtime / "compose.yml") == 0o644
    assert mode(box.runtime / "deploy" / "compose.yml") == 0o644
    assert mode(box.runtime / "deploy" / "vector.yaml") == 0o644
    assert mode(box.runtime / "deploy" / "vector-betterstack.yaml") == 0o644
    assert mode(box.runtime / "deploy" / "vector-entrypoint.sh") == 0o755
    assert mode(box.unit_file) == 0o644

    # snapshot holds what was live BEFORE the install
    snapshot = Path(box.pointer.read_text(encoding="utf-8").strip())
    assert snapshot.is_dir()
    assert snapshot.parent == box.snapshots
    assert (snapshot / "compose.yml").read_text(encoding="utf-8") == before["compose"]
    assert (snapshot / "deploy" / "vector.yaml").read_text(
        encoding="utf-8"
    ) == before["vector"]
    assert (snapshot / "deploy" / "vector-betterstack.yaml").read_text(
        encoding="utf-8"
    ) == before["betterstack"]
    assert (snapshot / "deploy" / "vector-entrypoint.sh").read_text(
        encoding="utf-8"
    ) == before["entrypoint"]
    assert (snapshot / "systemd" / "tinyassets-daemon.service").read_text(
        encoding="utf-8"
    ) == before["unit"]

    assert box.env_image() == NEW_IMAGE
    assert "daemon-reload" in box.systemctl_calls.read_text(encoding="utf-8")


def test_back_to_back_deploys_get_distinct_snapshots(box: Box):
    """Two deploys inside one UTC second must not share a snapshot directory.

    A bare second-resolution stamp let the SECOND deploy write its snapshot into
    the first's directory -- capturing the files the first deploy had just
    installed, so the rollback target became the state being rolled back from.
    """
    box.stage_bundle()
    assert box.run(NEW_IMAGE).returncode == 0, "first deploy"
    first_snapshot = box.pointer.read_text(encoding="utf-8").strip()
    installed_by_first = box.live()

    box.stage_bundle(compose_text=box.valid_compose() + "\n# second deploy\n")
    assert box.run(OTHER_IMAGE).returncode == 0, "second deploy"
    second_snapshot = box.pointer.read_text(encoding="utf-8").strip()

    # The NAME carries the fix, and asserting it is the only deterministic part:
    # whether two deploys land in the same UTC second depends on how long the
    # run takes, so a content-only assertion passes by luck on a slow box.
    assert re.fullmatch(r"\d{8}T\d{6}Z-[A-Za-z0-9]{6}", Path(second_snapshot).name), (
        f"snapshot {Path(second_snapshot).name!r} is a bare second-resolution "
        "stamp; two deploys in one second would share it"
    )
    assert second_snapshot != first_snapshot
    assert Path(first_snapshot).is_dir(), "the first snapshot must survive"
    second = Path(second_snapshot)
    assert (second / "compose.yml").read_text(encoding="utf-8") == installed_by_first[
        "compose"
    ], "the second snapshot must hold what the FIRST deploy installed"
    assert (second / "deploy" / "vector.yaml").read_text(
        encoding="utf-8"
    ) == installed_by_first["vector"]


def test_snapshot_retention_keeps_the_last_five(box: Box):
    box.stage_bundle()
    # One real snapshot plus older directories is enough to cross the retention
    # boundary; seven full deploys only repeat the setup proved elsewhere.
    completed = box.run(NEW_IMAGE)
    assert completed.returncode == 0, completed.stderr
    box.set_docker_state(containers=box.docker_state_json()["containers"])
    for index in range(BUNDLE_KEEP):
        (box.snapshots / f"20260101T00000{index}Z").mkdir(exist_ok=True)
    completed = box.run(NEW_IMAGE)
    assert completed.returncode == 0, completed.stderr
    assert len(list(box.snapshots.iterdir())) <= 5, (
        "snapshot retention must keep the last 5"
    )


def test_pointer_does_not_advance_when_the_bundle_is_refused(box: Box):
    """The pointer must name the snapshot the LAST successful install replaced."""
    box.stage_bundle()
    assert box.run(NEW_IMAGE).returncode == 0
    good_snapshot = box.pointer.read_text(encoding="utf-8").strip()

    box.stage_bundle(compose_text="services: {}\n")
    completed = box.run(NEW_IMAGE)

    assert _result(completed) == "bundle_invalid"
    assert box.pointer.read_text(encoding="utf-8").strip() == good_snapshot


@pytest.mark.skipif(
    not Path("/usr/bin/install").exists(),
    reason="the install(1) wrapper needs the real binary at /usr/bin/install",
)
def test_failed_install_restores_the_snapshot_and_leaves_the_pointer_alone(box: Box):
    """A part-way install must roll back and must NOT advance the pointer.

    The pointer is the contract a rollback depends on: if it advanced before the
    install succeeded it would name the state this deploy created, and
    `--restore-bundle` would restore the very bundle it is meant to undo.
    """
    box.stage_bundle()
    assert box.run(NEW_IMAGE).returncode == 0
    good_snapshot = box.pointer.read_text(encoding="utf-8").strip()
    before = box.live()

    # install(1) refuses ONCE, part-way down the bundle; the restore that
    # follows uses the same binary and must succeed.
    box.stage_bundle()
    # A THIRD image, so "the env still names the previous deploy's image" is a
    # real assertion rather than a coincidence of the fixture.
    completed = box.run(
        OTHER_IMAGE, FAKE_INSTALL_FAIL_MATCH="deploy/vector-betterstack.yaml"
    )

    assert completed.returncode == 1, completed.stderr
    assert _result(completed) == "bundle_install_failed"
    assert box.live() == before, "a failed install must restore its own snapshot"
    assert box.pointer.read_text(encoding="utf-8").strip() == good_snapshot
    assert box.env_image() == NEW_IMAGE, "a refused install must not swap the image"
    assert not box.dirty.exists(), "a completed restore clears the dirty marker"


# ---------------------------------------------------------------------------
# (c) converge failure after install -> snapshot restored, previous image back
# ---------------------------------------------------------------------------


def test_unhealthy_candidate_restores_the_bundle_and_the_previous_image(box: Box):
    before = box.live()
    box.stage_bundle()
    # The candidate converges but never becomes healthy; the previous image does.
    box.set_docker_state(unhealthy_images=[NEW_IMAGE])

    completed = box.run(NEW_IMAGE)

    assert completed.returncode == 2, completed.stderr
    assert _result(completed) == "rolled_back"
    assert _deployed_image(completed) == OLD_IMAGE
    assert box.live() == before, (
        "rollback must restore the runtime bundle, not just the image -- "
        "converging PREV_IMAGE against the NEW compose file rolls back half a change"
    )
    assert box.env_image() == OLD_IMAGE


@pytest.mark.parametrize("version", [1, 2])
def test_no_image_converges_onto_data_a_migration_has_touched(box: Box, version):
    """The data layout guard (design D7.2), before anything is mutated: an image
    must never start on data marked ``migrating``, even if it knows the layout."""
    volume = box.root / "data-volume"
    volume.mkdir()
    (volume / ".layout.json").write_text(
        json.dumps({"layout": version, "state": "migrating"}), encoding="utf-8",
    )
    box.set_docker_state(known_layouts={NEW_IMAGE: [1, 2]})
    before = box.live()
    box.stage_bundle()

    completed = box.run(NEW_IMAGE, FAKE_DOCKER_VOLUME_DIR=str(volume))

    assert completed.returncode == 1, completed.stderr
    assert _result(completed) == "layout_refused"
    assert box.env_image() == OLD_IMAGE and box.live() == before, "prod must be untouched"


@pytest.mark.parametrize("supported,allowed", [([1, 2], True), ([1, 2, 3], True), ([1], False)])
def test_forward_deploy_uses_the_candidate_layout_declaration(box: Box, supported, allowed):
    volume = box.root / "data-volume"
    volume.mkdir()
    (volume / ".layout.json").write_text('{"layout": 2, "state": "stable"}', encoding="utf-8")
    # The running image remains layout-1-only in both cases.
    box.set_docker_state(known_layouts={NEW_IMAGE: supported, OLD_IMAGE: [1]})
    before = box.live()
    box.stage_bundle()

    completed = box.run(NEW_IMAGE, FAKE_DOCKER_VOLUME_DIR=str(volume))

    assert completed.returncode == (0 if allowed else 1), completed.stderr
    assert _result(completed) == ("deployed" if allowed else "layout_refused")
    if allowed:
        assert _deployed_image(completed) == NEW_IMAGE
        assert box.env_image() == NEW_IMAGE
    else:
        assert box.env_image() == OLD_IMAGE and box.live() == before
        assert "compose up" not in box.docker_calls_text()
    probe = next(line for line in box.docker_calls_text().splitlines() if "KNOWN_LAYOUTS" in line)
    assert f"--entrypoint python {NEW_IMAGE} -c" in probe
    assert "--network=none" in probe and "--volume" not in probe and "--mount" not in probe


@pytest.mark.parametrize("probe_state", [
    {"layout_probe_ok": False},
    {"layout_probe_output": ""},
    {"layout_probe_output": "garbage"},
    {"layout_probe_output": "{}"},
    {"layout_probe_output": "[]"},
    {"layout_probe_output": '["1", "2"]'},
    {"layout_probe_output": "[true, 2]"},
])
def test_missing_or_invalid_candidate_declaration_refuses_without_mutation(box: Box, probe_state):
    box.set_docker_state(**probe_state)
    before = box.live()
    box.stage_bundle()

    completed = box.run(NEW_IMAGE)

    assert completed.returncode == 1, completed.stderr
    assert _result(completed) == "layout_refused"
    assert box.env_image() == OLD_IMAGE and box.live() == before
    assert "compose up" not in box.docker_calls_text()


@pytest.mark.parametrize("explicit", [False, True])
def test_layout_two_still_refuses_rollback_to_an_older_image(box: Box, explicit):
    volume = box.root / "data-volume"
    volume.mkdir()
    (volume / ".layout.json").write_text('{"layout": 2, "state": "stable"}', encoding="utf-8")
    box.set_docker_state(known_layouts={NEW_IMAGE: [1, 2]}, unhealthy_images=[NEW_IMAGE])
    box.stage_bundle()

    args = ("--restore-bundle", OLD_IMAGE) if explicit else (NEW_IMAGE,)
    completed = box.run(*args, FAKE_DOCKER_VOLUME_DIR=str(volume))

    assert completed.returncode == (1 if explicit else 3), completed.stderr
    assert _result(completed) == ("layout_refused" if explicit else "rollback_needs_restore")
    if explicit:
        assert "compose up" not in box.docker_calls_text()
    else:
        assert box.env_image() == NEW_IMAGE, "automatic rollback must not start the old image"
        assert "stop tinyassets-daemon" in box.docker_calls_text()


def test_no_image_rollback_onto_data_the_failed_candidate_began_migrating(box: Box):
    """The candidate marks the data ``migrating`` while it boots, then fails: the
    previous image must NOT start on it, and the daemon is left down for the
    restore (design D7.2)."""
    volume = box.root / "data-volume"
    volume.mkdir()
    box.stage_bundle()
    box.set_docker_state(
        unhealthy_images=[NEW_IMAGE],
        mark_migrating_on_up={NEW_IMAGE: str(volume / ".layout.json")},
    )

    completed = box.run(NEW_IMAGE, FAKE_DOCKER_VOLUME_DIR=str(volume))

    assert completed.returncode == 3, completed.stderr
    assert _result(completed) == "rollback_needs_restore"
    assert box.env_image() != OLD_IMAGE, "the previous image must not be started"


def test_env_write_failure_after_install_restores_the_bundle(box: Box):
    """A failure between install and converge must not strand the new config."""
    before = box.live()
    box.stage_bundle()

    completed = box.run(NEW_IMAGE, FAKE_ENV_HELPER_FAIL="1")

    assert completed.returncode == 1
    assert _result(completed) == "failed_env_write"
    assert box.live() == before
    assert box.env_image() == OLD_IMAGE


# ---------------------------------------------------------------------------
# (d) --restore-bundle restores the pointer's snapshot
# ---------------------------------------------------------------------------


def test_restore_bundle_reinstalls_the_pointed_snapshot_then_converges(box: Box):
    """The public-canary rollback path."""
    before = box.live()
    box.stage_bundle()
    assert box.run(NEW_IMAGE).returncode == 0
    assert box.live() != before, "precondition: the forward deploy installed the bundle"

    completed = box.run("--restore-bundle", OLD_IMAGE)

    assert completed.returncode == 0, completed.stderr
    assert _result(completed) == "deployed"
    assert _deployed_image(completed) == OLD_IMAGE
    assert box.live() == before, "--restore-bundle must reinstall the snapshot"
    assert box.env_image() == OLD_IMAGE


def test_restore_bundle_does_not_reinstall_the_still_staged_bundle(box: Box):
    """The staged bundle is what a canary rollback is undoing, not applying."""
    before = box.live()
    box.stage_bundle()
    assert box.run(NEW_IMAGE).returncode == 0
    assert box.stage.is_dir(), "precondition: the stage directory survives the deploy"

    completed = box.run("--restore-bundle", OLD_IMAGE)

    assert completed.returncode == 0, completed.stderr
    assert box.live() == before


def test_restore_bundle_on_a_virgin_box_still_rolls_the_image_back(box: Box):
    """A box that has NEVER installed a bundle must not lose its image rollback.

    Distinct from a missing pointer beside existing snapshots, which is a broken
    contract and refuses — see the test below it.
    """
    shutil.rmtree(box.stage, ignore_errors=True)
    assert not box.pointer.exists()
    assert not box.snapshots.exists() or not list(box.snapshots.iterdir())

    completed = box.run("--restore-bundle", NEW_IMAGE)

    assert completed.returncode == 0, completed.stderr
    assert _result(completed) == "deployed"
    assert box.env_image() == NEW_IMAGE
    assert "no bundle has ever been installed here" in completed.stdout


# ---------------------------------------------------------------------------
# (e) vector change -> --force-recreate logs, and the container is verified
# ---------------------------------------------------------------------------


def test_changed_vector_inputs_force_recreate_the_logs_container(box: Box):
    """vector-entrypoint.sh copies the mounts into /run/vector-config at START.

    `up -d` leaves a container whose image and compose config are unchanged
    alone, so a new vector.yaml would otherwise never reach the running vector.
    """
    box.stage_bundle()

    completed = box.run(NEW_IMAGE)

    assert completed.returncode == 0, completed.stderr
    calls = box.docker_calls_text()
    assert "up -d --force-recreate logs" in calls, calls


def test_unchanged_vector_inputs_do_not_recreate_the_logs_container(box: Box):
    """A no-op vector change must not churn the log sidecar."""
    (box.runtime / "deploy" / "vector.yaml").write_text(NEW_VECTOR, encoding="utf-8")
    (box.runtime / "deploy" / "vector-betterstack.yaml").write_text(
        NEW_BETTERSTACK, encoding="utf-8"
    )
    (box.runtime / "deploy" / "vector-entrypoint.sh").write_text(
        NEW_ENTRYPOINT, encoding="utf-8"
    )
    box.stage_bundle()

    completed = box.run(NEW_IMAGE)

    assert completed.returncode == 0, completed.stderr
    assert "--force-recreate" not in box.docker_calls_text()


def test_logs_container_that_will_not_run_after_recreate_fails_the_deploy(box: Box):
    """A dead log sidecar is a deploy failure, not a warning."""
    before = box.live()
    box.stage_bundle()
    # Dead on the forward recreate, alive again once the old config is restored.
    box.set_docker_state(logs_recreate_statuses=["exited", "running"])

    completed = box.run(NEW_IMAGE)

    assert completed.returncode == 2, completed.stderr
    assert _result(completed) == "rolled_back"
    assert box.live() == before
    assert "tinyassets-logs" in completed.stderr


def test_logs_that_stays_dead_through_the_rollback_is_rollback_failed(box: Box):
    """Reporting `rolled_back` with log forwarding down is a false receipt."""
    box.stage_bundle()
    box.set_docker_state(logs_recreate_statuses=["exited", "exited"])

    completed = box.run(NEW_IMAGE)

    assert completed.returncode == 3, completed.stderr
    assert _result(completed) == "rollback_failed"
    assert _deployed_image(completed) == ""


# ---------------------------------------------------------------------------
# (f) absent bundle -> image-only deploy
# ---------------------------------------------------------------------------


def test_absent_bundle_is_an_image_only_deploy(box: Box):
    """A manual run with no staged bundle must still deploy the image."""
    shutil.rmtree(box.stage, ignore_errors=True)
    before = box.live()

    completed = box.run(NEW_IMAGE)

    assert completed.returncode == 0, completed.stderr
    assert _result(completed) == "deployed"
    assert "bundle: absent, image-only deploy" in completed.stdout
    assert box.live() == before
    assert box.env_image() == NEW_IMAGE
    assert not box.pointer.exists()


# ---------------------------------------------------------------------------
# refusals that must still leave production untouched
# ---------------------------------------------------------------------------


def test_failed_image_pull_leaves_the_bundle_uninstalled(box: Box):
    """The pull failure Codex named: config must not already be live."""
    before = box.live()
    box.stage_bundle()
    box.set_docker_state(pull_ok=False)

    completed = box.run(NEW_IMAGE)

    assert completed.returncode == 1
    assert box.live() == before
    assert box.env_image() == OLD_IMAGE


def test_failed_candidate_preflight_leaves_the_bundle_uninstalled(box: Box):
    before = box.live()
    box.stage_bundle()
    box.set_docker_state(preflight_ok=False)

    completed = box.run(NEW_IMAGE)

    assert completed.returncode == 1
    assert box.live() == before


# ---------------------------------------------------------------------------
# the ownership contract the runtime tests cannot assert unprivileged
# ---------------------------------------------------------------------------


def test_install_ownership_defaults_match_the_unit_contract():
    """Runtime files are `tinyassets`-owned; the systemd unit stays root's.

    The unit runs compose as `tinyassets` (deploy/tinyassets-daemon.service), so
    the files it reads must be readable by that user -- the pre-#2442 contract.
    """
    script = SCRIPT.read_text(encoding="utf-8")
    assert 'RUNTIME_OWNER="${RUNTIME_OWNER:-tinyassets}"' in script
    assert 'RUNTIME_GROUP="${RUNTIME_GROUP:-tinyassets}"' in script
    assert 'UNIT_OWNER="${UNIT_OWNER:-root}"' in script
    assert 'UNIT_GROUP="${UNIT_GROUP:-root}"' in script
    assert 'BUNDLE_DIR="${BUNDLE_DIR:-/tmp/tinyassets-bundle}"' in script
    assert 'BUNDLE_STATE_DIR="${BUNDLE_STATE_DIR:-/var/lib/tinyassets-deploy}"' in script


# ---------------------------------------------------------------------------
# (g) fail-loud rollback — nothing reports success over a mixed tree
# ---------------------------------------------------------------------------


def test_pointer_write_failure_restores_the_snapshot_and_refuses(box: Box):
    """A pointer that did not advance would make every later rollback wrong.

    It would name the PREVIOUS deploy's snapshot, so a rollback would install a
    bundle that was never live. Fatal, not a warning.
    """
    before = box.live()
    box.stage_bundle()
    # A directory cannot be replaced by `mv -f <file>`, so write_pointer fails
    # at its last step — after the install has already happened.
    box.pointer.mkdir()

    completed = box.run(NEW_IMAGE)

    assert completed.returncode == 1, completed.stderr
    assert _result(completed) == "bundle_pointer_failed"
    assert box.live() == before, "the snapshot must be restored"
    assert box.env_image() == OLD_IMAGE, "the image must not be swapped"
    assert not box.dirty.exists(), "a completed restore must clear the dirty marker"


def test_pointer_is_written_atomically(box: Box):
    """tmp + rename. A truncating redirect can leave a pointer naming nothing."""
    script = SCRIPT.read_text(encoding="utf-8")
    assert "mv -f" in script, "the pointer must be renamed into place"
    assert 'printf \'%s\\n\' "$SNAPSHOT_PATH" >"$BUNDLE_POINTER"' not in script


def test_restore_of_an_incomplete_snapshot_reports_rollback_failed(box: Box):
    """A snapshot missing a manifest row must not read as a completed rollback."""
    box.stage_bundle()
    assert box.run(NEW_IMAGE).returncode == 0
    snapshot = Path(box.pointer.read_text(encoding="utf-8").strip())
    manifest = snapshot / "manifest"
    rows = [
        line
        for line in manifest.read_text(encoding="utf-8").splitlines()
        if not line.startswith("deploy/vector.yaml|")
    ]
    manifest.write_text("\n".join(rows) + "\n", encoding="utf-8")

    completed = box.run("--restore-bundle", OLD_IMAGE)

    assert completed.returncode == 3, completed.stderr
    assert _result(completed) == "rollback_failed"
    assert _deployed_image(completed) == "", "a failed rollback reports no image"


def test_restore_bundle_with_a_missing_snapshot_reports_rollback_failed(box: Box):
    box.stage_bundle()
    assert box.run(NEW_IMAGE).returncode == 0
    shutil.rmtree(Path(box.pointer.read_text(encoding="utf-8").strip()))

    completed = box.run("--restore-bundle", OLD_IMAGE)

    assert completed.returncode == 3, completed.stderr
    assert _result(completed) == "rollback_failed"


def test_restore_bundle_without_a_pointer_but_with_history_refuses(box: Box):
    """A missing pointer beside existing snapshots is a broken contract, not a
    virgin box — reporting `deployed` there claims a rollback that never ran."""
    box.stage_bundle()
    assert box.run(NEW_IMAGE).returncode == 0
    box.pointer.unlink()

    completed = box.run("--restore-bundle", OLD_IMAGE)

    assert completed.returncode == 3, completed.stderr
    assert _result(completed) == "rollback_failed"


def test_image_only_failed_deploy_leaves_an_older_pointer_alone(box: Box):
    """This run installed no bundle, so it must not restore someone else's.

    The surviving pointer names the state before the PREVIOUS deploy; restoring
    it here would revert a bundle change this run never made.
    """
    box.stage_bundle()
    assert box.run(NEW_IMAGE).returncode == 0
    installed = box.live()
    pointer_before = box.pointer.read_text(encoding="utf-8")

    shutil.rmtree(box.stage, ignore_errors=True)
    box.set_docker_state(unhealthy_images=[OTHER_IMAGE])
    completed = box.run(OTHER_IMAGE)

    assert completed.returncode == 2, completed.stderr
    assert _result(completed) == "rolled_back"
    assert box.live() == installed, "an image-only rollback must not touch the bundle"
    assert box.pointer.read_text(encoding="utf-8") == pointer_before


def test_failed_converge_rolls_back_instead_of_accepting(box: Box):
    """`up -d` can bring up some services and not others; accept() on that is
    how a partially converged stack earns a success receipt.

    The daemon comes up on the new image here, so health, running-image, tunnel
    and logs all look green. Only compose's exit status says otherwise, and
    discarding it is exactly what produced the false receipt.

    The vector inputs are pre-seeded to match the bundle so NO force-recreate
    runs: otherwise that second compose call fails too and masks which return
    code the script actually acted on.
    """
    (box.runtime / "deploy" / "vector.yaml").write_text(NEW_VECTOR, encoding="utf-8")
    (box.runtime / "deploy" / "vector-betterstack.yaml").write_text(
        NEW_BETTERSTACK, encoding="utf-8"
    )
    (box.runtime / "deploy" / "vector-entrypoint.sh").write_text(
        NEW_ENTRYPOINT, encoding="utf-8"
    )
    before = box.live()
    box.stage_bundle()
    box.set_docker_state(up_partial_images=[NEW_IMAGE])

    completed = box.run(NEW_IMAGE)

    assert _result(completed) != "deployed", (
        "a compose invocation that returned non-zero must never be accepted"
    )
    assert completed.returncode == 2, completed.stderr
    assert _result(completed) == "rolled_back"
    assert box.live() == before


def test_daemon_reload_failure_is_fatal_and_leaves_the_dirty_marker(box: Box):
    """systemd refusing the new unit must not pass as an installed bundle."""
    box.stage_bundle()

    completed = box.run(NEW_IMAGE, FAKE_SYSTEMCTL_FAIL="daemon-reload")

    assert completed.returncode == 3, completed.stderr
    assert _result(completed) == "rollback_failed"
    assert box.dirty.exists(), (
        "an install whose restore also failed must leave the dirty marker"
    )


def test_dirty_marker_blocks_the_next_normal_deploy(box: Box):
    """Otherwise the next deploy snapshots the mixed tree and legitimizes it."""
    box.stage_bundle()
    assert box.run(NEW_IMAGE, FAKE_SYSTEMCTL_FAIL="daemon-reload").returncode == 3
    assert box.dirty.exists()

    completed = box.run(OTHER_IMAGE)

    assert completed.returncode == 1, completed.stderr
    assert _result(completed) == "bundle_dirty"
    assert "--restore-bundle" in completed.stderr
    assert box.env_image() == OLD_IMAGE, "a refused deploy must not swap the image"


@pytest.mark.skipif(
    not Path("/bin/rm").exists(), reason="the rm(1) wrapper needs /bin/rm"
)
def test_marker_that_will_not_clear_is_terminal(box: Box):
    """A successful deploy that leaves the marker behind blocks the NEXT one.

    Reporting `deployed` there hides that production is now un-deployable, so
    the result is `marker_clear_failed` — while still naming the image, because
    the image DID change and the operator has to know which one is live.
    """
    box.stage_bundle()

    completed = box.run(NEW_IMAGE, FAKE_RM_FAIL_MATCH=str(box.dirty))

    assert completed.returncode == 3, completed.stderr
    assert _result(completed) == "marker_clear_failed"
    assert _deployed_image(completed) == NEW_IMAGE, (
        "the operator must still learn which image is live"
    )
    assert box.dirty.exists()
    assert box.env_image() == NEW_IMAGE, "the deploy itself did succeed"


def test_an_empty_marker_blocks_a_normal_deploy(box: Box):
    box.dirty.parent.mkdir(parents=True, exist_ok=True)
    box.dirty.write_text("", encoding="utf-8")
    box.stage_bundle()
    before = box.live()

    completed = box.run(NEW_IMAGE)

    assert completed.returncode == 1, completed.stderr
    assert _result(completed) == "bundle_dirty"
    assert box.live() == before


def test_an_empty_marker_makes_restore_bundle_refuse_not_fall_through(box: Box):
    """An empty marker means "interrupted, snapshot unknown".

    Falling through to the pointer would restore the last GOOD state over an
    interrupted one and report success for a tree nobody has accounted for.
    """
    box.stage_bundle()
    assert box.run(NEW_IMAGE).returncode == 0
    assert box.pointer.exists(), "precondition: a pointer the fall-through could use"
    box.dirty.write_text("", encoding="utf-8")

    completed = box.run("--restore-bundle", OLD_IMAGE)

    assert completed.returncode == 3, completed.stderr
    assert _result(completed) == "rollback_failed"
    assert str(box.dirty) in completed.stderr, "the refusal must name the empty marker"


def test_restore_bundle_clears_the_dirty_marker(box: Box):
    """`--restore-bundle` is the documented way out of the dirty state."""
    box.stage_bundle()
    assert box.run(NEW_IMAGE, FAKE_SYSTEMCTL_FAIL="daemon-reload").returncode == 3
    assert box.dirty.exists()

    completed = box.run("--restore-bundle", OLD_IMAGE)

    assert completed.returncode == 0, completed.stderr
    assert _result(completed) == "deployed"
    assert not box.dirty.exists()


# ---------------------------------------------------------------------------
# (h) exact restoration — the manifest, not the forward contract
# ---------------------------------------------------------------------------


def test_restore_puts_back_the_recorded_mode_not_the_install_contract(box: Box):
    """Live /opt/tinyassets/compose.yml is root:root 0644, but the forward
    install writes tinyassets:tinyassets. Re-asserting that contract on the way
    back makes the rollback change something."""
    (box.runtime / "compose.yml").chmod(0o640)
    (box.runtime / "deploy" / "vector-entrypoint.sh").chmod(0o700)
    modes_before = box.modes()
    box.stage_bundle()

    assert box.run(NEW_IMAGE).returncode == 0
    assert box.modes()["compose"] == 0o644, "the forward install applies 0644"

    completed = box.run("--restore-bundle", OLD_IMAGE)

    assert completed.returncode == 0, completed.stderr
    assert box.modes() == modes_before, (
        "the restore must reinstate the recorded modes, not the install contract"
    )


@pytest.mark.skipif(
    not Path("/usr/bin/install").exists(),
    reason="the install(1) wrapper needs the real binary at /usr/bin/install",
)
def test_restore_passes_the_manifest_uid_and_gid_to_install(box: Box):
    """Content and mode are assertable unprivileged; ownership is not.

    So assert the arguments: the restore's `install` calls must carry the uid/gid
    the manifest recorded, never the RUNTIME_OWNER/UNIT_OWNER the forward install
    used.
    """
    box.stage_bundle()
    assert box.run(NEW_IMAGE).returncode == 0
    snapshot = Path(box.pointer.read_text(encoding="utf-8").strip())
    manifest = {
        row.split("|")[0]: row.split("|")[2]
        for row in snapshot.joinpath("manifest").read_text(encoding="utf-8").splitlines()
        if row.strip()
    }
    box.install_calls.write_text("", encoding="utf-8")

    assert box.run("--restore-bundle", OLD_IMAGE).returncode == 0

    calls = box.install_calls_text()
    assert calls.strip(), "the restore must go through install(1)"
    for rel, meta in manifest.items():
        uid, gid, perms = meta.split()
        expected = f"-m {perms} -o {uid} -g {gid}"
        assert expected in calls, (
            f"restore of {rel} must use the manifest's {expected!r}; calls were:\n{calls}"
        )


def test_snapshot_records_a_manifest_row_per_bundle_file(box: Box):
    box.stage_bundle()
    assert box.run(NEW_IMAGE).returncode == 0
    snapshot = Path(box.pointer.read_text(encoding="utf-8").strip())
    rows = [
        row
        for row in snapshot.joinpath("manifest").read_text(encoding="utf-8").splitlines()
        if row.strip()
    ]
    assert {row.split("|")[0] for row in rows} == {
        "compose.yml",
        "deploy/compose.yml",
        "deploy/vector.yaml",
        "deploy/vector-betterstack.yaml",
        "deploy/vector-entrypoint.sh",
        "systemd/tinyassets-daemon.service",
    }
    for row in rows:
        rel, state, meta = row.split("|")
        assert state in {"present", "absent"}, row
        if state == "present":
            assert len(meta.split()) == 3, f"{rel}: expected 'uid gid mode', got {meta!r}"


def test_a_missing_root_compose_row_is_not_satisfied_by_the_deploy_row(box: Box):
    """The manifest lookup must match the key EXACTLY, not by suffix.

    `grep -F "compose.yml|"` also matched `deploy/compose.yml|`, so a missing
    root row was silently served by the deploy copy's metadata and the restore
    reported success having applied the wrong uid/gid/mode (Codex round 3).
    """
    # Distinct modes so a collision is visible in the result, not just the code.
    (box.runtime / "compose.yml").chmod(0o640)
    (box.runtime / "deploy" / "compose.yml").chmod(0o604)
    box.stage_bundle()
    assert box.run(NEW_IMAGE).returncode == 0
    snapshot = Path(box.pointer.read_text(encoding="utf-8").strip())
    manifest = snapshot / "manifest"
    rows = manifest.read_text(encoding="utf-8").splitlines()
    kept = [row for row in rows if not row.startswith("compose.yml|")]
    assert any(row.startswith("deploy/compose.yml|") for row in kept), (
        "the collision only exists while the deploy row is present"
    )
    assert len(kept) == len(rows) - 1
    manifest.write_text("\n".join(kept) + "\n", encoding="utf-8")

    completed = box.run("--restore-bundle", OLD_IMAGE)

    assert completed.returncode == 3, completed.stderr
    assert _result(completed) == "rollback_failed"
    assert stat.S_IMODE((box.runtime / "compose.yml").stat().st_mode) != 0o604, (
        "the root compose file was restored with the deploy copy's mode"
    )


def test_a_snapshot_without_a_manifest_is_refused(box: Box):
    """Guessing the ownership is how a rollback silently changes something."""
    box.stage_bundle()
    assert box.run(NEW_IMAGE).returncode == 0
    (Path(box.pointer.read_text(encoding="utf-8").strip()) / "manifest").unlink()

    completed = box.run("--restore-bundle", OLD_IMAGE)

    assert completed.returncode == 3, completed.stderr
    assert _result(completed) == "rollback_failed"


# ---------------------------------------------------------------------------
# (i) retention must never delete the rollback target
# ---------------------------------------------------------------------------


def test_retention_never_deletes_the_pointed_snapshot(box: Box):
    """Prune excess history while advancing to a restorable rollback target.

    Asserting `count <= 5` alone also passes when the pointer target is gone.
    The sorts_oldest case below deterministically puts it in the prune window.
    """
    box.stage_bundle()
    # Retention reads directory names, not old payload sizes. Seed the excess
    # history and perform two real installs so the pointer still advances.
    for index in range(BUNDLE_KEEP + 1):
        (box.snapshots / f"20260101T00000{index}Z-aaaaaa").mkdir(parents=True)
    for index in range(2):
        completed = box.run(NEW_IMAGE if index % 2 == 0 else OTHER_IMAGE)
        assert completed.returncode == 0, f"deploy {index}: {completed.stderr}"

    pointed = Path(box.pointer.read_text(encoding="utf-8").strip())
    assert pointed.is_dir(), "retention deleted the snapshot the pointer names"
    assert (pointed / "manifest").exists()
    assert len(list(box.snapshots.iterdir())) <= BUNDLE_KEEP


def test_retention_keeps_the_pointed_snapshot_when_it_sorts_oldest(box: Box):
    """The guard only matters when the pointed snapshot is in the prune window.

    Six far-future stamps push the freshly pointed 2026 snapshot into the oldest
    three, so retention would delete the rollback target if it did not check.
    """
    box.stage_bundle()
    assert box.run(NEW_IMAGE).returncode == 0
    for index in range(6):
        (box.snapshots / f"20990101T00000{index}Z-aaaaaa").mkdir()

    box.stage_bundle()
    assert box.run(OTHER_IMAGE).returncode == 0

    pointed = Path(box.pointer.read_text(encoding="utf-8").strip())
    assert pointed.is_dir(), "retention deleted the snapshot the pointer names"
    assert (pointed / "manifest").exists(), "the pointed snapshot must stay restorable"


# ---------------------------------------------------------------------------
# (j) the logs sidecar can die between being seen up and being accepted
# ---------------------------------------------------------------------------


def test_logs_that_exits_after_being_seen_running_fails_the_deploy(box: Box):
    """logs_running() returns on the first sighting, then health_ok can burn the
    whole health timeout. accept() re-checks last for exactly this."""
    before = box.live()
    box.stage_bundle()
    # Alive for the recreate check, dead by the time accept() looks again.
    box.set_docker_state(logs_dies_after=1, unhealthy_images=[])

    completed = box.run(NEW_IMAGE)

    assert completed.returncode != 0, completed.stdout
    assert _result(completed) != "deployed", "a dead log sidecar is not a green deploy"
    assert box.live() == before


# ---------------------------------------------------------------------------
# (z) platform secrets stay out of the daemon
#     (docs/concerns/2026-10-02-platform-secrets-in-daemon-env.md)
# ---------------------------------------------------------------------------


def _seed_platform_secrets(box: Box) -> None:
    box.env_file.write_text(
        f"TINYASSETS_IMAGE={OLD_IMAGE}\n"
        "DO_API_TOKEN=placeholder-do\n"
        "CLOUDFLARE_TUNNEL_TOKEN=placeholder-cf\n"
        "STRIPE_SECRET_KEY=placeholder-stripe\n",
        encoding="utf-8",
    )


def _config_env_names(box: Box) -> set[str]:
    daemon = box.docker_state_json()["containers"]["tinyassets-daemon"]
    return {entry.split("=", 1)[0] for entry in daemon.get("config_env") or []}


def test_a_deploy_leaves_platform_secrets_out_of_the_daemon(box: Box):
    _seed_platform_secrets(box)
    box.stage_bundle()

    completed = box.run(NEW_IMAGE)

    assert completed.returncode == 0, completed.stderr
    assert _result(completed) == "deployed"
    rendered = box.daemon_env_file.read_text(encoding="utf-8")
    assert "DO_API_TOKEN=" not in rendered
    assert "CLOUDFLARE_TUNNEL_TOKEN=" not in rendered
    names = _config_env_names(box)
    assert "DO_API_TOKEN" not in names and "CLOUDFLARE_TUNNEL_TOKEN" not in names
    # The daemon still gets what its own routes need.
    assert "STRIPE_SECRET_KEY" in names
    # Nothing was destroyed: the host file keeps every value for the host and
    # the tunnel's interpolation.
    assert "DO_API_TOKEN=placeholder-do" in box.env_file.read_text(encoding="utf-8")
    assert "placeholder" not in completed.stdout + completed.stderr


@pytest.mark.parametrize("where", ["extra_process_env", "extra_config_env"])
def test_a_daemon_holding_a_platform_secret_is_rolled_back(box: Box, where):
    """Checked on the running container, not on the files meant to produce it:
    an image ENV or an entrypoint can put a name back that no env file holds."""
    box.stage_bundle()
    box.set_docker_state(**{where: {"DO_API_TOKEN": "placeholder-do"}})

    completed = box.run(NEW_IMAGE)

    assert completed.returncode == 2, completed.stderr
    assert _result(completed) == "rolled_back"
    assert _deployed_image(completed) == OLD_IMAGE
    assert "DO_API_TOKEN" in completed.stderr
    assert "placeholder" not in completed.stdout + completed.stderr


def test_a_daemon_whose_processes_cannot_be_read_is_not_accepted(box: Box):
    """No environment read is not the same as a clean one."""
    box.stage_bundle()
    box.set_docker_state(environ_unreadable=True)

    completed = box.run(NEW_IMAGE)

    assert completed.returncode == 2, completed.stderr
    assert _result(completed) == "rolled_back"
    assert "pid 4242 of tinyassets-daemon has no readable environment" in completed.stderr


def test_a_refused_render_leaves_production_untouched(box: Box):
    box.env_file.write_text(
        f'TINYASSETS_IMAGE={OLD_IMAGE}\nDO_API_TOKEN="placeholder-start\nplaceholder-rest"\n',
        encoding="utf-8",
    )
    before = box.live()
    box.stage_bundle()

    completed = box.run(NEW_IMAGE)

    assert completed.returncode == 1
    assert _result(completed) == "daemon_env_render_failed"
    assert box.live() == before
    assert "compose" not in box.docker_calls_text()


def test_the_env_helper_is_kept_where_the_unit_points_an_operator(box: Box):
    box.stage_bundle()

    completed = box.run(NEW_IMAGE)

    assert completed.returncode == 0, completed.stderr
    assert box.helper_install_path.read_bytes() == box.env_helper.read_bytes()


def test_a_permitted_value_that_looks_like_a_forbidden_name_is_not_a_leak(box: Box):
    """Record boundaries are kept: a newline inside a permitted value is not a
    new variable (Codex on the first draft, which split on newlines)."""
    box.stage_bundle()
    box.set_docker_state(
        extra_config_env={"NOTES": "line one\nDO_API_TOKEN=looks-like-one"},
        extra_process_env={"MORE_NOTES": "x\nCLOUDFLARE_TUNNEL_TOKEN=also"},
    )

    completed = box.run(NEW_IMAGE)

    assert completed.returncode == 0, completed.stderr
    assert _result(completed) == "deployed"


def test_one_unreadable_live_process_is_not_skipped(box: Box):
    box.stage_bundle()
    box.set_docker_state(extra_pids={"4243": "unreadable"})

    completed = box.run(NEW_IMAGE)

    assert completed.returncode == 2, completed.stderr
    assert _result(completed) == "rolled_back"
    assert "pid 4243" in completed.stderr


def test_a_process_that_exited_before_the_read_is_not_a_failure(box: Box):
    box.stage_bundle()
    box.set_docker_state(extra_pids={"4244": "gone"})

    completed = box.run(NEW_IMAGE)

    assert completed.returncode == 0, completed.stderr
    assert _result(completed) == "deployed"


def test_restore_bundle_onto_a_pre_split_bundle_restores_the_previous_image(box: Box):
    """The public-canary rollback must land on the image it was given.

    The bundle it restores predates the split and loads the host env file, so
    the previous daemon holds the platform's secrets again. Checking scope on
    that path failed it and re-converged the image the canary had just
    rejected (Codex on the first draft).
    """
    pre_split = box.valid_compose().replace(
        str(box.daemon_env_file), str(box.env_file)
    )
    assert pre_split != box.valid_compose(), "precondition: the live bundle is pre-split"
    (box.runtime / "compose.yml").write_text(pre_split, encoding="utf-8")
    (box.runtime / "deploy" / "compose.yml").write_text(pre_split, encoding="utf-8")
    _seed_platform_secrets(box)
    box.stage_bundle()
    forward = box.run(NEW_IMAGE)
    assert forward.returncode == 0, forward.stderr

    restored = box.run("--restore-bundle", OLD_IMAGE)

    assert restored.returncode == 0, restored.stderr
    assert box.env_image() == OLD_IMAGE
    daemon = box.docker_state_json()["containers"]["tinyassets-daemon"]
    assert daemon["image_ref"] == OLD_IMAGE
    assert "DO_API_TOKEN" in _config_env_names(box), (
        "precondition: the restored bundle really does load the host env file"
    )


def test_the_first_deploy_after_the_split_renders_daemon_env_before_compose(box: Box):
    """The box has only /etc/tinyassets/env. The fake refuses a missing
    env_file as the real CLI does, so this fails if anything reads daemon.env
    before the render."""
    _seed_platform_secrets(box)
    assert not box.daemon_env_file.exists(), "precondition: a box that predates the split"
    box.stage_bundle()

    completed = box.run(NEW_IMAGE)

    assert completed.returncode == 0, completed.stderr
    assert _result(completed) == "deployed"
    assert box.daemon_env_file.is_file()
    assert "DO_API_TOKEN" not in _config_env_names(box)


def test_the_fake_refuses_a_missing_daemon_env_like_the_real_cli(box: Box):
    """Guards the test above: without the render, the same deploy must fail."""
    box.stage_bundle()
    box.set_docker_state()
    helper = box.env_helper.read_text(encoding="utf-8").replace(
        "daemon-forbidden-names|render-daemon-env) real",
        "daemon-forbidden-names) real",
    )
    helper = helper.replace(
        'case "${1:-}" in',
        'case "${1:-}" in\n  render-daemon-env) exit 0 ;;',
    )
    helper = helper.replace(
        "TINYASSETS_ENV_FILE=\"$DAEMON_ENV_FILE\" real render-daemon-env >/dev/null",
        "true",
    )
    assert "render-daemon-env) exit 0" in helper
    assert "real render-daemon-env" not in helper, "precondition: every render is disabled"
    box.env_helper.write_text(helper, encoding="utf-8", newline="\n")

    completed = box.run(NEW_IMAGE)

    assert completed.returncode == 1, completed.stderr
    assert _result(completed) == "bundle_invalid"
    assert not box.daemon_env_file.exists()


def test_restore_bundle_is_not_blocked_by_a_failed_render(box: Box):
    """A rollback must run even when the source cannot be rendered."""
    box.stage_bundle()
    assert box.run(NEW_IMAGE).returncode == 0
    box.env_file.write_text(
        f'TINYASSETS_IMAGE={NEW_IMAGE}\nDO_API_TOKEN="placeholder-start\nplaceholder-rest"\n',
        encoding="utf-8",
    )

    restored = box.run("--restore-bundle", OLD_IMAGE)

    assert restored.returncode == 0, restored.stderr
    assert box.env_image() == OLD_IMAGE
    assert "continuing the restore" in restored.stderr
    assert "placeholder" not in restored.stdout + restored.stderr
