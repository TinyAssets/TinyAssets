"""Mutation probe for the authoring capability's test suite.

A green test suite is not evidence that its tests *can* fail. This probe breaks
one authoring invariant at a time and asserts that the test naming that invariant
goes red. A mutation that leaves the suite green is a finding: either the test is
vacuous, or the invariant is enforced somewhere else (defence in depth) — decide
which, and record it, rather than trusting the green run.

Committed because the lane's evidence claim cites it
(``openspec/changes/archive/2026-08-26-complete-independent-full-platform-targets`` task 4.2): an
un-runnable probe is an unverifiable claim. Cross-family review 2026-07-25 raised
exactly that.

Usage::

    python scripts/authoring_mutation_probe.py            # all mutations
    python scripts/authoring_mutation_probe.py --only publish-clean-test-gate
    python scripts/authoring_mutation_probe.py --list

Safety: each target file is restored from an in-memory copy in a ``finally``
block — no git operation is used, so a dirty tree is never reset. Run it on a
clean tree anyway, and check ``git status`` afterwards.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

SESSIONS = "tests/test_authoring_sessions.py"
SANDBOX = "tests/test_authoring_sandbox.py"
FILE_IO = "tests/test_authoring_file_io.py"
EVALUATOR = "tests/test_evaluator_authoring.py"
SCALE = "tests/test_authoring_scale.py"

# (name, file, find, replace, tests that must go red)
MUTATIONS: list[tuple[str, str, str, str, list[str]]] = [
    (
        "owner-scoping-in-sql",
        "tinyassets/authoring/store.py",
        '"SELECT * FROM authoring_sessions WHERE session_id = ? AND owner_id = ?",\n'
        "                (session_id, actor_id),\n            ).fetchone()\n"
        "        if row is None:\n            raise access_denied()\n"
        "        return self._session_from_row(row)",
        '"SELECT * FROM authoring_sessions WHERE session_id = ?",\n'
        "                (session_id,),\n            ).fetchone()\n"
        "        if row is None:\n            raise access_denied()\n"
        "        return self._session_from_row(row)",
        [SESSIONS, f"{SCALE}::test_sequential_cross_account_sessions_never_bleed"],
    ),
    (
        "draft-cas",
        "tinyassets/authoring/store.py",
        'if int(row["draft_version"]) != int(expected_version):\n'
        "                raise AuthoringConflictError(",
        "if False:\n                raise AuthoringConflictError(",
        [SESSIONS],
    ),
    (
        "atomic-batch-purity",
        "tinyassets/authoring/models.py",
        "    working = copy.deepcopy(definition)",
        "    working = definition",
        [f"{SESSIONS}::test_apply_operations_never_mutates_the_caller_document"],
    ),
    (
        "effects-simulated-by-default",
        "tinyassets/authoring/sandbox.py",
        '    return {\n        "simulated": True,\n        "would_execute": {',
        '    return {\n        "simulated": False,\n        "would_execute": {',
        [SANDBOX],
    ),
    (
        "secret-redaction",
        "tinyassets/authoring/sandbox.py",
        "def redact(payload: Any) -> Any:\n"
        '    """Structure-preserving redaction of secret-shaped values."""',
        "def redact(payload: Any) -> Any:\n"
        '    """Structure-preserving redaction of secret-shaped values."""\n'
        "    return payload",
        [SANDBOX],
    ),
    (
        "confirmation-single-use",
        "tinyassets/authoring/store.py",
        "AND consumed_at IS NULL AND expires_at > ?",
        "AND expires_at > ?",
        [SANDBOX],
    ),
    (
        "publish-requires-test",
        "tinyassets/authoring/service.py",
        '        blockers.append(ValidationIssue("publish.untested_version", "tests", detail))',
        "        pass",
        [f"{SESSIONS}::test_publish_requires_a_test_of_the_exact_draft_version"],
    ),
    (
        "publish-clean-test-gate",
        "tinyassets/authoring/service.py",
        'and event.payload.get("clean") is True',
        "",
        [
            f"{SANDBOX}::test_raising_code_node_fails_the_test_and_blocks_publication",
        ],
    ),
    (
        "publish-test-version-gate",
        "tinyassets/authoring/service.py",
        'if int(event.payload.get("draft_version", -1)) == session.draft_version\n'
        '        and event.payload.get("clean") is True',
        'if event.payload.get("clean") is True',
        [
            f"{SESSIONS}::"
            "test_a_recurring_definition_hash_cannot_reuse_an_older_version_test"
        ],
    ),
    (
        "publish-version-hash-check",
        "tinyassets/authoring/service.py",
        "    if int(expected_version) != session.draft_version:\n"
        "        raise AuthoringConflictError(\n"
        '            "the session advanced after review',
        "    if False:\n"
        "        raise AuthoringConflictError(\n"
        '            "the session advanced after review',
        [f"{SESSIONS}::test_publication_fails_when_the_draft_advanced_after_review"],
    ),
    (
        "publish-atomic-session-check",
        "tinyassets/authoring/store.py",
        "if int(session_row[\"draft_version\"]) != int(expected_draft_version):",
        "if False:",
        [
            f"{SESSIONS}::"
            "test_store_publish_is_atomic_against_a_concurrent_draft_advance"
        ],
    ),
    (
        "publish-duplicate-version",
        "tinyassets/authoring/store.py",
        "                if duplicate is not None:",
        "                if False:",
        [f"{SESSIONS}::test_the_same_draft_version_cannot_be_published_twice"],
    ),
    (
        "draft-secret-material-refusal",
        "tinyassets/authoring/models.py",
        "    issues.extend(_validate_no_secret_material(definition))",
        "    pass",
        [SANDBOX],
    ),
    (
        "destination-display-sanitize",
        "tinyassets/authoring/sandbox.py",
        '    return f"{split.scheme}://{host}{split.path}" if host else raw',
        "    return raw",
        [f"{SANDBOX}::test_destination_display_never_echoes_userinfo_or_query"],
    ),
    (
        "network-capable-source-refusal",
        "tinyassets/authoring/service.py",
        "        network_imports = authoring_sandbox.network_capable_imports(source)",
        "        network_imports = []",
        [
            f"{SANDBOX}::"
            "test_network_capable_draft_source_is_refused_before_execution"
        ],
    ),
    (
        "draft-actually-executes",
        "tinyassets/authoring/service.py",
        "    executions, execution_budget_error = _execute_draft_nodes(\n"
        "        session, ledger=ledger, policy=policy, bound=bound\n    )",
        "    executions, execution_budget_error = ([], None)",
        [SANDBOX],
    ),
    (
        "retention-enforced",
        "tinyassets/authoring/service.py",
        "    if moment >= boundary:\n        raise AuthoringValidationError([",
        "    if False:\n        raise AuthoringValidationError([",
        [f"{SESSIONS}::test_an_expired_draft_is_readable_but_not_writable"],
    ),
    (
        "router-lookup-error-guard",
        "tinyassets/authoring/models.py",
        "        except (ValueError, LookupError, TypeError) as exc:",
        "        except (ValueError, KeyError, TypeError) as exc:",
        [f"{SESSIONS}::test_router_returns_json_for_an_out_of_range_list_index"],
    ),
    (
        "filename-traversal",
        "tinyassets/authoring/io.py",
        '    text = str(raw or "").replace("\\\\", "/").strip()',
        '    return str(raw or "") or fallback\n'
        '    text = str(raw or "").replace("\\\\", "/").strip()',
        [FILE_IO],
    ),
    (
        "manifest-media-type-gate",
        "tinyassets/authoring/io.py",
        "            if declaration.media_types and media_type not in declaration.media_types:",
        "            if False:",
        [FILE_IO],
    ),
    (
        "handle-expiry",
        "tinyassets/authoring/store.py",
        '        if float(row["expires_at"]) <= moment:\n            raise access_denied()',
        "        if False:\n            raise access_denied()",
        [FILE_IO],
    ),
    (
        "handle-session-scope",
        "tinyassets/authoring/store.py",
        '        if session_id and row["session_id"] != session_id:',
        "        if False:",
        [f"{FILE_IO}::test_handle_ids_are_unguessable_and_scoped_to_their_session"],
    ),
    (
        "isolation-honesty",
        "tinyassets/authoring/sandbox.py",
        '        "os_isolated": False,',
        '        "os_isolated": bool(probe.get("box_available")),',
        [f"{SANDBOX}::test_positive_host_probe_cannot_admit_authoring_os_isolation"],
    ),
    (
        "service-isolation-gate",
        "tinyassets/authoring/service.py",
        "    isolation = authoring_sandbox.require_isolation(policy)",
        "    isolation = authoring_sandbox.isolation_report()",
        [
            f"{SANDBOX}::"
            "test_positive_host_probe_denies_os_isolation_before_draft_execution"
        ],
    ),
    (
        "evaluator-chain-coverage",
        "tinyassets/authoring/models.py",
        "                issues.append(ValidationIssue(\n"
        '                    "evaluator.chain_uncovered_verdict",',
        "                pass\n            if False:\n"
        "                issues.append(ValidationIssue(\n"
        '                    "evaluator.chain_uncovered_verdict",',
        [EVALUATOR],
    ),
    (
        "router-authoring-arm",
        "tinyassets/api/extensions.py",
        "    authoring_handler = _AUTHORING_ACTIONS.get(action)",
        "    authoring_handler = None",
        [f"{SESSIONS}::test_router_roundtrip_start_edit_test_publish"],
    ),
    (
        "scope-derivation",
        "tinyassets/auth/provider.py",
        "    extension_writes.update(_AUTHORING_WRITE_ACTIONS)",
        "    pass",
        [f"{SESSIONS}::test_authoring_actions_are_listed_and_scope_derived"],
    ),
]

#: Mutations that are known to leave the suite green *because the invariant is
#: still enforced elsewhere*. Verified, not waved past — keep the reason current.
KNOWN_DEFENCE_IN_DEPTH: dict[str, str] = {
    "per-run-confirmation-early-raise": (
        "removing the early `if not token: raise` still refuses, because "
        "store.consume_confirmation matches no row for an empty token"
    ),
}


def run_tests(tests: list[str]) -> bool:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-x", "-q", "-p", "no:randomly", *tests],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", action="append", default=[], help="mutation name(s)")
    parser.add_argument("--list", action="store_true", help="list mutation names")
    args = parser.parse_args()

    if args.list:
        for name, rel, *_ in MUTATIONS:
            print(f"{name:38s} {rel}")
        return 0

    selected = [m for m in MUTATIONS if not args.only or m[0] in args.only]
    if not selected:
        print(f"no mutation matched {args.only}", file=sys.stderr)
        return 2

    findings: list[str] = []
    for name, rel, old, new, tests in selected:
        path = REPO / rel
        original = path.read_text(encoding="utf-8")
        if old not in original:
            findings.append(f"STALE ANCHOR: {name} ({rel}) — the code moved")
            print(f"[stale] {name}: anchor not found in {rel}")
            continue
        path.write_text(original.replace(old, new, 1), encoding="utf-8")
        try:
            passed = run_tests(tests)
        finally:
            path.write_text(original, encoding="utf-8")
        if passed:
            findings.append(f"GREEN UNDER MUTATION: {name}")
            print(f"[FINDING] {name}: suite stayed green with the invariant broken")
        else:
            print(f"[ok] {name}: went red")

    print("\n=== summary ===")
    if findings:
        for finding in findings:
            print(finding)
        print(
            "\nA GREEN result means the named test does not constrain the invariant, "
            "or another layer enforces it. Decide which and record it; do not "
            "assume the test is fine."
        )
        return 1
    print(f"every mutation went red ({len(selected)} checked)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
