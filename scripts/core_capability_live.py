"""Dedicated-owner observations; a failed platform row fails the step (deploy rolls back)."""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import shlex
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tinyassets.core_capabilities import CAPABILITIES, failure_code, validate_report  # noqa: E402

ORIGIN = "https://tinyassets.io"


class ProbeFailure(RuntimeError):
    pass


class DaemonRestart(RuntimeError):
    pass


def stream_transport(token, observations):
    """Observe the actual MCP response; never replace the transport or its bytes."""
    import httpx
    from fastmcp.client.transports import StreamableHttpTransport

    async def response_headers(response):
        request = response.request
        if request.method != "POST" or not request.content:
            return
        document = json.loads(request.content)
        if document.get("params", {}).get("name") == "converse":
            response.raise_for_status()
            if "text/event-stream" not in response.headers.get("content-type", ""):
                raise ProbeFailure("chat response did not use the live SSE transport")
            observations["sse_response"] = True

    def factory(**kwargs):
        return httpx.AsyncClient(**kwargs, event_hooks={"response": [response_headers]})

    return StreamableHttpTransport(ORIGIN + "/mcp", auth=token, httpx_client_factory=factory)


def passed(report, capability, observation):
    report["capabilities"].append(
        dict(
            capability=capability,
            status="passed",
            evidence=CAPABILITIES[capability].evidence,
            observation=observation,
        )
    )


async def _sign_in_stall(page, step, exc, email=""):
    """Where sign-in stopped: the step, the page host and title, and the page's
    visible text. innerText never includes a field value, and the canary email
    is masked, so no secret or address can reach a log."""
    alert = ""
    try:
        alert = " ".join((await page.locator("body").inner_text(timeout=5000)).split())
        if email:
            alert = alert.replace(email, "<email>")
        alert = re.sub(r"[^\s@]+@[^\s@]+", "<email>", alert)
        alert = alert[:300]
    except Exception:  # noqa: BLE001 - diagnostics only
        pass
    try:
        title = (await page.title())[:80]
    except Exception:  # noqa: BLE001 - diagnostics only
        title = ""
    return (
        f"sign-in step {step} failed ({type(exc).__name__}) on "
        f"{urlsplit(page.url).hostname or '?'} title={title!r} text={alert!r}"
    )


async def login(config, http):
    """Normal interactive owner login, including the protected approval cookie."""
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            context = await browser.new_context()
            page = await context.new_page()
            continue_button = re.compile("^continue( with email)?$", re.I)
            sign_in_button = re.compile("^(sign in|continue)$", re.I)
            steps = (
                ("open", lambda: page.goto(ORIGIN + "/app/owner-sign-in?app=1")),
                ("email", lambda: page.locator("input[type=email]").fill(config["email"])),
                ("continue", lambda: page.get_by_role("button", name=continue_button).click()),
                ("password", lambda: page.locator("input[type=password]").fill(config["password"])),
                ("sign_in", lambda: page.get_by_role("button", name=sign_in_button).click()),
                ("return", lambda: page.wait_for_url(ORIGIN + "/app**", timeout=60000)),
            )
            for step, action in steps:
                try:
                    await action()
                except Exception as exc:  # noqa: BLE001 - named, never a secret value
                    raise ProbeFailure(
                        await _sign_in_stall(page, step, exc, config["email"])
                    ) from exc
            for cookie in await context.cookies(ORIGIN):
                http.cookies.set(
                    cookie["name"], cookie["value"], domain=cookie["domain"], path=cookie["path"]
                )
            if not http.cookies.get("__Host-ta-owner"):
                raise ProbeFailure("owner callback did not issue its protected session")
        finally:
            await browser.close()
    response = await http.post(ORIGIN + "/app/token", json={"grant_type": "refresh_token"})
    response.raise_for_status()
    token = response.json()["access_token"]
    http.headers["Authorization"] = "Bearer " + token
    me = await http.get(ORIGIN + "/app/me")
    me.raise_for_status()
    home = me.json().get("universe_id", "")
    # The canary repairs whatever account it signs in to, so it must be the
    # dedicated one: secrets naming any other owner stop here, before a write.
    if not home or home != config.get("universe_id"):
        raise ProbeFailure("signed-in home differs from dedicated canary home")
    return token


async def health(http, report):
    bearer = os.environ.get("TINYASSETS_WIKI_CANARY_TOKEN", "")
    if not bearer:
        raise ProbeFailure("health canary credential is not configured")
    response = await http.get(ORIGIN + "/mcp/pulse", headers={"Authorization": "Bearer " + bearer})
    response.raise_for_status()
    value = response.json()
    sha = value.get("git_sha", "")
    if not re.fullmatch("[a-f0-9]{40}", sha):
        raise ProbeFailure("deployed SHA receipt is missing")
    report["deployed_sha"] = sha
    rates = value.get("capability_health")
    if not isinstance(rates, dict) or not isinstance(rates.get("alarms"), list):
        raise ProbeFailure("capability failure rates are unavailable")
    report["rate_alarms"] = rates["alarms"]
    report["rate_window"] = {
        key: rates[key] for key in ("boot_id", "observed_seconds", "window_seconds", "outcomes")
    }
    return rates


async def direct_tools(client, config, report, nonce, scratch):
    """Deterministic tool cell operations when the transport grants the four tools."""
    path = "/u/" + scratch + ".txt"

    async def tool(name, **arguments):
        value = await client.call_tool(name, arguments)
        if value.is_error:
            raise ProbeFailure(name + " tool cell refused")
        return value

    await tool("write", path=path, content="before-" + nonce)
    read = await tool("read", path=path)
    if "before-" + nonce not in str(read.content):
        raise ProbeFailure("direct read lost its nonce")
    await tool("edit", path=path, old_text="before-", new_text="after-")
    result = await tool("bash", command="cat " + shlex.quote(path))
    if "after-" + nonce not in str(result.content):
        raise ProbeFailure("direct edit/bash lost its nonce")
    for name in ("write", "read", "edit", "bash"):
        report.setdefault("direct_observations", {})[name] = {"nonce": nonce}
    from PIL import Image

    output = io.BytesIO()
    Image.new("RGB", (16, 16), "red").save(output, format="PNG")
    encoded = base64.b64encode(output.getvalue()).decode()
    png = "/u/" + scratch + ".png"
    await tool(
        "bash", command="printf %s " + shlex.quote(encoded) + " | base64 -d > " + shlex.quote(png)
    )
    result = await tool("read", path=png)
    if not any(getattr(block, "type", "") == "image" for block in result.content):
        raise ProbeFailure("direct image read returned no image content")
    report["direct_observations"]["read_image"] = {"image_content": True}
    request = json.dumps({"request": {"path": config["service_path"], "body": {"nonce": nonce}}})
    result = await tool(
        "bash",
        command="ta connection:" + config["connection_id"] + ":POST --json " + shlex.quote(request),
    )
    expected = hmac.new(
        config["service_hmac_key"].encode(), nonce.encode(), hashlib.sha256
    ).hexdigest()
    if expected not in str(result.content):
        raise ProbeFailure("direct connection returned no service HMAC")
    for name in ("ta", "connected_service"):
        report["direct_observations"][name] = {"service_hmac_verified": True}
    for name, observation in report["direct_observations"].items():
        passed(report, name, observation)


async def exercise(config, http, token, report):
    from fastmcp import Client
    from PIL import Image, ImageDraw, ImageFont

    universe = config["universe_id"]
    nonce = secrets.token_hex(12)
    scratch = "capability-" + nonce
    transport_observations = {}
    async with Client(stream_transport(token, transport_observations), timeout=180) as client:

        async def call(name, **arguments):
            result = await client.call_tool(name, arguments)
            value = json.loads(result.data) if isinstance(result.data, str) else result.data
            if result.is_error or not isinstance(value, dict) or value.get("error"):
                # Error codes only: a transport exception or body must not publish credentials.
                code = (
                    value.get("error", "tool_failed") if isinstance(value, dict) else "tool_failed"
                )
                raise ProbeFailure(name + " refused: " + failure_code(code))
            return value

        # Imported here: core_capability_image.py mounts this file without the package.
        from scripts.core_canary_provision import ProvisionError, provision

        report["active_capability"] = "canary_setup"
        try:
            await provision(config, http, ORIGIN, call)
        except (ProvisionError, ProbeFailure) as exc:
            report["provision_error"] = str(exc)
            raise
        for code in config.get("provision_warnings", ()):
            report["warnings"].append(dict(capability="canary_setup", code=code))
        report["active_capability"] = "public_mcp"
        await call("read_graph", target="status", graph_id=universe)
        passed(report, "public_mcp", {"url": ORIGIN + "/mcp", "authenticated_tool": "read_graph"})

        # A plain protected approval (no action): it grants nothing, so the
        # canary never consents to the deployment's real patch intake.
        report["active_capability"] = "approval"
        await approval(call, http, report, universe, nonce)

        async def model_probe():
            if report.get("direct_observations"):
                await call("converse", graph_id=universe, message="Reply with this nonce: " + nonce)
                if not transport_observations.get("sse_response"):
                    raise ProbeFailure("streamed chat evidence is missing")
                passed(report, "chat_stream", {"sse_reply_received": True, "nonce": nonce})
                return
            expected = secrets.token_hex(4).upper()
            picture = Image.new("RGB", (256, 64), "white")
            ImageDraw.Draw(picture).text(
                (8, 8), expected, fill="black", font=ImageFont.load_default(size=32)
            )
            output = io.BytesIO()
            picture.save(output, format="PNG")
            encoded = base64.b64encode(output.getvalue()).decode()
            connection_request = json.dumps(
                {"request": {"path": config["service_path"], "body": {"nonce": nonce}}}
            )
            prompt = (
                "Run this synthetic capability check only in this canary home. Do each operation "
                "with its named tool; stop and report a failure if one fails, without substituting "
                'another tool. Use write to put "before-' + nonce + '" in /u/' + scratch + ".txt. "
                "Use read on that file and retain its returned text. Use edit to change before- "
                "to after-. Use bash to cat the file and retain stdout, then use bash to decode "
                "this PNG into /u/"
                + scratch
                + ".png: "
                + encoded
                + ". Use read to view the PNG and name "
                "the eight characters printed in it. Through bash run ta connection:"
                + config["connection_id"]
                + ":POST --json "
                + shlex.quote(connection_request)
                + ". Reply ONLY with JSON keys read, bash, image_text, service_hmac "
                "(from the HTTP response's hmac field). "
                "Never infer a success or invent an output."
            )
            report["active_capability"] = "chat_stream"
            reply = await call("converse", graph_id=universe, message=prompt)
            text = str(reply.get("reply", "")).strip()
            if text.startswith("```"):
                text = "\n".join(text.splitlines()[1:-1])
            observed = json.loads(text)
            checks = {
                "read": observed.get("read") == "before-" + nonce,
                "bash": str(observed.get("bash", "")).strip() == "after-" + nonce,
                "read_image": observed.get("image_text", "").strip().upper() == expected,
                "connected_service": hmac.compare_digest(
                    str(observed.get("service_hmac", "")),
                    hmac.new(
                        config["service_hmac_key"].encode(), nonce.encode(), hashlib.sha256
                    ).hexdigest(),
                ),
            }
            stored = await call(
                "read_graph", target="universe_file", graph_id=universe, query=scratch + ".txt"
            )
            checks["write"] = checks["edit"] = stored.get("text", "").strip() == "after-" + nonce
            checks["ta"] = checks["connected_service"]
            for capability, ok in checks.items():
                if ok:
                    passed(report, capability, {"nonce": nonce, "observed_output": True})
                else:
                    report["capabilities"].append(
                        dict(
                            capability=capability,
                            status="failed",
                            code="probe_failed",
                            observation={"nonce": nonce},
                        )
                    )
            if not transport_observations.get("sse_response"):
                raise ProbeFailure("streamed chat evidence is missing")
            passed(report, "chat_stream", {"sse_reply_received": True, "nonce": nonce})

        # Public /mcp has no patch_request door, and the only receiver is the
        # deployment's real intake, so the canary never files one. The row
        # stays advisory (model_dependent below) until a record-only door exists.
        report["capabilities"].append(
            dict(capability="patch_request", status="failed", code="not_exercised")
        )

        # The founder installs this ordinary, private echo branch once. It is
        # read back before execution; the canary never trusts an arbitrary preset result.
        report["active_capability"] = "background_run"
        branch = await call(
            "read_graph", target="branch", graph_id=universe, branch_id=config["branch_id"]
        )
        if "source_code" not in json.dumps(branch):
            raise ProbeFailure("canary branch definition is unavailable")

        async def terminal(run_id):
            for _ in range(120):
                value = await call("read_graph", target="run", graph_id=universe, run_id=run_id)
                record = value.get("run", value)
                if record.get("status") in ("failed", "cancelled"):
                    raise ProbeFailure("background execution " + record["status"])
                if record.get("status") == "completed":
                    result = await call(
                        "read_graph",
                        target="run_output",
                        graph_id=universe,
                        run_id=run_id,
                        field_name="echo",
                    )
                    if nonce not in json.dumps(result):
                        raise ProbeFailure("background execution lost its nonce")
                    return
                await asyncio.sleep(0.5)
            raise ProbeFailure("background execution deadline exceeded")

        run = await call(
            "run_graph",
            graph_id=universe,
            branch_def_id=config["branch_id"],
            inputs_json=json.dumps({"nonce": nonce}),
        )
        await terminal(run["run_id"])
        passed(report, "background_run", {"run_id": run["run_id"], "nonce": nonce})
        report["active_capability"] = "scheduled_wake"
        wake = await call(
            "write_graph",
            target="automation",
            operation="create",
            graph_id=universe,
            payload_json=json.dumps(
                dict(
                    name="Synthetic canary " + nonce,
                    delay_seconds=1,
                    branch_def_id=config["branch_id"],
                    inputs={"nonce": nonce},
                )
            ),
        )
        wake_id = wake["automation"]["automation_id"]
        for _ in range(120):
            value = await call(
                "read_graph", target="automation", graph_id=universe, automation_id=wake_id
            )
            current = value.get("automation", value)
            if current.get("last_run_id"):
                await terminal(current["last_run_id"])
                passed(
                    report,
                    "scheduled_wake",
                    {"automation_id": wake_id, "run_id": current["last_run_id"]},
                )
                await call(
                    "write_graph",
                    target="automation",
                    operation="delete",
                    graph_id=universe,
                    automation_id=wake_id,
                )
                break
            await asyncio.sleep(0.5)
        else:
            raise ProbeFailure("daemon did not consume scheduled wake")
        # The public connector exposes graph APIs, not the served engine's four
        # tool handles. Do not add an authority bypass for a probe. Exercise
        # those handles directly when the authenticated transport exposes them.
        names = {tool.name for tool in await client.list_tools()}
        if {"read", "write", "edit", "bash"} <= names:
            await direct_tools(client, config, report, nonce, scratch)
        # Model output is an advisory observation even when it claims a tool
        # failed. Keep its missing/invalid evidence out of rollback selection.
        report["model_dependent"] = ["chat_stream", "patch_request"]
        if not report.get("direct_observations"):
            report["model_dependent"] += [
                "read", "write", "edit", "bash", "read_image", "ta", "connected_service"
            ]
        try:
            await model_probe()
        except Exception as exc:
            report.setdefault("warnings", []).append(
                dict(
                    capability="chat_stream",
                    code="model_probe_unavailable",
                    detail=type(exc).__name__,
                )
            )


async def approval(call, http, report, universe, nonce):
    ask = await call(
        "write_graph",
        target="connection",
        operation="request_from_user",
        graph_id=universe,
        payload_json=json.dumps(
            dict(
                kind="Approval",
                title="Synthetic canary " + nonce,
                body="Synthetic canary check; approving grants nothing.",
                fields=[],
            )
        ),
    )
    request_id = ask.get("request_id") or ask.get("request", {}).get("request_id")
    if not request_id:
        raise ProbeFailure("approval request was not created")
    answer = await http.post(
        ORIGIN + "/app/approvals/answer",
        json=dict(universe_id=universe, request_id=request_id, values={}),
    )
    answer.raise_for_status()
    decision = answer.json()
    if decision.get("status") != "answered" or decision.get("decision") != "allowed":
        raise ProbeFailure("protected approval was not answered")
    passed(report, "approval", {"request_id": request_id, "decision": "allowed"})


async def run(report):
    import httpx

    from scripts.core_canary_provision import credentials

    config = credentials()
    if config is None:
        report["configuration_missing"] = True
        return
    async with httpx.AsyncClient(
        timeout=180,
        follow_redirects=True,
        headers={"Origin": ORIGIN, "User-Agent": "tinyassets-core-canary/1.0"},
    ) as http:
        try:
            before = await health(http, report)
        except Exception as exc:
            report["health_failure"] = dict(
                capability="public_mcp",
                code=failure_code(exc),
                detail=str(exc) if isinstance(exc, ProbeFailure) else type(exc).__name__,
            )
        report["active_capability"] = "owner_sign_in"
        try:
            token = await login(config, http)
            passed(report, "owner_sign_in", {"callback": "verified", "home": "dedicated canary"})
            await exercise(config, http, token, report)
        except Exception:
            # A disconnect during a deployment is not a capability outage.
            if not report.get("health_failure"):
                try:
                    after = await health(http, report)
                except Exception:
                    raise DaemonRestart() from None
                if before["boot_id"] != after["boot_id"]:
                    raise DaemonRestart() from None
            raise
        report["active_capability"] = "public_mcp"
        try:
            after = await health(http, report)
        except Exception:
            # The final pulse can hit the restart window just like a tool call.
            raise DaemonRestart() from None
        if report.get("health_failure"):
            return
        if before["boot_id"] != after["boot_id"]:
            raise DaemonRestart()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", default="core-capabilities-live.json")
    args = parser.parse_args(argv)
    report = asyncio.run(collect())
    Path(args.report).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    # A missing dedicated owner means the probe never ran. That is a loud
    # warning, not a rollback: deploy_fail_safe already refused a data-moving
    # deploy before the swap, and an ordinary deploy continues. Once the canary
    # is provisioned, a failed platform row or a crashed probe fails the step,
    # and deploy-prod rolls back to the previous image: this canary is the
    # post-deploy acceptance (founder ruling, #4657). Model-dependent rows stay
    # warnings: a provider error does not say the previous image would fare
    # better. The scheduled uptime run keeps alerting only (continue-on-error).
    for row in report.get("warnings", []) + report.get("failures", []):
        print("::warning::core canary " + row["capability"] + ": " + row["code"])
    if report.get("probe_error"):
        print("::error::core canary probe crashed: " + report["probe_error"])
    return 1 if report.get("failures") or report.get("probe_error") else 0


async def collect():
    for attempt in range(3):
        report = {
            "capabilities": [],
            "started_at": time.time(),
            "deployed_sha": "",
            "warnings": [],
            "failures": [],
            "attempt": attempt + 1,
        }
        try:
            await run(report)
        except DaemonRestart:
            if attempt < 2:
                await asyncio.sleep(5)
                continue
            report["warnings"].append(dict(capability="public_mcp", code="deployment_in_progress"))
            return report
        except Exception as exc:
            report["probe_error"] = type(exc).__name__
            if isinstance(exc, ProbeFailure):
                report["probe_error"] += ": " + str(exc)
        if report.get("configuration_missing"):
            # Not an observation and not a fabricated capability failure.
            # deploy_fail_safe refuses the data-moving cases before the swap.
            report["warnings"].append(dict(capability="canary", code="canary_not_provisioned"))
            return report
        for failure in validate_report(report):
            destination = (
                "warnings"
                if failure["capability"] in report.get("model_dependent", [])
                else "failures"
            )
            report[destination].append(failure)
        if report.get("health_failure"):
            report["warnings"].append(report["health_failure"])
        report["warnings"].extend(
            dict(
                capability=row["capability"],
                code="rate_alarm",
                detail=f"{row['failures']}/{row['samples']} failures across "
                f"{row.get('failing_owners', 0)} owners",
            )
            for row in report.get("rate_alarms", [])
            if row.get("failing_owners", 0) >= 2
        )
        return report


if __name__ == "__main__":
    raise SystemExit(main())
