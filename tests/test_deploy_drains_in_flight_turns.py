"""A deploy's drain is short, because the drain is a public outage.

History, because the numbers have moved twice:

* Until #4039 compose declared no ``stop_grace_period``, so a recreate SIGKILLed
  the old daemon after docker's 10-second default and cut turns off mid-flight.
* #4039 raised it to 180s so turns could finish. On 2026-10-01 that held
  production at 502 from 22:49:48Z to 22:53:04Z behind one long codex turn
  (``docs/concerns/2026-10-01-deploy-drain-outage-and-watchdog-race.md``).
  Uvicorn closes its only listener the moment SIGTERM arrives, and nothing else
  can bind 127.0.0.1:8001 until the old container is gone, so every second of
  drain is a second of outage. The process then sat in "Waiting for application
  shutdown" on the turn's worker thread until docker force-killed it.

The drain never saved the reply either: sse-starlette cancels the SSE response
as shutdown begins (``docs/audits/2026-09-26-pr4039-drain-repro.py``). A turn
cut off now is settled truthfully at the next boot by
``tinyassets/agent_turn_reconcile.py``.

What needs testing is still the RELATIONSHIP between numbers in different files,
because each one alone looks fine while the set is broken:

    universe_server.GRACEFUL_SHUTDOWN_S  <  compose daemon.stop_grace_period
                                         ==  deploy_fail_safe MAX_DAEMON_STOP_GRACE_S
                                         <=  OUTAGE_BUDGET_S

plus the deploy passing that ceiling to ``up --timeout``. Compose otherwise stops
the old container with the StopTimeout it was CREATED with, so without the flag
the first deploy after a lowered grace would still drain for the old 180s.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
COMPOSE = REPO / "deploy" / "compose.yml"
SCRIPT = REPO / "deploy" / "deploy_fail_safe.sh"
SERVER = REPO / "tinyassets" / "universe_server.py"

#: What the deploy job allows for deploy work, from `.github/workflows/deploy-prod.yml`:
#: the job's timeout less the "Wait for in-flight turns" step's.
DEPLOY_JOB_BUDGET_S = 15 * 60
#: How many times one run can converge, and therefore drain: the forward converge,
#: plus the rollback converge when the new image does not become acceptable.
#: Missing this is what made the first version's budget check wrong.
CONVERGES_PER_RUN = 2
#: Everything else the job must still afford after the drains and health waits:
#: image pull, bundle claim/validate/snapshot/install, env write, canary.
DEPLOY_OVERHEAD_S = 120
#: The most public 502 one daemon stop may cost. The drain happens with the
#: listener already closed, so it is outage time, not grace.
OUTAGE_BUDGET_S = 30


def _workflow_job_timeout_s() -> int:
    """What the job allows for deploy WORK: its timeout minus the turn wait.

    "Wait for in-flight turns" may spend its whole step timeout before the swap
    starts (``tests/test_turns_in_flight.py``), so that share is not available
    to the converges and health waits this file budgets.
    """
    text = (REPO / ".github" / "workflows" / "deploy-prod.yml").read_text(encoding="utf-8")
    match = re.search(r"^    timeout-minutes:\s*(\d+)$", text, re.M)
    assert match, "the deploy job's timeout-minutes moved; re-point this"
    steps = yaml.safe_load(text)["jobs"]["deploy"]["steps"]
    wait = next(s for s in steps if s.get("name") == "Wait for in-flight turns")
    return (int(match.group(1)) - int(wait["timeout-minutes"])) * 60


def _health_timeout_s() -> int:
    """The health wait that follows EVERY converge, from the workflow env."""
    text = (REPO / ".github" / "workflows" / "deploy-prod.yml").read_text(encoding="utf-8")
    match = re.search(r"HEALTH_TIMEOUT:?=?\s*[\"']?(\d+)", text)
    assert match, "HEALTH_TIMEOUT is no longer set in deploy-prod.yml"
    return int(match.group(1))


def _systemd_start_timeout_s() -> int:
    text = (REPO / "deploy" / "tinyassets-daemon.service").read_text(encoding="utf-8")
    match = re.search(r"^TimeoutStartSec=(\d+)s?$", text, re.M)
    assert match, "TimeoutStartSec is no longer a plain seconds value"
    return int(match.group(1))


def _compose_grace_s() -> float:
    daemon = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]["daemon"]
    raw = daemon.get("stop_grace_period")
    assert raw is not None, (
        "the daemon must declare stop_grace_period; absent means docker's 10s default, "
        "which is what killed the founder's in-flight turns")
    match = re.fullmatch(r"(\d+)(s|m)?", str(raw).strip())
    assert match, f"stop_grace_period {raw!r} is not a plain seconds/minutes duration"
    return int(match.group(1)) * (60 if match.group(2) == "m" else 1)


def _script_ceiling_s() -> int:
    match = re.search(r"^MAX_DAEMON_STOP_GRACE_S=(\d+)$", SCRIPT.read_text(encoding="utf-8"), re.M)
    assert match, "MAX_DAEMON_STOP_GRACE_S is no longer a plain integer assignment"
    return int(match.group(1))


def _restart_stack_body() -> str:
    body = SCRIPT.read_text(encoding="utf-8")
    match = re.search(r"^restart_stack\(\) \{\n(.*?)^\}", body, re.S | re.M)
    assert match, "restart_stack is no longer a plain shell function"
    return match.group(1)


def test_the_servers_own_bound_expires_before_dockers():
    """Uvicorn cancels first, so the two bounds do not race.

    Not a clean-exit guarantee, which is what the first version of this docstring
    claimed. Codex refuted it: the timeout bounds uvicorn's wait on connections
    and tracked request tasks, and expiry cancels those -- it does not stop an
    AnyIO worker thread or bound lifespan shutdown, and a 0.25s value still let a
    worker run to 1.98s. Docker's SIGKILL is the real bound. What the ordering
    buys is that the ordinary case reaches uvicorn's own cancellation inside the
    window instead of being cut off mid-write by docker.
    """
    from tinyassets.universe_server import GRACEFUL_SHUTDOWN_S

    grace = _compose_grace_s()
    assert GRACEFUL_SHUTDOWN_S < grace, (
        f"the server waits {GRACEFUL_SHUTDOWN_S}s but docker SIGKILLs at {grace}s, so "
        "an in-flight turn dies mid-write instead of being closed cleanly")
    assert grace - GRACEFUL_SHUTDOWN_S >= 5, (
        "leave the server a real margin to close connections and exit, not a rounding error")


def test_the_deploy_gate_enforces_exactly_the_bound_that_shipped():
    """The ceiling the validator asserts IS the value in the file it validates.

    A ceiling above the shipped value would let a later bundle quietly raise the
    drain back toward an outage; one below it would refuse the live bundle.
    """
    assert _script_ceiling_s() == _compose_grace_s()


def test_the_drain_fits_the_outage_budget():
    """The drain is public 502 with the listener already closed (2026-10-01)."""
    assert _compose_grace_s() <= OUTAGE_BUDGET_S, (
        f"a {_compose_grace_s()}s drain is {_compose_grace_s()}s of public 502 on every "
        "deploy that lands during a turn")


def test_the_converge_bounds_the_old_containers_stop_explicitly():
    """`up --timeout` binds the container being REPLACED.

    Compose stops the old container with the StopTimeout it was created with, so
    lowering the compose value alone leaves the next deploy draining for the old
    180s. The flag is what makes the bound hold on the very next deploy.
    """
    function = _restart_stack_body()
    assert re.search(
        r'up -d \\\n\s*--timeout "\$MAX_DAEMON_STOP_GRACE_S" daemon cloudflared logs', function,
    ), "restart_stack must pass the ceiling to `docker compose up --timeout`"


def test_the_converge_clears_compose_temp_containers_first():
    """A leftover `<hex>_tinyassets-daemon` made the 2026-10-01 rollback fail."""
    function = _restart_stack_body()
    assert "remove_compose_temp_daemons" in function
    assert function.index("remove_compose_temp_daemons") < function.index("docker compose"), (
        "the strays must be gone BEFORE compose tries to create its own temp name")


def test_the_worst_case_deploy_still_fits_inside_the_job():
    """Both drains AND both health waits, not the grace on its own.

    A run that fails converges TWICE -- forward, then the rollback -- and waits
    HEALTH_TIMEOUT after each. The first version of this test compared the grace
    against half the budget and passed at 300s, where the real worst case was
    2*300 + 2*180 = 960 against a 900s job. Exceeding the job does not make a slow
    deploy; it CANCELS one part-way through a bundle install, which is worse than
    the bug this change fixes (Codex on #4039, P1).
    """
    grace = _compose_grace_s()
    budget = _workflow_job_timeout_s()
    assert budget == DEPLOY_JOB_BUDGET_S, "the job budget changed; re-derive the bound"
    worst_case = CONVERGES_PER_RUN * (grace + _health_timeout_s()) + DEPLOY_OVERHEAD_S
    assert worst_case <= budget, (
        f"worst case {worst_case}s ({CONVERGES_PER_RUN} x ({grace}s drain + "
        f"{_health_timeout_s()}s health) + {DEPLOY_OVERHEAD_S}s overhead) exceeds the "
        f"{budget}s job; a slow deploy is cancelled mid-install rather than rolled back")


def test_the_grace_stays_inside_the_units_own_start_deadline():
    """`apply-daemon-env-remote.sh` drives a recreate THROUGH the systemd unit.

    A grace longer than the unit's TimeoutStartSec means that path can be killed
    by its controller while docker was still willing to wait (Codex on #4039).
    """
    grace = _compose_grace_s()
    start_timeout = _systemd_start_timeout_s()
    assert grace < start_timeout, (
        f"a {grace}s drain outlives tinyassets-daemon.service's {start_timeout}s "
        "TimeoutStartSec, so a unit-driven recreate is cut off by systemd")


def test_the_served_launch_chooses_its_shutdown_bound():
    """Passed explicitly at the launch, not left to uvicorn's default.

    Uvicorn's default is to wait INDEFINITELY, which reads generous and is
    worthless: docker's SIGKILL arrives first, so the real bound was 10 seconds
    and nothing in the code said so. An AST check because the claim IS about the
    call site's arguments.
    """
    calls = [
        node for node in ast.walk(ast.parse(SERVER.read_text(encoding="utf-8")))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "run"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "uvicorn"
    ]
    assert calls, "the ASGI launch moved; re-point this assertion at it"
    for call in calls:
        keywords = {keyword.arg: keyword.value for keyword in call.keywords}
        assert "timeout_graceful_shutdown" in keywords, (
            "uvicorn's default waits forever and docker SIGKILLs first; choose the bound")
        value = keywords["timeout_graceful_shutdown"]
        assert isinstance(value, ast.Name) and value.id == "GRACEFUL_SHUTDOWN_S", (
            "pass the module constant, so the tests above constrain what actually runs")


def test_uvicorn_accepts_the_keyword_we_are_passing():
    """The kwarg is real, in the installed version. A silent typo here would mean
    the server keeps uvicorn's wait-forever default while every check above
    passes -- the whole failure mode this change exists to remove.
    """
    import inspect

    import uvicorn

    assert "timeout_graceful_shutdown" in inspect.signature(uvicorn.Config).parameters


def test_the_converge_is_timed_so_the_bound_can_be_measured():
    """The bound is a judgement, and it is only revisable with data.

    `restart_stack` logs how long the converge took, and says so when it reaches
    the grace bound -- which is the observable that says whether turns are still
    being cut off.
    """
    body = SCRIPT.read_text(encoding="utf-8")
    match = re.search(r"^restart_stack\(\) \{\n(.*?)^\}", body, re.S | re.M)
    assert match, "restart_stack is no longer a plain shell function"
    function = match.group(1)
    assert 'started="$SECONDS"' in function
    assert "SECONDS - started" in function
    assert "converge took" in function
    assert "MAX_DAEMON_STOP_GRACE_S" in function, (
        "reaching the bound is the signal that a turn was cut off; say so in the log")


def test_the_measurement_behind_the_narrowed_claim_is_retained():
    """The reply-is-not-saved finding is evidence, and evidence rots when deleted.

    Every comment in this change says the grace preserves the TURN and not the
    reply. That is a measurement, not a reading of the code, so the script that
    produces it and the concern entry that dates it both have to stay -- otherwise
    the next person re-derives the stronger, false claim from the code alone,
    which is exactly what the first version of this PR did.
    """
    repro = REPO / "docs" / "audits" / "2026-09-26-pr4039-drain-repro.py"
    assert repro.is_file(), "the shutdown reproduction is the basis of the narrowed claim"
    # Carried into the open zero-downtime concern when the 2026-08-29 concern was
    # resolved by the live proof of the deploy wait (2026-10-02).
    concern = (REPO / "docs" / "concerns"
               / "2026-10-01-deploys-are-not-zero-downtime.md").read_text(encoding="utf-8")
    assert "sse-starlette" in concern, (
        "the concern must record WHY a longer grace does not save the reply")


def test_the_deploy_wait_was_resolved_by_a_live_observation():
    """The 2026-08-29 concern could close only on a live observation of a turn
    surviving a deploy, not on a green suite. That happened on 2026-10-02, and the
    as-built requirement records it: deploy run 36979226551 waited for the
    founder's turn c5264d0a, which completed."""
    spec = (REPO / "openspec" / "specs" / "uptime-and-alarms" / "spec.md").read_text(
        encoding="utf-8")
    assert "A Deploy Waits For In-Flight Work Before It Swaps The Daemon" in spec
    assert "36979226551" in spec and "c5264d0a" in spec


@pytest.mark.parametrize("name", ["daemon"])
def test_only_the_daemon_carries_the_grace(name: str):
    """The sidecars are stateless; a grace on them only slows every deploy down."""
    services = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]
    carriers = {
        service for service, body in services.items()
        if isinstance(body, dict) and body.get("stop_grace_period") is not None
    }
    assert carriers == {name}, f"{sorted(carriers)} declare a grace; only {name} should"


def test_the_units_own_converge_carries_the_same_stop_ceiling():
    """Watchdog and manual restarts go through tinyassets-daemon.service. Its
    `up -d` would otherwise stop the old container with its create-time
    StopTimeout, which is 180s for anything recreated from a pre-2026-10-01
    bundle."""
    text = (REPO / "deploy" / "tinyassets-daemon.service").read_text(encoding="utf-8")
    exec_start = [ln for ln in text.splitlines() if ln.startswith("ExecStart=")]
    assert len(exec_start) == 1
    assert f"up -d --timeout {_script_ceiling_s()} daemon cloudflared logs" in exec_start[0]
