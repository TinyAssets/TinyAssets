"""What remains of the retired file_bug -> investigation pipeline (Task #33).

The auto-trigger is GONE (AGENTS.md Hard Rule 15, the platform has no LLM,
2026-09-24). It resolved a platform-chosen "canonical" investigation branch --
through the quality leaderboard, whose selector was a model call served from
the host's credentials -- and queued it to run on a universe's model because a
bug was filed. Investigation is platform operation; it runs no model. Users who
want bug triage build their own workflow and pass inputs and outputs between
nodes (cross-owner delivery). ``wiki action=file_bug`` already never called
this path (``tests/test_bug_investigation_wiring.py``).

What is left serves only ``bug_investigation`` tasks that may already sit in a
queue: the payload mapper the claimed-task executor uses for legacy tasks and
the patch-packet attach helper. Both are listed for deletion in
``docs/decisions/ADR-006-the-platform-has-no-llm.md``.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

_logger = logging.getLogger(__name__)

_BUGS_CATEGORY = "bugs"
_PATCH_PACKET_HEADING = "## Patch Packet"

_PAYLOAD_KEYS = (
    "bug_id",
    "title",
    "component",
    "severity",
    "kind",
    "effort_class",
    "effort_attention",
    "effort_dispatch_lane",
    "observed",
    "expected",
    "repro",
    "workaround",
)


def build_run_payload(bug_frontmatter: dict) -> dict:
    """Map BUG-NNN frontmatter → canonical investigation branch input shape."""
    payload = {k: bug_frontmatter.get(k, "") for k in _PAYLOAD_KEYS}
    if bug_frontmatter.get("effort_classification"):
        payload["effort_classification"] = bug_frontmatter["effort_classification"]
    if bug_frontmatter.get("effort_dispatch_route"):
        payload["effort_dispatch_route"] = bug_frontmatter["effort_dispatch_route"]
    payload["request_text"] = str(
        bug_frontmatter.get("request_text") or _format_request_text(payload)
    )
    return payload


def _format_request_text(payload: dict) -> str:
    kind = str(payload.get("kind") or "bug").strip() or "bug"
    bug_id = str(payload.get("bug_id") or "untracked").strip() or "untracked"
    title = str(payload.get("title") or "Untitled").strip() or "Untitled"
    lines = [f"{kind} {bug_id}: {title}", ""]
    for label, key in (
        ("Component", "component"),
        ("Severity", "severity"),
        ("Effort Class", "effort_class"),
        ("Dispatch Lane", "effort_dispatch_lane"),
        ("Observed", "observed"),
        ("Expected", "expected"),
        ("Repro", "repro"),
        ("Workaround", "workaround"),
    ):
        value = str(payload.get(key) or "").strip()
        if value:
            lines.append(f"{label}: {value}")
    return "\n".join(lines).strip()


def format_patch_packet_comment(patch_packet: dict) -> str:
    """Format the Patch Packet section appended to the bug page after run completes."""
    sections = []
    for key in ("minimal_repro", "root_cause", "test_plan", "implementation_sketch"):
        if patch_packet.get(key):
            label = key.replace("_", " ").title()
            sections.append(f"### {label}\n\n{patch_packet[key]}")
    if not sections:
        return ""
    return "\n\n## Patch Packet\n\n" + "\n\n".join(sections)


def _slug_from_bug_id(bug_id: str) -> str:
    """Convert BUG-NNN (or bug-nnn) to the canonical lowercase slug prefix."""
    return re.sub(r"[^a-z0-9-]", "-", bug_id.lower()).strip("-")


def _find_bug_page(bug_id: str) -> Path | None:
    """Locate the bug page file in pages/bugs/ resolving case aliases."""
    from tinyassets.storage import wiki_path

    bugs_dir = wiki_path() / "pages" / _BUGS_CATEGORY
    if not bugs_dir.is_dir():
        return None

    slug_prefix = _slug_from_bug_id(bug_id)
    # Exact prefix match (lowercase) — the file starts with the bug slug
    for candidate in bugs_dir.glob("*.md"):
        if candidate.stem.lower().startswith(slug_prefix):
            return candidate
    return None


def attach_patch_packet_comment(
    bug_id: str,
    patch_packet: dict,
    base_path: "Path | str | None" = None,  # noqa: F821 — accepted but unused; wiki root resolves independently
) -> dict:
    """Append (or replace) a Patch Packet section on the bug's wiki page.

    Returns:
        {"status": "attached", "bug_id": ..., "patch_packet_size_bytes": ...}
        {"status": "error",    "bug_id": ..., "error": "<reason>"}
    """
    if not patch_packet or not any(patch_packet.get(k) for k in (
        "minimal_repro", "root_cause", "test_plan", "implementation_sketch"
    )):
        return {
            "status": "error",
            "bug_id": bug_id,
            "error": "patch_packet is empty — nothing to attach",
        }

    page_path = _find_bug_page(bug_id)
    if page_path is None:
        return {
            "status": "error",
            "bug_id": bug_id,
            "error": f"Bug page not found for {bug_id}",
        }

    try:
        existing = page_path.read_text(encoding="utf-8")
    except OSError as exc:
        return {"status": "error", "bug_id": bug_id, "error": f"Read failed: {exc}"}

    packet_section = format_patch_packet_comment(patch_packet)

    # Replace existing Patch Packet section if present, otherwise append.
    if _PATCH_PACKET_HEADING in existing:
        # Trim from the heading to end-of-file (or next same-level heading).
        head_idx = existing.index(_PATCH_PACKET_HEADING)
        # Find next ## heading after the patch packet (if any)
        next_h2 = re.search(r"\n## ", existing[head_idx + len(_PATCH_PACKET_HEADING):])
        if next_h2:
            tail = existing[head_idx + len(_PATCH_PACKET_HEADING) + next_h2.start():]
            body = existing[:head_idx].rstrip() + packet_section + "\n\n" + tail.lstrip("\n")
        else:
            body = existing[:head_idx].rstrip() + packet_section + "\n"
    else:
        body = existing.rstrip() + packet_section + "\n"

    try:
        page_path.write_text(body, encoding="utf-8")
    except OSError as exc:
        return {"status": "error", "bug_id": bug_id, "error": f"Write failed: {exc}"}

    _logger.info("attach_patch_packet_comment | %s | %s", bug_id, page_path.name)
    return {
        "status": "attached",
        "bug_id": bug_id,
        "patch_packet_size_bytes": len(packet_section.encode()),
    }


# Request type of any already-queued legacy investigation task. Nothing
# enqueues new ones.
REQUEST_TYPE_BUG_INVESTIGATION = "bug_investigation"
