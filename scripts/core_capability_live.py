"""Dedicated-owner production canary. Missing setup and missing evidence are red."""

from __future__ import annotations

import argparse
import asyncio
import base64
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
            await page.get_by_role("button", name=re.compile("continue", re.I)).click()
            await page.locator("input[type=password]").fill(config["password"])
            await page.get_by_role("button", name=re.compile("sign in|continue", re.I)).click()
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


async def exercise(config, http, token, report):
    from fastmcp import Client
    from PIL import Image, ImageDraw

    universe = config["universe_id"]
    nonce = secrets.token_hex(12)
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

        colors = ("red", "green", "blue", "yellow")
        expected = secrets.choice(colors)
        picture = Image.new("RGB", (96, 64), "white")
        ImageDraw.Draw(picture).rectangle((12, 12, 83, 51), fill=expected)
        output = io.BytesIO()
        picture.save(output, format="PNG")
        encoded = base64.b64encode(output.getvalue()).decode()
        connection_request = json.dumps(
            {"request": {"path": config["service_path"], "body": {"nonce": nonce}}}
        )
        prompt = (
            "Run this synthetic capability check only in this canary home. Do each operation "
            "with its named tool; stop and report a failure if one fails, without substituting "
            'another tool. Use write to put "before-' + nonce + '" in /u/capability.txt. '
            "Use read on that file and retain its returned text. Use edit to change before- "
            "to after-. Use bash to cat the file and retain stdout, then use bash to decode "
            "this PNG into /u/capability.png: " + encoded + ". Use read to view the PNG and name "
            "the rectangle color. Through bash run ta connection:"
            + config["connection_id"]
            + ":POST --json "
            + shlex.quote(connection_request)
            + ". Then use ta write_graph "
            'target=patch_request operation=send with title "Synthetic canary ' + nonce + '" '
            'and details "Synthetic verification ' + nonce + '". Reply ONLY with JSON keys '
            "read, bash, image_color, service_nonce (from the HTTP response), delivery_id. "
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
            "read_image": observed.get("image_color", "").lower() == expected,
            "connected_service": observed.get("service_nonce") == nonce,
        }
        stored = await call(
            "read_graph", target="universe_file", graph_id=universe, query="capability.txt"
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
        delivery_id = observed.get("delivery_id", "")
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
                return
            await asyncio.sleep(0.5)
        raise ProbeFailure("daemon did not consume scheduled wake")


async def run(report):
    import httpx

    async with httpx.AsyncClient(
        timeout=180,
        follow_redirects=True,
        headers={"Origin": ORIGIN, "User-Agent": "tinyassets-core-canary/1.0"},
    ) as http:
        try:
            await health(http, report)
        except Exception as exc:
            report["health_failure"] = dict(
                capability="public_mcp",
                code=failure_code(exc),
                detail=str(exc) if isinstance(exc, ProbeFailure) else type(exc).__name__,
            )
        raw = os.environ.get("TINYASSETS_CORE_CANARY_CONFIG", "")
        if not raw:
            report["configuration_missing"] = True
            return
        config = json.loads(raw)
        for name in (
            "email",
            "password",
            "universe_id",
            "connection_id",
            "service_path",
            "intake_receiver_id",
            "branch_id",
        ):
            if not isinstance(config.get(name), str) or not config[name]:
                raise ProbeFailure("canary configuration missing " + name)
        report["active_capability"] = "owner_sign_in"
        token = await login(config, http)
        passed(report, "owner_sign_in", {"callback": "verified", "home": "dedicated canary"})
        await exercise(config, http, token, report)
        report["active_capability"] = "public_mcp"
        await health(http, report)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", default="core-capabilities-live.json")
    args = parser.parse_args()
    report = {"capabilities": [], "started_at": time.time(), "deployed_sha": ""}
    try:
        asyncio.run(run(report))
    except Exception as exc:
        report["probe_error"] = str(exc) if isinstance(exc, ProbeFailure) else type(exc).__name__
    report["failures"] = validate_report(report)
    if report.get("probe_error"):
        report["failures"].append(
            dict(
                capability=report.get("active_capability", "public_mcp"),
                code="probe_failed",
                detail=report["probe_error"],
            )
        )
    if report.get("configuration_missing"):
        report["failures"] = [
            dict(capability=name, code="configuration_missing") for name in CAPABILITIES
        ]
    if report.get("health_failure"):
        report["failures"].append(report["health_failure"])
    for row in report.get("rate_alarms", []):
        report["failures"].append(
            dict(
                capability=row["capability"],
                code=row["code"],
                detail=f"{row['failures']}/{row['samples']} failures in the recent window",
            )
        )
    Path(args.report).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"deployed_sha": report["deployed_sha"], "failures": report["failures"]}))
    return int(bool(report["failures"] or report.get("probe_error")))


if __name__ == "__main__":
    raise SystemExit(main())
