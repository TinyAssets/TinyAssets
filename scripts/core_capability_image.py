"""Exercise the PR production image with synthetic owner data and real role services."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import runpy
import secrets
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace


def model_boundary():
    """Only the model endpoint changes; credentials still cross the sealed snapshot."""
    from core_capability_fixture import MODEL_KEY

    from tinyassets.credential_vault import _write_exclusive_snapshot_file
    from tinyassets.providers import base as provider_base
    from tinyassets.providers import claude_provider

    original = provider_base.subprocess_env_for_provider

    def environment(name, **kwargs):
        env = original(name, **kwargs)
        if name == "claude-code":
            snapshot = Path(kwargs["credential_snapshot_dir"])
            for filename, value in (
                ("capability-key", MODEL_KEY),
                ("capability-url", "https://model.capability.test"),
            ):
                if not (snapshot / filename).exists():
                    _write_exclusive_snapshot_file(snapshot / filename, value.encode())
            env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
            env.update(
                ANTHROPIC_API_KEY=MODEL_KEY, ANTHROPIC_BASE_URL="https://model.capability.test"
            )
        return env

    provider_base.subprocess_env_for_provider = environment
    claude_provider.subprocess_env_for_provider = environment


async def refused_cli_keeps_its_reason():
    """#4580: an early CLI exit must retain stderr despite unread prompt bytes."""
    from tinyassets.auth.middleware import identity_context
    from tinyassets.auth.provider import Identity
    from tinyassets.credential_vault import (
        cleanup_llm_credential_snapshot,
        current_llm_subscription_custody,
        snapshot_llm_subscription_credential,
    )
    from tinyassets.exceptions import ProviderError
    from tinyassets.providers.base import ModelConfig, subprocess_env_for_provider
    from tinyassets.providers.claude_provider import ClaudeProvider
    from tinyassets.providers.owned_process import aspawn_owned
    from tinyassets.providers.provider_jail import provider_launch_scope
    from tinyassets.storage.provider_work_authority import SQLiteProviderWorkAuthorityStore

    center = Path("/data/u-alice")
    with identity_context(Identity("alice", "alice")):
        with SQLiteProviderWorkAuthorityStore(center.parent).connection() as conn:
            custody = current_llm_subscription_custody(
                conn,
                universe_dir=center,
                owner_user_id="alice",
                universe_id=center.name,
                service="claude",
            )
        snapshot = snapshot_llm_subscription_credential(universe_dir=center, custody=custody)
        try:
            env = subprocess_env_for_provider(
                "claude-code", universe_dir=center, credential_snapshot_dir=snapshot.directory
            )
            with provider_launch_scope(center, credential_dir=snapshot.directory):
                proc = await aspawn_owned(
                    ["claude", "--capability-invalid-option"],
                    env=env,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            try:
                await ClaudeProvider()._read_stream(
                    proc,
                    "unread prompt " * 5000,
                    ModelConfig(init_timeout_s=10, idle_timeout_s=10, absolute_cap_s=20),
                )
            except ProviderError as exc:
                if "unknown option" not in str(exc) or "capability-invalid-option" not in str(exc):
                    raise RuntimeError(
                        "early CLI exit lost its stderr reason: " + str(exc)
                    ) from exc
            else:
                raise RuntimeError("CLI accepted an invalid option")
        finally:
            cleanup_llm_credential_snapshot(snapshot)


def seed_serving():
    """Use the normal writers after the actual broker and launcher have booted."""
    from core_capability_fixture import MODEL_KEY, OWNER

    from tinyassets.api.http_connection import connect_http
    from tinyassets.auth.middleware import identity_context
    from tinyassets.auth.provider import Identity
    from tinyassets.credential_vault import write_credential_vault
    from tinyassets.daemon_server import ensure_universe_registered
    from tinyassets.onboarding.serving import ensure_founder_serving
    from tinyassets.storage.effector_consents import grant_consent

    root, center = Path("/data"), Path("/data/u-alice")
    with identity_context(Identity(OWNER, OWNER)):
        ensure_universe_registered(root, universe_id=center.name, universe_path=center)
        write_credential_vault(
            center,
            [dict(credential_type="llm_subscription", service="claude", oauth_token=MODEL_KEY)],
            owner_user_id=OWNER,
            universe_id=center.name,
        )
        serving = ensure_founder_serving(
            base_path=root,
            universe_dir=center,
            owner_user_id=OWNER,
            universe_id=center.name,
            service="claude",
        )
        if serving.get("status") != "serving":
            raise RuntimeError("synthetic owner serving admission failed: " + json.dumps(serving))
        connection = connect_http(
            universe_id=center.name,
            payload=dict(
                destination="capability-service",
                auth_scheme="bearer",
                secret="capability-service-token",
                allowed_endpoints=[
                    dict(host="service.capability.test", path_template="/probe", methods=["POST"])
                ],
            ),
        )
        if connection.get("status") != "provisioned":
            raise RuntimeError(
                "synthetic HTTP connection admission failed: " + json.dumps(connection)
            )
        grant_consent(
            center,
            sink="authenticated_external_call",
            destination="capability-service",
            granted_by=OWNER,
        )
    return connection


def seed_intake():
    from tinyassets.api.receiver_links import save_receiver
    from tinyassets.auth.middleware import identity_context
    from tinyassets.auth.provider import Identity
    from tinyassets.branches import BranchDefinition, EdgeDefinition, GraphNodeRef, NodeDefinition
    from tinyassets.daemon_server import (
        ensure_universe_registered,
        grant_universe_access,
        save_branch_definition,
    )

    root = Path("/data")
    with identity_context(Identity("bob", "bob", capabilities=["tinyassets.extensions.write"])):
        ensure_universe_registered(root, universe_id="u-bob", universe_path=root / "u-bob")
        grant_universe_access(root, universe_id="u-bob", actor_id="bob", permission="admin")
        branch = BranchDefinition(
            branch_def_id="capability-intake",
            name="Synthetic intake",
            author="bob",
            visibility="private",
            entry_point="entry",
            node_defs=[
                NodeDefinition(
                    node_id="entry",
                    display_name="Record synthetic report",
                    input_keys=["topic"],
                    output_keys=["recorded"],
                    source_code='def run(state):\n    return {"recorded": state["topic"]}',
                )
            ],
            graph_nodes=[GraphNodeRef(id="entry", node_def_id="entry")],
            edges=[EdgeDefinition("START", "entry"), EdgeDefinition("entry", "END")],
            state_schema=[dict(name=name, type="str") for name in ("topic", "recorded")],
        )
        save_branch_definition(root, branch_def=branch.to_dict())
        receiver = save_receiver(
            universe_id="u-bob",
            branch_def_id=branch.branch_def_id,
            node_id="entry",
            input_keys=["topic"],
            allowed_senders=["alice"],
        )
    os.environ["TINYASSETS_PATCH_INTAKE_RECEIVER_ID"] = receiver["receiver_id"]
    return receiver


async def exercise(connection, report):
    import ssl

    import httpx
    from core_capability_live import stream_transport
    from fastmcp import Client

    from tinyassets.core_capabilities import CAPABILITIES
    from tinyassets.onboarding.owner_sessions import COOKIE

    def passed(name, observation):
        report["capabilities"].append(
            dict(
                capability=name,
                status="passed",
                evidence=CAPABILITIES[name].evidence,
                observation=observation,
            )
        )

    context = ssl.create_default_context(cafile="/fixture/ca.pem")
    async with httpx.AsyncClient(verify=context, timeout=180, follow_redirects=True) as http:
        response = await http.get(
            "https://tinyassets.io/app/owner-sign-in?app=1",
            headers={"Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"},
        )
        response.raise_for_status()
        if not http.cookies.get(COOKIE):
            raise RuntimeError("owner sign-in did not create a protected session")
        response = await http.post(
            "https://tinyassets.io/app/token",
            json={"grant_type": "refresh_token"},
            headers={"Origin": "https://tinyassets.io"},
        )
        response.raise_for_status()
        token = response.json()["access_token"]
        profile = await http.get(
            "https://tinyassets.io/app/me", headers={"Authorization": "Bearer " + token}
        )
        profile.raise_for_status()
        passed("owner_sign_in", {"callback": "verified", "refresh": "verified"})

        transport_observations = {}
        async with Client(stream_transport(token, transport_observations), timeout=180) as client:

            async def call(name, **arguments):
                result = await client.call_tool(name, arguments)
                value = json.loads(result.data) if isinstance(result.data, str) else result.data
                if result.is_error or not isinstance(value, dict) or value.get("error"):
                    raise RuntimeError(name + " failed: " + json.dumps(value))
                return value

            status = await client.call_tool("read_graph", dict(target="status", graph_id="u-alice"))
            if status.is_error:
                raise RuntimeError("authenticated public MCP read failed")
            passed("public_mcp", {"authenticated_tool": "read_graph"})
            rail = await call("read_graph", target="pending_requests", graph_id="u-alice")
            (approval,) = [
                row
                for row in rail["pending"]
                if row.get("action", {}).get("type") == "grant_patch_intake"
            ]
            answered = await http.post(
                "https://tinyassets.io/app/approvals/answer",
                json=dict(universe_id="u-alice", request_id=approval["request_id"], values={}),
                headers={"Authorization": "Bearer " + token, "Origin": "https://tinyassets.io"},
            )
            answered.raise_for_status()
            decision = answered.json()
            if decision.get("status") != "answered" or decision.get("decision") != "allowed":
                raise RuntimeError("protected approval failed: " + json.dumps(decision))
            passed("approval", {"request_id": approval["request_id"], "decision": "allowed"})
            nonce = secrets.token_hex(12)
            armed = await http.post(
                "https://model.capability.test/scenario",
                json=dict(nonce=nonce, connection=connection["connection_id"]),
            )
            armed.raise_for_status()
            result = await client.call_tool(
                "converse",
                dict(
                    graph_id="u-alice",
                    message="Use write, read, edit, bash, image reading and the connected service. "
                    "Report the observed results. " + nonce + "\nSynthetic context: " + "x" * 63000,
                ),
            )
            document = json.loads(result.data) if isinstance(result.data, str) else result.data
            evidence = (await http.get("https://model.capability.test/evidence")).json()
            for name in evidence["scenario"]["verified"]:
                if name != "patch_request":
                    passed(name, {"model_received_result": True, "nonce": nonce})
            if evidence["errors"]:
                from tinyassets.core_capabilities import failure_code

                step = evidence["scenario"]["steps"][evidence["scenario"]["index"] - 1][0]
                report["capabilities"].append(
                    dict(
                        capability=step, status="failed", code=failure_code(str(evidence["errors"]))
                    )
                )
                raise RuntimeError(
                    "model-boundary verification failed: " + json.dumps(evidence["errors"])
                )
            if result.is_error or document.get("reply") != "CAPABILITY-REPLY-" + nonce:
                raise RuntimeError("real streamed reply failed: " + json.dumps(document))
            if evidence["scenario"].get("reply_deltas") != 2:
                raise RuntimeError("model SSE reply was not emitted in two real deltas")
            if not transport_observations.get("sse_response"):
                raise RuntimeError("public MCP streamed response was not observed")
            await refused_cli_keeps_its_reason()
            passed("chat_stream", {"reply": document["reply"], "model_sse_deltas": 2})
            delivery_id = evidence["scenario"]["delivery_id"]
            delivery = await call(
                "read_graph", target="delivery", graph_id="u-alice", query=delivery_id
            )
            if delivery.get("delivery_id") != delivery_id or (
                float(delivery.get("accepted_at", 0)) < time.time() - 180
            ):
                raise RuntimeError("patch request has no fresh durable delivery")
            passed("patch_request", {"delivery_id": delivery_id, "nonce": nonce})

            branch = await call(
                "write_graph",
                target="branch",
                operation="create",
                graph_id="u-alice",
                payload_json=json.dumps(
                    dict(
                        name="Synthetic capability " + nonce,
                        visibility="private",
                        entry_point="code",
                        state_schema=[
                            dict(name="nonce", type="str"),
                            dict(name="echo", type="str"),
                        ],
                        node_defs=[
                            dict(
                                node_id="code",
                                display_name="Echo in owner cell",
                                input_keys=["nonce"],
                                output_keys=["echo"],
                                source_code='def run(state):\n    return {"echo": state["nonce"]}',
                            )
                        ],
                        edges=[{"from": "code", "to": "END"}],
                    )
                ),
            )

            async def terminal(run_id):
                for _ in range(180):
                    value = await call(
                        "read_graph", target="run", graph_id="u-alice", run_id=run_id
                    )
                    record = value.get("run", value)
                    if record.get("status") in ("completed", "failed", "cancelled"):
                        if record["status"] != "completed":
                            raise RuntimeError("owner run failed: " + json.dumps(record))
                        output = await call(
                            "read_graph",
                            target="run_output",
                            graph_id="u-alice",
                            run_id=run_id,
                            field_name="echo",
                        )
                        if nonce not in json.dumps(output):
                            raise RuntimeError("owner run lost its nonce: " + json.dumps(output))
                        return record
                    await asyncio.sleep(0.5)
                raise TimeoutError("owner run did not finish: " + run_id)

            started = await call(
                "run_graph",
                graph_id="u-alice",
                branch_def_id=branch["branch_def_id"],
                inputs_json=json.dumps({"nonce": nonce}),
            )
            await terminal(started["run_id"])
            passed("background_run", {"run_id": started["run_id"], "echo": nonce})
            wake = await call(
                "write_graph",
                target="automation",
                operation="create",
                graph_id="u-alice",
                payload_json=json.dumps(
                    dict(
                        name="Synthetic wake " + nonce,
                        branch_def_id=branch["branch_def_id"],
                        delay_seconds=1,
                        inputs={"nonce": nonce},
                    )
                ),
            )
            wake_id = wake["automation"]["automation_id"]
            for _ in range(180):
                listed = await call(
                    "read_graph", target="automation", graph_id="u-alice", automation_id=wake_id
                )
                current = listed.get("automation", listed)
                if current.get("last_run_id"):
                    await terminal(current["last_run_id"])
                    passed(
                        "scheduled_wake",
                        {"automation_id": wake_id, "run_id": current["last_run_id"], "echo": nonce},
                    )
                    break
                await asyncio.sleep(0.5)
            else:
                raise TimeoutError("daemon did not consume scheduled wake: " + wake_id)


def inside():
    from tinyassets.core_capabilities import failure_code, validate_report

    report = dict(capabilities=[], image=os.environ["TINYASSETS_IMAGE"])
    try:
        sys.argv = sys.argv[:1]
        sys.path.remove("/checks")
        launch = runpy.run_path("/usr/local/libexec/ta-launch.py")
        launch["boot"](launch)
        sys.path.insert(0, "/checks")
        model_boundary()
        connection = seed_serving()
        seed_intake()
        from tinyassets import universe_server

        threading.Thread(target=universe_server.main, daemon=True).start()
        import urllib.error
        import urllib.request

        for _ in range(120):
            try:
                urllib.request.urlopen("http://127.0.0.1:8001/mcp/pulse", timeout=1)
                break
            except urllib.error.HTTPError as exc:
                if exc.code == 401:
                    break
                raise
            except OSError:
                time.sleep(0.5)
        else:
            raise TimeoutError("production server never started")
        # Real process exits race procfs accounting (#4576). Keep unrelated
        # children churning throughout the actual provider/tool execution.
        stop = threading.Event()
        churn_errors = []

        def churn():
            try:
                while not stop.is_set():
                    children = [subprocess.Popen(["/bin/true"]) for _ in range(24)]
                    for child in children:
                        child.wait()
            except Exception as exc:
                churn_errors.append(type(exc).__name__)

        worker = threading.Thread(target=churn)
        worker.start()
        try:
            asyncio.run(exercise(connection, report))
        finally:
            stop.set()
            worker.join(10)
        if worker.is_alive() or churn_errors:
            raise RuntimeError("process churn did not complete: " + str(churn_errors))
        from tinyassets.capability_health import RATES
        from tinyassets.core_capabilities import CAPABILITIES

        report["runtime_rates"] = RATES.snapshot()
        for name in CAPABILITIES:
            if not report["runtime_rates"]["capability_outcomes"].get(name):
                raise RuntimeError("daemon did not observe capability: " + name)
    except Exception as exc:
        import traceback

        traceback.print_exc()
        report["error"] = dict(code=failure_code(exc), detail=str(exc))
    report["failures"] = validate_report(report)
    print("CORE CAPABILITY REPORT " + json.dumps(report), flush=True)
    return int(bool(report["failures"] or report.get("error")))


def image_check(args):
    import core_capability_fixture as fixture
    import role_image_oracle as image

    prefix = "ta-core-" + secrets.token_hex(5)
    options = SimpleNamespace(
        image=args.image, prefix=prefix, volume=prefix + "-data", backup_archive=None
    )
    source = Path(__file__).resolve().parent
    with tempfile.TemporaryDirectory(prefix="ta-core-") as temporary:
        directory = Path(temporary)
        fixture.prepare(directory)
        try:
            image.stage_migrate(options)
            # The migration fixture deliberately contains malformed vault bytes.
            # Replace only those synthetic inputs before starting the application.
            seed = image._posture(
                prefix + "-seed",
                args.image,
                options.volume,
                user="0",
                caps=image.MIGRATION_CAPS,
                entrypoint="/opt/venv/bin/python",
                extra=["--rm", "--network", "none"],
            )
            subprocess.run(
                seed
                + [
                    "-c",
                    "from pathlib import Path; "
                    '[(p.write_text(\'{"schema_version":1,"credentials":[]}\')) '
                    "for p in Path('/data').glob('*/.credential-vault.json')]; "
                    "import os, pwd; uid=pwd.getpwnam('tinyassets').pw_uid; "
                    "[(p.write_text('{}'), os.chown(p,uid,uid)) for i in range(300) "
                    "for p in [Path('/data/u-alice') / ('.capability-sidecar-'+str(i))]]",
                ],
                check=True,
            )
            setup = image.ISOLATED_METADATA_SETUP + image.ISOLATED_METADATA_SETUP.replace(
                "lo:oracle", "lo:service"
            ).replace(image.METADATA_ADDRESS, fixture.ADDRESS)
            service = (
                setup + "\nimport sys; sys.path.insert(0, '/checks')\n"
                "from pathlib import Path\nfrom core_capability_fixture import serve\n"
                "serve(Path('/fixture'))\n"
                + image.METADATA_SERVER.format(body=image.METADATA_INSTANCE_ID)
            )
            image.docker(
                "run",
                "-d",
                "--name",
                prefix + "-upstream",
                "--network",
                "none",
                "--user",
                "0",
                "--cap-drop",
                "ALL",
                "--cap-add",
                "NET_ADMIN",
                "--cap-add",
                "NET_BIND_SERVICE",
                "-v",
                str(source) + ":/checks:ro",
                "-v",
                temporary + ":/fixture:ro",
                "--entrypoint",
                "/opt/venv/bin/python",
                args.image,
                "-I",
                "-B",
                "-c",
                service,
            )
            env = dict(
                TINYASSETS_IMAGE=args.image,
                UNIVERSE_SERVER_AUTH="workos",
                WORKOS_AUTHKIT_DOMAIN="auth.capability.test",
                WORKOS_MCP_RESOURCE=fixture.RESOURCE,
                TINYASSETS_ONBOARDING_APP_CLIENT_ID="synthetic-client",
                TINYASSETS_SESSION_SEAL_KEY=secrets.token_hex(32),
                TINYASSETS_ENGINE_MCP_TOOLS="1",
                TINYASSETS_OUTBOUND_HTTP_CONNECTIONS_ENABLED="1",
                TINYASSETS_ASSIGNED_QUEUE_CONSUMER="1",
                SSL_CERT_FILE="/fixture/ca.pem",
                REQUESTS_CA_BUNDLE="/fixture/ca.pem",
            )
            extra = [
                "--rm",
                "--network",
                "container:" + prefix + "-upstream",
                "-v",
                str(source) + ":/checks:ro",
                "-v",
                temporary + "/ca.pem:/fixture/ca.pem:ro",
                "-v",
                temporary + "/hosts:/etc/hosts:ro",
                "-v",
                temporary + "/ca.pem:/etc/ssl/certs/ca-certificates.crt:ro",
            ]
            for key, value in env.items():
                extra += ["-e", key + "=" + value]
            command = image._posture(
                prefix + "-app",
                args.image,
                options.volume,
                user=image.COMPOSE_USER,
                caps=image.COMPOSE_CAPS,
                entrypoint="/opt/venv/bin/python",
                extra=extra,
            )
            command[command.index("apparmor=unconfined")] = "apparmor=" + args.apparmor
            result = subprocess.run(
                command + ["-I", "-B", "/checks/core_capability_image.py", "--inside"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=600,
            )
            rows = [
                line.removeprefix("CORE CAPABILITY REPORT ")
                for line in result.stdout.splitlines()
                if line.startswith("CORE CAPABILITY REPORT ")
            ]
            if not rows:
                upstream = image.docker("logs", prefix + "-upstream", check=False)
                raise RuntimeError(
                    "image check produced no report: " + result.stderr[-4000:] + str(upstream)
                )
            report = json.loads(rows[-1])
            if result.returncode:
                print(result.stderr[-6000:], file=sys.stderr)
            Path(args.report).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(report, indent=2))
            return result.returncode
        finally:
            for suffix in ("app", "upstream", "seed"):
                image.docker("rm", "-f", prefix + "-" + suffix, check=False)
            image.docker("volume", "rm", "-f", options.volume, check=False)


def main():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.path.insert(
        0,
        "/app" if os.environ.get("TINYASSETS_IMAGE") else str(Path(__file__).resolve().parents[1]),
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inside", action="store_true")
    parser.add_argument("--image")
    parser.add_argument("--apparmor", default="unconfined")
    parser.add_argument("--report", default="core-capabilities.json")
    args = parser.parse_args()
    if args.inside:
        return inside()
    if not args.image:
        parser.error("--image is required")
    return image_check(args)


if __name__ == "__main__":
    raise SystemExit(main())
