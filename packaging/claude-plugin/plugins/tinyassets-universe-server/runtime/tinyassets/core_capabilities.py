"""The single capability contract shared by runtime, CI and live assurance."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Capability:
    name: str
    evidence: str


CAPABILITIES = {
    "owner_sign_in": Capability("Owner sign-in", "verified_owner_session"),
    "chat_stream": Capability("Streamed chat reply", "reply_deltas"),
    "bash": Capability("bash", "tool_result"),
    "read": Capability("read", "tool_result"),
    "write": Capability("write", "tool_result"),
    "edit": Capability("edit", "tool_result"),
    "ta": Capability("ta", "completed_capability"),
    "connected_service": Capability("Connected HTTP service", "http_effect"),
    "read_image": Capability("Read an image", "image_content"),
    "approval": Capability("Answer an approval request", "answered_request"),
    "background_run": Capability("Background run / sub-agent", "completed_run"),
    "scheduled_wake": Capability("Scheduled wake", "consumed_wake"),
    "patch_request": Capability("File a patch request", "filed_request"),
    "public_mcp": Capability("Public MCP", "authenticated_tool_call"),
}

# Bounded codes: never key metrics on exception strings, paths or owner IDs.
ERROR_CODES = frozenset(
    {
        "operation_failed",
        "content_frame_invalid",
        "broker_refused",
        "broker_unavailable",
        "cell_entry_failed",
        "cell_execution_failed",
        "provider_reply_failed",
        "deadline_exceeded",
        "http_failed",
        "authentication_failed",
        "authority_refused",
        "evidence_missing",
        "configuration_missing",
        "rate_alarm",
        "probe_failed",
    }
)


def failure_code(error: object) -> str:
    """Classify locally; callers must never publish the input exception text."""
    name = type(error).__name__
    message = str(error).lower()
    if "owner content" in message and ("frame" in message or "proof" in message):
        return "content_frame_invalid"
    if (
        name == "BrokerRefused"
        or "broker refused" in message
        or "inference usage authority refused" in message
    ):
        return "broker_refused"
    if "broker" in message and any(s in message for s in ("unavailable", "failed", "invalid")):
        return "broker_unavailable"
    if name == "PermissionError" or "owner scope is not admitted" in message:
        return "authority_refused"
    if (
        name == "OwnerLaunchRefused"
        or "launcher" in message
        or "could not enter" in message
        or "cell entry" in message
    ):
        return "cell_entry_failed"
    if "cell" in message:
        return "cell_execution_failed"
    if isinstance(error, TimeoutError) or "deadline" in message or "timed out" in message:
        return "deadline_exceeded"
    if name.startswith("Provider"):
        return "provider_reply_failed"
    if "http operation failed" in message:
        return "http_failed"
    return "operation_failed"


def validate_report(report: dict) -> list[dict]:
    """Missing, duplicate, skipped and unverifiable rows all fail closed."""
    rows = report.get("capabilities", [])
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("capability report must contain a list of outcomes")
    failures = []
    for capability, contract in CAPABILITIES.items():
        matches = [row for row in rows if row.get("capability") == capability]
        if len(matches) != 1:
            failures.append({"capability": capability, "code": "evidence_missing"})
            continue
        row = matches[0]
        if (
            row.get("status") != "passed"
            or row.get("evidence") != contract.evidence
            or not row.get("observation")
        ):
            code = row.get("code", "evidence_missing")
            failures.append(
                {"capability": capability, "code": code if code in ERROR_CODES else "probe_failed"}
            )
    if any(row.get("capability") not in CAPABILITIES for row in rows):
        raise ValueError("unknown capability in report")
    return failures
