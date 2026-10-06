#!/usr/bin/env python3
"""Answer "is this commit actually in production?" — Hard Rule 14's gate.

*Merged is not deployed.* Merges performed by the Actions app via
``GITHUB_TOKEN`` raise no ``push`` event, so ``build-image`` / ``deploy-prod``
never fire. Five PRs landed on 2026-07-21 and none reached production. No
commit touched the broken surface, so only an out-of-band probe can catch this
class — the same shape as the 2026-04-19 tunnel outage.

By default this reads ``release_state.git_sha`` from the existing public
``HEAD /app`` response's ``X-TinyAssets-Build`` header (no credentials, no
response body). The app projects that header from the deploy receipt and sends
``Cache-Control: no-store``. Missing or malformed full SHAs fail closed.

    python scripts/deployed_sha.py --assert-contains HEAD
    python scripts/deployed_sha.py --json

An explicit ``--url https://tinyassets.io/mcp`` retains the authenticated pulse
receipt and its image-tag cross-check. It requires
``TINYASSETS_WIKI_CANARY_TOKEN``; use that mode with ``--report-provenance`` for
the canary-only diagnostic. Public-header mode reports provenance as unknown
and image_tag as null: no corroborating tag is exposed by this public surface.

**Known limit — it proves the RECEIPT, not the running binary.**
``release_state`` is a JSON file the deploy job writes to the host volume;
``tinyassets/api/status.py`` reads it back without comparing it to the revision
actually running. A manual rollback or an older-image restart that leaves the
receipt intact would make an older server report a newer sha, and
``--assert-contains`` would return 0 for code that is not running. Codex found
this reviewing the 2026-08-25 harness reset; closing it needs the public surface
to expose a runtime-derived revision, which is a product change, not a harness
one. Tracked at ``docs/concerns/2026-08-26-deployed-sha-proves-receipt-only.md``.

In authenticated pulse mode: ``git_sha`` and
``image_tag`` must agree, and a receipt missing either is exit 2 rather than a
pass. That catches a partial or tampered receipt; it cannot catch a coherent
receipt describing a build that is no longer running.

``--report-provenance`` adds a record-only diagnostic line from the SAME receipt
response when available: the cached cloud-provenance verdict the responding process resolved at
its own startup (openspec change ``cloud-only-runtime-admission``). It makes no
second request, prints only allowlisted typed fields, and reports anything
missing or malformed as ``unknown``. It does NOT change this gate's exit codes,
and an ``unknown`` verdict is the absence of an observation, never a pass of
cloud acceptance. It samples one responding worker and says nothing about binary
freshness, the current container incarnation, attestation or custody.

**This is a post-deploy check, never a merge-required one.** A PR-required
check cannot demand that production already contain an unmerged head; wiring it
that way is circular and can never pass. Codex flagged exactly that in the
2026-08-25 harness-reset review. Run it *after* a deploy, or before claiming a
fix is shipped.

Exit codes: 0 pass, 1 assertion failed (commit not in production), 2 could not
determine (network, missing field, unknown sha). A commit that descends from the
served sha and changes no runtime input since (``scripts/runtime_paths.py``)
passes as *runtime-equivalent*: build-image never builds such a head, and the
running image already equals it on every path production runs. 2 is deliberately distinct
from 1 — "I could not tell" must never read as "yes, it shipped."
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))

import runtime_paths  # noqa: E402
from _canary_common import require_canary_bearer  # noqa: E402

DEFAULT_URL = "https://tinyassets.io/app"
#: Cloudflare answers the stdlib's default ``Python-urllib/3.x`` agent with a
#: managed-challenge 403 (measured against the live surface 2026-09-02), which
#: this gate would report as "cannot determine" forever. Every other probe in
#: scripts/ already names itself for the same reason.
PULSE_USER_AGENT = "tinyassets-deploy-gate/1.0"
DEFAULT_TIMEOUT = 30.0


class DeployedShaError(Exception):
    """Could not determine what production is serving."""


class PublicReleaseState(dict):
    """SHA-only receipt read from the public app's no-store build header."""


def public_release_state(url: str, timeout: float) -> PublicReleaseState:
    """Read only the existing public release SHA, without credentials or a body."""
    request = urllib.request.Request(
        url, method="HEAD", headers={"User-Agent": PULSE_USER_AGENT},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status != 200:
                raise DeployedShaError(f"non-200 status {response.status} from {url}")
            sha = response.headers.get("X-TinyAssets-Build", "").strip()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise DeployedShaError(f"probe failed against {url}: {exc}") from exc
    if not re.fullmatch(r"[0-9a-fA-F]{40}", sha):
        raise DeployedShaError("public release header missing or not a full git SHA")
    return PublicReleaseState(git_sha=sha.lower())


# What counts as "changes what production runs" is scripts/runtime_paths.py --
# the same classifier build-image.yml uses to skip a redundant image and
# release-reconcile.yml uses to find drift. A commit it calls non-runtime is
# never built, so it can never appear in the receipt's sha; it is served the
# moment production serves a runtime-equivalent tree.


def _git(*args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if proc.returncode != 0:
        raise DeployedShaError(f"git {' '.join(args)}: {(proc.stderr or '').strip()}")
    return proc.stdout.strip()


def live_release_state(url: str, timeout: float) -> dict[str, Any]:
    """Read the public app header; explicit MCP URLs retain canary diagnostics."""
    if url.rstrip("/").endswith("/app"):
        return public_release_state(url, timeout)
    pulse_url = f"{url.rstrip('/')}/pulse"
    bearer = require_canary_bearer("deployed-sha")
    request = urllib.request.Request(
        pulse_url,
        method="GET",
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {bearer}",
            "User-Agent": PULSE_USER_AGENT,
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status != 200:
                raise DeployedShaError(
                    f"non-200 status {response.status} from {pulse_url}"
                )
            body = response.read()
    except urllib.error.HTTPError as exc:
        raise DeployedShaError(f"non-200 status {exc.code} from {pulse_url}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise DeployedShaError(f"probe failed against {pulse_url}: {exc}") from exc

    try:
        result = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DeployedShaError(f"non-JSON response from {pulse_url}") from exc
    if not isinstance(result, dict):
        raise DeployedShaError(
            f"pulse response is not an object: {type(result).__name__}"
        )

    return result


#: The ONLY provenance keys this reporter will print, with the type each must
#: have. Anything else in the server's object is dropped: the point of an
#: allowlist is that a future server field cannot start appearing in a CI log
#: without someone editing this list.
PROVENANCE_STR_FIELDS = ("verdict", "reason", "mode")
PROVENANCE_BOOL_FIELDS = (
    "observed",
    "metadata_reachable",
    "expected_identity_prepared",
    "enforced",
)
#: Known protocol VALUES, not a token shape. A shape allowlist was the first
#: attempt and it was wrong in three ways at once: `reason="instance_412903887"`
#: is a perfectly good snake_case token that carries a droplet id into a CI log;
#: `mode="enforcement_enabled"` prints a fake enforcement claim; and Python's
#: `$` matches before a trailing newline, so `"instance_match\n"` passes a
#: `^...$` check and injects a line break into the log. Only a value the
#: protocol actually defines is printable. A reason this list has not learned
#: yet prints as unknown -- the safe direction for a diagnostic whose job is to
#: not leak.
PROVENANCE_VERDICTS = frozenset({"cloud", "not_cloud", "unknown"})
PROVENANCE_MODES = frozenset({"observation_only", "application_admission"})
#: Every reason `tinyassets.platform_runtime_provenance` can put on a resolved
#: verdict or an unobserved peek. Kept in step with that module by a test.
PROVENANCE_REASONS = frozenset({
    "not_observed",
    "instance_match",
    "instance_mismatch",
    "metadata_probe_failed",
    "metadata_timeout",
    "metadata_unreachable",
    "metadata_redirect_refused",
    "metadata_http_error",
    "metadata_malformed_body",
    "metadata_body_too_large",
    "metadata_empty_id",
    "metadata_malformed_instance_id",
    "expected_identity_root_unresolved",
    "expected_identity_missing",
    "expected_identity_state_too_large",
    "expected_identity_state_unreadable",
    "expected_identity_malformed",
    "expected_identity_schema_unknown",
    "expected_identity_version_unsupported",
})
PROVENANCE_ALLOWED_VALUES = {
    "verdict": PROVENANCE_VERDICTS,
    "reason": PROVENANCE_REASONS,
    "mode": PROVENANCE_MODES,
}
PROVENANCE_UNKNOWN = "unknown"


def provenance_report(release_state: Any) -> dict[str, Any]:
    """Project allowlisted, type-checked provenance fields out of one response.

    Record-only diagnostic. It reads the pulse object ALREADY fetched — there is
    no second request — copies nothing it was not told to copy, and reports
    missing, mistyped or unexpected values as ``unknown``. It never raises, so
    it can never change this gate's exit code.

    ``unknown`` is the absence of an observation. It is NOT a pass of cloud
    acceptance and must never be read as one.
    """
    result: dict[str, Any] = {name: PROVENANCE_UNKNOWN for name in PROVENANCE_STR_FIELDS}
    result.update({name: PROVENANCE_UNKNOWN for name in PROVENANCE_BOOL_FIELDS})
    result["reported"] = False
    if not isinstance(release_state, dict):
        return result
    raw = release_state.get("platform_runtime_provenance")
    if not isinstance(raw, dict):
        # Absent field = an older build that does not carry it. Unknown, not a
        # verdict, and not an error either.
        return result
    result["reported"] = True
    for name in PROVENANCE_STR_FIELDS:
        value = raw.get(name)
        # Exact membership, no strip() and no normalization: a value that needs
        # cleaning up before it matches is not the protocol value, and cleaning
        # it is how a newline or a padded id gets through.
        if isinstance(value, str) and value in PROVENANCE_ALLOWED_VALUES[name]:
            result[name] = value
    for name in PROVENANCE_BOOL_FIELDS:
        value = raw.get(name)
        # `type(...) is bool` on purpose: 1/0 are not booleans here, and a
        # truthy string must not become True.
        if type(value) is bool:
            result[name] = value
    return result


def report(
    url: str, timeout: float, *, include_provenance: bool = False
) -> dict[str, Any]:
    release_state = live_release_state(url, timeout)
    deployed = (release_state.get("git_sha") or "").strip()
    if not deployed:
        raise DeployedShaError("release_state.git_sha is empty — cannot tell what is deployed")

    # Cross-check the two independent fields the receipt carries. They are
    # written together, so agreement does not prove the running binary -- but
    # DISagreement proves the receipt is untrustworthy, and an untrustworthy
    # receipt must be exit 2, never a pass.
    # Cross-check the receipt against itself. Written together, so agreement
    # does not prove the running binary -- but DISagreement proves the receipt
    # is untrustworthy, and an untrustworthy receipt must be exit 2, never a
    # pass. Hardened after a cross-family review found four holes: a missing
    # tag passed while the docstring claimed it would not; a one-character tag
    # sharing the sha's first character passed; valid OCI forms like
    # `release-<sha>` and uppercase hex were rejected; and a non-string tag
    # raised an uncaught AttributeError.
    raw_tag = release_state.get("image_tag")
    if raw_tag is not None and not isinstance(raw_tag, str):
        raise DeployedShaError(
            f"release_state.image_tag is {type(raw_tag).__name__}, not a string - "
            "refusing to interpret a malformed receipt"
        )
    image_tag = (raw_tag or "").strip()
    if not image_tag and not isinstance(release_state, PublicReleaseState):
        raise DeployedShaError(
            "release_state carries git_sha but no image_tag - cannot corroborate "
            "the receipt, so the deploy state is unknown"
        )

    # Take the tag's reference part and pull the longest hex run out of it, so
    # `release-<sha>`, `v<sha>`, and bare `<sha>` all work. Case-insensitive.
    reference = image_tag.rsplit(":", 1)[-1].strip()
    hex_runs = re.findall(r"[0-9a-fA-F]{7,40}", reference)
    if not hex_runs and not isinstance(release_state, PublicReleaseState):
        raise DeployedShaError(
            f"release_state.image_tag {image_tag!r} carries no sha-shaped reference - "
            "cannot corroborate git_sha"
        )
    tag_sha = max(hex_runs, key=len).lower() if hex_runs else None
    if tag_sha and not deployed.lower().startswith(tag_sha):
        raise DeployedShaError(
            f"release_state is inconsistent: git_sha {deployed[:12]} does not match "
            f"image_tag {image_tag!r} - refusing to report a deploy state from a "
            "receipt that disagrees with itself"
        )

    info: dict[str, Any] = {
        "deployed_sha": deployed,
        "url": url,
        "image_tag": image_tag or None,
        "image_digest": (release_state.get("image_digest") or "").strip() or None,
        "deployed_at": (release_state.get("deployed_at") or "").strip() or None,
        "proves": "receipt",  # not the running binary; see module docstring
        "receipt_source": (
            "public_app_header" if isinstance(release_state, PublicReleaseState)
            else "authenticated_pulse"
        ),
    }
    if include_provenance:
        # Purely additive diagnostic, from the response already in hand. It is
        # read AFTER the receipt checks above so it can neither satisfy nor
        # break any of them.
        info["platform_runtime_provenance"] = provenance_report(release_state)
    try:
        _git("cat-file", "-e", f"{deployed}^{{commit}}")
        info["known_to_git"] = True
        info["deployed_subject"] = _git("log", "-1", "--format=%s", deployed)
        try:
            behind = _git("rev-list", "--count", f"{deployed}..origin/main")
            info["commits_on_main_not_deployed"] = int(behind)
            # A docs or CI commit CANNOT reach production: build-image.yml
            # builds only runtime changes, so nothing is built and nothing is
            # deployed. Counting those as drift makes the tool cry wolf
            # permanently -- on 2026-08-27 it reported 9 undeployed commits,
            # all of which touched zero build paths. Split the count so a real
            # gap is distinguishable from "nothing to deploy".
            undeployed = runtime_paths.runtime_commits(
                REPO_ROOT, deployed, "origin/main"
            )
            info["build_affecting_not_deployed"] = len(undeployed)
        except (DeployedShaError, runtime_paths.ClassifyError):
            info["commits_on_main_not_deployed"] = None
            info["build_affecting_not_deployed"] = None
    except DeployedShaError:
        # Not an error by itself: a shallow clone or a build from another
        # remote can serve a commit this checkout has never fetched.
        info["known_to_git"] = False
    return info


def contains(deployed: str, commit: str) -> bool:
    """True when ``commit`` is an ancestor of (or equal to) what is deployed."""
    resolved = _git("rev-parse", f"{commit}^{{commit}}")
    if _git("rev-parse", f"{deployed}^{{commit}}") == resolved:
        return True
    proc = subprocess.run(
        ["git", "merge-base", "--is-ancestor", resolved, deployed],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode == 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument(
        "--url", default=DEFAULT_URL,
        help=f"public app URL, or authenticated MCP URL (default {DEFAULT_URL})",
    )
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    ap.add_argument(
        "--assert-contains",
        metavar="COMMIT",
        help="exit 1 unless production is serving a build containing COMMIT",
    )
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument(
        "--report-provenance",
        action="store_true",
        help=(
            "also print the responding process's cached cloud-provenance "
            "observation from the SAME pulse response (record-only diagnostic; "
            "does not affect the exit code, and unknown is not a pass)"
        ),
    )
    args = ap.parse_args(argv)

    try:
        info = report(args.url, args.timeout, include_provenance=args.report_provenance)
    except DeployedShaError as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        else:
            print(f"cannot determine deployed sha: {exc}", file=sys.stderr)
        return 2

    if args.report_provenance and not args.json:
        prov = info.get("platform_runtime_provenance") or {}
        # One line, allowlisted values only, and the limits stated on it so the
        # CI log cannot be quoted as more than it is.
        print(
            "platform runtime provenance (record-only, one responding worker): "
            f"verdict={prov.get('verdict')} reason={prov.get('reason')} "
            f"observed={prov.get('observed')} enforced={prov.get('enforced')} "
            f"reported={prov.get('reported')}"
        )
        print(
            "  proves only that the process answering this probe holds that "
            "cached startup verdict -- not binary freshness, not the current "
            "container incarnation, not all workers, not attestation or custody"
        )

    if args.assert_contains:
        if not info["known_to_git"]:
            msg = (
                f"production serves {info['deployed_sha'][:12]}, which this checkout does not "
                "have - fetch it, then re-run"
            )
            if args.json:
                print(json.dumps({"ok": False, "error": msg, **info}, indent=2))
            else:
                print(f"UNKNOWN: {msg}", file=sys.stderr)
            return 2
        try:
            ok = contains(info["deployed_sha"], args.assert_contains)
        except DeployedShaError as exc:
            print(f"cannot compare: {exc}", file=sys.stderr)
            return 2
        info["asserted"] = args.assert_contains
        info["contains"] = ok
        # Not in the receipt's history. It is still SERVED when it descends
        # from the served sha and changes no runtime input since: build-image
        # never builds such a head (it cancels itself), so its sha can never
        # appear in a receipt, and the running image's tree already equals it
        # on every path production runs. Anything this cannot establish stays
        # "not shipped" -- the equivalence is a second way to pass, never a
        # softer reading of a failure.
        runtime_hits: list[str] | None = None
        if not ok:
            try:
                deployed_full = _git("rev-parse", f"{info['deployed_sha']}^{{commit}}")
                target_full = _git("rev-parse", f"{args.assert_contains}^{{commit}}")
                if runtime_paths.is_ancestor(REPO_ROOT, deployed_full, target_full):
                    runtime_hits = runtime_paths.runtime_changes(
                        REPO_ROOT, deployed_full, target_full
                    )
            except (DeployedShaError, runtime_paths.ClassifyError):
                runtime_hits = None
        equivalent = runtime_hits == []
        info["runtime_equivalent"] = equivalent
        info["undeployed_runtime_changes"] = runtime_hits
        passed = ok or equivalent
        if args.json:
            print(json.dumps({"ok": passed, **info}, indent=2))
        elif ok:
            print(
                f"SHIPPED (per receipt): production reports {info['deployed_sha'][:12]}, "
                f"which contains {args.assert_contains}"
            )
        elif equivalent:
            print(
                f"SHIPPED (runtime-equivalent, per receipt): production reports "
                f"{info['deployed_sha'][:12]}; {args.assert_contains} descends from it "
                "and changes no runtime input since (scripts/runtime_paths.py), so no "
                "image was built for it and the running image already serves it."
            )
        else:
            # Say WHICH kind of "not shipped" this is: runtime changes waiting
            # to deploy, or a commit that is not on the served line at all.
            if runtime_hits:
                shown = ", ".join(runtime_hits[:8])
                if len(runtime_hits) > 8:
                    shown += f" (+{len(runtime_hits) - 8} more)"
                detail = (
                    f"Runtime inputs changed since the served sha and are not "
                    f"deployed: {shown}. Merged is not deployed - check that the "
                    "merge raised a push event (docs/decisions/"
                    "ADR-004-merge-attribution-and-the-deploy-gap.md)."
                )
            else:
                detail = (
                    "Merged is not deployed - check that the merge raised a push "
                    "event (docs/decisions/"
                    "ADR-004-merge-attribution-and-the-deploy-gap.md)."
                )
            print(
                f"NOT SHIPPED: production serves {info['deployed_sha'][:12]} "
                f"({info.get('deployed_subject', '')!r}), which does NOT "
                f"contain {args.assert_contains}.\n" + detail,
                file=sys.stderr,
            )
        return 0 if passed else 1

    if args.json:
        print(json.dumps({"ok": True, **info}, indent=2))
    else:
        print(f"production serves {info['deployed_sha']}")
        if info.get("deployed_subject"):
            print(f"  {info['deployed_subject']}")
        behind = info.get("commits_on_main_not_deployed")
        build_gap = info.get("build_affecting_not_deployed")
        if behind and build_gap:
            print(
                f"  {build_gap} of {behind} undeployed commit(s) change runtime "
                f"inputs -- production IS behind"
            )
        elif behind:
            print(
                f"  {behind} commit(s) on origin/main are not in production, but "
                f"NONE changes a runtime input -- nothing to deploy"
            )
        elif behind == 0:
            print("  up to date with origin/main")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
