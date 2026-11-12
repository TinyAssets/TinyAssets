"""The scope guard counts the release-critical files a PR touches, for its reviewer."""
from __future__ import annotations

import re
from pathlib import Path

_GUARD = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "pr-scope-guard.yml"


def _release_critical() -> re.Pattern[str]:
    found = re.search(r"^\s*SENSITIVE_RE='([^']+)'", _GUARD.read_text(encoding="utf-8"), re.M)
    assert found, "pr-scope-guard.yml has no SENSITIVE_RE"
    return re.compile(found.group(1))


def test_platform_deploy_is_release_critical() -> None:
    pattern = _release_critical()
    for path in ("platform/deploy/deploy.ts", "platform/deploy/platform.json",
                 "deploy/deploy_fail_safe.sh", ".github/workflows/tests.yml"):
        assert pattern.search(path), path
    for path in ("platform/doors/src/doors.ts", "docs/platform/deploy/x.md"):
        assert not pattern.search(path), path
