"""Dedicated-owner observations: alert-only; rollback requires paired image proof."""

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


async def login(config, http):
    """Normal interactive owner login, including the protected approval cookie."""
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            context = await browser.new_context()
            page = await context.new_page()
            await page.goto(ORIGIN + "/app/owner-sign-in?app=1")
            await page.locator("input[type=email]").fill(config["email"])
            await page.get_by_role("button", name=re.compile("^continue$", re.I)).click()
            await page.locator("input[type=password]").fill(config["password"])
            await page.get_by_role("button", name=re.compile("^(sign in|continue)$", re.I)).click()
            await page.wait_for_url(ORIGIN + "/app**", timeout=60000)
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
    if me.json().get("universe_id") != config["universe_id"]:
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

        report["active_capability"] = "public_mcp"
        await call("read_graph", target="status", graph_id=universe)
        passed(report, "public_mcp", {"url": ORIGIN + "/mcp", "authenticated_tool": "read_graph"})

        # Repeatable protected approval on the already configured synthetic intake.
        report["active_capability"] = "approval"
        ask = await call(
            "write_graph",
            target="connection",
            operation="request_from_user",
            graph_id=universe,
            payload_json=json.dumps(
                dict(
                    kind="Approval",
                    title="Synthetic canary " + nonce,
                    body="Approve the synthetic canary intake only.",
                    fields=[],
                    action=dict(
                        type="grant_patch_intake", receiver_id=config["intake_receiver_id"]
                    ),
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

        report["active_capability"] = "patch_request"
        delivery_result = await call(
            "write_graph",
            target="patch_request",
            operation="send",
            graph_id=universe,
            payload_json=json.dumps(
                {"title": "Synthetic canary " + nonce, "details": "Synthetic verification " + nonce}
            ),
        )
        delivery_id = delivery_result.get("delivery_id", "")
        if not delivery_id:
            raise ProbeFailure("patch request returned no delivery id")
        delivery = await call("read_graph", target="delivery", graph_id=universe, query=delivery_id)
        if (
            delivery.get("delivery_id") != delivery_id
            or delivery.get("receiver_id") != config["intake_receiver_id"]
            or float(delivery.get("accepted_at", 0)) < time.time() - 180
        ):
            raise ProbeFailure("patch request has no fresh durable delivery to the canary intake")
        passed(report, "patch_request", {"delivery_id": delivery_id})

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
        report["model_dependent"] = ["chat_stream"]
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


async def run(report):
    import httpx

    raw = os.environ.get("TINYASSETS_CORE_CANARY_CONFIG", "")
    if not raw:
        report["configuration_missing"] = True
        return
    try:
        config = json.loads(raw)
        required = (
            "email",
            "password",
            "universe_id",
            "connection_id",
            "service_path",
            "service_hmac_key",
            "intake_receiver_id",
            "branch_id",
        )
        if any(not isinstance(config.get(key), str) or not config[key] for key in required):
            raise ValueError("incomplete")
    except (ValueError, AttributeError):
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", default="core-capabilities-live.json")
    args = parser.parse_args()
    report = asyncio.run(collect())
    Path(args.report).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    for row in report.get("warnings", []) + report.get("failures", []):
        print("::warning::core canary " + row["capability"] + ": " + row["code"])
    # Observations, model/provider/configuration errors and rate alarms cannot
    # establish that the previous image would fare better.
    return 0


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
        if report.get("configuration_missing"):
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
