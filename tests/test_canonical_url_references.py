"""Sentinel: user-facing docs must not name api.tinyassets.io (retired)
or describe mcp.tinyassets.io/mcp as the canonical user URL.

Row G sweep. File list is an explicit allowlist — no repo walk.
Canonical user URL: https://tinyassets.io/mcp (only).
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Explicit allowlist — only these files are checked.
# Add new user-facing docs here when they are created.
_WATCHED: list[str] = [
    "README.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "docs/getting-started/README.md",
    "docs/getting-started/testers.md",
    "docs/getting-started/self-hosting.md",
    "INDEX.md",
    "SUCCESSION.md",
    "deploy/DEPLOY.md",
    "deploy/README.md",
    "deploy/cloudflare-worker/README.md",
    "deploy/cloudflared-README.md",
    "docs/ops/tinyassets-probe-reference.md",
    "docs/ops/launch-readiness-checklist.md",
    "docs/ops/anthropic-connector-catalog-submission.md",
    ".claude/skills/ui-test/SKILL.md",
    ".agents/skills/ui-test/SKILL.md",
]

# Lines that mention mcp.tinyassets.io/mcp alongside these phrases indicate
# user-accessible framing that must NOT appear.
_USER_FACING_FRAMING = re.compile(
    r"user.facing\s+URL|canonical\s+URL|installed.+connector|connect\s+to|public\s+URL"
    r"|users\s+connect|Claude\.ai\s+connector",
    re.IGNORECASE,
)

# Phrases that mark a line as explicitly describing the tunnel-internal role
# (NOT user-facing) — exempt from the mcp. framing check.
_TUNNEL_INTERNAL_MARKERS = (
    "not the user-facing",
    "not user-facing",
    "access-gated",
    "tunnel-internal",
    "tunnel origin",
    "direct-tunnel",
)


def _lines(rel: str):
    p = REPO / rel
    if not p.exists():
        return
    for lineno, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        yield lineno, line


def test_no_api_tinyassets_in_watched_docs():
    """api.tinyassets.io is retired (NXDOMAIN). Must not appear in watched docs."""
    hits = []
    for rel in _WATCHED:
        for lineno, line in _lines(rel):
            if "api.tinyassets.io" in line:
                hits.append(f"{rel}:{lineno}: {line.strip()}")
    assert hits == [], (
        "Found api.tinyassets.io (retired URL) in watched docs:\n" + "\n".join(hits)
    )


def test_mcp_subdomain_not_described_as_user_url():
    """mcp.tinyassets.io/mcp must not be framed as the canonical user URL.

    Lines that explicitly label it as tunnel-internal/debug/access-gated are exempt.
    """
    hits = []
    for rel in _WATCHED:
        for lineno, line in _lines(rel):
            if "mcp.tinyassets.io/mcp" not in line:
                continue
            if not _USER_FACING_FRAMING.search(line):
                continue
            lower = line.lower()
            if any(k in lower for k in _TUNNEL_INTERNAL_MARKERS):
                continue
            hits.append(f"{rel}:{lineno}: {line.strip()}")
    assert hits == [], (
        "Found mcp.tinyassets.io/mcp framed as user-accessible in watched docs:\n"
        + "\n".join(hits)
    )
