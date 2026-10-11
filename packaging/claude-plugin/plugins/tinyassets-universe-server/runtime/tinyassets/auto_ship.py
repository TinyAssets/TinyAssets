"""Auto-ship dry-run shipper — PR #198 Phase 1.

Pure-Python validator that runs a coding_packet (or constructed ship_request)
through the auto-ship safety envelope from
``openspec/specs/evaluation-outcomes-and-attribution/spec.md`` §5.2, §6.1, §6.2, §6.3 and returns
a structured ship_decision explaining whether the packet WOULD ship, why,
and what rollback handle would be used.

Phase 1 contract: NO repo writes, NO IO, NO network. Validator only.
``ship_status`` is always ``"skipped"`` in this phase — the ``would_open_pr``
field tells callers whether the packet PASSED the envelope. Phase 2 (PR-open
mode) wires the actual repo operation behind a flag; Phase 3 enables
auto-merge for narrow canary classes after one successful PR-open round.

Pairs with:
- ``openspec/specs/evaluation-outcomes-and-attribution/spec.md`` (PR #211) — defines what KEEP +
  evidence-bundle-complete actually mean. The validator below enforces the
  envelope; the rubric defines what packets ARE before the envelope sees them.
- ``openspec/specs/evaluation-outcomes-and-attribution/spec.md`` (PR #198) — milestone spec
  this module implements one phase of.

The shipper is the LAST enforcement layer. The release_gate may recommend
auto-ship; the shipper is what actually decides whether the recommendation
is safe enough to act on. Every check below is structural — no LLM in the
loop, no prose interpretation.
"""

from __future__ import annotations

import re
from typing import Any

# ── Envelope constants from auto-ship-canary-v0.md §6 ─────────────────────

#: Ship classes allowed for v0 (§6.2). Any other class is blocked.
ALLOWED_SHIP_CLASSES: frozenset[str] = frozenset({
    "docs_canary",
    "metadata_canary",
    "test_fixture_canary",
})

#: Path prefixes a v0 canary patch may touch (§6.2). Forbidden paths win on conflict.
ALLOWED_PATH_PREFIXES: tuple[str, ...] = (
    "docs/autoship-canaries/",
    "tinyassets/autoship_canaries/",
    "tests/fixtures/autoship_canaries/",
)

#: Path prefixes that must NEVER auto-ship (§6.2). Even matches against an allowed
#: prefix get rejected if any forbidden prefix also matches — defense in depth.
FORBIDDEN_PATH_PREFIXES: tuple[str, ...] = (
    "tinyassets/runtime/",
    "tinyassets/providers/",
    "tinyassets/api/",
    "tinyassets/wiki/",
    "tinyassets/dispatcher/",
    ".github/",
    "scripts/deploy/",
    "migrations/",
)

#: Forbidden substring patterns in a path (§6.2). Case-insensitive match.
FORBIDDEN_PATH_SUBSTRINGS: tuple[str, ...] = (
    ".env",
    "secret",
    "auth",
)

#: Required packet fields (§6.1) that must hold a non-empty value.
REQUIRED_FIELDS_NONEMPTY: tuple[str, ...] = (
    "release_gate_result",
    "ship_class",
    "child_keep_reject_decision",
    "child_score",
    "risk_level",
    "blocked_execution_record",
    "stable_evidence_handle",
    "automation_claim_status",
    "rollback_plan",
)

#: Allowed values for automation_claim_status (§6.1).
ALLOWED_AUTOMATION_CLAIM_STATUS: frozenset[str] = frozenset({
    "child_attached_with_handle",
    "parent_completed_with_handle",
    "direct_packet_with_handle",
})

#: Allowed coding_packet.status values for auto-ship (§6.1).
ALLOWED_CODING_PACKET_STATUSES: frozenset[str] = frozenset({
    "KEEP_READY",
    "AUTO_SHIP_READY",
})

#: Minimum child_score for KEEP (§6.1 + rubric §5).
KEEP_SCORE_MIN: float = 9.0

#: Maximum diff size in bytes for a v0 canary patch.
#: Tight bound on purpose — canary patches should be a few lines, not a refactor.
DIFF_SIZE_BYTES_MAX: int = 8 * 1024  # 8 KB

#: Auto-ship rubric mode flag. "warn" (default): compute the coding-packet
#: rubric and annotate the decision with `rubric_warnings`, but NEVER block.
#: "enforce": promote rubric-only violations to blocking. "off": skip entirely.
#: Producers + tests must populate the rubric fields BEFORE flipping to enforce
#: (see openspec/specs/evaluation-outcomes-and-attribution/spec.md S2).
RUBRIC_MODE_FLAG: str = "TINYASSETS_AUTO_SHIP_RUBRIC_MODE"

#: Rubric rule IDs that validate_ship_request does NOT already enforce. Scoped
#: deliberately narrow so checks the envelope already covers are NOT
#: double-reported in enforce mode (Codex review 2026-06-25). Excluded for now:
#: `child_output_evidence_missing` (overlaps the envelope `stable_evidence_handle`
#: check — only its `child_candidate_patch_packet` half is genuinely new) and
#: `contradictory_child_claim` (overlaps `automation_claim_status_not_allowed`).
#: Both can rejoin once they carry de-overlap logic. The set can widen as
#: producers populate the corresponding fields.
_RUBRIC_ONLY_RULE_IDS: frozenset[str] = frozenset({
    "release_evidence_bundle_incomplete",
    "child_run_not_completed_for_keep",
})


def _rubric_mode() -> str:
    """Auto-ship rubric mode: 'off' | 'warn' (default) | 'enforce'."""
    import os

    mode = os.environ.get(RUBRIC_MODE_FLAG, "warn").strip().lower()
    return mode if mode in {"off", "warn", "enforce"} else "warn"


def _rubric_only_violations(packet: dict) -> list:
    """Rubric-only violations not already covered by the envelope checks.

    Fail-open by design: a rubric bug must NEVER break the live gate, so any
    exception yields an empty list (the envelope checks still stand on their own).
    """
    try:
        from tinyassets.coding_packet_rubric import validate_coding_packet_rubric

        rubric = validate_coding_packet_rubric(packet)
        return [
            v
            for v in rubric.get("violations", [])
            if v.get("rule_id") in _RUBRIC_ONLY_RULE_IDS
        ]
    except Exception:  # noqa: BLE001 — fail-open: never break the gate on a rubric bug
        return []


#: Coding-lane trajectory (path-quality) eval mode flag. "warn" (default):
#: compute the trajectory eval and annotate the decision with
#: `trajectory_warnings` on a channel SEPARATE from `rubric_warnings`, but NEVER
#: block. "off": skip entirely. "enforce": promote a CONCLUSIVE path-quality
#: FAIL to a blocking violation on its own `trajectory_path_unsound` channel —
#: gating on execution-path soundness, a distinct axis from the output rubric, so
#: it never double-reports a rubric block rule (the S2 de-overlap lesson). The
#: enforce flip is host-gated + warn-data-gated (see
#: openspec/specs/evaluation-outcomes-and-attribution/spec.md S3).
TRAJECTORY_MODE_FLAG: str = "TINYASSETS_AUTO_SHIP_TRAJECTORY_MODE"


def _trajectory_mode() -> str:
    """Coding-lane trajectory eval mode: 'off' | 'warn' (default) | 'enforce'.

    Unknown values resolve to 'warn'. 'enforce' promotes a CONCLUSIVE
    path-quality failure to a blocking violation on its own channel; the flip is
    host-gated + warn-data-gated (see the design note), so the default 'warn'
    never blocks until someone deliberately sets enforce.
    """
    import os

    mode = os.environ.get(TRAJECTORY_MODE_FLAG, "warn").strip().lower()
    return mode if mode in {"off", "warn", "enforce"} else "warn"


def _trajectory_warnings(packet: dict) -> list:
    """Path-quality warnings for the trajectory warn period.

    Surfaces one record per applicable failing check, but only when the eval is
    CONCLUSIVE and its verdict is ``fail`` — i.e. exactly what a future enforce
    mode would block — so the warn ledger measures the real prospective
    block-rate. Inconclusive (skip) or passing trajectories yield nothing.

    Fail-open by design: any exception yields an empty list, so a trajectory-eval
    bug can never break the live ship gate.
    """
    try:
        from tinyassets.evaluation.coding_process import (
            coding_trajectory_from_packet,
            evaluate_coding_trajectory,
        )

        result = evaluate_coding_trajectory(coding_trajectory_from_packet(packet))
        if not result.conclusive or result.verdict != "fail":
            return []
        return [
            {
                "event": "trajectory_warning",
                "check": check.name,
                "score": check.score,
                "observation": check.observation,
            }
            for check in result.checks
            if check.applicable and not check.passed
        ]
    except Exception:  # noqa: BLE001 — fail-open: never break the gate on an eval bug
        return []


#: Heuristic regex patterns flagging probable secrets in diff content.
#: Conservative — false positives are tolerable; false negatives ship secrets.
SECRET_REGEX_PATTERNS: tuple[str, ...] = (
    r"sk-[A-Za-z0-9_-]{20,}",          # OpenAI / Anthropic style
    r"AIza[A-Za-z0-9_-]{35}",          # Google API
    r"ghp_[A-Za-z0-9]{36}",            # GitHub PAT (classic)
    r"github_pat_[A-Za-z0-9_]{22,}",   # GitHub PAT (fine-grained)
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",  # PEM private keys
    r"xox[baprs]-[A-Za-z0-9-]{10,}",   # Slack tokens
    r"AKIA[0-9A-Z]{16}",               # AWS access key id
)


def _normalize_path(path: str) -> str:
    """Cheap path normalization — strip literal leading ``./``, collapse ``//``,
    NO symlink resolution (caller's responsibility if needed).

    Defense against trivially-encoded forbidden paths like ``./tinyassets/api/x``
    or ``tinyassets//api/x``. Real symlink resolution happens upstream — this is
    just structural normalization. Note: uses ``removeprefix`` (Python 3.9+)
    rather than ``lstrip`` so paths like ``.github/x`` (which start with a dot
    but not the literal ``./`` prefix) are preserved verbatim — otherwise the
    forbidden-prefix check would miss them.
    """
    if not path:
        return ""
    p = path
    # Strip literal "./" prefix if present (do not strip ".github/" -> "github/").
    while p.startswith("./"):
        p = p[2:]
    while "//" in p:
        p = p.replace("//", "/")
    return p


def _path_violations(changed_paths: list[str]) -> list[dict[str, Any]]:
    """Return one violation record per (path, rule) violation. Empty if clean."""
    out: list[dict[str, Any]] = []
    if not isinstance(changed_paths, list):
        return [{
            "rule_id": "changed_paths_not_list",
            "field": "changed_paths",
            "severity": "block",
            "message": (
                f"changed_paths must be a list of strings; got {type(changed_paths).__name__}"
            ),
        }]
    if not changed_paths:
        out.append({
            "rule_id": "changed_paths_empty",
            "field": "changed_paths",
            "severity": "block",
            "message": "changed_paths is empty — auto-ship requires at least one path",
        })
    for raw_path in changed_paths:
        if not isinstance(raw_path, str) or not raw_path:
            out.append({
                "rule_id": "changed_path_invalid",
                "field": "changed_paths",
                "severity": "block",
                "message": f"changed_paths entry is not a non-empty string: {raw_path!r}",
            })
            continue
        normalized = _normalize_path(raw_path)
        lower = normalized.lower()
        # Forbidden first — wins even if also matches an allowed prefix.
        for forbidden in FORBIDDEN_PATH_PREFIXES:
            if normalized.startswith(forbidden):
                out.append({
                    "rule_id": "changed_path_forbidden_prefix",
                    "field": "changed_paths",
                    "severity": "block",
                    "message": (
                        f"path {raw_path!r} starts with forbidden prefix "
                        f"{forbidden!r} — auto-ship cannot touch this surface"
                    ),
                })
                break
        for sub in FORBIDDEN_PATH_SUBSTRINGS:
            if sub in lower:
                out.append({
                    "rule_id": "changed_path_forbidden_substring",
                    "field": "changed_paths",
                    "severity": "block",
                    "message": (
                        f"path {raw_path!r} contains forbidden substring "
                        f"{sub!r} (env / secret / auth surface)"
                    ),
                })
                break
        # Allowed-prefix check (only if not already forbidden).
        if not any(normalized.startswith(p) for p in ALLOWED_PATH_PREFIXES):
            out.append({
                "rule_id": "changed_path_not_allowed",
                "field": "changed_paths",
                "severity": "block",
                "message": (
                    f"path {raw_path!r} is not under any allowed canary prefix "
                    f"({', '.join(ALLOWED_PATH_PREFIXES)})"
                ),
            })
    return out


def _diff_violations(diff: str) -> list[dict[str, Any]]:
    """Diff-size, binary, and secret checks (§6.3)."""
    out: list[dict[str, Any]] = []
    if not isinstance(diff, str):
        return [{
            "rule_id": "diff_not_string",
            "field": "diff",
            "severity": "block",
            "message": f"diff must be a string; got {type(diff).__name__}",
        }]
    if len(diff.encode("utf-8")) > DIFF_SIZE_BYTES_MAX:
        out.append({
            "rule_id": "diff_too_large",
            "field": "diff",
            "severity": "block",
            "message": (
                f"diff is {len(diff.encode('utf-8'))} bytes; v0 canary cap is "
                f"{DIFF_SIZE_BYTES_MAX} bytes"
            ),
        })
    if "\0" in diff:
        out.append({
            "rule_id": "diff_binary_content",
            "field": "diff",
            "severity": "block",
            "message": "diff contains a null byte — auto-ship rejects binary content",
        })
    for pat in SECRET_REGEX_PATTERNS:
        if re.search(pat, diff):
            out.append({
                "rule_id": "diff_secret_pattern",
                "field": "diff",
                "severity": "block",
                "message": (
                    "diff matched a heuristic secret pattern — auto-ship "
                    "blocks. False positive? Manually review before shipping."
                ),
            })
            # Don't break — we want all matches recorded for audit.
    return out


def _coding_packet_status(packet: dict[str, Any]) -> Any:
    """Extract coding_packet.status from supported packet shapes."""
    coding_packet = packet.get("coding_packet")
    if isinstance(coding_packet, dict):
        status = coding_packet.get("status")
        if status not in (None, ""):
            return status

    status = packet.get("coding_packet_status")
    if status not in (None, ""):
        return status

    source_packet = packet.get("source_packet")
    if isinstance(source_packet, dict):
        source_coding_packet = source_packet.get("coding_packet")
        if isinstance(source_coding_packet, dict):
            status = source_coding_packet.get("status")
            if status not in (None, ""):
                return status

    return None


def validate_ship_request(packet: dict[str, Any]) -> dict[str, Any]:
    """Run the auto-ship safety envelope on ``packet``. Returns a ship_decision dict.

    Pure: no IO, no network, no repo writes. Caller decides what to do with
    the decision.

    Decision shape::

        {
            "ship_status": "skipped",        # always "skipped" in Phase 1 (dry-run)
            "would_open_pr": bool,           # True iff envelope passed
            "validation_result": "passed" | "blocked",
            "violations": list[dict],        # rule violations; empty when passed
            "rollback_handle": str | None,   # "revert:<plan>" when passed; None when blocked
            "dry_run": True,                 # always True in Phase 1
        }

    Each violation record::

        {
            "rule_id": str,        # short stable identifier for the rule
            "field": str | None,   # packet field that violated, when applicable
            "severity": "block",   # all envelope violations are blocking in v0
            "message": str,        # human-readable explanation
        }

    The validator is conservative: it BLOCKS on any unmet rule. There is no
    "warning" tier in v0 — anything that doesn't pass cannot ship.
    """
    if not isinstance(packet, dict):
        return {
            "ship_status": "skipped",
            "would_open_pr": False,
            "validation_result": "blocked",
            "violations": [{
                "rule_id": "packet_not_dict",
                "field": None,
                "severity": "block",
                "message": (
                    f"packet must be a dict; got {type(packet).__name__}"
                ),
            }],
            "rollback_handle": None,
            "dry_run": True,
        }

    violations: list[dict[str, Any]] = []

    # §6.1 — required packet fields must be present + non-empty
    for field in REQUIRED_FIELDS_NONEMPTY:
        value = packet.get(field)
        if value is None or value == "":
            violations.append({
                "rule_id": f"required_field_missing:{field}",
                "field": field,
                "severity": "block",
                "message": f"packet is missing required field {field!r}",
            })

    # §6.1 — field-value gates
    coding_packet_status = _coding_packet_status(packet)
    if coding_packet_status in (None, ""):
        violations.append({
            "rule_id": "coding_packet_status_missing",
            "field": "coding_packet.status",
            "severity": "block",
            "message": (
                "coding_packet.status is required and must prove KEEP_READY "
                "or AUTO_SHIP_READY before auto-ship"
            ),
        })
    elif coding_packet_status not in ALLOWED_CODING_PACKET_STATUSES:
        violations.append({
            "rule_id": "coding_packet_status_not_allowed",
            "field": "coding_packet.status",
            "severity": "block",
            "message": (
                f"coding_packet.status {coding_packet_status!r} not in allowlist "
                f"({', '.join(sorted(ALLOWED_CODING_PACKET_STATUSES))})"
            ),
        })

    if packet.get("release_gate_result") not in (None, ""):
        if packet["release_gate_result"] != "APPROVE_AUTO_SHIP":
            violations.append({
                "rule_id": "release_gate_not_approved",
                "field": "release_gate_result",
                "severity": "block",
                "message": (
                    f"release_gate_result must be 'APPROVE_AUTO_SHIP'; got "
                    f"{packet['release_gate_result']!r}"
                ),
            })

    if packet.get("child_keep_reject_decision") not in (None, ""):
        if packet["child_keep_reject_decision"] != "KEEP":
            violations.append({
                "rule_id": "child_decision_not_keep",
                "field": "child_keep_reject_decision",
                "severity": "block",
                "message": (
                    f"child_keep_reject_decision must be 'KEEP'; got "
                    f"{packet['child_keep_reject_decision']!r}"
                ),
            })

    if "child_score" in packet:
        score = packet["child_score"]
        if not isinstance(score, (int, float)):
            violations.append({
                "rule_id": "child_score_not_numeric",
                "field": "child_score",
                "severity": "block",
                "message": f"child_score must be numeric; got {type(score).__name__}",
            })
        elif score < KEEP_SCORE_MIN:
            violations.append({
                "rule_id": "child_score_below_threshold",
                "field": "child_score",
                "severity": "block",
                "message": (
                    f"child_score {score} below KEEP threshold {KEEP_SCORE_MIN}"
                ),
            })

    if "risk_level" in packet and packet["risk_level"] != "low":
        violations.append({
            "rule_id": "risk_level_not_low",
            "field": "risk_level",
            "severity": "block",
            "message": (
                f"risk_level must be 'low' for v0 canary; got {packet['risk_level']!r}"
            ),
        })

    if "blocked_execution_record" in packet and packet["blocked_execution_record"] != {}:
        violations.append({
            "rule_id": "blocked_execution_record_nonempty",
            "field": "blocked_execution_record",
            "severity": "block",
            "message": (
                "blocked_execution_record must be empty dict — packet has unresolved "
                "execution blocks"
            ),
        })

    if packet.get("automation_claim_status") not in (None, ""):
        if packet["automation_claim_status"] not in ALLOWED_AUTOMATION_CLAIM_STATUS:
            violations.append({
                "rule_id": "automation_claim_status_not_allowed",
                "field": "automation_claim_status",
                "severity": "block",
                "message": (
                    f"automation_claim_status {packet['automation_claim_status']!r} "
                    f"not in allowlist ({', '.join(sorted(ALLOWED_AUTOMATION_CLAIM_STATUS))})"
                ),
            })

    # §6.2 — ship class
    if packet.get("ship_class") not in (None, ""):
        if packet["ship_class"] not in ALLOWED_SHIP_CLASSES:
            violations.append({
                "rule_id": "ship_class_not_allowed",
                "field": "ship_class",
                "severity": "block",
                "message": (
                    f"ship_class {packet['ship_class']!r} not in v0 allowlist "
                    f"({', '.join(sorted(ALLOWED_SHIP_CLASSES))})"
                ),
            })

    # §6.2 — path checks
    violations.extend(_path_violations(packet.get("changed_paths", [])))

    # §6.3 — diff-content checks
    violations.extend(_diff_violations(packet.get("diff", "")))

    # §6.4 — coding-packet rubric (warn-only by default; enforce via
    # TINYASSETS_AUTO_SHIP_RUBRIC_MODE). Warn annotates `rubric_warnings` without
    # blocking; enforce promotes rubric-only violations to blocking. Fail-open.
    rubric_warnings: list = []
    _mode = _rubric_mode()
    if _mode != "off":
        _rubric_only = _rubric_only_violations(packet)
        if _rubric_only:
            if _mode == "enforce":
                violations.extend(_rubric_only)
            else:  # warn
                rubric_warnings = _rubric_only

    # §6.5 — coding-lane trajectory (path-quality) eval. Warn annotates
    # `trajectory_warnings` on a channel SEPARATE from `rubric_warnings` and
    # never blocks; enforce promotes a CONCLUSIVE path-quality FAIL to one
    # blocking violation on its own `trajectory_path_unsound` channel — a
    # distinct axis from the output rubric (execution-path soundness vs claim
    # validity), so it never double-reports a rubric block rule. Mirrors the
    # §6.4 warn/enforce shape. Fail-open.
    trajectory_warnings: list = []
    _traj_mode = _trajectory_mode()
    if _traj_mode != "off":
        _traj_records = _trajectory_warnings(packet)
        if _traj_records:
            if _traj_mode == "enforce":
                violations.append({
                    "severity": "block",
                    "rule_id": "trajectory_path_unsound",
                    "message": (
                        "coding trajectory eval is a conclusive path-quality FAIL: "
                        + "; ".join(
                            f"{r['check']}={r['score']:.2f} ({r['observation']})"
                            for r in _traj_records
                        )
                    ),
                })
            else:  # warn
                trajectory_warnings = _traj_records

    # Decision
    if violations:
        return {
            "ship_status": "skipped",
            "would_open_pr": False,
            "validation_result": "blocked",
            "violations": violations,
            "rollback_handle": None,
            "dry_run": True,
            "rubric_warnings": rubric_warnings,
            "trajectory_warnings": trajectory_warnings,
        }

    # Passed — compute rollback handle from packet
    rollback_plan = packet.get("rollback_plan", "")
    handle_target = (
        rollback_plan
        if rollback_plan and rollback_plan != "auto"
        else packet.get("stable_evidence_handle", "unknown")
    )
    return {
        "ship_status": "skipped",  # Phase 1 is dry-run only
        "would_open_pr": True,
        "validation_result": "passed",
        "violations": [],
        "rollback_handle": f"revert:{handle_target}",
        "dry_run": True,
        "rubric_warnings": rubric_warnings,
        "trajectory_warnings": trajectory_warnings,
    }
