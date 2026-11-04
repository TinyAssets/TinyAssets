"""Check for drift between project-wide rules and provider-specific files.

This is intentionally conservative. It catches the drift classes that have
already hurt the project without trying to turn AGENTS.md into a fuzzy semantic
diff target:

- referenced guard artifacts must exist;
- `.agents/skills/*/SKILL.md` and `.claude/skills/*/SKILL.md` must match;
- newly edited provider-specific files must not introduce broad project rules
  that belong in AGENTS.md unless the section is explicitly harness-specific.

Project-local skills are cross-provider by design, so they are mirror-checked
but not treated as provider-specific semantic drift candidates.

Exit 0 when clean and 2 when drift is found so hooks can block the edit.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except (AttributeError, ValueError, OSError):
        pass

REQUIRED_ARTIFACTS = ("scripts/check_cross_provider_drift.py",)

# Provider-SPECIFIC surfaces only. A broad rule found here belongs in AGENTS.md
# instead -- which is the whole point of the check, so AGENTS.md is never itself
# watched. Trimmed 2026-08-26: `CLAUDE_LEAD_OPS.md`, `LAUNCH_PROMPT.md`,
# `.cursorrules`, `.claude/agents/*.md`, `.codex/**`, and `.cursor/rules/*.mdc`
# are all deleted or retired, and the drift-guard hook in REQUIRED_ARTIFACTS
# went with them. `CODEX.md` was deleted 2026-11-04: Codex reads AGENTS.md.
WATCHED_FILES = (
    "CLAUDE.md",
)

WATCHED_GLOBS = (
    ".agents/skills/*/SKILL.md",
    ".claude/skills/*/SKILL.md",
)

HARNESS_TAGS = (
    "[harness-specific]",
    "[claude code only]",
    "[codex only]",
)

BROAD_RULE_PATTERNS = (
    re.compile(r"\b(cross-provider|project-level|provider-specific)\b", re.IGNORECASE),
    re.compile(r"\b(all|any|every)\s+(provider|agent|session)\b", re.IGNORECASE),
    re.compile(
        r"\b(Codex|Cursor|Cowork|Aider)\b.*\b(must|should|never|always|rule)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(must|should|never|always|rule)\b.*\b(Codex|Cursor|Cowork|Aider)\b",
        re.IGNORECASE,
    ),
)


@dataclass(frozen=True)
class Issue:
    code: str
    path: str
    message: str
    prescription: str


def find_repo_root(start: Path) -> Path:
    current = start.resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / "AGENTS.md").exists():
            return candidate
    return Path.cwd().resolve()


def relpath(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (FileNotFoundError, UnicodeDecodeError):
        return ""


def is_watched_path(path: str) -> bool:
    normalized = path.replace("\\", "/")
    if normalized in WATCHED_FILES:
        return True
    watched_prefixes = (
        ".agents/skills/",
        ".claude/skills/",
    )
    return any(normalized.startswith(prefix) for prefix in watched_prefixes)


def provider_harness_paths(root: Path) -> list[Path]:
    """The provider-SPECIFIC harness files, without the skill mirrors.

    Skills are deliberately identical across providers (AGENTS.md
    § *Project Skills*), so a SKILL.md holding a project-level convention is
    correct by construction -- flagging it is guaranteed noise, and running the
    section scan across the globs produced 19 such reports on a clean tree.
    Skills are covered by `check_skill_mirrors` instead, which is the check
    that actually applies to them.
    """
    return [root / raw for raw in WATCHED_FILES if (root / raw).exists()]


def iter_watched_paths(root: Path) -> list[Path]:
    paths: list[Path] = []
    for raw in WATCHED_FILES:
        path = root / raw
        if path.exists():
            paths.append(path)
    for raw_glob in WATCHED_GLOBS:
        paths.extend(path for path in root.glob(raw_glob) if path.is_file())
    return sorted({path.resolve() for path in paths})


def normalize_cli_paths(root: Path, raw_paths: list[str] | None) -> list[Path] | None:
    if raw_paths is None:
        return None

    expanded: list[str] = []
    for raw in raw_paths:
        expanded.extend(part.strip() for part in raw.split(",") if part.strip())

    paths: list[Path] = []
    for raw in expanded:
        path = Path(raw)
        if not path.is_absolute():
            path = root / path
        paths.append(path)
    return paths


def check_required_artifacts(root: Path, paths: list[Path]) -> list[Issue]:
    issues: list[Issue] = []
    references: dict[str, list[str]] = {artifact: [] for artifact in REQUIRED_ARTIFACTS}

    for path in paths:
        rel = relpath(root, path)
        text = read_text(path)
        for artifact in REQUIRED_ARTIFACTS:
            if artifact in text:
                references[artifact].append(rel)

    for artifact, referrers in references.items():
        if not referrers:
            continue
        if (root / artifact).exists():
            continue
        issues.append(
            Issue(
                code="missing-artifact",
                path=artifact,
                message=(
                    f"{artifact} is referenced by {', '.join(referrers[:5])} "
                    "but does not exist."
                ),
                prescription=f"Create {artifact}, or remove/retarget the stale reference.",
            )
        )

    if any(referrers for referrers in references.values()):
        agents = read_text(root / "AGENTS.md")
        if "Where new conventions live" not in agents:
            issues.append(
                Issue(
                    code="missing-agents-rule",
                    path="AGENTS.md",
                    message=(
                        "Guard artifacts are referenced, but AGENTS.md lacks the "
                        "'Where new conventions live' convention section."
                    ),
                    prescription=(
                        "Add the canonical AGENTS.md rule so provider-specific files "
                        "do not become competing process truth."
                    ),
                )
            )

    return issues


def check_skill_mirrors(root: Path, paths: list[Path] | None) -> list[Issue]:
    issues: list[Issue] = []
    pairs: set[tuple[Path, Path]] = set()

    candidates = paths if paths is not None else iter_watched_paths(root)
    for path in candidates:
        rel = relpath(root, path)
        if rel.startswith(".agents/skills/") and rel.endswith("/SKILL.md"):
            pairs.add((path, root / rel.replace(".agents/skills/", ".claude/skills/", 1)))
        elif rel.startswith(".claude/skills/") and rel.endswith("/SKILL.md"):
            pairs.add((root / rel.replace(".claude/skills/", ".agents/skills/", 1), path))

    if paths is None:
        for canonical in (root / ".agents" / "skills").glob("*/SKILL.md"):
            mirror = root / ".claude" / "skills" / canonical.parent.name / "SKILL.md"
            pairs.add((canonical, mirror))

    for canonical, mirror in sorted(pairs, key=lambda pair: relpath(root, pair[0])):
        canonical_rel = relpath(root, canonical)
        mirror_rel = relpath(root, mirror)
        if not canonical.exists():
            issues.append(
                Issue(
                    code="skill-mirror-missing-source",
                    path=canonical_rel,
                    message=f"{mirror_rel} has no canonical .agents skill source.",
                    prescription="Restore the .agents skill source or remove the orphan mirror.",
                )
            )
            continue
        if not mirror.exists():
            issues.append(
                Issue(
                    code="skill-mirror-missing",
                    path=mirror_rel,
                    message=f"{canonical_rel} has no Claude skill mirror.",
                    prescription="Run scripts/sync-skills.ps1 or add the matching mirror file.",
                )
            )
            continue
        if read_text(canonical) != read_text(mirror):
            issues.append(
                Issue(
                    code="skill-mirror-drift",
                    path=mirror_rel,
                    message=f"{canonical_rel} and {mirror_rel} differ.",
                    prescription=(
                        "Run powershell -ExecutionPolicy Bypass -File "
                        "scripts/sync-skills.ps1."
                    ),
                )
            )

    return issues


def markdown_sections(text: str) -> list[tuple[str, str]]:
    sections: list[tuple[str, list[str]]] = []
    current_heading = ""
    current_body: list[str] = []

    for line in text.splitlines():
        if line.startswith("#"):
            if current_heading or current_body:
                sections.append((current_heading, current_body))
            current_heading = line.strip()
            current_body = []
        else:
            current_body.append(line)

    if current_heading or current_body:
        sections.append((current_heading, current_body))

    return [(heading, "\n".join(body).strip()) for heading, body in sections]


def has_harness_tag(heading: str, body: str) -> bool:
    probe = f"{heading}\n{body[:240]}".lower()
    return any(tag in probe for tag in HARNESS_TAGS)


def looks_like_broad_rule(heading: str, body: str) -> bool:
    text = f"{heading}\n{body}"
    if not any(pattern.search(text) for pattern in BROAD_RULE_PATTERNS):
        return False
    return bool(re.search(r"\b(must|should|never|always|rule|convention|standard)\b", text, re.I))


def compact_section(heading: str, body: str) -> str:
    lines = [heading.strip()]
    for line in body.splitlines():
        stripped = line.strip()
        if stripped:
            lines.append(stripped)
        if len(" ".join(lines)) >= 220:
            break
    return " ".join(lines)[:260]


def check_provider_sections(root: Path, changed_paths: list[Path] | None) -> list[Issue]:
    # `None` means "no staged-file list" -- the invariant runner and a bare CLI
    # run both arrive that way. Returning [] there made this the ONLY half of
    # the guard that could never fire outside a pre-commit hook, so the
    # `cross-provider-drift` invariant reported "clean" unconditionally while
    # gating every PR. Proven 2026-08-26 by appending a broad rule to a watched
    # file and watching the check pass. Fall back to the full watched set instead;
    # `check_skill_mirrors` already resolves `None` the same way.
    scan = provider_harness_paths(root) if changed_paths is None else changed_paths

    agents_text = read_text(root / "AGENTS.md").lower()
    issues: list[Issue] = []

    for path in scan:
        rel = relpath(root, path)
        if rel not in WATCHED_FILES:
            continue

        for heading, body in markdown_sections(read_text(path)):
            if not body or has_harness_tag(heading, body):
                continue
            if not looks_like_broad_rule(heading, body):
                continue
            snippet = compact_section(heading, body)
            key_terms = [
                term
                for term in ("cross-provider", "project-level", "provider-specific")
                if term in snippet.lower()
            ]
            mirrored = all(term in agents_text for term in key_terms) if key_terms else False
            if mirrored:
                continue
            issues.append(
                Issue(
                    code="provider-rule-candidate",
                    path=rel,
                    message=(
                        "Provider-specific file appears to contain an untagged "
                        f"project-level convention: {snippet}"
                    ),
                    prescription=(
                        "Move the convention to AGENTS.md, or tag the heading/body "
                        "with one of: " + ", ".join(HARNESS_TAGS) + "."
                    ),
                )
            )

    return issues


def run_checks(root: Path, changed_paths: list[Path] | None = None) -> list[Issue]:
    watched = iter_watched_paths(root)
    scan_paths = watched if changed_paths is None else sorted({*watched, *changed_paths})
    issues: list[Issue] = []
    issues.extend(check_required_artifacts(root, scan_paths))
    issues.extend(check_skill_mirrors(root, changed_paths))
    issues.extend(check_provider_sections(root, changed_paths))
    return issues


def print_text(issues: list[Issue]) -> None:
    if not issues:
        print("cross-provider drift check: clean")
        return

    print("cross-provider drift check: DRIFT")
    for issue in issues:
        print(f"- [{issue.code}] {issue.path}: {issue.message}")
        print(f"  fix: {issue.prescription}")


def run_self_test() -> int:
    with tempfile.TemporaryDirectory(prefix="workflow-drift-check-") as tmp:
        root = Path(tmp)
        (root / "scripts").mkdir()
        (root / ".claude" / "hooks").mkdir(parents=True)
        (root / ".agents" / "skills" / "website-editing").mkdir(parents=True)
        (root / ".claude" / "skills" / "website-editing").mkdir(parents=True)
        (root / ".claude" / "agents").mkdir(parents=True)
        (root / "AGENTS.md").write_text("# TinyAssets\n", encoding="utf-8")
        skill_text = (
            "# Website editing\n\n"
            "Cross-provider drift -> scripts/check_cross_provider_drift.py -> "
            ".claude/hooks/cross_provider_drift_guard.py PostToolUse.\n"
        )
        canonical = root / ".agents" / "skills" / "website-editing" / "SKILL.md"
        mirror = root / ".claude" / "skills" / "website-editing" / "SKILL.md"
        canonical.write_text(skill_text, encoding="utf-8")
        mirror.write_text(skill_text, encoding="utf-8")

        missing_issues = run_checks(root)
        missing_codes = {issue.code for issue in missing_issues}
        assert "missing-artifact" in missing_codes
        assert "missing-agents-rule" in missing_codes

        shutil.copyfile(Path(__file__), root / "scripts" / "check_cross_provider_drift.py")
        (root / ".claude" / "hooks" / "cross_provider_drift_guard.py").write_text(
            "# hook placeholder\n", encoding="utf-8"
        )
        (root / "AGENTS.md").write_text(
            "# TinyAssets\n\n### Where new conventions live\n\nUse AGENTS.md first.\n",
            encoding="utf-8",
        )
        assert not run_checks(root)

        mirror.write_text(skill_text + "\nextra\n", encoding="utf-8")
        drift = run_checks(root)
        assert {issue.code for issue in drift} == {"skill-mirror-drift"}

        mirror.write_text(skill_text, encoding="utf-8")

        # Every watched harness file must be able to trip the section scan.
        broad_rule = "## Queue\n\nEvery provider must add project-level rules here.\n"
        for name in WATCHED_FILES:
            harness = root / name
            harness.write_text(broad_rule, encoding="utf-8")
            staged = run_checks(root, [harness])
            assert {issue.code for issue in staged} == {"provider-rule-candidate"}, name
            # ...and with NO staged list, which is how the invariant runner and
            # a bare CLI run both call it. Returning [] there made the
            # `cross-provider-drift` invariant permanently green.
            unstaged = run_checks(root)
            assert {issue.code for issue in unstaged} == {"provider-rule-candidate"}, name
            harness.unlink()

        assert not run_checks(root)

        # A SKILL.md carrying a project-level convention is CORRECT -- skills are
        # identical across providers by design -- so it must not be reported.
        canonical.write_text(skill_text + "\n" + broad_rule, encoding="utf-8")
        mirror.write_text(skill_text + "\n" + broad_rule, encoding="utf-8")
        assert not run_checks(root)
        assert not run_checks(root, [canonical])

    print("cross-provider drift check self-test: clean")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--paths",
        nargs="*",
        help="Changed provider-specific paths to apply edit-time semantic checks to.",
    )
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        return run_self_test()

    root = find_repo_root(Path.cwd())
    changed_paths = normalize_cli_paths(root, args.paths)
    issues = run_checks(root, changed_paths)

    if args.format == "json":
        print(json.dumps([asdict(issue) for issue in issues], indent=2))
    else:
        print_text(issues)

    return 2 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
