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
import tarfile
import io
import time


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

    if argv[:1] == ["create"]:
        print("fake-harness-container")
        return 0
    if argv[:1] == ["export"]:
        with tarfile.open(fileobj=sys.stdout.buffer, mode="w|") as archive:
            for name in ("app/tinyassets/boxd.py", "data/private.txt",
                         ".broker/secret", ".platform/secret", ".runtime/secret"):
                payload = b"image bytes"
                entry = tarfile.TarInfo(name)
                entry.size = len(payload)
                entry.mode = 0o644
                archive.addfile(entry, io.BytesIO(payload))
        return 0
    if argv[:1] == ["rm"]:
        return 0

    if argv[:1] == ["pull"]:
        if not state.get("pull_ok", True):
            sys.stderr.write("fake docker: pull refused\n")
            return 1
        state.setdefault("images", {})[argv[-1]] = image_id(argv[-1])
        save_state(state)
        return 0

    if argv[:1] == ["run"]:
        if any("TURNS_PROBE_KIND" in arg for arg in argv):
            rc = int(state.get("turns_rc", 0))
            if rc:
                sys.stderr.write("fake turns probe rc=%s\n" % rc)
            else:
                print('{"in_flight": 0}')
            return rc
        if "ACTIVE_BOX_LAYOUT" in argv[-1]:
            target = argv[argv.index("--entrypoint") + 2]
            print(state.get("active_box_layout", {}).get(target, "owner-split"))
            return 0
        if "tinyassets.box_layout_migration" in argv:
            marker = os.path.join(os.environ.get("FAKE_DOCKER_VOLUME_DIR") or "", ".layout.json")
            command = argv[argv.index("tinyassets.box_layout_migration") + 1]

            def census(layout, marker_state, pending):
                print(json.dumps({
                    "box_layout": layout,
                    "marker_state": marker_state,
                    "pending": pending,
                    "counts": {
                        "owner-split": {"centers": 1, "admitted_owners": None},
                        "owner-box": {
                            "registered_boxes": 1 if layout == "owner-box" else None,
                            "registry_present": layout == "owner-box",
                        },
                    },
                }, sort_keys=True))

            if command == "check" and state.get("box_census_rc") is not None:
                forced = int(state["box_census_rc"])
                census("unreadable" if forced == 3 else "absent", None, None)
                return forced
            if command == "check" and not os.path.isfile(marker):
                census("absent", None, None)
                return 4
            try:
                with open(marker, encoding="utf-8") as handle:
                    document = json.load(handle)
            except (OSError, ValueError):
                if command == "check":
                    census("unreadable", None, None)
                    return 3
                raise
            if not isinstance(document, dict):
                document = {}
            if command == "check":
                layout = document.get("box_layout", "owner-split")
                marker_state = document.get("state")
                if marker_state != "stable" or layout not in ("owner-split", "owner-box"):
                    census(layout, marker_state, None)
                    return 3
                census(
                    layout,
                    marker_state,
                    True if layout == "owner-split" else False,
                )
                return 0 if layout == "owner-box" else 10
            if command == "preflight":
                store = os.environ.get("FAKE_DOCKER_BOX_STORE_DIR")
                if store and any(name != "lost+found" for name in os.listdir(store)):
                    sys.stderr.write(
                        "box layout migration refused: "
                        "box store must be empty before the one-shot move\n"
                    )
                    return 3
                return 0
            if command == "census":
                ready = state.get("box_detail_census_ready", True)
                print(json.dumps({"ready": ready}))
                return 0 if ready else 3
            if command == "verify":
                return int(state.get("box_verify_rc", 0))
            state["box_migration_apply_attempted"] = True
            outcome = state.get("box_migration_apply", "commit")
            if outcome == "commit":
                document.update(box_layout="owner-box", state="stable")
            else:
                document.update(state="migrating")
            with open(marker, "w", encoding="utf-8") as handle:
                json.dump(document, handle)
            save_state(state)
            return 0 if outcome == "commit" else 3
        if "tinyassets.agent_file_migration" in argv:
            # The C8 cutover container (deploy step 3c). `check` reports the
            # rows still in SQL; a failed `apply` can be before or after commit,
            # independently of the marker it left.
            marker = os.path.join(os.environ.get("FAKE_DOCKER_VOLUME_DIR", ""), ".layout.json")
            command = argv[argv.index("tinyassets.agent_file_migration") + 1]
            if command == "check":
                if state.get("migration_recheck_error") and state.get("migration_apply_attempted"):
                    sys.stderr.write("fake migration: recheck failed: sqlite locked\n")
                    return 1
                if state.get("migration_probe_error"):
                    sys.stderr.write(
                        "fake migration: probe failed: %s\n" % state["migration_probe_error"]
                    )
                    return int(state.get("migration_probe_rc", 1))
                pending = int(bool(state.get("migration_pending")))
                print(json.dumps({"pending": pending}))
                return 10 if pending else 0
            outcome = state.get("migration_apply", "commit")
            state["migration_apply_attempted"] = True
            if outcome == "commit":
                state["migration_pending"] = False
                save_state(state)
                with open(marker, "w", encoding="utf-8") as handle:
                    handle.write('{"layout": 1, "state": "stable"}')
                print("{}")
                return 0
            if outcome.startswith("committed_"):
                state["migration_pending"] = False
                save_state(state)
                outcome = outcome.removeprefix("committed_")
            else:
                save_state(state)
            with open(marker, "w", encoding="utf-8") as handle:
                handle.write('{"layout": 1, "state": "%s"}' % outcome)
            sys.stderr.write("fake migration: apply failed outcome=%s\n" % outcome)
            return 1
        if "KNOWN_LAYOUTS" in argv[-1]:
            if not state.get("layout_probe_ok", True):
                return 1
            # Declarations belong to the requested image, not the running one.
            target = argv[argv.index("--entrypoint") + 2]
            declaration = state.get("known_layouts", {}).get(target, [1])
            if isinstance(declaration, list):
                declaration = {"layouts": declaration, "boxes": ["owner-split"]}
            print(state.get("layout_probe_output", json.dumps(declaration)))
        return 0 if state.get("preflight_ok", True) else 1

    if argv[:1] == ["ps"]:
        # `ps -q --filter name=^X$`: the id of a running container, or nothing.
        wanted = argv[-1].split("=", 1)[-1].strip("^$")
        container = (state.get("containers") or {}).get(wanted)
        if container is not None and container.get("status", "running") == "running":
            print("c0ffee")
        return 0
    if argv[:1] in (["stop"], ["start"]):
        container = (state.get("containers") or {}).get(argv[-1])
        if argv[0] == "stop" and state.get("stop_refused"):
            sys.stderr.write(
                "fake docker: stop refused for %s status=%s\n"
                % (argv[-1], (container or {}).get("status", "missing"))
            )
            return 1
        if argv[0] == "start" and state.get("start_refused"):
            sys.stderr.write(
                "fake docker: start refused for %s status=%s\n"
                % (argv[-1], (container or {}).get("status", "missing"))
            )
            return 1
        if argv[0] == "stop" and state.get("stop_delay_s"):
            delay = state.pop("stop_delay_s")
            save_state(state)
            time.sleep(delay)
        if container is not None:
            container["status"] = "running" if argv[0] == "start" else "exited"
            save_state(state)
        return 0
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
            if ref in (state.get("up_start_delay_s") or {}):
                time.sleep(state["up_start_delay_s"][ref])
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

# deploy/backup.sh in BACKUP_MODE=snapshot: the full-volume rollback point the
# migration verifies. It reports the archive the way the real script does.
FAKE_BACKUP = r'''#!/usr/bin/env bash
set -euo pipefail
[ "${BACKUP_MODE:-}" = "snapshot" ] || { echo "fake backup: not a snapshot" >&2; exit 1; }
stamp="$(date -u +%Y-%m-%dT%H-%M-%SZ)"
name="tinyassets-snapshot-${stamp}.tar.gz"
mkdir -p "${BACKUP_SNAPSHOT_DIR}"
python3 - "${BACKUP_SNAPSHOT_DIR}" "${stamp}" "${BACKUP_REQUIRE_BOX_STORE:-0}" <<'PY'
import hashlib, io, os, sys, tarfile
directory, stamp, require_box = sys.argv[1:]
names = ["tinyassets-snapshot-%s.tar.gz" % stamp]
if require_box == "1":
    names.append("tinyassets-box-store-%s.tar.gz" % stamp)
for name in names:
    path = os.path.join(directory, name)
    payload = b"snapshot-member\n"
    with tarfile.open(path, "w:gz") as archive:
        info = tarfile.TarInfo("inventory.txt")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
    digest = hashlib.sha256(open(path, "rb").read()).hexdigest()
    with open(path + ".sha256", "w", encoding="ascii") as handle:
        handle.write("%s  %s\n" % (digest, name))
PY
echo "SNAPSHOT id=${name} sha256=0 bytes=3 names=1"
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
            "BACKUP_SCRIPT": str(self.root / "backup.sh"),
            "MIGRATION_BACKUP_DIR": str(self.root / "backups"),
            "TURNS_PROBE": str(self.root / "turns_probe.py"),
            "VERIFY_BACKUP_PY": str(REPO / "scripts" / "verify_deploy_backup.py"),
            # A provisioned canary is the ordinary path. Tests of the missing
            # config set this to 0. The volume marker is stable owner-split so
            # the census is a real layout, not an absent one.
            "CORE_CANARY_PROVISIONED": "1",
            "FAKE_DOCKER_VOLUME_DIR": str(self.root / "default-data-volume"),
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
    backup = fake.root / "backup.sh"
    backup.write_text(FAKE_BACKUP, encoding="utf-8", newline="\n")
    backup.chmod(0o755)
    (fake.root / "backups").mkdir()
    (fake.root / "turns_probe.py").write_text("# in-flight probe stand-in\n", encoding="utf-8")
    default_volume = fake.root / "default-data-volume"
    default_volume.mkdir()
    (default_volume / ".layout.json").write_text(
        '{"layout": 1, "state": "stable", "box_layout": "owner-split"}\n',
        encoding="utf-8",
    )

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




# ---------------------------------------------------------------------------
# (a) invalid bundle -> refused before any install, production untouched
# ---------------------------------------------------------------------------


def _mutate(compose_text: str, old: str, new: str) -> str:
    assert old in compose_text, f"fixture drift: {old!r} is no longer in compose.yml"
    return compose_text.replace(old, new, 1)




_DAEMON_IMAGE_LINE = (
    "image: ${TINYASSETS_IMAGE:?Set TINYASSETS_IMAGE to an immutable "
    "ghcr.io/tinyassets/tinyassets-daemon@sha256:<digest> ref}"
)
















# ---------------------------------------------------------------------------
# (b) valid bundle -> snapshot written, files installed, pointer advanced
# ---------------------------------------------------------------------------












# ---------------------------------------------------------------------------
# (c) converge failure after install -> snapshot restored, previous image back
# ---------------------------------------------------------------------------




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
    # Stable before the swap, so the census is not the refusal. The candidate
    # then rewrites the marker to migrating as it boots.
    (volume / ".layout.json").write_text(
        '{"layout": 1, "state": "stable"}', encoding="utf-8",
    )
    box.stage_bundle()
    box.set_docker_state(
        unhealthy_images=[NEW_IMAGE],
        mark_migrating_on_up={NEW_IMAGE: str(volume / ".layout.json")},
    )

    completed = box.run(NEW_IMAGE, FAKE_DOCKER_VOLUME_DIR=str(volume))

    assert completed.returncode == 3, completed.stderr
    assert _result(completed) == "rollback_needs_restore"
    assert box.env_image() != OLD_IMAGE, "the previous image must not be started"




# ---------------------------------------------------------------------------
# (d) --restore-bundle restores the pointer's snapshot
# ---------------------------------------------------------------------------








# ---------------------------------------------------------------------------
# (e) vector change -> --force-recreate logs, and the container is verified
# ---------------------------------------------------------------------------










# ---------------------------------------------------------------------------
# (f) absent bundle -> image-only deploy
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# refusals that must still leave production untouched
# ---------------------------------------------------------------------------






# ---------------------------------------------------------------------------
# the ownership contract the runtime tests cannot assert unprivileged
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# (g) fail-loud rollback — nothing reports success over a mixed tree
# ---------------------------------------------------------------------------




























# ---------------------------------------------------------------------------
# (h) exact restoration — the manifest, not the forward contract
# ---------------------------------------------------------------------------












# ---------------------------------------------------------------------------
# (i) retention must never delete the rollback target
# ---------------------------------------------------------------------------






# ---------------------------------------------------------------------------
# (j) the logs sidecar can die between being seen up and being accepted
# ---------------------------------------------------------------------------




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




























# ---------------------------------------------------------------------------
# (e) the agent-files cutover (step 3c): once apply has committed, the previous
# image never starts again in this run
# ---------------------------------------------------------------------------


def _cutover(box: Box) -> tuple[Path, dict[str, str]]:
    """A volume still holding bindings, the snapshot script, the snapshot dir."""
    volume = box.root / "data-volume"
    volume.mkdir()
    (volume / ".layout.json").write_text('{"layout": 1, "state": "stable"}', encoding="utf-8")
    backup = box.root / "backup.sh"
    backup.write_text(FAKE_BACKUP, encoding="utf-8", newline="\n")
    backup.chmod(0o755)
    box.set_docker_state(migration_pending=True)
    env = {
        "FAKE_DOCKER_VOLUME_DIR": str(volume),
        "BACKUP_SCRIPT": str(backup),
        "MIGRATION_BACKUP_DIR": str(box.root / "backups"),
    }
    return volume, env


def _migration_refusal(completed: subprocess.CompletedProcess) -> str:
    """The deploy-log line. The workflow echoes stdout; stderr is annotations."""
    lines = [
        line for line in completed.stdout.splitlines()
        if line.startswith("[deploy-fail-safe] migration refused:")
    ]
    assert lines, completed.stdout
    return "\n".join(lines)


def _daemon_starts(box: Box) -> list[str]:
    """Every call that would start a daemon: `start`, and a `compose up` that
    names the daemon service (the log forwarder's own recreate is not one)."""
    return [
        line for line in box.docker_calls_text().splitlines()
        if line.startswith("start ")
        or ("compose" in line and " up " in line and " daemon " in line + " ")
    ]


def _owner_box_cutover(box: Box, *, device_present: bool = True):
    volume, env = _cutover(box)
    with box.env_file.open("a") as stream:
        stream.write("BACKUP_DEST=:local:/synthetic-snapshots\n")
    box.set_docker_state(
        migration_pending=False,
        active_box_layout={NEW_IMAGE: "owner-box"},
        known_layouts={NEW_IMAGE: {"layouts": [1, 2], "boxes": ["owner-box"]}},
    )
    env["BOX_STORE_CHECK"] = "/bin/true"
    harness_root = box.root / "boxhostd"
    harness_root.mkdir(exist_ok=True)
    env["BOX_HARNESS_ROOT"] = str(harness_root)
    device = "/dev/tinyassets-test-box-store"
    findmnt = box.bin / "findmnt"
    findmnt.write_text(
        f'#!/bin/sh\nif [ "$*" = "--mountpoint /var/lib/boxhostd/owners -no SOURCE" ]; '
        f'then printf "%s\\n" "{device if device_present else "/dev/missing-box-store"}"; '
        'else exec /usr/bin/findmnt "$@"; fi\n',
        encoding="utf-8",
    )
    findmnt.chmod(0o755)
    fake_stat = box.root / "fake-block-stat.c"
    fake_stat.write_text(r'''
#define _GNU_SOURCE
#include <dlfcn.h>
#include <string.h>
#include <sys/stat.h>
int stat(const char *path, struct stat *out) {
    int (*real_stat)(const char *, struct stat *) = dlsym(RTLD_NEXT, "stat");
    if (strcmp(path, "/dev/tinyassets-test-box-store") != 0)
        return real_stat(path, out);
    int result = real_stat("/dev/null", out);
    if (result == 0) out->st_mode = (out->st_mode & ~S_IFMT) | S_IFBLK;
    return result;
}
''', encoding="utf-8")
    preload = box.root / "fake-block-stat.so"
    subprocess.run(["cc", "-shared", "-fPIC", "-o", str(preload), str(fake_stat), "-ldl"],
                   check=True)
    env["LD_PRELOAD"] = str(preload)
    return volume, env, device, harness_root


@pytest.mark.parametrize("outcome", ["commit", "fail"])
def test_owner_box_move_commits_once_or_requires_snapshot_restore(box: Box, outcome):
    volume, env, device, harness_root = _owner_box_cutover(box)
    box.set_docker_state(box_migration_apply=outcome)
    box.stage_bundle()
    completed = box.run(NEW_IMAGE, **env)
    calls = box.docker_calls_text()
    assert "tinyassets.box_layout_migration preflight" in calls, completed.stdout + completed.stderr
    preflight_call = next(line for line in calls.splitlines()
                          if "tinyassets.box_layout_migration preflight" in line)
    assert "tinyassets-data:/data:ro" in preflight_call
    assert "dst=/box-store,readonly" in preflight_call
    assert calls.index(preflight_call) < calls.index("stop --time")
    apply_call = next(line for line in calls.splitlines()
                      if "tinyassets.box_layout_migration apply" in line)
    assert "apply /data --store /box-store" in apply_call
    assert "--cap-drop ALL --user 0 --privileged" in apply_call
    assert f"--device {device}:{device}:r" in apply_call
    assert "--mount type=bind,src=/var/lib/boxhostd/owners,dst=/box-store" in apply_call
    assert "--security-opt no-new-privileges=true --network bridge" in apply_call
    assert "BACKUP_DEST=:local:/synthetic-snapshots" in apply_call
    assert "/root/.config/rclone:/root/.config/rclone:ro" in apply_call
    assert "--box-backup /backup/tinyassets-box-store-" in calls
    detail_call = next(line for line in calls.splitlines()
                       if "tinyassets.box_layout_migration census" in line)
    assert "tinyassets-data:/data:ro" in detail_call
    assert calls.index(preflight_call) < calls.index(detail_call) < calls.index(apply_call)
    if outcome == "commit":
        verify_call = next(line for line in calls.splitlines()
                           if "tinyassets.box_layout_migration verify" in line)
        assert "tinyassets-data:/data:ro" in verify_call
        assert "--mount type=bind,src=/var/lib/boxhostd/owners,dst=/box-store " in verify_call
        assert "--cap-drop ALL --user 0 --privileged" in verify_call
        assert "--network none" in verify_call
        assert calls.index(apply_call) < calls.index(verify_call)
    harness = (harness_root / "harness-current").resolve()
    assert (harness / "app" / "tinyassets" / "boxd.py").is_file()
    assert not any((harness / name).exists() for name in (
        "data", ".broker", ".platform", ".runtime"
    ))
    marker = json.loads((volume / ".layout.json").read_text())
    if outcome == "commit":
        assert _result(completed) == "deployed", completed.stderr
        assert marker["box_layout"] == "owner-box"
    else:
        assert _result(completed) == "rollback_needs_restore", completed.stderr
        assert "-m tinyassets.cutover_control rollback --state" in completed.stderr
        assert "backup-restore.sh" not in completed.stderr
        assert marker["state"] == "migrating"
        assert not _daemon_starts(box)


@pytest.mark.parametrize("failure,reason", [
    ("leftover", "box store must be empty before the one-shot move"),
    ("device", "owner store has no block device"),
])
def test_owner_box_preflight_refuses_before_daemon_stop_and_preserves_prod(
    box: Box, failure: str, reason: str,
):
    volume, env, _, _ = _owner_box_cutover(box, device_present=failure != "device")
    if failure == "leftover":
        store = box.root / "owners"
        (store / "300001").mkdir(parents=True)
        env["FAKE_DOCKER_BOX_STORE_DIR"] = str(store)
    before = box.live()
    marker_before = (volume / ".layout.json").read_bytes()
    box.stage_bundle()

    completed = box.run(NEW_IMAGE, **env)

    assert completed.returncode == 1
    assert _result(completed) == "migration_refused"
    assert "check=box_layout_preflight" in _migration_refusal(completed)
    assert reason in _migration_refusal(completed)
    assert box.live() == before
    assert (volume / ".layout.json").read_bytes() == marker_before
    if failure == "leftover":
        assert (store / "300001").is_dir()
    calls = box.docker_calls_text()
    assert "stop --time" not in calls
    assert "start tinyassets-daemon" not in calls
    assert "tinyassets.box_layout_migration apply" not in calls
    assert not list((box.root / "backups").glob("*.tar.gz"))


@pytest.mark.parametrize("candidate", ["healthy", "unhealthy", "converge_fails"])
def test_a_committed_migration_never_restarts_the_previous_image(box: Box, candidate):
    """The old image serving with its bindings deleted from SQL is the failure
    the whole cutover exists to prevent (Codex on #4744). After apply commits,
    a candidate that fails leaves the service down with the exact restore
    command for the snapshot this run took: no image-only rollback."""
    volume, env = _cutover(box)
    if candidate == "unhealthy":
        box.set_docker_state(unhealthy_images=[NEW_IMAGE])
    elif candidate == "converge_fails":
        box.set_docker_state(up_fail_images=[NEW_IMAGE])
    before = box.live()
    box.stage_bundle()

    completed = box.run(NEW_IMAGE, **env)

    calls = box.docker_calls_text()
    assert "agent_file_migration apply /data --backup /backup/tinyassets-snapshot-" in calls
    assert "--user 0 --cap-add CHOWN --cap-add FOWNER --cap-add DAC_OVERRIDE" in calls
    assert "stop --time" in calls, "the daemon stops before the snapshot and the move"
    assert completed.stdout.index("stopping tinyassets-daemon") < completed.stdout.index(
        "verified backup:"
    )
    assert "verified backup:" in completed.stdout
    assert json.loads((volume / ".layout.json").read_text(encoding="utf-8"))["state"] == "stable"
    snapshots = list((box.root / "backups").glob("tinyassets-snapshot-*.tar.gz"))
    assert len(snapshots) == 1
    if candidate == "healthy":
        assert completed.returncode == 0, completed.stderr
        assert _result(completed) == "deployed"
        assert _deployed_image(completed) == NEW_IMAGE
        return
    assert completed.returncode == 3, completed.stderr
    assert _result(completed) == "rollback_needs_restore"
    assert "start " not in calls, "the previous image must never start on migrated data"
    assert len(_daemon_starts(box)) == 1, (
        "only the candidate's converge was attempted; no rollback converge of the previous image"
    )
    assert f"NOT starting {OLD_IMAGE}" in completed.stderr
    assert "python3 -m tinyassets.cutover_control rollback --state " in completed.stderr
    assert "/cutover --data" in completed.stderr
    assert "backup-restore.sh" not in completed.stderr
    assert box.live() == before, "the bundle this run installed is restored"
    assert box.env_image() == NEW_IMAGE, "the env must not be re-pointed at the previous image"
    daemon = box.docker_state_json()["containers"]["tinyassets-daemon"]
    assert daemon["status"] == "exited", "the service is left down for the restore"
    # A refused converge never replaced the container stopped for the move.
    assert daemon["image_ref"] == (OLD_IMAGE if candidate == "converge_fails" else NEW_IMAGE)


@pytest.mark.parametrize("outcome,result,code", [
    ("stable", "migration_refused", 1),
    ("migrating", "migration_refused", 1),
    ("committed_stable", "rollback_needs_restore", 3),
    ("committed_migrating", "rollback_needs_restore", 3),
])
def test_a_failed_apply_is_judged_by_sql(box: Box, outcome, result, code):
    """SQL decides whether an apply committed, regardless of marker state."""
    volume, env = _cutover(box)
    box.set_docker_state(migration_apply=outcome)
    before = box.live()
    box.stage_bundle()

    completed = box.run(NEW_IMAGE, **env)

    assert completed.returncode == code, completed.stderr
    assert _result(completed) == result
    assert box.live() == before and box.env_image() == OLD_IMAGE
    calls = box.docker_calls_text()
    assert calls.count("agent_file_migration check /data") == 2
    assert "compose" not in calls.split("agent_file_migration apply")[-1], (
        "no image is converged after a failed apply"
    )
    daemon = box.docker_state_json()["containers"]["tinyassets-daemon"]
    assert f"fake migration: apply failed outcome={outcome.removeprefix('committed_')}" in (
        completed.stdout
    )
    if result == "migration_refused":
        assert "start tinyassets-daemon" in calls and daemon["status"] == "running"
        reason = _migration_refusal(completed)
        assert "check=move_committed" in reason
        assert "apply_rc=1" in reason
        assert f"outcome={outcome}" in reason
        assert "recheck_rc=10" in reason
        assert 'recheck_output={"pending": 1}' in reason
    else:
        assert "start " not in calls and daemon["status"] == "exited"
        assert "rollback through deployment control:" in completed.stderr
        assert "tinyassets.cutover_control rollback" in completed.stderr
        assert "apply_rc=1 apply_output=fake migration: apply failed outcome=" in (
            completed.stdout
        )


def test_a_failed_sql_recheck_keeps_the_old_image_down(box: Box):
    _, env = _cutover(box)
    box.set_docker_state(migration_apply="stable", migration_recheck_error=True)
    completed = box.run(NEW_IMAGE, **env)

    assert completed.returncode == 3, completed.stderr
    assert _result(completed) == "rollback_needs_restore"
    assert not _daemon_starts(box)
    assert "could not verify SQL after apply failed" in completed.stderr
    assert "rc=1 output=fake migration: recheck failed: sqlite locked" in completed.stdout
    assert "apply_output=fake migration: apply failed outcome=stable" in completed.stdout


def _refusing_backup(box: Box, body: str) -> None:
    script = box.root / "backup.sh"
    script.write_text(body, encoding="utf-8", newline="\n")
    script.chmod(0o755)














def test_every_forward_deploy_proves_idle_and_snapshots_only_for_a_move(box: Box):
    box.stage_bundle()
    completed = box.run(NEW_IMAGE)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    stdout = completed.stdout
    assert "in-flight proof: idle" in stdout
    assert stdout.index("in-flight proof: idle") < stdout.index("migration: asking")
    assert "stopping tinyassets-daemon" not in stdout
    assert "migration: nothing to move; inspected" in stdout
    assert "box layout inspected: candidate=owner-split rc=10" in stdout
    assert "stable owner-split census" in stdout
    assert not list((box.root / "backups").glob("tinyassets-snapshot-*.tar.gz"))


def test_an_in_flight_turn_refuses_before_any_stop(box: Box):
    box.stage_bundle()
    box.set_docker_state(turns_rc=10)
    completed = box.run(NEW_IMAGE)
    assert completed.returncode == 1, completed.stderr
    assert _result(completed) == "migration_refused"
    assert "check=turns_idle rc=10" in _migration_refusal(completed)
    assert "stop --time" not in box.docker_calls_text()
    assert not list((box.root / "backups").glob("*.tar.gz"))


def test_a_backup_older_than_this_run_is_refused(box: Box):
    _refusing_backup(
        box,
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "name='tinyassets-snapshot-2020-01-01T00-00-00Z.tar.gz'\n"
        "mkdir -p \"${BACKUP_SNAPSHOT_DIR}\"\n"
        "python3 - \"${BACKUP_SNAPSHOT_DIR}\" \"$name\" <<'PY'\n"
        "import hashlib, io, os, sys, tarfile\n"
        "directory, name = sys.argv[1:]\n"
        "path = os.path.join(directory, name)\n"
        "payload = b'stale\\n'\n"
        "with tarfile.open(path, 'w:gz') as archive:\n"
        "    info = tarfile.TarInfo('inventory.txt')\n"
        "    info.size = len(payload)\n"
        "    archive.addfile(info, io.BytesIO(payload))\n"
        "digest = hashlib.sha256(open(path, 'rb').read()).hexdigest()\n"
        "open(path + '.sha256', 'w').write('%s  %s\\n' % (digest, name))\n"
        "PY\n"
        "echo \"SNAPSHOT id=${name} sha256=0 bytes=1 names=1\"\n",
    )
    box.set_docker_state(migration_pending=True)
    box.stage_bundle()
    completed = box.run(NEW_IMAGE)
    assert completed.returncode == 1, completed.stdout + completed.stderr
    reason = _migration_refusal(completed)
    assert "check=backup_verify" in reason
    assert "older than this run" in reason
    stdout = completed.stdout
    assert stdout.index("stopping tinyassets-daemon") < stdout.index("migration refused:")
    assert "agent_file_migration apply" not in box.docker_calls_text()
    assert "start tinyassets-daemon" in box.docker_calls_text()




def test_unprovisioned_canary_refuses_an_owner_box_candidate(box: Box):
    """An owner-box candidate would move data, so a missing canary stops first."""
    _volume, env = _cutover(box)
    box.set_docker_state(
        migration_pending=False,
        active_box_layout={NEW_IMAGE: "owner-box"},
        known_layouts={NEW_IMAGE: {"layouts": [1, 2], "boxes": ["owner-box"]}},
    )
    env["CORE_CANARY_PROVISIONED"] = "0"
    env["BOX_STORE_CHECK"] = "/bin/true"
    before = box.live()
    box.stage_bundle()
    completed = box.run(NEW_IMAGE, **env)
    assert completed.returncode == 1, completed.stdout + completed.stderr
    assert _result(completed) == "migration_refused"
    assert "check=core_canary_not_provisioned move=owner-box_candidate" in (
        _migration_refusal(completed)
    )
    calls = box.docker_calls_text()
    assert "stop --time" not in calls
    assert "box_layout_migration apply" not in calls
    assert "agent_file_migration apply" not in calls
    assert box.live() == before


def test_unprovisioned_canary_refuses_a_pending_migration(box: Box):
    """Agent-files pending is a data move even when the candidate is owner-split."""
    _volume, env = _cutover(box)
    env["CORE_CANARY_PROVISIONED"] = "0"
    box.stage_bundle()
    completed = box.run(NEW_IMAGE, **env)
    assert completed.returncode == 1, completed.stdout + completed.stderr
    assert _result(completed) == "migration_refused"
    assert "check=core_canary_not_provisioned move=agent_files_pending" in (
        _migration_refusal(completed)
    )
    calls = box.docker_calls_text()
    assert "agent_file_migration apply" not in calls
    assert "box_layout_migration apply" not in calls
    assert "start tinyassets-daemon" in calls


def test_an_absent_layout_census_refuses_instead_of_skipping(box: Box):
    """No marker is not 'nothing to move'. The census refuses the deploy."""
    volume = box.root / "empty-volume"
    volume.mkdir()
    box.stage_bundle()
    completed = box.run(NEW_IMAGE, FAKE_DOCKER_VOLUME_DIR=str(volume))
    assert completed.returncode == 1, completed.stdout + completed.stderr
    assert _result(completed) == "migration_refused"
    reason = _migration_refusal(completed)
    assert "check=box_layout_absent" in reason
    assert "box_layout_migration apply" not in box.docker_calls_text()
    assert "stable owner-split census" not in completed.stdout
    assert "start tinyassets-daemon" in box.docker_calls_text()


def test_an_ambiguous_layout_census_refuses_instead_of_skipping(box: Box):
    """An unreadable census is a refusal, not a skip past the move."""
    box.set_docker_state(box_census_rc=3)
    box.stage_bundle()
    completed = box.run(NEW_IMAGE)
    assert completed.returncode == 1, completed.stdout + completed.stderr
    assert _result(completed) == "migration_refused"
    reason = _migration_refusal(completed)
    assert "check=box_layout_ambiguous rc=3" in reason
    assert "box_layout_migration apply" not in box.docker_calls_text()
    assert "start tinyassets-daemon" in box.docker_calls_text()


@pytest.mark.parametrize("gate,reason", [
    ("canary", "check=core_canary_not_provisioned move=owner-box_candidate"),
    ("in_flight", "check=turns_idle rc=10"),
    ("backup", "check=backup_verify"),
    ("census", "check=box_layout_probe rc=4"),
])
def test_flipping_active_box_layout_without_a_gate_refuses(box: Box, gate, reason):
    """ACTIVE_BOX_LAYOUT=owner-box would move this owner-split volume on the next deploy.

    The constant only selects the move step. That step refuses unless the fresh
    verified backup, the in-flight probe, the census, and the canary all passed.
    """
    box.set_docker_state(active_box_layout={NEW_IMAGE: "owner-box"})
    harness = box.root / "boxhostd"
    harness.mkdir()
    env = {
        "BOX_HARNESS_ROOT": str(harness),
        "BOX_STORE_CHECK": "/bin/true",
    }
    if gate == "canary":
        env["CORE_CANARY_PROVISIONED"] = "0"
    elif gate == "in_flight":
        box.set_docker_state(turns_rc=10)
    elif gate == "backup":
        _, ready_env, _, _ = _owner_box_cutover(box)
        env.update(ready_env)
        _refusing_backup(
            box,
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "name='tinyassets-snapshot-2020-01-01T00-00-00Z.tar.gz'\n"
            "mkdir -p \"${BACKUP_SNAPSHOT_DIR}\"\n"
            "python3 - \"${BACKUP_SNAPSHOT_DIR}\" \"$name\" <<'PY'\n"
            "import hashlib, io, os, sys, tarfile\n"
            "directory, name = sys.argv[1:]\n"
            "path = os.path.join(directory, name)\n"
            "payload = b'stale\\n'\n"
            "with tarfile.open(path, 'w:gz') as archive:\n"
            "    info = tarfile.TarInfo('inventory.txt')\n"
            "    info.size = len(payload)\n"
            "    archive.addfile(info, io.BytesIO(payload))\n"
            "digest = hashlib.sha256(open(path, 'rb').read()).hexdigest()\n"
            "open(path + '.sha256', 'w').write('%s  %s\\n' % (digest, name))\n"
            "PY\n"
            "echo \"SNAPSHOT id=${name} sha256=0 bytes=1 names=1\"\n",
        )
    elif gate == "census":
        box.set_docker_state(box_census_rc=4)
    else:
        raise AssertionError(gate)
    before = box.live()
    box.stage_bundle()

    completed = box.run(NEW_IMAGE, **env)

    assert completed.returncode == 1, completed.stdout + completed.stderr
    assert _result(completed) == "migration_refused"
    assert "candidate box layout: owner-box" in completed.stdout
    refusal = _migration_refusal(completed)
    assert reason in refusal
    calls = box.docker_calls_text()
    assert "box_layout_migration apply" not in calls
    assert "agent_file_migration apply" not in calls
    marker = json.loads(
        (box.root / "default-data-volume" / ".layout.json").read_text(encoding="utf-8")
    )
    assert marker["box_layout"] == "owner-split"
    assert marker["state"] == "stable"
    assert box.live() == before
    if gate == "backup":
        assert "older than this run" in refusal
        assert completed.stdout.index("stopping tinyassets-daemon") < completed.stdout.index(
            "migration refused:"
        )
    else:
        assert "stop --time" not in calls
        assert "start tinyassets-daemon" in calls
