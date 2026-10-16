"""Runtime memory packet builder for soul-bearing daemon wikis.

The daemon wiki is durable storage. Runtime prompts get a bounded memory packet
built from that storage, not the entire wiki -- the bound is on what is SENT to
a model, which is a prompt-shaping decision.

Nothing here bounds or evicts the STORAGE. This module used to carry an
age-scaled byte cap (16 MiB in the first month, plateauing at 64/128 MiB) and a
compactor that DELETED the daemon's own raw signal files to stay under it.
Founder, 2026-09-30: an account has exactly two limits, the cloud bytes a
universe occupies and its concurrent agent seats. Wiki bytes are those bytes;
they are measured once, over the universe, by the tier storage gate -- not by a
second cap per primitive, and never by deleting the user's records.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

#: Characters of wiki/brain context a runtime PROMPT may carry. A prompt bound,
#: not a storage bound: it shapes what one model call sees and deletes nothing.
DEFAULT_MEMORY_PACKET_CHARS = 8000
DEFAULT_BRAIN_PACKET_CHARS = 1600


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _metadata_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _iter_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return [path for path in root.rglob("*") if path.is_file()]


def daemon_wiki_status(
    base_path: str | Path,
    *,
    daemon_id: str,
    daemon: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Observe how much a daemon wiki occupies. An OBSERVATION, not a limit.

    No cap, no usage ratio, no pressure level and no ``needs_compaction``: those
    described a per-wiki byte ceiling this platform no longer has, and the last
    of them was the trigger for deleting the daemon's own records. Universe bytes
    are accounted once, by the tier storage gate.
    """
    from tinyassets.daemon_wiki import daemon_wiki_root

    root = daemon_wiki_root(base_path, daemon_id)
    files = _iter_files(root)
    return {
        "daemon_id": daemon_id,
        "host_local": True,
        "exists": root.exists(),
        "wiki_root": str(root),
        "total_bytes": sum(path.stat().st_size for path in files),
        "file_count": len(files),
    }


def _read_section(
    root: Path,
    rel_path: str,
    *,
    remaining: int,
) -> tuple[str, bool]:
    if remaining <= 0:
        return "", True
    path = root / rel_path
    if not path.exists():
        return "", False
    text = path.read_text(encoding="utf-8")
    section = f"\n\n<!-- {rel_path} -->\n{text.strip()}\n"
    if len(section) > remaining:
        marker = "\n[truncated]\n"
        if remaining <= len(marker):
            return section[:remaining], True
        return section[: remaining - len(marker)].rstrip() + marker, True
    return section, False


def _default_brain_query(daemon: dict[str, Any]) -> str:
    claims = daemon.get("domain_claims") or []
    metadata = _metadata_dict(daemon.get("metadata"))
    role_terms = [
        str(metadata.get("loop_core_role") or ""),
        str(metadata.get("fixed_llm") or metadata.get("pinned_llm") or ""),
    ]
    return " ".join(
        item.strip()
        for item in [*role_terms, *[str(claim) for claim in claims]]
        if item and item.strip()
    )


def build_daemon_memory_packet(
    base_path: str | Path,
    *,
    daemon_id: str,
    max_chars: int = DEFAULT_MEMORY_PACKET_CHARS,
    include_brain: bool = True,
    brain_query: str | None = None,
    brain_max_chars: int = DEFAULT_BRAIN_PACKET_CHARS,
    brain_limit: int = 5,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build the bounded soul/wiki context packet for one daemon run.

    ``max_chars`` bounds the PROMPT. Nothing here evicts stored wiki files: the
    ``enforce_cap`` flag that used to run a compactor on every packet build is
    gone with the per-wiki byte cap it served.
    """
    from tinyassets.daemon_registry import get_daemon
    from tinyassets.daemon_wiki import daemon_wiki_root, scaffold_daemon_wiki

    current = now or _utc_now()
    daemon = get_daemon(base_path, daemon_id=daemon_id, include_soul=True)
    if not daemon.get("has_soul"):
        raise ValueError("soulless daemons do not have memory packets")

    scaffold_daemon_wiki(
        base_path,
        daemon=daemon,
        soul_text=str(daemon.get("soul_text") or ""),
    )
    status = daemon_wiki_status(
        base_path,
        daemon_id=daemon_id,
        daemon=daemon,
        now=current,
    )
    root = daemon_wiki_root(base_path, daemon_id)

    budget = max(0, int(max_chars))
    soul_text = str(daemon.get("soul_text") or "").strip()
    soul_capsule = soul_text[:1200].rstrip()
    if len(soul_text) > len(soul_capsule):
        soul_capsule += "\n[truncated]"

    header = (
        "# Daemon Memory Packet\n\n"
        f"- Daemon: {daemon.get('display_name')} ({daemon_id})\n"
        f"- Soul hash: {daemon.get('soul_hash')}\n"
        f"- Domain claims: {', '.join(daemon.get('domain_claims') or []) or 'none'}\n"
        f"- Wiki bytes: {status['total_bytes']} in {status['file_count']} file(s)\n\n"
        "## Soul Capsule\n\n"
        f"{soul_capsule or 'No soul text available.'}\n"
    )
    context = header[:budget]
    truncated = len(header) > budget
    reserved_brain_chars = 0
    if include_brain and brain_query is not None and not truncated:
        reserved_brain_chars = min(
            max(0, int(brain_max_chars)),
            max(0, budget - len(context)),
        )

    for rel_path in (
        "WIKI.md",
        "index.md",
        "pages/decisions/decision-policy.md",
        "pages/self-model/current-self.md",
        "pages/brain/blocked-patterns.md",
        "pages/brain/review.md",
        "pages/signals/compaction-summary.md",
        "pages/signals/learning-signals.md",
        "pages/soul-evolution/proposals.md",
    ):
        if truncated:
            break
        wiki_remaining = budget - len(context) - reserved_brain_chars
        if wiki_remaining <= 0:
            break
        section, section_truncated = _read_section(
            root,
            rel_path,
            remaining=wiki_remaining,
        )
        context += section
        truncated = section_truncated

    brain = None
    if include_brain and (not truncated or reserved_brain_chars > 0):
        remaining = budget - len(context)
        if remaining > 0:
            from tinyassets.daemon_brain import DaemonBrain
            from tinyassets.memory.runtime import session_memory_scope

            with session_memory_scope() as memory:
                brain = DaemonBrain(
                    memory.source("daemon"), daemon_id=daemon_id
                ).build_packet(
                    query=brain_query if brain_query is not None else _default_brain_query(daemon),
                    max_chars=min(max(0, int(brain_max_chars)), remaining),
                    limit=brain_limit,
                )
            brain_section = "\n\n" + str(brain.get("context") or "")
            if len(brain_section) > remaining:
                marker = "\n[truncated]\n"
                if remaining <= len(marker):
                    brain_section = brain_section[:remaining]
                else:
                    brain_section = brain_section[: remaining - len(marker)].rstrip() + marker
                truncated = True
            context += brain_section

    return {
        "daemon_id": daemon_id,
        "host_local": True,
        "exists": root.exists(),
        "schema_version": 1,
        "context": context.strip(),
        "max_chars": budget,
        "truncated": truncated,
        "memory_status": status,
        "brain": brain,
    }
