"""A deploy never swaps the daemon while it is running someone's work.

``scripts/turns_in_flight.py`` is the question; the ``Wait for in-flight turns``
step of ``deploy-prod.yml`` is the loop that asks it before the swap. Both are
exercised for real here: seats taken through ``universe_seats`` itself, the
script run the way production runs it (piped to ``python -`` with no
``tinyassets`` importable), and the workflow's own bash loop run against a
stand-in ``ssh``.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from scripts import turns_in_flight as tif
from tinyassets import universe_seats as seats
from tinyassets.storage import DB_FILENAME
from tinyassets.storage.agent_turn_journal import WORKING_STATES, ensure_schema

try:
    import yaml
except ImportError:  # pragma: no cover - CI installs it
    yaml = None

_REPO = Path(__file__).resolve().parent.parent
_SCRIPT = _REPO / "scripts" / "turns_in_flight.py"
_WORKFLOW = _REPO / ".github" / "workflows" / "deploy-prod.yml"


# --- the names the script cannot import --------------------------------------


def test_duplicated_names_match_the_daemon():
    assert tif.SEATS_DB == seats.LEDGER_NAME
    assert tif.JOURNAL_DB == DB_FILENAME
    assert set(tif.WORKING_STATES) == set(WORKING_STATES)
    from tinyassets import process_liveness, runs

    assert set(tif.IN_FLIGHT_RUN_STATUSES) == {
        runs.RUN_STATUS_QUEUED, runs.RUN_STATUS_RUNNING, runs.RUN_STATUS_RESUMED}
    assert tif.RUNS_DB == runs.runs_db_path(Path("x")).name
    assert tif.LIVENESS_DIR == process_liveness.LIVENESS_DIR
    assert (tif.ALIVE, tif.DEAD, tif.UNKNOWN_OWNER) == (
        process_liveness.ALIVE, process_liveness.DEAD, process_liveness.UNKNOWN)


def test_script_imports_nothing_from_the_repo():
    """It runs inside whatever image is live, which may predate any helper."""
    source = _SCRIPT.read_text(encoding="utf-8")
    assert "import tinyassets" not in source
    assert "from tinyassets" not in source
    assert "from scripts" not in source


# --- the question ------------------------------------------------------------


def test_missing_data_dir_is_idle_and_creates_nothing(tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    status, report = tif.observe(root)
    assert status == tif.IDLE
    assert report["in_flight"] == 0
    assert list(root.iterdir()) == []


def test_a_held_seat_is_busy_and_its_release_is_idle(tmp_path):
    db = tmp_path / tif.SEATS_DB
    seat = seats.acquire(
        "acct", seat_class=seats.CLASS_INTERACTIVE, kind=seats.KIND_CHAT_TURN,
        universe_id="u-village", db=db,
    )
    assert isinstance(seat, seats.Seat)
    status, report = tif.observe(tmp_path)
    assert status == tif.BUSY
    assert report["in_flight"] == 1
    assert report["seats"][0]["kind"] == seats.KIND_CHAT_TURN
    assert report["seats"][0]["universe_id"] == "u-village"

    assert seats.release(seat.seat_id, db=db)
    status, report = tif.observe(tmp_path)
    assert status == tif.IDLE
    assert report["in_flight"] == 0


def _set_holder(db: Path, holder: str) -> None:
    conn = sqlite3.connect(db, isolation_level=None)
    conn.execute("UPDATE account_seats SET holder = ?", (holder,))
    conn.close()


def _dead_token(root: Path, token: str = "deadholder") -> str:
    """A liveness file nobody holds: the proof that its owner died."""
    (root / tif.LIVENESS_DIR).mkdir(exist_ok=True)
    (root / tif.LIVENESS_DIR / f"{token}.lock").write_text("", encoding="utf-8")
    return token


def test_an_expired_seat_whose_holder_is_alive_is_still_work(tmp_path):
    """The ledger keeps it (``universe_seats._reap``), so the probe must too: a
    lapsed refresh is not a finished turn."""
    db = tmp_path / tif.SEATS_DB
    seat = seats.acquire("acct", db=db, now=time.time() - 600, lease_s=120)
    assert isinstance(seat, seats.Seat)  # holder = this live process
    status, report = tif.observe(tmp_path)
    assert status == tif.BUSY, report
    assert report["seats"][0]["expired"] is True
    assert report["seats"][0]["holder"] == "alive"


def test_a_dead_holders_seat_is_not_work_expired_or_not(tmp_path):
    db = tmp_path / tif.SEATS_DB
    seats.acquire("acct", db=db)
    _set_holder(db, _dead_token(tmp_path))
    status, report = tif.observe(tmp_path)
    assert status == tif.IDLE, report


def test_an_unprovable_holder_counts_only_until_its_lease_expires(tmp_path):
    db = tmp_path / tif.SEATS_DB
    seats.acquire("acct", db=db)
    _set_holder(db, "no-liveness-file")
    assert tif.observe(tmp_path)[0] == tif.BUSY
    assert tif.observe(tmp_path, now=time.time() + 600)[0] == tif.IDLE


# --- graph runs: seats cover agent calls, not whole runs ----------------------


def _run_row(base: Path, *, status: str, owner: str | None, started_at: float | None = None):
    from tinyassets import runs

    runs.initialize_runs_db(base)
    conn = sqlite3.connect(runs.runs_db_path(base), isolation_level=None)
    conn.execute(
        "INSERT INTO runs (run_id, branch_def_id, thread_id, status, actor, started_at, "
        "owner_token) VALUES (?, 'b', 't', ?, 'universe:u', ?, ?)",
        (f"run-{status}-{owner}", status, started_at or time.time(), owner),
    )
    conn.close()


def test_a_running_graph_run_with_a_live_owner_holds_the_deploy(tmp_path):
    """An automation or background run executing a code node holds no seat."""
    from tinyassets.process_liveness import owner_token

    universe = tmp_path / "u-village"
    universe.mkdir()
    _run_row(universe, status="running", owner=owner_token(universe))
    status, report = tif.observe(tmp_path)
    assert status == tif.BUSY, report
    assert report["runs"][0]["store"] == "u-village"
    assert report["seats"] == []


def test_root_store_queued_run_counts_and_finished_or_dead_runs_do_not(tmp_path):
    from tinyassets.process_liveness import owner_token

    _run_row(tmp_path, status="queued", owner=owner_token(tmp_path))
    assert tif.observe(tmp_path)[0] == tif.BUSY

    other = tmp_path / "u-other"
    other.mkdir()
    _run_row(other, status="completed", owner=owner_token(other))
    _run_row(other, status="running", owner=_dead_token(other))
    _, report = tif.observe(tmp_path)
    assert len(report["runs"]) == 1 and report["runs"][0]["store"] == ""


def test_a_tokenless_run_counts_only_if_it_started_after_this_boot(tmp_path, monkeypatch):
    universe = tmp_path / "u-old"
    universe.mkdir()
    _run_row(universe, status="running", owner=None, started_at=1000.0)
    assert tif.observe(tmp_path, boot=2000.0)[0] == tif.IDLE
    assert tif.observe(tmp_path, boot=500.0)[0] == tif.BUSY
    assert tif.observe(tmp_path, boot=None)[0] == tif.IDLE


def test_relative_paths_work_after_entering_the_data_root(tmp_path, monkeypatch):
    """A data root given relative to the working directory still resolves."""
    seats.acquire("acct", db=tmp_path / tif.SEATS_DB)
    monkeypatch.chdir(tmp_path)
    status, report = tif.observe(Path("."))
    assert status == tif.BUSY, report
    rc = tif.main(["--data-dir", ".", "--mark-pending", "--ttl", "60"])
    assert rc == tif.BUSY
    assert (tmp_path / tif.MARKER).is_file()


def test_a_working_journal_row_is_reported_but_does_not_hold_the_deploy(tmp_path):
    """A cancelled task leaves ``native_started`` behind until the next boot.
    Gating on it would hold every deploy to the cap behind a phantom."""
    conn = sqlite3.connect(tmp_path / DB_FILENAME, isolation_level=None)
    ensure_schema(conn)
    conn.execute(
        "INSERT INTO agent_turns (owner_user_id, universe_id, turn_id, version, generation, "
        "state, round_ordinal, input_json, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        ("owner", "u-village", "652a2f31e82546a1", 1, 1, "native_started", 1, "{}",
         "2026-10-02T01:00:00Z"),
    )
    conn.close()
    status, report = tif.observe(tmp_path)
    assert status == tif.IDLE
    assert report["journal_working"] == [{
        "universe_id": "u-village", "turn": "652a2f31", "state": "native_started",
        "age_s": report["journal_working"][0]["age_s"],
    }]


def test_an_unreadable_ledger_is_unknown_never_idle(tmp_path):
    (tmp_path / tif.SEATS_DB).write_bytes(b"this is not a sqlite database at all" * 40)
    status, report = tif.observe(tmp_path)
    assert status == tif.UNKNOWN
    assert report["in_flight"] is None
    assert "unreadable" in report["error"]


def test_runs_piped_to_python_with_no_repo_on_the_path(tmp_path):
    """Exactly how the workflow runs it: ``docker exec -i ... python - ARGS < file``."""
    db = tmp_path / tif.SEATS_DB
    seat = seats.acquire("acct", db=db)
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    result = subprocess.run(
        [sys.executable, "-I", "-", "--data-dir", str(tmp_path)],
        input=_SCRIPT.read_bytes(), capture_output=True, cwd=str(tmp_path), env=env,
        timeout=60,
    )
    assert result.returncode == tif.BUSY, result.stderr
    assert json.loads(result.stdout)["in_flight"] == 1
    seats.release(seat.seat_id, db=db)
    result = subprocess.run(
        [sys.executable, "-I", "-", "--data-dir", str(tmp_path)],
        input=_SCRIPT.read_bytes(), capture_output=True, cwd=str(tmp_path), env=env,
        timeout=60,
    )
    assert result.returncode == tif.IDLE, result.stderr


# --- the marker and the status field ----------------------------------------


def test_marker_is_visible_in_status_until_it_expires_or_is_cleared(tmp_path, monkeypatch):
    from tinyassets.api import status as status_mod

    monkeypatch.setattr(status_mod, "_base_path", lambda: tmp_path)
    assert status_mod._load_deploy_pending() == {"pending": False}

    seats.acquire("acct", db=tmp_path / tif.SEATS_DB)
    rc = tif.main([
        "--data-dir", str(tmp_path), "--mark-pending", "--target", "abc123",
        "--waiting-since", str(time.time() - 30), "--ttl", "60",
        "--run-url", "https://example.invalid/run",
    ])
    assert rc == tif.BUSY
    seen = status_mod._load_deploy_pending()
    assert seen["pending"] is True
    assert seen["target"] == "abc123"
    assert seen["in_flight"] == 1
    assert seen["run_url"] == "https://example.invalid/run"

    # A deploy job that died mid-wait stops meaning anything once the ttl lapses.
    later = time.time() + 120
    assert status_mod._load_deploy_pending(now=later) == {
        "pending": False, "warning": "deploy_pending_marker_expired",
    }

    assert tif.main(["--data-dir", str(tmp_path), "--clear-pending"]) == tif.IDLE
    assert not (tmp_path / tif.MARKER).exists()
    assert status_mod._load_deploy_pending() == {"pending": False}


def test_a_garbled_marker_never_reads_as_pending(tmp_path, monkeypatch):
    from tinyassets.api import status as status_mod

    monkeypatch.setattr(status_mod, "_base_path", lambda: tmp_path)
    (tmp_path / tif.MARKER).write_text("{not json", encoding="utf-8")
    assert status_mod._load_deploy_pending()["pending"] is False
    (tmp_path / tif.MARKER).write_text('["pending"]', encoding="utf-8")
    assert status_mod._load_deploy_pending()["pending"] is False
    (tmp_path / tif.MARKER).write_text('{"pending": true}', encoding="utf-8")
    assert status_mod._load_deploy_pending()["pending"] is False  # no expires_at




# --- the workflow step and deploy/wait_for_turns.sh ---------------------------


_WAIT_SCRIPT = _REPO / "deploy" / "wait_for_turns.sh"


def _steps() -> list[dict]:
    wf = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    return wf["jobs"]["deploy"]["steps"]


def _step(name: str) -> dict:
    return next(step for step in _steps() if step.get("name") == name)


@pytest.mark.skipif(yaml is None, reason="pyyaml not installed")
def test_wait_runs_before_the_swap_and_outside_its_lock():
    names = [step.get("name") for step in _steps()]
    assert names.index("Wait for in-flight turns") < names.index(
        "Run fail-safe deploy on the droplet")
    step = _step("Wait for in-flight turns")
    assert step["run"].strip() == "bash deploy/wait_for_turns.sh"
    body = _WAIT_SCRIPT.read_text(encoding="utf-8")
    assert "scripts/turns_in_flight.py" in body
    # Not under the host-mutation lock: the watchdogs need it while we wait.
    assert "host-mutation.lock" not in body and "deploy_fail_safe.sh" not in body
    assert not any(line.split()[:1] == ["flock"] for line in body.splitlines())
    # Every recovery workflow sharing the group is one the wait yields to.
    group_peers = {"p0-outage-triage.yml", "restart-daemon.yml",
                   "install-host-services.yml", "apply-daemon-env.yml"}
    for peer in group_peers:
        text = (_REPO / ".github" / "workflows" / peer).read_text(encoding="utf-8")
        assert "production-host-mutation" in text, peer
    assert set(step["env"]["YIELD_WORKFLOWS"].split()) == group_peers
    assert step["env"]["IMAGE_REF"] == "${{ steps.tag.outputs.image_ref }}"


@pytest.mark.skipif(yaml is None, reason="pyyaml not installed")
def test_job_and_step_budgets_cover_the_cap():
    step = _step("Wait for in-flight turns")
    cap_s = int(step["env"]["TURN_WAIT_CAP_S"])
    assert cap_s == 2700
    # Cap + one bounded prefetch-free poll + cleanup must fit inside the step.
    # The cap already includes the prefetch. Past it: one worst-case poll (a
    # bounded check, four bounded recovery queries, a sleep) plus the cleanup.
    check_s, gh_s, poll_s = 90, 30, int(step["env"]["TURN_POLL_S"])
    worst_tail = check_s + 4 * gh_s + poll_s + check_s
    assert step["timeout-minutes"] * 60 >= cap_s + worst_tail + 60
    job = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))["jobs"]["deploy"]
    assert job["timeout-minutes"] >= step["timeout-minutes"] + 15


_FAKE_SSH = r"""#!/usr/bin/env bash
# Stand-in for the droplet: answers each in-flight check from a script of exit
# codes, one per call, and records every command it was asked to run.
cmd="${@: -1}"
printf '%s\n' "$cmd" >> "$FAKE_DIR/calls"
# FAKE_EXEC: run the REAL remote command here, with docker and sudo stubbed, so
# the host-side half (checksum, volume, probe) is exercised, not assumed.
if [ -n "${FAKE_EXEC:-}" ]; then
  case "$cmd" in *"docker pull"*) exit 0 ;; esac
  exec bash -c "$cmd"
fi
case "$cmd" in
  *"docker pull"*) exit 0 ;;
  *--clear-pending*) echo '{"cleared": true}'; exit 0 ;;
esac
if [ -n "${FAKE_HANG:-}" ]; then exec python -c "import time; time.sleep(60)"; fi
n=$(cat "$FAKE_DIR/n" 2>/dev/null || echo 0)
n=$((n + 1)); echo "$n" > "$FAKE_DIR/n"
rc=$(sed -n "${n}p" "$FAKE_DIR/script")
[ -n "$rc" ] || rc=$(tail -n 1 "$FAKE_DIR/script")
echo "{\"poll\": $n, \"rc\": $rc}"
exit "$rc"
"""

#: Answers a runs query with $FAKE_GH_QUEUED for the named workflow, 0 otherwise.
_FAKE_GH = r"""#!/usr/bin/env bash
printf '%s\n' "$*" >> "$FAKE_DIR/gh_calls"
case "$*" in
  *"${FAKE_GH_WORKFLOW:-none}"*) echo "${FAKE_GH_QUEUED:-0}" ;;
  *) echo 0 ;;
esac
"""


_FAKE_DOCKER = r"""#!/usr/bin/env bash
# inspect: the daemon's state|StartedAt|image. run: drop docker's own flags up to
# the image, map the volume's /data onto $FAKE_VOLUME, run the real probe.
printf 'docker %s\n' "$*" >> "$FAKE_DIR/docker_calls"
case "$1" in
  inspect) echo "${FAKE_STATE:-running/healthy}|2026-10-02T00:00:00.5Z|sha256:fakeimage" ;;
  run)
    while [ "$#" -gt 0 ] && [ "$1" != "sha256:fakeimage" ]; do shift; done
    shift
    args=()
    for a in "$@"; do [ "$a" = "/data" ] && a="$FAKE_VOLUME"; args+=("$a"); done
    exec python3 "${args[@]}" ;;
  *) echo "unexpected docker $*" >&2; exit 99 ;;
esac
"""


def _run_wait(tmp_path: Path, codes: list[int], *, cap_s: int = 2700,
              extra_env: dict[str, str] | None = None,
              exec_volume: Path | None = None, scp_fails: bool = False) -> dict[str, str]:
    """Run the step exactly as Actions does: its `run` text under `bash -eo pipefail`.

    ``exec_volume`` runs the REAL remote command against that directory as the
    daemon's data volume, with docker and sudo stubbed and the real probe.
    """
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash not available")
    fake = tmp_path / "fake"
    bin_dir = fake / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "ssh").write_text(_FAKE_SSH, encoding="utf-8", newline="\n")
    (bin_dir / "gh").write_text(_FAKE_GH, encoding="utf-8", newline="\n")
    (bin_dir / "docker").write_text(_FAKE_DOCKER, encoding="utf-8", newline="\n")
    scp_body = "exit 1" if scp_fails else (
        'a=("$@"); cp "${a[-2]}" "${a[-1]#*:}"' if exec_volume is not None else "exit 0")
    python = Path(sys.executable).as_posix()
    for name, body in (("scp", scp_body), ("sleep", "exit 0"), ("sudo", 'exec "$@"'),
                       ("python3", f'exec "{python}" "$@"')):
        (bin_dir / name).write_text(f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8",
                                    newline="\n")
    for tool in bin_dir.iterdir():
        tool.chmod(0o755)
    (fake / "script").write_text("\n".join(str(c) for c in codes) + "\n", encoding="utf-8",
                                 newline="\n")
    (fake / "calls").write_text("", encoding="utf-8")
    out = tmp_path / "gh_output"
    out.write_text("", encoding="utf-8")
    step = _step("Wait for in-flight turns")
    script = tmp_path / "step.sh"
    script.write_text(step["run"], encoding="utf-8", newline="\n")
    env = dict(os.environ)
    env.update({k: v for k, v in step["env"].items() if "${{" not in str(v)})
    env.update({
        "FAKE_DIR": fake.as_posix(),
        "GITHUB_OUTPUT": out.as_posix(),
        "GITHUB_REPOSITORY": "owner/repo",
        "DO_SSH_USER": "deploy", "DO_DROPLET_HOST": "droplet.invalid",
        "TARGET_REVISION": "a" * 40, "TURN_WAIT_CAP_S": str(cap_s),
        "RUN_URL": "https://example.invalid/run",
        "IMAGE_REF": "ghcr.io/o/tinyassets-daemon@sha256:" + "0" * 64,
        "CHECK_TIMEOUT_S": "2" if exec_volume is None else "60",
        "GITHUB_RUN_ID": tmp_path.name, "GITHUB_RUN_ATTEMPT": "1",
    })
    if exec_volume is not None:
        env.update({"FAKE_EXEC": "1", "FAKE_VOLUME": exec_volume.as_posix()})
    env.update(extra_env or {})
    if os.name == "nt":
        # Git Bash resolves PATH entries in POSIX form; prepend inside bash.
        wrapper = (f'export PATH="$(cygpath -u "{bin_dir}"):$PATH"; '
                   f'bash -eo pipefail "$(cygpath -u "{script}")"')
    else:
        wrapper = f'export PATH="{bin_dir}:$PATH"; bash -eo pipefail "{script}"'
    result = subprocess.run([bash, "-c", wrapper], env=env, capture_output=True, text=True,
                            timeout=120, cwd=str(_REPO))
    assert result.returncode == 0, result.stdout + result.stderr
    outputs = dict(
        line.split("=", 1) for line in out.read_text(encoding="utf-8").splitlines() if "=" in line
    )
    outputs["_calls"] = (fake / "calls").read_text(encoding="utf-8")
    outputs["_stdout"] = result.stdout
    return outputs


@pytest.mark.skipif(yaml is None, reason="pyyaml not installed")
def test_wait_holds_while_busy_then_deploys_when_idle(tmp_path):
    """Under Actions' own `bash -e`: a busy answer is data, not a failed step."""
    out = _run_wait(tmp_path, [10, 10, 10, 0])
    assert out["outcome"] == "idle"
    assert out["polls"] == "4"
    calls = out["_calls"].splitlines()
    assert sum("--mark-pending" in c for c in calls) == 4
    assert "--clear-pending" in calls[-1]


@pytest.mark.skipif(yaml is None, reason="pyyaml not installed")
def test_the_image_is_prefetched_before_the_first_poll(tmp_path):
    calls = _run_wait(tmp_path, [0])["_calls"].splitlines()
    assert "docker pull" in calls[0]
    assert "--mark-pending" in calls[1]


@pytest.mark.skipif(yaml is None, reason="pyyaml not installed")
def test_wait_proceeds_at_the_cap(tmp_path):
    out = _run_wait(tmp_path, [10], cap_s=0)
    assert out["outcome"] == "cap_reached"
    assert out["polls"] == "1"


@pytest.mark.skipif(yaml is None, reason="pyyaml not installed")
def test_a_daemon_that_is_not_serving_is_deployed_at_once(tmp_path):
    out = _run_wait(tmp_path, [20])
    assert out["outcome"] == "daemon_not_serving"
    assert out["polls"] == "1"


@pytest.mark.skipif(yaml is None, reason="pyyaml not installed")
def test_unknown_is_retried_then_proceeds_loudly(tmp_path):
    out = _run_wait(tmp_path, [2, 2, 2])
    assert out["outcome"] == "check_unavailable"
    assert out["polls"] == "3"
    assert "could not answer" in out["_stdout"]


@pytest.mark.skipif(yaml is None, reason="pyyaml not installed")
def test_one_unknown_between_busy_answers_does_not_end_the_wait(tmp_path):
    out = _run_wait(tmp_path, [10, 2, 10, 2, 10, 2, 0])
    assert out["outcome"] == "idle"
    assert out["polls"] == "7"


@pytest.mark.skipif(yaml is None, reason="pyyaml not installed")
def test_a_hung_check_is_bounded_and_counts_as_unknown(tmp_path):
    out = _run_wait(tmp_path, [10], extra_env={"FAKE_HANG": "1"})
    assert out["outcome"] == "check_unavailable"
    assert out["polls"] == "3"
    assert "rc=124" in out["_stdout"]


@pytest.mark.skipif(yaml is None, reason="pyyaml not installed")
def test_a_queued_recovery_workflow_ends_the_wait(tmp_path):
    out = _run_wait(tmp_path, [10, 10, 10], extra_env={
        "FAKE_GH_WORKFLOW": "p0-outage-triage.yml", "FAKE_GH_QUEUED": "1",
    })
    assert out["outcome"] == "yield_to_host_mutation"
    assert out["polls"] == "1"


@pytest.mark.skipif(yaml is None, reason="pyyaml not installed")
def test_no_queued_recovery_keeps_waiting(tmp_path):
    out = _run_wait(tmp_path, [10, 10, 0], extra_env={"FAKE_GH_QUEUED": "0"})
    assert out["outcome"] == "idle"
    assert out["polls"] == "3"


# --- the host-side half, run for real --------------------------------------


@pytest.mark.skipif(yaml is None, reason="pyyaml not installed")
def test_the_real_remote_command_reads_the_volume_and_reports_idle(tmp_path):
    volume = tmp_path / "volume"
    volume.mkdir()
    out = _run_wait(tmp_path, [], exec_volume=volume)
    assert out["outcome"] == "idle", out["_stdout"]
    assert '"in_flight": 0' in out["_stdout"]
    assert not (volume / tif.MARKER).exists(), "cleared after the wait"


@pytest.mark.skipif(yaml is None, reason="pyyaml not installed")
def test_the_real_remote_command_sees_a_held_seat_and_marks_pending(tmp_path):
    volume = tmp_path / "volume"
    volume.mkdir()
    seats.acquire("acct", seat_class=seats.CLASS_INTERACTIVE, kind=seats.KIND_CHAT_TURN,
                  db=volume / tif.SEATS_DB)
    out = _run_wait(tmp_path, [], cap_s=0, exec_volume=volume)
    assert out["outcome"] == "cap_reached", out["_stdout"]
    assert '"in_flight": 1' in out["_stdout"]
    # A sibling container as the daemon's uid on the daemon's image and volume,
    # never an exec into the daemon itself.
    docker_calls = (tmp_path / "fake" / "docker_calls").read_text(encoding="utf-8")
    run = next(line for line in docker_calls.splitlines() if line.startswith("docker run"))
    for flag in ("--rm", "-i", "--network none", "--user 1001:1001",
                 "-v tinyassets-data:/data", "--entrypoint python", "sha256:fakeimage"):
        assert flag in run, flag
    assert "docker exec" not in docker_calls
    # The boot epoch came from the container's StartedAt, through `date -d`.
    assert '"boot_epoch": 1790899200.0' in out["_stdout"]


@pytest.mark.skipif(yaml is None, reason="pyyaml not installed")
def test_a_failed_upload_is_unknown_never_idle(tmp_path):
    """An empty or stale probe file must not run: `python3 -` on an empty file
    exits 0, which would read as idle (Codex round 2)."""
    volume = tmp_path / "volume"
    volume.mkdir()
    seats.acquire("acct", db=volume / tif.SEATS_DB)
    out = _run_wait(tmp_path, [], exec_volume=volume, scp_fails=True)
    assert out["outcome"] == "check_unavailable", out["_stdout"]
    assert "probe checksum mismatch" in out["_stdout"]


@pytest.mark.skipif(yaml is None, reason="pyyaml not installed")
def test_a_container_that_is_not_running_is_deployed_at_once_for_real(tmp_path):
    volume = tmp_path / "volume"
    volume.mkdir()
    out = _run_wait(tmp_path, [], exec_volume=volume,
                    extra_env={"FAKE_STATE": "exited/"})
    assert out["outcome"] == "daemon_not_serving", out["_stdout"]
