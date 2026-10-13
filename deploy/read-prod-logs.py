#!/usr/bin/env python3
"""Read-only timeline of one person's session trail.

sshd runs this as a forced command. ``SSH_ORIGINAL_COMMAND`` is parsed and
never executed. The only host command is a fixed ``journalctl`` read of the
Vector container's journal. Stdout is redacted JSONL. Stderr is one summary
line. Nothing here writes, restarts, or opens a shell.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

SCHEMA = "tinyassets.event.v1"
_STAMP = "%Y-%m-%dT%H:%M:%SZ"
_IDENT = re.compile(r"^[A-Za-z0-9_|.-]{1,128}$")
_TRACE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
_FLAGS = {
    "--since": "since",
    "--until": "until",
    "--session": "session",
    "--account": "account",
    "--trace-id": "trace",
    "--grep": "grep",
}
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_BEARER = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=\-]{8,}")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
_SECRET_PREFIX = re.compile(r"\b(?:sk|pk|rk)_(?:live|test)_[A-Za-z0-9]{8,}\b")
_LONG_TOKEN = re.compile(r"\b[A-Za-z0-9_+/=-]{80,}\b")
_QUERY = re.compile(r"(https?://[^\s?#]+)\?[^\s#]*")
_PAIR = re.compile(
    r"(?i)\b(code|token|access_token|refresh_token|id_token|state|"
    r"client_secret|password|authorization)=([^\s&]+)"
)
_COOKIE_HEADER = re.compile(r"(?i)\b(cookie|set-cookie)\s*[:=]\s*[^\r\n]*")
_COOKIE_PAIR = re.compile(
    r"(?i)\b([A-Za-z0-9_.-]*(?:session|sess|sid|cookie|csrf|xsrf|auth|secret|key|jwt)"
    r"[A-Za-z0-9_.-]*)=([^\s;&,)\"']+)"
)
_LONG_PAIR = re.compile(r"\b([A-Za-z_][A-Za-z0-9_.-]*)=([A-Za-z0-9_+/=.~%-]{16,})")
_CARD = re.compile(r"\b(?:\d[ -]?){12,18}\d\b")
_STRIPE_ID = re.compile(
    r"\b(?:cus|pi|pm|sub|in|ch|py|re|prod|price|acct|evt|cs|seti|si|txn|ba|card|src|"
    r"tok|whsec|sk|pk|rk)_(?=[A-Za-z0-9_]*[0-9A-Z])[A-Za-z0-9_]{8,}\b"
)
_READ_BYTES = 8_000_000
_READ_EVENTS = 4000
_LINE_BYTES = 1_000_000
_COMPONENT_CAP = 5000


class QueryError(Exception):
    """The caller asked for something this command does not do."""


def redact(text: str) -> str:
    """The same removals the writer applies, repeated on the way out."""
    if not text:
        return ""
    cleaned = _QUERY.sub(r"\1", text)
    cleaned = _COOKIE_HEADER.sub(lambda match: f"{match.group(1)}: [redacted]", cleaned)
    cleaned = _PAIR.sub(lambda match: f"{match.group(1)}=[redacted]", cleaned)
    cleaned = _COOKIE_PAIR.sub(lambda match: f"{match.group(1)}=[redacted]", cleaned)
    cleaned = _LONG_PAIR.sub(lambda match: f"{match.group(1)}=[redacted]", cleaned)
    cleaned = _EMAIL.sub("[redacted]", cleaned)
    cleaned = _BEARER.sub(lambda match: f"{match.group(1)} [redacted]", cleaned)
    cleaned = _JWT.sub("[redacted]", cleaned)
    cleaned = _SECRET_PREFIX.sub("[redacted]", cleaned)
    cleaned = _STRIPE_ID.sub("[redacted]", cleaned)
    cleaned = _CARD.sub("[redacted]", cleaned)
    return _LONG_TOKEN.sub("[redacted]", cleaned)


def parse_command(argv: list[str]) -> dict[str, str]:
    """Accept only ``read-logs`` and the documented flags."""
    if not argv or argv[0] != "read-logs":
        raise QueryError("usage: read-logs --since TIME --until TIME")
    values = {
        "since": "", "until": "", "session": "", "account": "", "trace": "", "grep": "",
    }
    index = 1
    while index < len(argv):
        flag = argv[index]
        if flag not in _FLAGS:
            raise QueryError("read-logs rejected the command")
        name = _FLAGS[flag]
        if values[name]:
            raise QueryError("read-logs rejected the command")
        index += 1
        if index >= len(argv):
            raise QueryError("read-logs rejected the command")
        values[name] = argv[index]
        index += 1
    _validate(values)
    return values


def _validate(values: dict[str, str]) -> None:
    for key, value in values.items():
        if "\n" in value or "\r" in value or "\x00" in value:
            raise QueryError(f"{key} contains a newline")
    since = _clock(values["since"], "since")
    until = _clock(values["until"], "until")
    if until < since or until - since > timedelta(days=7):
        raise QueryError("the window must be within 7 days")
    for key in ("session", "account"):
        value = values[key]
        if value and (not _IDENT.fullmatch(value) or "@" in value):
            raise QueryError(f"{key} is not an opaque id")
    if values["trace"] and not _TRACE.fullmatch(values["trace"]):
        raise QueryError("trace is not a trace id")
    if len(values["grep"]) > 80:
        raise QueryError("grep is longer than 80 characters")


def _clock(value: str, name: str) -> datetime:
    try:
        parsed = datetime.strptime(value, _STAMP)
    except ValueError as exc:
        raise QueryError(f"{name} must be YYYY-MM-DDTHH:MM:SSZ") from exc
    return parsed.replace(tzinfo=timezone.utc)


def parse_ts(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.endswith("Z"):
        return None
    for pattern in ("%Y-%m-%dT%H:%M:%S.%fZ", _STAMP):
        try:
            return datetime.strptime(value, pattern).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def unwrap_line(line: str) -> dict[str, Any] | None:
    """Pull a trail event out of a raw journal line or a Vector JSON wrapper."""
    text = line.strip()
    if not text:
        return None
    try:
        obj = json.loads(text)
    except ValueError:
        return None
    found = _as_event(obj)
    if found is not None:
        return found
    if not isinstance(obj, dict):
        return None
    for key in ("log", "message", "MESSAGE"):
        inner = obj.get(key)
        found = _as_event(inner)
        if found is not None:
            return found
        if isinstance(inner, str):
            try:
                found = _as_event(json.loads(inner))
            except ValueError:
                found = None
            if found is not None:
                return found
    return None


def _as_event(value: object) -> dict[str, Any] | None:
    if isinstance(value, dict) and value.get("schema") == SCHEMA:
        return value
    return None


#: The only link a trail joins through: the server-signed session cookie.
#: Other links (MCP session, OAuth state) are request-supplied, so a caller
#: could name someone else's value; they stay in the event as data only.
_JOIN_LINKS = ("cookie",)


def event_keys(event: dict[str, Any]) -> set[tuple[str, str]]:
    """Join keys: the event's session, its signed-cookie link, its account."""
    keys: set[tuple[str, str]] = set()
    session = event.get("session_id")
    if isinstance(session, str) and session:
        keys.add(("session", session))
    links = event.get("links")
    if isinstance(links, dict):
        for name in _JOIN_LINKS:
            value = links.get(name)
            if isinstance(value, str) and value:
                keys.add(("session", value))
    context = event.get("context")
    account = context.get("account_id") if isinstance(context, dict) else None
    if isinstance(account, str) and account:
        keys.add(("account", account))
    return keys


def select_timeline(
    events: list[dict[str, Any]],
    *,
    since: datetime,
    until: datetime,
    session: str = "",
    account: str = "",
    trace: str = "",
    grep: str = "",
) -> tuple[list[dict[str, Any]], bool]:
    """Ordered events for a window, one session, or one account.

    A session, account, or trace id seeds the trail. The trail then grows
    through shared session ids, link values, and account ids inside the
    window. ``grep`` runs after that join, so a link event is not dropped
    before it can connect the rest.
    """
    windowed = []
    for event in events:
        stamp = parse_ts(event.get("ts"))
        if stamp is None or stamp < since or stamp > until:
            continue
        windowed.append(event)
    truncated = False
    if session or account or trace:
        chosen, truncated = _expand(windowed, session, account, trace)
    else:
        chosen = list(windowed)
    if grep:
        chosen = [
            event for event in chosen
            if grep in json.dumps(event, sort_keys=True, default=str)
        ]
    chosen.sort(key=lambda event: (str(event.get("ts") or ""), str(event.get("trace_id") or "")))
    if len(chosen) > _COMPONENT_CAP:
        return chosen[:_COMPONENT_CAP], True
    return chosen, truncated


def _expand(
    windowed: list[dict[str, Any]], session: str, account: str, trace: str,
) -> tuple[list[dict[str, Any]], bool]:
    indexed = [(event, event_keys(event)) for event in windowed]
    frontier: set[tuple[str, str]] = set()
    for event, keys in indexed:
        if _seed(event, keys, session, account, trace):
            frontier |= keys
    if not frontier and not trace:
        return [], False
    if trace and not (session or account):
        # A caller chooses its own trace id, so a trace names requests, never
        # a trail: it returns those requests and does not expand.
        direct = [event for event, _keys in indexed if event.get("trace_id") == trace]
        return direct[:_COMPONENT_CAP], len(direct) > _COMPONENT_CAP
    selected: list[dict[str, Any]] = []
    seen: set[int] = set()
    changed = True
    truncated = False
    while changed and len(selected) < _COMPONENT_CAP:
        changed = False
        for event, keys in indexed:
            marker = id(event)
            if marker in seen:
                continue
            # Only trusted keys grow a trail; a shared trace id never admits.
            if not (keys & frontier):
                continue
            seen.add(marker)
            selected.append(event)
            if keys - frontier:
                frontier |= keys
                changed = True
            if len(selected) >= _COMPONENT_CAP:
                truncated = True
                break
    return selected, truncated


def _seed(
    event: dict[str, Any],
    keys: set[tuple[str, str]],
    session: str,
    account: str,
    trace: str,
) -> bool:
    if session and ("session", session) not in keys:
        return False
    if account and ("account", account) not in keys:
        return False
    if trace and event.get("trace_id") != trace:
        return False
    return bool(session or account or trace)


#: The public bucket for every browser-reported error. A browser names its own
#: error type, so that string is never a key in output that leaves the host.
CLIENT_ERROR_KIND = "client_error"


def _browser_originated(event: dict[str, Any]) -> bool:
    return event.get("event") == "client.error" or event.get("step") == "client.error"


def summary_for(events: list[dict[str, Any]], *, truncated: bool) -> dict[str, Any]:
    """Aggregates only, for the public job log: error types by commit.

    Server-originated types (exception class names, ``HTTP5xx``) are counted by
    name. A browser report is counted under ``client_error`` whatever type it
    carried, because the browser chose that string.
    """
    counts: dict[str, dict[str, int]] = {}
    for event in events:
        http = event.get("http") if isinstance(event.get("http"), dict) else {}
        error = event.get("error") if isinstance(event.get("error"), dict) else {}
        status = http.get("status")
        kind = error.get("type") if isinstance(error.get("type"), str) and error.get("type") else ""
        if not kind and isinstance(status, int) and status >= 500:
            kind = f"HTTP{status}"
        if _browser_originated(event):
            kind = CLIENT_ERROR_KIND if kind or error else ""
        if not kind:
            continue
        commit = event.get("commit") if isinstance(event.get("commit"), str) else ""
        commit = commit or "unknown"
        bucket = counts.setdefault(commit, {})
        bucket[kind] = bucket.get(kind, 0) + 1
    return {"errors_by_commit": counts, "lines": len(events), "truncated": truncated}


def render(event: dict[str, Any]) -> str:
    return json.dumps(_redact_value(event), sort_keys=True, separators=(",", ":"), default=str)


def _redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {str(key): _redact_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    return value


def journal_argv(since: datetime, until: datetime) -> list[str]:
    """Fixed argv. The end is one second past the inclusive bound; the filter trims it."""
    start = since.strftime("%Y-%m-%d %H:%M:%S UTC")
    end = (until + timedelta(seconds=1)).strftime("%Y-%m-%d %H:%M:%S UTC")
    command = [
        "journalctl", "CONTAINER_NAME=tinyassets-logs",
        "--since", start, "--until", end, "--no-pager", "-o", "cat",
    ]
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        return ["sudo", "-n", *command]
    return command


def read_journal(since: datetime, until: datetime) -> tuple[list[dict[str, Any]], bool]:
    """Stream journalctl line by line and stop the process at the byte or event cap."""
    try:
        process = subprocess.Popen(
            journal_argv(since, until),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        raise QueryError("journalctl failed") from exc
    assert process.stdout is not None
    events: list[dict[str, Any]] = []
    truncated = False
    consumed = 0
    skipping = False
    try:
        while True:
            line = process.stdout.readline(_LINE_BYTES)
            if not line:
                break
            consumed += len(line)
            if consumed > _READ_BYTES:
                truncated = True
                break
            complete = line.endswith(b"\n")
            if skipping or not complete:
                # One line past _LINE_BYTES is never a journal event; drop
                # every chunk of it rather than hold it in memory.
                skipping = not complete
                continue
            event = unwrap_line(line.decode("utf-8", "replace").rstrip("\r\n"))
            if event is None:
                continue
            events.append(event)
            if len(events) >= _READ_EVENTS:
                truncated = True
                break
    finally:
        _stop(process)
    if not truncated and process.returncode != 0:
        raise QueryError("journalctl failed")
    return events, truncated


def _stop(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
    if process.stdout is not None:
        process.stdout.close()


def command_from_environment(argv: list[str]) -> list[str]:
    raw = os.environ.get("SSH_ORIGINAL_COMMAND")
    if raw is None:
        return argv
    if not raw.strip():
        raise QueryError("usage: read-logs --since TIME --until TIME")
    try:
        return shlex.split(raw)
    except ValueError as exc:
        raise QueryError("read-logs rejected the command") from exc


def main(argv: list[str] | None = None) -> int:
    try:
        supplied = argv if argv is not None else sys.argv[1:]
        parsed = parse_command(command_from_environment(supplied))
        since = _clock(parsed["since"], "since")
        until = _clock(parsed["until"], "until")
        events, truncated = read_journal(since, until)
        chosen, clipped = select_timeline(
            events,
            since=since,
            until=until,
            session=parsed["session"],
            account=parsed["account"],
            trace=parsed["trace"],
            grep=parsed["grep"],
        )
        for event in chosen:
            sys.stdout.write(render(event) + "\n")
        payload = summary_for(chosen, truncated=truncated or clipped)
        sys.stderr.write("TINYASSETS_SUMMARY " + json.dumps(payload, sort_keys=True) + "\n")
    except QueryError as exc:
        sys.stderr.write(str(exc) + "\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
