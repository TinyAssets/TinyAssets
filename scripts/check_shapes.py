#!/usr/bin/env python3
"""Mechanical, shrink-only checks for the architecture Shape registry."""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import tomllib
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
            ("tinyassets/ta_capabilities.py", r"\bdef _turn_reviewer\b"),
            ("tinyassets/providers/router.py", r"chain = \[served_authority\.provider\]"),
        ),
        ceiling=1,
    ),
    "S2": Shape(
        ("tinyassets/control_plane/**",),
        (("tinyassets/**/*.py", r"\b(?:_call_writer|make_interactive_agent_turn)\("),),
        ceiling=5,
    ),
    "S3": Shape(
        ("tinyassets/agent_loop/**",),
        (
            ("tinyassets/providers/codex_provider.py", ""),
            ("tinyassets/providers/codex_app_server.py", ""),
        ),
        ceiling=1,
    ),
    "S4": Shape(
        ("tinyassets/providers/**", "tinyassets/connections/**"),
        (
            ("tinyassets/providers/codex_provider.py", ""),
            ("tinyassets/providers/codex_app_server.py", ""),
            ("tinyassets/providers/router.py", r"\bFALLBACK_CHAINS\b"),
        ),
        ceiling=3,
    ),
    "S5": Shape(
        ("tinyassets/agent_definition.py", "agents/**"),
        (
            ("tinyassets/custom_agents.py", ""),
            ("tinyassets/agent_interchange.py", ""),
            ("tinyassets/harness_settings.py", ""),
        ),
        ceiling=0,
    ),
    "S6": Shape(
        ("tinyassets/extension_manifest.py", "tinyassets/extension_state.py"),
        (("tinyassets/api/extensions.py", ""), ("tinyassets/ta_cli.py", r"workspace.?executable")),
        ceiling=1,
    ),
    "S7": Shape(
        ("tinyassets/universe_tools.py", "tinyassets/ta_cli.py",
         "tinyassets/control_plane/registration.py"),
        (("tinyassets/engine_mcp_server.py", ""),
         ("tinyassets/engine_mcp_server.py", r"\bFastMCP\(")),
        ceiling=1,
    ),
    "S8": Shape(("tinyassets/boxes/**",), (("tinyassets/**/role_*.py", ""),), ceiling=0),
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
        ("tinyassets/universe_server.py",), (("tinyassets/mcp_server.py", ""),), ceiling=0
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
        forbid=(r"Route\s*\(\s*['\"]/mcp/a(?:pp)",),
    ),
    "S17": Shape(("tinyassets/okf/**",), (("tinyassets/daemon_brain.py", ""),), ceiling=1),
    "S18": Shape(
        ("starter/**", "packages/**"),
        (
            ("domains/fantasy_daemon/**/*.py", ""),
            ("domains/research_probe/**/*.py", ""),
            ("tinyassets/api/market.py", ""),
            ("tinyassets/bid/**/*.py", ""),
            ("tinyassets/treasury/**/*.py", ""),
            ("tinyassets/executors/node_bid.py", ""),
            ("tinyassets/gates/actions.py", ""),
            ("tinyassets/payments/actions.py", ""),
            ("tinyassets/payments/funding.py", ""),
            ("tinyassets/payments/settlement_backend.py", ""),
            ("tinyassets/payments/settlement_ledger.py", ""),
            ("tinyassets/payments/wallets.py", ""),
            ("tinyassets/api/extensions.py", r"_ESCROW_ACTIONS"),
            ("tinyassets/desktop/dashboard.py", r"TINYASSETS_PAID_MARKET"),
        ),
        ceiling=56,
    ),
    "S19": Shape(
        ("packaging/claude-plugin/.claude-plugin/marketplace.json",),
        (("packaging/claude-plugin/plugins/tinyassets-universe-server/runtime/**", ""),),
        ceiling=0,
    ),
    "S20": Shape(("docs/architecture.md", "docs/decisions/**", "openspec/**")),
    "S21": Shape(
        ("openspec/changes/**",),
        (("openspec/changes/*/proposal.md", r"(?i)\bsuperseded by\b"),),
        ceiling=3,
    ),
    "S22": Shape(
        (
            "tinyassets/command_center/**",
            "tinyassets/universe_tools.py",
            "tinyassets/universe_server.py",
        ),
        # The rename ratchet: no new universe_* module (renames lower the count);
        # new *Universe* classes/defs are refused by the S22 entry symbol. Mentions
        # inside existing files are renamed as each slice touches them.
        (("tinyassets/**/universe_*.py", ""),),
        ceiling=10,
    ),
    "S23": Shape(("tinyassets/graph_compiler.py",)),
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
    "N11": Shape(("tinyassets/app_object.py",)),  # one user-built app; the command center
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
    "N11": r"\bdef\s+app_manifest\b",
}
# Genuinely old file probes also have a line ratchet: growing an existing file
# cannot hide behind an unchanged file count.
LINE_CEILINGS = {"S3": 1335, "S5": 0, "S9": 4638, "S15": 7547}
FLOOR_RAISE_SHAPES = frozenset({"S3", "S5", "S9", "S15"})
FLOOR_REASON = re.compile(r"\b(?:security|data[ -]loss|outage)\b", re.I)
VENDOR_FILE_CEILING = 145
MIRROR_ROOT = "packaging/claude-plugin/plugins/tinyassets-universe-server/runtime/"
VENDOR_NAMES = re.compile(
    r"anthropic|chatgpt|claude|codex|gemini|grok|groq|kimi|mistral|ollama|openai|openrouter", re.I
)
DUAL_FLAGS = re.compile(r"\bTINYASSETS_(?:AGENT_LOOP|ALLOW_CLAUDE_SERVING)\b")
CONSUMER_DISABLE_SITES = frozenset({
    "tinyassets/control_plane/tick.py",
})
RETIRED_ACCOUNT_LIMITS = re.compile(
    r"\b(?:RUN_WRITE_LIMIT|RUN_TOTAL_LIMIT|RUN_DAY_LIMIT|DISPATCHES_PER_HOUR|"
    r"BYTES_PER_HOUR|BUDGET_WINDOW_S|usage_notice|charge_dispatch|"
    r"dispatch_window_usage)\b"
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

HOST_PATHS = (
    "tinyassets/host_pool",
    "tinyassets/mcp_server.py",
    "fantasy_daemon",
    "tinyassets/__main__.py",
    "tinyassets/background_served_provider.py",
    "tinyassets/cloud_automation_continuation.py",
    "tinyassets/branch_tasks.py",
    "tinyassets_tray.py",
    "tinyassets.pyw",
    "tinyassets/desktop/launcher.py",
    "create-desktop-shortcut.bat",
    "start-tinyassets-server.bat",
    "start-tinyassets-server.vbs",
)
LIVE_SCRIPTS = {
    "tinyassets-universe-server": "tinyassets.universe_server:main",
    "tinyassets-probe": "tinyassets.probe:entry_point",
}
LIVE_DOMAIN_ENTRY_POINTS = {}


def dead_entry_problems(root: Path = ROOT) -> list[str]:
    """Keep removed host paths and extra start surfaces out of the package."""
    problems = [
        f"host-paths: {path}" for path in HOST_PATHS
        if (root / path).is_file()
        or ((root / path).is_dir() and any(files(f"{path}/**", root)))
    ]
    if (root / "prototype").exists():
        problems.append("host-paths: prototype/ fixture tree returned")
    compose = root / "deploy/compose.yml"
    if compose.is_file() and re.search(
        r"(?m)^  slack-agent:\s*$", compose.read_text(encoding="utf-8")
    ):
        problems.append("host-paths: compose slack-agent returned")
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    scripts = project.get("scripts", {})
    if scripts != LIVE_SCRIPTS or project.get("gui-scripts"):
        problems.append(f"spawn-entry: expected live scripts only, got {scripts!r}")
    domains = project.get("entry-points", {}).get("tinyassets.domains", {})
    if domains != LIVE_DOMAIN_ENTRY_POINTS:
        problems.append(f"domain-entry-points: changed registered domains {domains!r}")
    return problems


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
    if shape is SHAPES["S19"]:
        return len(subprocess.check_output(
            ["git", "ls-files", "--cached", "--", MIRROR_ROOT], cwd=root, text=True
        ).splitlines())
    total = 0
    for glob, pattern in shape.old:
        for path in files(glob, root):
            if shape is SHAPES["S22"] and any(
                registered_path(path.relative_to(root).as_posix(), home) for home in shape.paths
            ):
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


HISTORY_FILES = frozenset({".agents/activity.log"})


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
        # Append-only history no PR may edit (scripts/check_forbidden_pr_paths.py).
        if rel in HISTORY_FILES:
            continue
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


def ceiling_raise_authorized() -> bool:
    """Only a PR event with the floor label and stated reason can raise an old-path pin."""
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path:
        return False
    try:
        event = json.loads(Path(event_path).read_text(encoding="utf-8"))
        pr = event["pull_request"]
        return (
            isinstance(pr, dict)
            and any(label.get("name") == "shape-ceiling-raise" for label in pr["labels"])
            and bool(FLOOR_REASON.search(pr["body"] or ""))
        )
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False


def merge_queue_pin_check() -> bool:
    """Queued commits already passed the required PR guard with their PR event."""
    if (
        os.environ.get("GITHUB_ACTIONS") != "true"
        or os.environ.get("GITHUB_EVENT_NAME") != "merge_group"
    ):
        return False
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path:
        return False
    try:
        event = json.loads(Path(event_path).read_text(encoding="utf-8"))
        return event["merge_group"]["head_sha"] == os.environ.get("GITHUB_SHA")
    except (OSError, ValueError, KeyError, TypeError):
        return False


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
        if current.ceiling > ceiling and not (
            shape_id in FLOOR_RAISE_SHAPES
            and (ceiling_raise_authorized() or merge_queue_pin_check())
        ):
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
                if shape_id not in LINE_CEILINGS:
                    problems.append(f"{shape_id} line ceiling: pin removed")
                elif LINE_CEILINGS[shape_id] > prior and not (
                    shape_id in FLOOR_RAISE_SHAPES
                    and (ceiling_raise_authorized() or merge_queue_pin_check())
                ):
                    problems.append(
                        f"{shape_id} line ceiling: pin raised {prior} -> {LINE_CEILINGS[shape_id]}"
                    )
    return problems


def registered_path(rel: str, glob: str) -> bool:
    if glob.endswith("/**"):
        return rel.startswith(glob[:-3] + "/")
    if not any(char in glob for char in "*?["):
        return rel == glob
    return PurePosixPath(rel).match(glob)


_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
# The linux oracle bind-mounts the workflow checkout here and then `git init`s
# a one-commit snapshot at /work. History stays on the mount.
_CHECKOUT_GIT = Path("/src/.git")


def _on_actions() -> bool:
    return bool(os.environ.get("GITHUB_ACTIONS"))


def _checkout_git_dir() -> Path | None:
    return _CHECKOUT_GIT if _CHECKOUT_GIT.is_dir() else None


def _git(cwd: Path, *args: str, git_dir: Path | None = None) -> subprocess.CompletedProcess[str]:
    command = ["git", "-c", "safe.directory=*"]
    if git_dir is not None:
        command.extend(["--git-dir", str(git_dir)])
    command.extend(args)
    return subprocess.run(command, cwd=cwd, capture_output=True, text=True)


def _commit_sha(cwd: Path, rev: str, *, git_dir: Path | None = None) -> str | None:
    result = _git(cwd, "rev-parse", "--verify", f"{rev}^{{commit}}", git_dir=git_dir)
    if result.returncode != 0:
        return None
    sha = result.stdout.strip()
    return sha if _COMMIT_RE.fullmatch(sha) else None


def _borrow_checkout_objects(root: Path) -> bool:
    """Let the oracle snapshot read the checkout's objects without copying them."""
    source = _checkout_git_dir()
    local = root / ".git"
    if source is None or not local.is_dir():
        return False
    info = local / "objects" / "info"
    info.mkdir(parents=True, exist_ok=True)
    alternates = info / "alternates"
    # Git's alternates file uses slash paths even when the host is Windows.
    entry = f"{(source / 'objects').resolve().as_posix()}\n"
    current = alternates.read_bytes().decode("utf-8") if alternates.exists() else ""
    if entry not in current:
        # Git reads this file bytewise; Windows text mode would add CR bytes.
        alternates.write_bytes((current + entry).encode("utf-8"))
        # Local config only: a global safe.directory would outlive this repo.
        subprocess.run(
            ["git", "config", "--add", "safe.directory", str(source.parent.resolve())],
            cwd=root,
            capture_output=True,
            check=False,
        )
    return True


def _borrow_commit(root: Path, rev: str) -> str | None:
    """Resolve `rev` from the checkout mount. Actions only; a local oracle stays history-free."""
    if not _on_actions():
        return None
    source = _checkout_git_dir()
    if source is None:
        return None
    sha = _commit_sha(root, rev, git_dir=source)
    if sha is None or not _borrow_checkout_objects(root):
        return None
    return _commit_sha(root, sha)


def _oracle_snapshot_main(root: Path) -> bool:
    """True when local `main` is the oracle's one-commit snapshot, not upstream.

    `git init` names that commit `main` when the image's default branch is main.
    Diffing against it is empty, so the guard would pass without seeing the
    change. The checkout mount still has the real base. The oracle image today
    inits `master` (run 38208790984 raised instead of passing), and this keeps
    the other default from becoming a silent pass.
    """
    if not _on_actions() or _checkout_git_dir() is None:
        return False
    return _commit_sha(root, "HEAD~1") is None


def _related_merge_base(root: Path, rev: str) -> str:
    """The fork point of HEAD and `rev`. Raises when they do not share history."""
    return subprocess.check_output(
        ["git", "-c", "safe.directory=*", "merge-base", "HEAD", rev],
        cwd=root,
        text=True,
    ).strip()


def _merge_group_base_sha() -> str | None:
    """The queue base is the event's base_sha, not whatever origin/main resolves to.

    A merge group can contain several entries. HEAD~1 is then the previous
    entry, and a stale origin/main is an older main. The diff has to start at
    `github.event.merge_group.base_sha`. The oracle does not receive
    GITHUB_EVENT_PATH, so the shard also passes the sha in the environment.
    """
    raw = os.environ.get("MERGE_GROUP_BASE_SHA", "").strip()
    if _COMMIT_RE.fullmatch(raw):
        return raw
    if os.environ.get("GITHUB_EVENT_NAME") != "merge_group":
        return None
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path:
        return None
    try:
        event = json.loads(Path(event_path).read_text(encoding="utf-8"))
        sha = event["merge_group"]["base_sha"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return sha if isinstance(sha, str) and _COMMIT_RE.fullmatch(sha) else None


def _merge_base(root: Path) -> str | None:
    if not (root / ".git").exists():
        return None
    queue_base = _merge_group_base_sha()
    if queue_base:
        resolved = _commit_sha(root, queue_base) or _borrow_commit(root, queue_base)
        if resolved:
            return resolved
        if _on_actions():
            raise RuntimeError("shape guard needs the merge queue base; fetch-depth must be 0")
    for ref in ("origin/main", "main", "HEAD~1"):
        local = _commit_sha(root, ref)
        if local and not (ref == "main" and _oracle_snapshot_main(root)):
            return _related_merge_base(root, local)
        borrowed = _borrow_commit(root, ref)
        if borrowed:
            # The snapshot commit does not descend from main. Its working tree
            # is the checkout, so the borrowed commit is the tree to diff.
            try:
                return _related_merge_base(root, borrowed)
            except subprocess.CalledProcessError:
                return borrowed
    if _on_actions():
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
        if (DUAL_FLAGS.search(source) or (
            "TINYASSETS_ASSIGNED_QUEUE_CONSUMER" in source
            and rel not in CONSUMER_DISABLE_SITES
        )) or re.search(
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


def control_plane_policy_problems(root: Path = ROOT) -> list[str]:
    """Only the explicit consumer-off boundary may gate the owner tick."""
    problems: list[str] = []
    for path in files("tinyassets/**/*.py", root) + files("scripts/**/*.py", root):
        if path.name == "check_shapes.py":
            continue
        source = path.read_text(encoding="utf-8", errors="replace")
        rel = path.relative_to(root).as_posix()
        if "TINYASSETS_ASSIGNED_QUEUE_CONSUMER" in source and rel not in CONSUMER_DISABLE_SITES:
            problems.append(f"opt-in-flags: {rel} gates the owner tick")
        if rel.startswith("tinyassets/") and RETIRED_ACCOUNT_LIMITS.search(source):
            problems.append(f"limit-names: {rel} contains a retired account meter")
    return problems


def check(root: Path = ROOT, *, added: set[str] | None = None) -> list[str]:
    problems: list[str] = []
    problems.extend(control_plane_policy_problems(root))
    problems.extend(dead_entry_problems(root))
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
