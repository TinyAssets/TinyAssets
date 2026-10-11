#!/usr/bin/env python3
"""Mechanical, shrink-only checks for the architecture Shape registry."""

from __future__ import annotations

import ast
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "docs/architecture.md"


@dataclass(frozen=True)
class Shape:
    paths: tuple[str, ...]
    old: tuple[tuple[str, str], ...] = ()  # (glob, regex); empty regex counts files
    forbid: tuple[str, ...] = ()
    ceiling: int = 0


# The sole shape inventory. Ceilings are measured on main at C1; lowering a
# count needs no table edit, and a cutover sets the ceiling to zero.
SHAPES: dict[str, Shape] = {
    "B1": Shape(
        ("starter/**", "packages/**"),
        (
            ("tinyassets/api/market.py", ""),
            ("tinyassets/automations.py", ""),
            ("tinyassets/agent_wakes.py", ""),
            ("tinyassets/request_continuations.py", ""),
            ("tinyassets/bid/**/*.py", ""),
            ("tinyassets/treasury/**/*.py", ""),
            ("domains/fantasy_daemon/**/*.py", ""),
            ("domains/research_probe/**/*.py", ""),
        ),
        ceiling=68,
    ),
    "B2": Shape(
        ("tinyassets/agent_loop/**", "tinyassets/model_call.py", "tinyassets/universe_tools.py"),
        (
            ("tinyassets/providers/*_provider.py", ""),
            ("tinyassets/**/*.py", r"\bcall_provider\("),
            ("tinyassets/**/*.py", r"\b(?:_call_writer|make_interactive_agent_turn)\("),
            ("tinyassets/custom_agents.py", ""),
            ("tinyassets/api/extensions.py", ""),
        ),
        ceiling=23,
    ),
    "S1": Shape(
        ("tinyassets/model_call.py", "tinyassets/providers/**"),
        (
            ("tinyassets/**/*.py", r"\bcall_provider\("),
            ("tinyassets/**/*.py", r"\bcall_with_policy(?:_sync)?\("),
            ("tinyassets/agent_turn_coordinator.py", r"\bTurnRequestBudget\("),
            ("tinyassets/background_served_provider.py", r"\bTurnRequestBudget\("),
            ("tinyassets/foreground_run_provider.py", r"\bTurnRequestBudget\("),
            ("tinyassets/providers/router.py", r"\bTurnRequestBudget\("),
            ("tinyassets/universe_intelligence.py", r"\bTurnRequestBudget\("),
        ),
        ceiling=27,
    ),
    "S2": Shape(
        ("tinyassets/control_plane/**",),
        (("tinyassets/**/*.py", r"\b(?:_call_writer|make_interactive_agent_turn)\("),),
        ceiling=5,
    ),
    "S3": Shape(
        ("tinyassets/agent_loop/**",),
        (
            ("tinyassets/providers/claude_provider.py", ""),
            ("tinyassets/providers/codex_provider.py", ""),
            ("tinyassets/providers/codex_app_server.py", ""),
        ),
        ceiling=3,
    ),
    "S4": Shape(
        ("tinyassets/providers/**", "tinyassets/connections/**"),
        (
            ("tinyassets/providers/router.py", r"\bFALLBACK_CHAINS\b"),
            ("tinyassets/onboarding/openai_device.py", ""),
        ),
        ceiling=4,
    ),
    "S5": Shape(
        ("tinyassets/agent_definition.py", "agents/**"),
        (
            ("tinyassets/custom_agents.py", ""),
            ("tinyassets/agent_interchange.py", ""),
            ("tinyassets/harness_settings.py", ""),
        ),
        ceiling=3,
    ),
    "S6": Shape(
        ("tinyassets/extension_manifest.py", "tinyassets/extension_state.py"),
        (("tinyassets/api/extensions.py", ""), ("tinyassets/ta_cli.py", r"workspace.?executable")),
        ceiling=1,
    ),
    "S7": Shape(
        ("tinyassets/universe_tools.py", "tinyassets/ta_cli.py"),
        (("tinyassets/engine_mcp_server.py", ""),),
        ceiling=1,
    ),
    "S8": Shape(("tinyassets/boxes/**",), (("tinyassets/**/role_*.py", ""),), ceiling=26),
    "S9": Shape(
        ("tinyassets/control_plane/**",),
        (
            ("tinyassets/scheduler.py", ""),
            ("tinyassets/cloud_automation_continuation.py", ""),
            ("tinyassets/branch_tasks.py", ""),
        ),
        ceiling=3,
    ),
    "S10": Shape(
        ("tinyassets/storage/**",),
        (("tinyassets/daemon_server.py", r"\bCREATE TABLE\b"),),
        ceiling=27,
    ),
    "S11": Shape(
        ("tinyassets/owner_door/**", "tinyassets/api/**"),
        forbid=(r"from\s+tinyassets\.model_door\b",),
    ),
    "S12": Shape(
        ("tinyassets/universe_server.py",), (("tinyassets/mcp_server.py", ""),), ceiling=1
    ),
    "S13": Shape(
        ("tinyassets/providers/owner_binding.py",),
        (("tinyassets/**/*.py", r"\b(?:reflexion|raptor|agentic_search|extract_learning)\b"),),
        ceiling=14,
    ),
    "S14": Shape(
        ("tinyassets/api/visibility.py", "tinyassets/api/universe.py"),
        forbid=(r"DEFAULT_CREATE_VISIBILITY\s*=\s*['\"]public['\"]",),
    ),
    "S15": Shape(
        ("tinyassets/boxes/**",),
        (
            ("tinyassets/host_pool/**/*.py", ""),
            ("desktop/**/*.py", ""),
            ("fantasy_daemon/**/*.py", ""),
        ),
        ceiling=122,
    ),
    "S16": Shape(
        ("tinyassets/onboarding/**", "mobile/**", "desktop-app/**"),
        forbid=(r"Route\s*\(\s*['\"]/mcp/app",),
    ),
    "S17": Shape(("tinyassets/okf/**",), (("tinyassets/daemon_brain.py", ""),), ceiling=1),
    "S18": Shape(
        ("starter/**", "packages/**"),
        (
            ("domains/fantasy_daemon/**/*.py", ""),
            ("domains/research_probe/**/*.py", ""),
            ("tinyassets/api/market.py", ""),
        ),
        ceiling=57,
    ),
    "S19": Shape(
        ("packaging/claude-plugin/build_plugin.py",),
        (("packaging/claude-plugin/plugins/tinyassets-universe-server/runtime/**", ""),),
        ceiling=6,
    ),
    "S20": Shape(("docs/architecture.md", "docs/decisions/**", "openspec/**")),
    "S21": Shape(
        ("openspec/changes/**",),
        (("openspec/changes/*/proposal.md", r"(?i)\bsuperseded by\b"),),
        ceiling=3,
    ),
    "S22": Shape(
        ("tinyassets/command_center/**",), (("tinyassets/universe_*.py", ""),), ceiling=11
    ),
    "S23": Shape(("domains/fantasy_daemon/state/book_state.py",)),
    "S24": Shape(("tinyassets/owner_door/**",)),
    "S25": Shape(
        ("tinyassets/universe_server.py", "tinyassets/onboarding/**", "tinyassets/api/**")
    ),
    "S26": Shape(("tinyassets/usage_policy.py",)),
    "S27": Shape(("deploy/**", "tinyassets/boxes/**")),
    "S28": Shape(
        ("tinyassets/broker/**", "tinyassets/credential_vault.py"),
        (("tinyassets/credential_vault.py", ""), ("tinyassets/broker/browser_vault.py", "")),
        ceiling=2,
    ),
    "N1": Shape(("tinyassets/owner_door/**", "tinyassets/onboarding/**")),  # inbound-door
    "N2": Shape(("tinyassets/control_plane/**",)),  # notify-path
    "N3": Shape(  # call-sites, opt-in-flags
        ("tinyassets/model_call.py", "tinyassets/agent_loop/**")
    ),
    "N4": Shape(("tinyassets/boxes/**",)),  # box-view
    "N5": Shape(("tinyassets/boxes/**",)),  # egress-gate
    "N6": Shape(("tinyassets/boxes/**",)),  # box-snapshot
    "N7": Shape(("tinyassets/broker/**",)),  # spend-path
    "N8": Shape(("tinyassets/boxes/**",)),  # host-paths
    "N9": Shape(("tinyassets/model_call.py",)),  # call-sites, media-receipt
    "N10": Shape(("tinyassets/boxes/**", "tinyassets/broker/**")),  # owner-policy-write
}

ENTRY_SYMBOLS: dict[str, str] = {
    "S1": (
        r"\b(?:(?:def\s+)?(?:call_provider|call_with_policy|model_call)\s*\("
        r"|class\s+TurnRequestBudget\b)"
    ),
    "S2": (
        r"\b(?:def\s+(?:session_start|start_agent|make_interactive_agent_turn)\s*\("
        r"|class\s+\w*Admission\b)"
    ),
    "S5": (
        r"\b(?:def\s+(?:load_agent_definition|agent_definition|stage_import|parse_settings)\b"
        r"|class\s+(?:SettingsSnapshot|AgentInterchange\w*)\b)"
    ),
    "S6": r"\bdef\s+(?:load_extension|build_revision)\b",
    "S8": r"\bdef\s+(?:box_access|admit_box)\b",
    "S12": r"\bdef\s+(?:register_mcp_handle|register_handle)\b",
    "S3": r"\bclass\s+(?:ClaudeProvider|CodexProvider|AppServerTurn)\b",
    "S9": r"\bclass\s+(?:Scheduler|CloudContinuation\w*|BranchTask)\b",
    "S15": r"\bclass\s+(?:HostPool\w*|HeartbeatLoop|BidPoller)\b",
    "S22": r"\b(?:class|def)\s+\w*Universe\w*\b",
}
# File probes also have a line ratchet: growing an existing file cannot hide
# behind an unchanged file count. Entry symbols above catch moved implementations.
LINE_CEILINGS = {"S3": 2434, "S5": 4552, "S9": 4638, "S15": 7547, "S22": 11095}
VENDOR_FILE_CEILING = 147
MIRROR_ROOT = "packaging/claude-plugin/plugins/tinyassets-universe-server/runtime/"
VENDOR_NAMES = re.compile(
    r"anthropic|chatgpt|claude|codex|gemini|grok|groq|kimi|mistral|ollama|openai|openrouter", re.I
)
DUAL_FLAGS = re.compile(
    r"\bTINYASSETS_(?:AGENT_LOOP|ALLOW_CLAUDE_SERVING|ASSIGNED_QUEUE_CONSUMER)\b"
)
PUBLIC_ROUTE = re.compile(
    r"(?:mount|route|Route|Mount|include_router|add_api_route|@app\.(?:get|post|put|patch|delete))"
    r"\s*\(\s*['\"](/[^'\"]*)"
)
PUBLIC_ROOTS = frozenset({"/", "/mcp", "/api", "/hooks/inbound", "/app"})
DOC_HOMES = (
    "docs/architecture.md",
    "docs/host-actions.md",
    "docs/decisions/",
    "docs/concerns/",
    "docs/reference/",
    "docs/getting-started/",
    "docs/ops/",
)


def registry_rows(text: str) -> dict[str, str]:
    rows: dict[str, str] = {}
    for line in text.splitlines():
        match = re.match(r"\| (B\d+|S\d+|N\d+) \|", line)
        if match:
            rows[match.group(1)] = line.split("|")[5].strip()
    return rows


def files(glob: str, root: Path = ROOT) -> list[Path]:
    if glob.endswith("/**"):
        glob += "/*"  # Path.glob('**') omits files on Python 3.11
    return sorted(
        path
        for path in root.glob(glob)
        if path.is_file()
        and "__pycache__" not in path.parts
        and path.suffix != ".pyc"
        and path.name != ".build.lock"
    )


def old_count(shape: Shape, root: Path = ROOT) -> int:
    total = 0
    for glob, pattern in shape.old:
        for path in files(glob, root):
            if shape is SHAPES["S19"]:
                counterpart = (
                    root / "tinyassets" / path.relative_to(root / MIRROR_ROOT / "tinyassets")
                    if path.is_relative_to(root / MIRROR_ROOT / "tinyassets")
                    else None
                )
                if counterpart is not None and counterpart.is_file():
                    continue
            total += (
                len(re.findall(pattern, path.read_text(encoding="utf-8", errors="replace")))
                if pattern
                else 1
            )
    return total


def old_lines(shape: Shape, root: Path = ROOT) -> int:
    paths = {path for glob, pattern in shape.old if not pattern for path in files(glob, root)}
    return sum(
        len(path.read_text(encoding="utf-8", errors="replace").splitlines()) for path in paths
    )


def vendor_file_count(root: Path = ROOT) -> int:
    return sum(
        bool(VENDOR_NAMES.search(path.read_text(encoding="utf-8", errors="replace")))
        for path in files("tinyassets/**/*.py", root)
    )


def deleted_doc_references(root: Path = ROOT) -> list[str]:
    base = _merge_base(root)
    if base is None:
        return []
    deleted = set(
        subprocess.check_output(
            ["git", "diff", "--diff-filter=D", "--name-only", base], cwd=root, text=True
        ).splitlines()
    )
    deleted = {path for path in deleted if path.startswith("docs/")}
    if not deleted:
        return []
    tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=root).decode().split("\0")
    problems = []
    for rel in tracked:
        path = root / rel
        if not path.is_file():
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except (UnicodeError, OSError):
            continue
        for ref in {item.rstrip(".,;") for item in re.findall(r"docs/[\w./-]+", source)} & deleted:
            problems.append(f"deleted-doc-reference: {rel} cites {ref}")
    return sorted(problems)


def recorded_shapes(
    source: str,
) -> dict[str, tuple[int, tuple[tuple[str, str], ...], tuple[str, ...]]]:
    """Read base pins and probes as syntax; never import code from the base tree."""
    tree = ast.parse(source)
    result = {}
    for assignment in tree.body:
        if not isinstance(assignment, ast.AnnAssign) or not isinstance(assignment.target, ast.Name):
            continue
        if assignment.target.id != "SHAPES" or not isinstance(assignment.value, ast.Dict):
            continue
        for key, value in zip(assignment.value.keys, assignment.value.values):
            if not isinstance(value, ast.Call) or not isinstance(value.func, ast.Name):
                continue
            if value.func.id != "Shape" or key is None:
                continue
            shape_id = ast.literal_eval(key)
            old = ast.literal_eval(value.args[1]) if len(value.args) > 1 else ()
            ceiling = next(
                (ast.literal_eval(kw.value) for kw in value.keywords if kw.arg == "ceiling"), 0
            )
            forbid = ast.literal_eval(value.args[2]) if len(value.args) > 2 else ()
            forbid = next(
                (ast.literal_eval(kw.value) for kw in value.keywords if kw.arg == "forbid"),
                forbid,
            )
            result[shape_id] = (ceiling, old, forbid)
    return result


def base_shape_problems(root: Path = ROOT) -> list[str]:
    merge_base = _merge_base(root)
    if merge_base is None:
        return []
    baseline = subprocess.run(
        ["git", "show", f"{merge_base}:scripts/check_shapes.py"],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if baseline.returncode:
        return []  # C1 introduces the table
    problems = []
    for shape_id, (ceiling, old, forbid) in recorded_shapes(baseline.stdout).items():
        if shape_id not in SHAPES:
            problems.append(f"{shape_id} registry-parity: existing shape removed")
            continue
        current = SHAPES[shape_id]
        if current.ceiling > ceiling:
            problems.append(f"{shape_id} ceiling: pin raised {ceiling} -> {current.ceiling}")
        if not set(old) <= set(current.old):
            problems.append(f"{shape_id} ceiling: old-path probe removed")
        if not set(forbid) <= set(current.forbid):
            problems.append(f"{shape_id} converged: forbidden-path probe removed")
    tree = ast.parse(baseline.stdout)
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if any(
            isinstance(target, ast.Name) and target.id == "VENDOR_FILE_CEILING"
            for target in node.targets
        ):
            prior = ast.literal_eval(node.value)
            if VENDOR_FILE_CEILING > prior:
                problems.append(
                    f"vendor-names ceiling: pin raised {prior} -> {VENDOR_FILE_CEILING}"
                )
        if any(
            isinstance(target, ast.Name) and target.id == "LINE_CEILINGS" for target in node.targets
        ):
            for shape_id, prior in ast.literal_eval(node.value).items():
                if LINE_CEILINGS.get(shape_id, 0) > prior:
                    problems.append(
                        f"{shape_id} line ceiling: pin raised {prior} -> {LINE_CEILINGS[shape_id]}"
                    )
    return problems


def registered_path(rel: str, glob: str) -> bool:
    if glob.endswith("/**"):
        return rel.startswith(glob[:-3] + "/")
    return PurePosixPath(rel).match(glob)


def _merge_base(root: Path) -> str | None:
    if not (root / ".git").exists():
        return None
    for ref in ("origin/main", "main", "HEAD~1"):
        if (
            subprocess.run(
                ["git", "rev-parse", "--verify", ref], cwd=root, capture_output=True
            ).returncode
            == 0
        ):
            return subprocess.check_output(
                ["git", "merge-base", "HEAD", ref], cwd=root, text=True
            ).strip()
    if os.environ.get("GITHUB_ACTIONS"):
        raise RuntimeError("shape guard needs main history; fetch-depth must be 0")
    return None  # linux_oracle constructs a single-commit snapshot repository


def git_added(root: Path = ROOT) -> set[str]:
    merge_base = _merge_base(root)
    if merge_base is None:
        return set()

    def git(*args: str) -> str:
        result = subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True, check=True
        )
        return result.stdout.strip()

    added = set(
        git("diff", "--no-renames", "--name-only", "--diff-filter=A", merge_base).splitlines()
    )
    return {path.replace("\\", "/") for path in added}


def git_added_lines(root: Path = ROOT) -> dict[str, str]:
    """Include committed, staged and working changes against main."""
    merge_base = _merge_base(root)
    if merge_base is None:
        return {}
    diff = subprocess.check_output(
        ["git", "diff", "--no-renames", "--unified=0", merge_base],
        cwd=root,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    lines: dict[str, list[str]] = {}
    current = ""
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            current = line[6:]
        elif line.startswith("+") and not line.startswith("+++") and current:
            lines.setdefault(current, []).append(line[1:])
    for rel in git_added(root):
        path = root / rel
        if path.is_file() and rel not in lines:
            lines[rel] = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return {path: "\n".join(source) for path, source in lines.items()}


def added_line_problems(changes: dict[str, str]) -> list[str]:
    problems = []
    for rel, source in changes.items():
        if not rel.startswith("tinyassets/") or not rel.endswith(".py"):
            continue
        if DUAL_FLAGS.search(source) or re.search(
            r"(?:getenv|environ|if)\b[^\n]*TINYASSETS_\w*(?:LOOP|LEGACY|V2|FALLBACK)\b", source
        ):
            problems.append(f"opt-in-flags: {rel} selects a second path")
        for route in PUBLIC_ROUTE.findall(source):
            if (
                route.split("/{", 1)[0] not in PUBLIC_ROOTS
                and not route.startswith("/.well-known/")
                and not any(route.startswith(root + "/") for root in PUBLIC_ROOTS)
            ):
                problems.append(f"public-routes: {rel} mounts {route}")
    return problems


def check(root: Path = ROOT, *, added: set[str] | None = None) -> list[str]:
    problems: list[str] = []
    rows = registry_rows((root / "docs/architecture.md").read_text(encoding="utf-8"))
    missing = sorted(rows.keys() - SHAPES.keys())
    extra = sorted(SHAPES.keys() - rows.keys())
    if missing or extra:
        problems.append(f"registry-parity: missing={missing}, extra={extra}")
    problems.extend(base_shape_problems(root))
    vendor_files = vendor_file_count(root)
    if vendor_files > VENDOR_FILE_CEILING:
        problems.append(f"vendor-names ceiling: {vendor_files} > {VENDOR_FILE_CEILING}")
    for shape_id, shape in SHAPES.items():
        if shape_id not in rows:
            continue
        count = old_count(shape, root)
        status = rows[shape_id].lower()
        if count > shape.ceiling:
            problems.append(f"{shape_id} ceiling: old path {count} > {shape.ceiling}")
        if shape_id in LINE_CEILINGS and old_lines(shape, root) > LINE_CEILINGS[shape_id]:
            problems.append(
                f"{shape_id} line ceiling: old path grew beyond {LINE_CEILINGS[shape_id]}"
            )
        if status == "converged" and count:
            problems.append(f"{shape_id} converged: {count} old-path occurrences remain")
        if status == "drifted" and shape.old and not count:
            problems.append(f"{shape_id} registry-parity: zero old paths; mark converged")
        for pattern in shape.forbid:
            for glob in shape.paths:
                for path in files(glob, root):
                    if re.search(pattern, path.read_text(encoding="utf-8", errors="replace")):
                        problems.append(
                            f"{shape_id} forbid: {path.relative_to(root)} matches {pattern}"
                        )
    if added is None:
        added = git_added(root)
    for rel in sorted(added):
        path = root / rel
        if not path.is_file():
            continue
        if rel.startswith("docs/") and not any(
            rel == home or rel.startswith(home) for home in DOC_HOMES
        ):
            problems.append(f"doc-homes: {rel}")
        if path.suffix not in {".py", ".js", ".ts"} or rel.startswith("tests/"):
            continue
        if rel.startswith(MIRROR_ROOT):
            continue
        source = path.read_text(encoding="utf-8", errors="replace")
        for shape_id, pattern in ENTRY_SYMBOLS.items():
            if not any(registered_path(rel, glob) for glob in SHAPES[shape_id].paths) and re.search(
                pattern, source
            ):
                problems.append(f"{shape_id} second-path: {rel} matches {pattern}")
    problems.extend(added_line_problems(git_added_lines(root)))
    for adr in files("docs/decisions/ADR-*.md", root):
        source = adr.read_text(encoding="utf-8")
        status = re.search(r"(?m)^## Status\s*\n\s*([^\n]+)", source)
        if not status or not status.group(1).startswith("Accepted"):
            continue
        cited = re.findall(r"openspec/(?:specs|changes)/[\w./-]+", source)
        if not any((root / ref.rstrip(".,);`")).exists() for ref in cited):
            problems.append(f"adr-has-spec: {adr.relative_to(root)}")
    for proposal in files("openspec/changes/*/proposal.md", root):
        source = proposal.read_text(encoding="utf-8")
        for name in re.findall(r"(?i)\bsupersedes?\s*:?\s*[`'\"]?([a-z][a-z0-9-]+)", source):
            if (root / "openspec/changes" / name).exists():
                problems.append(f"superseded-open: {proposal.parent.name} supersedes {name}")
    problems.extend(deleted_doc_references(root))
    return problems


def main() -> int:
    problems = check()
    for problem in problems:
        print(problem)
    if not problems:
        print("shape registry clean")
    return bool(problems)


if __name__ == "__main__":
    raise SystemExit(main())
