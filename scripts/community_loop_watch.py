"""Community loop watch - cloud-visible uptime and deploy health.

The cheat-loop intake/writer/checker machinery was retired on 2026-06-25.
This script keeps the parts that still protect uptime surfaces:

- public MCP observation canary freshness
- open P0 outage issues
- Tier-3 clean-clone smoke issues
- production and website deploy workflow visibility

It is read-only. It queries public GitHub state, optionally with a token for
higher rate limits, and exits non-zero only when a retained uptime/deploy stage
is red.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

DEFAULT_REPO = "TinyAssets/TinyAssets"
DEFAULT_API = "https://api.github.com"
DEFAULT_TIMEOUT = 20.0

WORKFLOWS = {
    "observation": "uptime-canary.yml",
    "tier3": "tier3-oss-clone-nightly.yml",
    "deploy_prod": "deploy-prod.yml",
    "deploy_site": "deploy-site-react.yml",
}

P0_OUTAGE_LABEL = "p0-outage"
TIER3_BROKEN_LABEL = "tier3-broken"
STATUS_RANK = {"green": 0, "yellow": 1, "unknown": 2, "red": 3}

# Run conclusions that attest an actual failure (page red) versus ones that
# carry no failure signal (warn yellow). Anything unrecognized fails closed.
RED_CONCLUSIONS = {"failure", "timed_out", "action_required"}
BENIGN_CONCLUSIONS = {"skipped", "cancelled", "neutral", "stale"}


class WatchError(Exception):
    """Raised when the watch cannot read its evidence source."""

    def __init__(self, msg: str, *, code: int = 3) -> None:
        super().__init__(msg)
        self.msg = msg
        self.code = code


def _utc_now() -> dt.datetime:
    return dt.datetime.now(tz=dt.timezone.utc)


def _gh_cli_token(timeout: float = 5.0) -> str | None:
    try:
        result = subprocess.run(
            ["gh", "auth", "token"],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    token = result.stdout.strip()
    return token or None


def _github_token(args: argparse.Namespace) -> str | None:
    return args.token or os.environ.get("GITHUB_TOKEN") or _gh_cli_token()


def _parse_time(value: str | None) -> dt.datetime | None:
    if not isinstance(value, str) or not value:
        return None
    text = value.replace("Z", "+00:00") if value.endswith("Z") else value
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def _age_min(value: str | None, now: dt.datetime) -> float | None:
    parsed = _parse_time(value)
    if parsed is None:
        return None
    return max(0.0, (now - parsed).total_seconds() / 60.0)


def _api_url(api: str, path: str, params: dict[str, Any] | None = None) -> str:
    base = api.rstrip("/")
    if not path.startswith("/"):
        path = f"/{path}"
    url = f"{base}{path}"
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    return url


def _parse_next_link(link_header: str) -> str | None:
    if not link_header:
        return None
    for piece in link_header.split(","):
        bits = piece.strip().split(";")
        if len(bits) != 2:
            continue
        target, rel = bits
        if rel.strip() == 'rel="next"' and target.startswith("<") and target.endswith(">"):
            return target[1:-1]
    return None


def _gh_get_url(url: str, *, token: str | None, timeout: float) -> tuple[Any, str | None]:
    request = urllib.request.Request(url)
    request.add_header("Accept", "application/vnd.github+json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
            return payload, response.headers.get("Link")
    except urllib.error.HTTPError as exc:
        raise WatchError(f"GitHub API error {exc.code} for {url}: {exc.reason}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise WatchError(f"GitHub evidence read failed for {url}: {exc}") from exc


def _gh_get_paginated(
    path: str,
    *,
    api: str,
    token: str | None,
    timeout: float,
    params: dict[str, Any] | None = None,
    max_pages: int = 5,
) -> list[dict[str, Any]]:
    url: str | None = _api_url(api, path, params)
    out: list[dict[str, Any]] = []
    pages = 0
    while url and pages < max_pages:
        payload, link = _gh_get_url(url, token=token, timeout=timeout)
        if isinstance(payload, list):
            out.extend(item for item in payload if isinstance(item, dict))
        elif isinstance(payload, dict):
            out.append(payload)
        url = _parse_next_link(link or "")
        pages += 1
    return out


def _latest_workflow_run(
    repo: str,
    workflow_id: str,
    *,
    api: str,
    token: str | None,
    timeout: float,
    per_page: int = 30,
) -> dict[str, Any] | None:
    payloads = _gh_get_paginated(
        f"/repos/{repo}/actions/workflows/{workflow_id}/runs",
        api=api,
        token=token,
        timeout=timeout,
        params={"per_page": per_page},
        max_pages=1,
    )
    if not payloads:
        return None
    runs = payloads[0].get("workflow_runs") if isinstance(payloads[0], dict) else None
    if not isinstance(runs, list) or not runs:
        return None
    first = runs[0]
    return first if isinstance(first, dict) else None


def list_open_issues_by_label(
    repo: str,
    label: str,
    *,
    api: str,
    token: str | None,
    timeout: float,
) -> list[dict[str, Any]]:
    issues = _gh_get_paginated(
        f"/repos/{repo}/issues",
        api=api,
        token=token,
        timeout=timeout,
        params={"state": "open", "labels": label, "per_page": 100},
    )
    return [issue for issue in issues if "pull_request" not in issue]


def _stage(
    name: str,
    status: str,
    summary: str,
    *,
    evidence: str | None = None,
    url: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "name": name,
        "status": status,
        "summary": summary,
    }
    if evidence:
        result["evidence"] = evidence
    if url:
        result["url"] = url
    if details is not None:
        result["details"] = details
    return result


def _staleness_stage(
    name: str,
    workflow_id: str,
    latest: dict[str, Any],
    details: dict[str, Any],
    *,
    max_age_min: int,
    stale_red_min: int | None,
    age: float | None,
    evidence: str,
) -> dict[str, Any]:
    """Classify a stale heartbeat.

    A stale heartbeat is a monitoring-cadence gap, not a measured endpoint
    red, so it warns (yellow) first and only pages (red) when the monitor
    itself looks dead — stale far beyond the warn window.
    """
    url = latest.get("html_url")
    if stale_red_min is not None and age is not None and age > stale_red_min:
        return _stage(
            name,
            "red",
            f"{workflow_id} has not run successfully within {stale_red_min} min",
            evidence=evidence,
            url=url,
            details=details,
        )
    return _stage(
        name,
        "yellow",
        f"{workflow_id} has not run successfully within {max_age_min} min",
        evidence=evidence,
        url=url,
        details=details,
    )


def workflow_stage(
    name: str,
    repo: str,
    workflow_id: str,
    *,
    api: str,
    token: str | None,
    timeout: float,
    now: dt.datetime,
    max_age_min: int | None,
    stale_red_min: int | None = None,
) -> dict[str, Any]:
    latest = _latest_workflow_run(
        repo,
        workflow_id,
        api=api,
        token=token,
        timeout=timeout,
        per_page=100,
    )
    details: dict[str, Any] = {
        "workflow_id": workflow_id,
        "max_age_min": max_age_min,
        "stale_red_min": stale_red_min,
    }
    if latest is None:
        return _stage(
            name,
            "red",
            f"{workflow_id} has no visible workflow runs",
            details=details,
        )

    status = str(latest.get("status") or "unknown")
    conclusion = latest.get("conclusion")
    created_at = latest.get("created_at")
    age = _age_min(created_at, now)
    details.update(
        {
            "run_id": latest.get("id"),
            "run_status": status,
            "conclusion": conclusion,
            "created_at": created_at,
            "age_min": age,
        }
    )

    if status != "completed":
        return _stage(
            name,
            "yellow",
            f"{workflow_id} latest run is {status}",
            evidence=f"run {latest.get('id')}",
            url=latest.get("html_url"),
            details=details,
        )
    if workflow_id == WORKFLOWS["observation"]:
        if not _canary_source_matches(repo, latest, now):
            return _stage(
                name, "unknown", "required production canary source unavailable",
                url=latest.get("html_url"), details=details,
            )
        created = _parse_time(created_at)
        if created is not None and created <= now and max_age_min is not None and age > max_age_min:
            # A stale heartbeat is a monitoring-cadence gap, not a measured
            # endpoint red: warn first, page only when the monitor itself
            # looks dead (stale far beyond the warn window).
            return _staleness_stage(
                name,
                workflow_id,
                latest,
                details,
                max_age_min=max_age_min,
                stale_red_min=stale_red_min,
                age=age,
                evidence="monitoring cadence unavailable; not an endpoint-health measurement",
            )
        measured, reason = _canary_receipt(
            repo, latest, api=api, token=token, timeout=timeout, now=now,
            max_age_min=max_age_min or 90,
        )
        details.update(measured_observation=measured, observation_reason=reason)
        return _stage(
            name, measured, f"Layer-1 observation {measured}: {reason}",
            evidence=f"run {latest.get('id')} attempt {latest.get('run_attempt')}",
            url=latest.get("html_url"), details=details,
        )
    if conclusion in RED_CONCLUSIONS:
        return _stage(
            name,
            "red",
            f"{workflow_id} latest run concluded {conclusion}",
            evidence=f"run {latest.get('id')} at {created_at}",
            url=latest.get("html_url"),
            details=details,
        )
    if conclusion != "success":
        # Skipped/cancelled/neutral runs carry no failure signal: warn, don't
        # page. Anything unrecognized still fails closed below via red.
        if conclusion in BENIGN_CONCLUSIONS:
            return _stage(
                name,
                "yellow",
                f"{workflow_id} latest run concluded {conclusion} — no failure signal",
                evidence=f"run {latest.get('id')} at {created_at}",
                url=latest.get("html_url"),
                details=details,
            )
        return _stage(
            name,
            "red",
            f"{workflow_id} latest run concluded {conclusion or 'unknown'}",
            evidence=f"run {latest.get('id')} at {created_at}",
            url=latest.get("html_url"),
            details=details,
        )
    if max_age_min is not None and (age is None or age > max_age_min):
        age_text = "unknown age" if age is None else f"{age:.1f} min old"
        return _staleness_stage(
            name,
            workflow_id,
            latest,
            details,
            max_age_min=max_age_min,
            stale_red_min=stale_red_min,
            age=age,
            evidence=f"latest success run {latest.get('id')} is {age_text}",
        )
    return _stage(
        name,
        "green",
        f"{workflow_id} latest run succeeded",
        evidence=f"run {latest.get('id')} at {created_at}",
        url=latest.get("html_url"),
        details=details,
    )


def _canary_source_matches(repo, run, now):
    run_id, attempt, head = run.get("id"), run.get("run_attempt"), run.get("head_sha")
    created = _parse_time(run.get("created_at"))
    return not (
        type(run_id) is not int or run_id < 1 or type(attempt) is not int or attempt < 1
        or not isinstance(head, str) or len(head) != 40
        or any(char not in "0123456789abcdef" for char in head)
        or run.get("path") != ".github/workflows/uptime-canary.yml"
        or run.get("head_branch") != "main"
        or not isinstance(run.get("head_repository"), dict)
        or run["head_repository"].get("full_name") != repo
        or not isinstance(run.get("event"), str)
        or run.get("event") not in {"schedule", "workflow_dispatch", "workflow_run"}
        or run.get("status") != "completed"
        or created is None or created > now
    )


def _canary_receipt(repo, run, *, api, token, timeout, now, max_age_min):
    """Consume bounded exact-attempt step metadata, never logs or run success."""
    unknown = ("unknown", "required Layer-1 receipt unavailable or ambiguous")
    if not _canary_source_matches(repo, run, now):
        return unknown
    run_id, attempt, head = run["id"], run["run_attempt"], run["head_sha"]
    created = _parse_time(run["created_at"])
    try:
        payload, _ = _gh_get_url(
            _api_url(api, f"/repos/{repo}/actions/runs/{run_id}/attempts/{attempt}/jobs",
                     {"per_page": 100}), token=token, timeout=timeout,
        )
        if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
            return unknown
        jobs = payload["jobs"]
        if type(payload.get("total_count")) is not int or payload["total_count"] != len(jobs):
            return unknown
        if any(not isinstance(job, dict) for job in jobs):
            return unknown
        probes = [job for job in jobs if job.get("name") == "probe"]
        if len(probes) != 1:
            return unknown
        job = probes[0]
        finished = _parse_time(job.get("completed_at"))
        if (
            job.get("run_id") != run_id or job.get("run_attempt") != attempt
            or job.get("head_sha") != head or job.get("status") != "completed"
            or finished is None or finished < created or finished > now
            or (now - finished).total_seconds() > max_age_min * 60
        ):
            return unknown
        steps = job.get("steps")
        if not isinstance(steps, list) or any(not isinstance(step, dict) for step in steps):
            return unknown
        markers = {}
        for color in ("red", "green"):
            matches = [step for step in steps if step.get("name") == f"Layer-1 measured {color} v1"]
            if len(matches) > 1 or (matches and matches[0].get("status") != "completed"):
                return unknown
            if matches and not isinstance(matches[0].get("conclusion"), str):
                return unknown
            markers[color] = matches[0].get("conclusion") if matches else None
        if markers == {"red": "skipped", "green": "success"}:
            measured = "green"
        elif markers["red"] == "failure" and markers["green"] in {None, "skipped"}:
            # Existing pre-green-marker measured reds remain usable evidence.
            measured = "red"
        else:
            return unknown
        fresh, _ = _gh_get_url(
            _api_url(api, f"/repos/{repo}/actions/runs/{run_id}"), token=token, timeout=timeout,
        )
        if not isinstance(fresh, dict) or fresh.get("status") != "completed":
            return unknown
        for key in ("id", "run_attempt", "head_sha", "head_branch", "path", "head_repository",
                    "created_at", "event"):
            if fresh.get(key) != run.get(key):
                return unknown
        return measured, "exact-attempt measured receipt"
    except WatchError:
        return "unknown", "Layer-1 receipt could not be read; monitoring coverage unavailable"


def incident_stage(
    repo: str,
    *,
    api: str,
    token: str | None,
    timeout: float,
) -> dict[str, Any]:
    issues = list_open_issues_by_label(repo, P0_OUTAGE_LABEL, api=api, token=token, timeout=timeout)
    if issues:
        issue = issues[0]
        return _stage(
            "Observation incidents",
            "red",
            f"{len(issues)} open {P0_OUTAGE_LABEL} issue(s)",
            evidence=f"#{issue.get('number')}: {issue.get('title')}",
            url=issue.get("html_url"),
            details={"open_p0_outages": [i.get("number") for i in issues]},
        )
    return _stage(
        "Observation incidents",
        "green",
        f"no open {P0_OUTAGE_LABEL} issues",
        details={"open_p0_outages": []},
    )


def tier3_clone_smoke_stage(
    repo: str,
    *,
    api: str,
    token: str | None,
    timeout: float,
) -> dict[str, Any]:
    issues = list_open_issues_by_label(
        repo,
        TIER3_BROKEN_LABEL,
        api=api,
        token=token,
        timeout=timeout,
    )
    if not issues:
        return _stage(
            "Tier-3 clone smoke",
            "green",
            f"no open {TIER3_BROKEN_LABEL} issues",
            details={"open_tier3_broken": []},
        )

    issue = issues[0]
    latest = _latest_workflow_run(
        repo,
        WORKFLOWS["tier3"],
        api=api,
        token=token,
        timeout=timeout,
    )
    latest_time = _parse_time(latest.get("created_at")) if latest else None
    issue_times = [
        parsed
        for parsed in (
            _parse_time(item.get("created_at") or item.get("updated_at")) for item in issues
        )
        if parsed is not None
    ]
    newest_issue_time = max(issue_times) if issue_times else None
    if (
        latest is not None
        and latest.get("status") == "completed"
        and latest.get("conclusion") == "success"
        and latest_time is not None
        and newest_issue_time is not None
        and latest_time > newest_issue_time
    ):
        return _stage(
            "Tier-3 clone smoke",
            "yellow",
            (
                f"latest {WORKFLOWS['tier3']} success is newer than "
                f"{len(issues)} open {TIER3_BROKEN_LABEL} issue(s)"
            ),
            evidence=(
                f"latest successful tier-3 run {latest.get('id')} at "
                f"{latest.get('created_at')}; newest issue #{issue.get('number')}"
            ),
            url=latest.get("html_url") or issue.get("html_url"),
            details={
                "open_tier3_broken": [i.get("number") for i in issues],
                "latest_run_id": latest.get("id"),
                "latest_run_conclusion": latest.get("conclusion"),
                "latest_run_created_at": latest.get("created_at"),
                "newest_issue_at": newest_issue_time.isoformat().replace("+00:00", "Z"),
            },
        )
    return _stage(
        "Tier-3 clone smoke",
        "red",
        (
            f"{len(issues)} open {TIER3_BROKEN_LABEL} issue(s); "
            "Forever Rule tier-3 clone/run surface is red"
        ),
        evidence=f"#{issue.get('number')}: {issue.get('title')}",
        url=issue.get("html_url"),
        details={"open_tier3_broken": [i.get("number") for i in issues]},
    )


def classify(stages: list[dict[str, Any]]) -> str:
    return max((stage["status"] for stage in stages), key=lambda s: STATUS_RANK[s])


def build_status(args: argparse.Namespace, now: dt.datetime | None = None) -> dict[str, Any]:
    current_now = now or _utc_now()
    token = _github_token(args)
    repo = args.repo
    api = args.api
    timeout = args.timeout

    stages = [
        workflow_stage(
            "Observation canary",
            repo,
            WORKFLOWS["observation"],
            api=api,
            token=token,
            timeout=timeout,
            now=current_now,
            max_age_min=args.max_observation_age_min,
            stale_red_min=args.max_observation_stale_red_min,
        ),
        incident_stage(repo, api=api, token=token, timeout=timeout),
        tier3_clone_smoke_stage(repo, api=api, token=token, timeout=timeout),
        workflow_stage(
            "Production deploy",
            repo,
            WORKFLOWS["deploy_prod"],
            api=api,
            token=token,
            timeout=timeout,
            now=current_now,
            max_age_min=None,
        ),
        workflow_stage(
            "Website deploy",
            repo,
            WORKFLOWS["deploy_site"],
            api=api,
            token=token,
            timeout=timeout,
            now=current_now,
            max_age_min=None,
        ),
    ]
    overall = classify(stages)
    return {
        "version": 2,
        "checked_at": current_now.isoformat().replace("+00:00", "Z"),
        "repo": repo,
        "overall": overall,
        "exit_code": 2 if overall == "red" else 0,
        "stages": stages,
    }


def error_status(exc: WatchError, now: dt.datetime | None = None) -> dict[str, Any]:
    current_now = now or _utc_now()
    return {
        "version": 2,
        "checked_at": current_now.isoformat().replace("+00:00", "Z"),
        "repo": None,
        "overall": "red",
        "exit_code": exc.code,
        "stages": [
            _stage(
                "GitHub evidence read",
                "red",
                "community loop watch could not read GitHub evidence",
                evidence=exc.msg,
            )
        ],
    }


def format_human(status: dict[str, Any]) -> str:
    lines = [
        f"Community loop status: {status['overall'].upper()}",
        f"Checked: {status['checked_at']}",
        f"Repo: {status.get('repo') or '(unknown)'}",
        "",
    ]
    for stage in status["stages"]:
        lines.append(f"- {stage['status'].upper()} {stage['name']}: {stage['summary']}")
        if stage.get("evidence"):
            lines.append(f"  evidence: {stage['evidence']}")
        if stage.get("url"):
            lines.append(f"  url: {stage['url']}")
    return "\n".join(lines)


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Report cloud-visible uptime and deploy health.",
    )
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--api", default=DEFAULT_API)
    parser.add_argument(
        "--token",
        default=None,
        help="GitHub token; defaults to GITHUB_TOKEN or `gh auth token`.",
    )
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument(
        "--max-observation-age-min",
        type=int,
        default=90,
        help="Warn (yellow) if uptime-canary latest success is older than this.",
    )
    parser.add_argument(
        "--max-observation-stale-red-min",
        type=int,
        default=360,
        help="Page (red) only if uptime-canary latest success is older than "
        "this; staleness beyond --max-observation-age-min only warns.",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON instead of text.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    try:
        status = build_status(args)
    except WatchError as exc:
        status = error_status(exc)

    if args.json:
        print(json.dumps(status, indent=2, sort_keys=True))
    else:
        print(format_human(status))
    return int(status["exit_code"])


if __name__ == "__main__":
    sys.exit(main())
