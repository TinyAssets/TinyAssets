"""Mirror-parity invariant: canonical `tinyassets/**` == plugin mirror.

Compares every canonical file under `tinyassets/` with its paired plugin-mirror
path under
`packaging/claude-plugin/plugins/tinyassets-universe-server/runtime/tinyassets/`.
Two ways to break parity, and the check names both by path:

* **diverged** - both copies exist and differ. The mirror is running older code.
* **missing** - the canonical file has no mirror counterpart at all. This used
  to be SKIPPED as "a new module the plugin build has not picked up yet", which
  is the same sentence as "the mirror does not ship this module": `workspace_pool.py`
  and `workspace_fs.py` sat outside the mirror across three commits and the gate
  reported clean every time (2026-08-30). A file the build would copy and the
  mirror does not have is drift, not a grace period.

What the build copies is the definition of what must be mirrored, so the
exclusion list below is the build's own, pinned by a test.

Pre-commit runs it on the staged set (`scripts/check_mirror_parity.py`, which
the hook calls); CI runs `invariants_run.py --pre-commit`, which lands here and
scans the whole tree. Auto-heal is disabled: fixing drift means rebuilding the
plugin, which is too heavyweight for a silent background heal.
"""

from __future__ import annotations

import filecmp
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

from . import CheckResult, HealResult, Invariant, Status

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CANONICAL_ROOT = REPO_ROOT / "tinyassets"
MIRROR_ROOT = (
    REPO_ROOT
    / "packaging"
    / "claude-plugin"
    / "plugins"
    / "tinyassets-universe-server"
    / "runtime"
    / "tinyassets"
)

#: MUST stay identical to ``_TREE_EXCLUDES`` in
#: ``packaging/claude-plugin/build_plugin.py`` - the build's copy rule IS the
#: parity contract, and a gate holding a different list would either miss drift
#: or fail on files the build never copies. ``tests/test_mirror_parity_gate.py``
#: reads both and refuses to let them diverge.
TREE_EXCLUDES: tuple[str, ...] = (
    "__pycache__",
    "*.db",
    "*.db-journal",
    "*.log",
    "*.pyc",
    ".pytest_cache",
    "*.tmp",
    # Vendored custom-UI libraries (~3 MB) are served only by the hosted app;
    # the plugin runtime has no app. Their pinned manifest IS mirrored.
    "ui_libraries",
)

#: Divergence is compared for text the mirror is expected to ship verbatim.
SCAN_SUFFIXES = (".py", ".md", ".json", ".toml")


def is_excluded(path: Path) -> bool:
    """The build's own predicate: would ``build_plugin.py`` skip this path?"""
    name = path.name
    for pattern in TREE_EXCLUDES:
        if path.match(pattern) or name == pattern:
            return True
    return False


def iter_canonical_files(canonical_root: Path) -> Iterator[Path]:
    """Every file the plugin build would copy out of ``canonical_root``."""
    for path in canonical_root.rglob("*"):
        if not path.is_file():
            continue
        if is_excluded(path) or any(is_excluded(parent) for parent in path.parents):
            continue
        yield path


@dataclass(frozen=True)
class ParityReport:
    """What the scan saw. ``ok`` is the gate's verdict."""

    checked: int
    diverged: tuple[str, ...]
    missing: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.diverged and not self.missing

    def message(self) -> str:
        if self.ok:
            return f"all {self.checked} canonical file(s) mirror-matched"
        parts = []
        if self.diverged:
            parts.append(f"{len(self.diverged)} diverged")
        if self.missing:
            parts.append(f"{len(self.missing)} missing from the mirror")
        return f"{', '.join(parts)} (out of {self.checked} checked)"

    def detail_lines(self) -> list[str]:
        """One line per offending path, for a human reading a failed gate."""
        lines: list[str] = []
        for path in self.diverged:
            lines.append(f"  diverged: {path}")
        for path in self.missing:
            lines.append(f"  missing from the mirror: {path}")
        return lines


def scan_parity(
    canonical_root: Path,
    mirror_root: Path,
    *,
    relative_paths: Iterable[str] | None = None,
) -> ParityReport:
    """Compare canonical against mirror; whole tree, or just ``relative_paths``.

    ``relative_paths`` are relative to ``canonical_root`` (the hook passes the
    staged set). A named path that does not exist is skipped - a rename's source
    side is not drift.
    """
    canonical_root = Path(canonical_root)
    mirror_root = Path(mirror_root)
    if relative_paths is None:
        candidates = list(iter_canonical_files(canonical_root))
    else:
        candidates = []
        for raw in relative_paths:
            path = canonical_root / str(raw).replace("\\", "/")
            if not path.is_file():
                continue
            if is_excluded(path) or any(is_excluded(parent) for parent in path.parents):
                continue
            candidates.append(path)

    checked = 0
    diverged: list[str] = []
    missing: list[str] = []
    for canonical in candidates:
        rel = canonical.relative_to(canonical_root)
        mirror = mirror_root / rel
        name = str(rel).replace("\\", "/")
        checked += 1
        if not mirror.exists():
            missing.append(name)
            continue
        if canonical.suffix not in SCAN_SUFFIXES:
            continue
        if not filecmp.cmp(canonical, mirror, shallow=False):
            diverged.append(name)
    return ParityReport(
        checked=checked, diverged=tuple(sorted(diverged)), missing=tuple(sorted(missing))
    )


class MirrorParityInvariant(Invariant):
    name = "mirror-parity"
    description = "Canonical tinyassets/ == plugin-mirror byte-for-byte."
    pre_commit_scope = True
    poll_interval_s = None  # diagnostic / pre-commit only
    auto_heal = False

    def __init__(
        self,
        canonical_root: Path | None = None,
        mirror_root: Path | None = None,
    ) -> None:
        # Roots are injectable so the gate can be driven against a temp tree in
        # a test. A gate nobody can point at a fixture is a gate nobody proves
        # can fail (`scripts/invariants/__init__.py`: a check that cannot go red
        # is decor).
        self.canonical_root = Path(canonical_root or CANONICAL_ROOT)
        self.mirror_root = Path(mirror_root or MIRROR_ROOT)

    def _check(self) -> CheckResult:
        if not self.canonical_root.is_dir():
            return CheckResult(
                status=Status.SKIPPED,
                message=f"canonical root not found: {self.canonical_root}",
            )
        if not self.mirror_root.is_dir():
            # The canonical tree exists, so the mirror is owed: a mirror that is
            # gone entirely is the largest drift there is, not a reason to skip
            # (a build that died between its renames once left exactly this).
            return CheckResult(
                status=Status.VIOLATED,
                message=f"mirror root not found: {self.mirror_root}; rebuild with "
                "python packaging/claude-plugin/build_plugin.py",
            )

        report = scan_parity(self.canonical_root, self.mirror_root)
        if not report.ok:
            return CheckResult(
                status=Status.VIOLATED,
                message=report.message()
                + "; rebuild with python packaging/claude-plugin/build_plugin.py",
                evidence={
                    "mismatches": list(report.diverged),
                    "missing": list(report.missing),
                    "checked": report.checked,
                    "paths": report.detail_lines(),
                },
            )
        return CheckResult(
            status=Status.OK,
            message=report.message(),
            evidence={"checked": report.checked},
        )

    def _heal(self) -> HealResult:
        # auto_heal = False, so the base class short-circuits this.
        # Kept as placeholder for documentation completeness.
        return HealResult(
            healed=False,
            message="mirror-parity heal is manual; re-run the packaging build",
        )
