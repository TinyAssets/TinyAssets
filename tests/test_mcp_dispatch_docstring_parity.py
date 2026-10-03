"""MCP dispatch-dict <-> docstring parity guard.

Closes the structural class surfaced in dev-2's REFLECTION (Task #18 audit
follow-up): "feature added under time pressure, dispatch dict grows,
human-readable contract doesn't." Each `@mcp.tool` handler in
`tinyassets.universe_server` that uses an `action: str` dispatch table must
keep its docstring action list in sync with the actually-callable surface.
The docstring is what FastMCP serializes into `tool.description` for every
MCP client (Claude.ai, ChatGPT, Codex, etc.) — drift is a silent
breakage of the human-readable contract.

The test verifies four directions of drift simultaneously:

1. **Undocumented dispatch.** A new dispatch key without a docstring entry
   FAILS. Catches "added under time pressure, forgot the docstring."
2. **Orphaned doc.** A docstring entry that no longer maps to a dispatch
   key FAILS. Catches the reverse drift — action removed from dispatch,
   doc not updated.
3. **Stale allowlist.** A `KNOWN_DEBT` allowlist entry that no longer
   exists in the dispatch dict FAILS. Forces "remove the allowlist line
   when you remove the action."
4. **Over-broad allowlist.** A `KNOWN_DEBT` entry that IS now in the
   docstring FAILS. Forces shrink-or-delete: as docstring debt gets
   backfilled, the allowlist must shrink.

The two allowlist modes (3 + 4) prevent the allowlist from becoming a
write-only dumping ground.

`mcp_server.py` is intentionally out of scope: its 12 `@mcp.tool`
handlers are parameterless single-action tools (`get_status`, `pause`,
`resume`, `add_note`, etc.) with no dispatch table to drift against.
`universe_server.get_status` is similarly parameterless and skipped.
"""

from __future__ import annotations

import inspect
import re

import pytest

from tinyassets import universe_server as us
from tinyassets.api import branches as branches_mod
from tinyassets.api import evaluation as evaluation_mod
from tinyassets.api import market as market_mod
from tinyassets.api import runs as runs_mod
from tinyassets.api import runtime_ops as runtime_ops_mod
from tinyassets.api import universe as universe_mod  # noqa: F401
from tinyassets.api import wiki as wiki_mod

# ---------------------------------------------------------------------------
# KNOWN_DEBT — pre-existing undocumented dispatch keys.
# ---------------------------------------------------------------------------
# Auditable, inline. Each tool's frozenset captures dispatch keys that
# are NOT mentioned in the handler's docstring action list as of the
# 2026-04-26 audit:
#
# - **goals.set_canonical** — added with the Mark-branch-canonical
#   decision (STATUS.md "Mark-branch canonical decision (Task #33 phase 0)")
#   without a docstring update. Trivial backfill, tracked separately.
#
# - **extensions.<42 keys>** — Phase 3-7 dispatch tables outgrew the
#   docstring's `Action groups:` block during 2026-Q1. Tracked as a
#   navigator-vetted backfill task: some of these (e.g. internal escrow,
#   gate_event ops) may deserve permanent allowlist status as advanced
#   internal verbs; others (e.g. messaging_*, project_memory_*) belong in
#   the user-facing docstring.
#
# To shrink: when a docstring backfill lands, remove the corresponding
# entries here. The over-broad-allowlist test (mode 4) will tell you if
# you forgot.
# ---------------------------------------------------------------------------

KNOWN_DEBT: dict[str, frozenset[str]] = {
    "universe": frozenset(),
    "gates": frozenset(),
    "wiki": frozenset(),
    "goals": frozenset(),
    "extensions": frozenset({
        # Escrow surface — chatbot-facing only after Phase 6 ladder lands.
        "escrow_lock", "escrow_release", "escrow_refund", "escrow_inspect",
        # Gate-event surface — chatbot-facing only after gate UX matures.
        "attest_gate_event", "verify_gate_event", "dispute_gate_event",
        "retract_gate_event", "get_gate_event", "list_gate_events",
        # Outcome ledger — internal until Phase 6 surfaces it to chatbots.
        "record_outcome", "list_outcomes", "get_outcome",
        # Attribution / provenance — internal substrate.
        "record_remix", "get_provenance",
        # Dry-inspect surfaces — debug-only, not for normal chat use.
        "dry_inspect_node", "dry_inspect_patch",
        # Messaging surface — pre-launch, not yet chatbot-routed.
        "messaging_send", "messaging_receive", "messaging_ack",
    }),
}


# ---------------------------------------------------------------------------
# Per-tool dispatch-key sources.
# ---------------------------------------------------------------------------


def _universe_dispatch_keys() -> set[str]:
    """Hand-maintained mirror of the `universe` tool's dispatchable actions.

    Add a key here when you add one to ``universe_mod.UNIVERSE_ACTIONS`` and
    document it in the tool docstring — the mirror is what makes an undocumented
    *or* an orphaned-doc addition fail rather than pass silently.
    """
    return {
        "list", "inspect", "read_output", "query_world",
        "get_activity", "get_recent_events", "get_ledger",
        "submit_request", "give_direction",
        "read_premise", "set_premise", "set_visibility", "soul.edit",
        "add_canon",
        "list_canon", "read_canon", "list_sources", "read_source",
        "control_daemon", "switch_universe", "create_universe",
        "queue_list", "queue_cancel",
        "subscribe_goal", "unsubscribe_goal", "list_subscriptions",
        "post_to_goal_pool", "submit_node_bid",
        "daemon_overview", "daemon_list", "daemon_get",
        "daemon_create", "daemon_summon", "daemon_pause",
        "daemon_resume", "daemon_restart", "daemon_banish",
        "daemon_update_behavior", "daemon_control_status",
        "daemon_memory_capture", "daemon_memory_search",
        "daemon_memory_list", "daemon_memory_review",
        "daemon_memory_promote", "daemon_memory_status",
        "set_tier_config", "treasury_status",
    }


def _wiki_dispatch_keys() -> set[str]:
    """Derived from the real dispatch table in tinyassets.api.wiki."""
    return set(wiki_mod.WIKI_ACTIONS)


def _extensions_dispatch_keys() -> set[str]:
    """Union of every dispatch table the `extensions` tool routes."""
    inline = {
        "register", "list", "inspect",
        "approve", "disable", "enable", "remove", "get_action_scope_status",
    }
    return (
        inline
        | set(branches_mod._BRANCH_ACTIONS.keys())
        | set(runs_mod._RUN_ACTIONS.keys())
        | set(evaluation_mod._JUDGMENT_ACTIONS.keys())
        | set(runtime_ops_mod._PROJECT_MEMORY_ACTIONS.keys())
        | set(evaluation_mod._BRANCH_VERSION_ACTIONS.keys())
        | set(runtime_ops_mod._MESSAGING_ACTIONS.keys())
        | set(market_mod._ESCROW_ACTIONS.keys())
        | set(market_mod._GATE_EVENT_ACTIONS.keys())
        | set(runtime_ops_mod._INSPECT_DRY_ACTIONS.keys())
        | set(runtime_ops_mod._SCHEDULER_ACTIONS.keys())
        | set(market_mod._OUTCOME_ACTIONS.keys())
        | set(market_mod._ATTRIBUTION_ACTIONS.keys())
    )


# ---------------------------------------------------------------------------
# Per-tool docstring action-list extractors.
#
# The docstring action list is the authoritative source for "what's
# documented" — we can't ask the test author to re-state it. Each tool
# uses a slightly different convention:
#
# - universe / wiki: `action: One of — <group>: a, b, c; <group>: d, e;`
# - gates / goals: an `Actions:` block with `  <name>  <description>` lines
# - extensions: an `Action groups:` bullet list `- <Group>: a, b, c.`
#
# Extractors return the set of identifier-shaped action names mentioned
# inside the action-list slab only. Prose elsewhere in the docstring is
# ignored — that's why we slice via stable section anchors first.
# ---------------------------------------------------------------------------


def _extract_slab(doc: str, start_re: str, end_re: str) -> str:
    """Return the docstring slab between two anchor regexes, or empty."""
    sm = re.search(start_re, doc)
    if not sm:
        return ""
    rest = doc[sm.end():]
    em = re.search(end_re, rest)
    return rest[:em.start()] if em else rest


def _flat_group_actions(slab: str) -> set[str]:
    """Parse a `<group>: a, b, c; <group>: d, e;` slab into action names."""
    # Parentheticals can span lines; strip them first so internal
    # punctuation doesn't fragment the group structure.
    slab = re.sub(r"\([^)]*\)", "", slab, flags=re.DOTALL)
    out: set[str] = set()
    for chunk in slab.split(";"):
        # Drop the "<group>:" prefix if present.
        if ":" in chunk:
            chunk = chunk.split(":", 1)[1]
        for item in chunk.split(","):
            item = item.strip().rstrip(".").strip()
            if re.fullmatch(r"[a-z_][a-z0-9_.]*[a-z0-9_]", item) or re.fullmatch(r"[a-z_]", item):
                out.add(item)
    return out


def _bullet_group_actions(slab: str) -> set[str]:
    """Parse a `- <Group>: a, b, c.` bullet-list slab into action names.

    Continuation lines (any line starting with whitespace) are folded into
    their parent bullet so "Branch atomic: a, b, c,\\n  d, e, f" parses
    as one bullet with six actions.
    """
    bullets: list[str] = []
    cur = ""
    for line in slab.splitlines():
        if not line.strip():
            continue
        if line.startswith((" ", "\t")):
            cur += " " + line.strip()
        else:
            if cur:
                bullets.append(cur)
            cur = line.strip()
    if cur:
        bullets.append(cur)

    out: set[str] = set()
    for b in bullets:
        if b.startswith("- "):
            b = b[2:]
        b = re.sub(r"\([^)]*\)", "", b)
        if ":" in b:
            b = b.split(":", 1)[1]
        for item in re.split(r"[,;]", b):
            item = item.strip().rstrip(".").strip()
            if re.fullmatch(r"[a-z_][a-z0-9_.]*[a-z0-9_]", item) or re.fullmatch(r"[a-z_]", item):
                out.add(item)
    return out


def _block_actions(slab: str, indent: int = 2) -> set[str]:
    """Parse an `Actions:`-style block (one action per line) into names.

    Each action line is `<indent-spaces><name><whitespace><description>`.
    The action name is the first identifier on a line at *exactly* the
    given indent (continuation lines are deeper-indented and ignored).
    A single space between name and description is enough — gates and
    goals column-align via padding spaces, but the longest name in each
    block is followed by just one.
    """
    pattern = re.compile(rf"^ {{{indent}}}([a-z_][a-z0-9_]+)\s+\S")
    out: set[str] = set()
    for line in slab.splitlines():
        # Continuation lines have deeper indent — skip explicitly so a
        # word starting with `[a-z_]` mid-description can't masquerade
        # as an action.
        leading = len(line) - len(line.lstrip(" "))
        if leading != indent:
            continue
        m = pattern.match(line)
        if m:
            out.add(m.group(1))
    return out


def _tool_doc(handler) -> str:
    """Return the normalized docstring shape exposed to MCP clients."""
    return inspect.getdoc(handler) or ""


def _docstring_actions_universe() -> set[str]:
    doc = _tool_doc(us.universe)
    slab = _extract_slab(
        doc,
        r"action:\s*One of\s*[—\-]",
        r"\n {4}\w+:\s",
    )
    return _flat_group_actions(slab)


def _docstring_actions_wiki() -> set[str]:
    doc = _tool_doc(us.wiki)
    slab = _extract_slab(
        doc,
        r"action:\s*One of\s*[—\-]",
        r"\n {4}\w+:\s",
    )
    return _flat_group_actions(slab)


def _docstring_actions_gates() -> set[str]:
    doc = _tool_doc(us.gates)
    primary = _extract_slab(doc, r"\nActions \([^)]*\):\s*\n", r"\n\n")
    bonus = _extract_slab(doc, r"\nBonus actions \([^)]*\):\s*\n", r"\n\n")
    return _block_actions(primary) | _block_actions(bonus)


def _docstring_actions_goals() -> set[str]:
    doc = _tool_doc(us.goals)
    slab = _extract_slab(doc, r"\nActions:\s*\n", r"\n\n")
    return _block_actions(slab)


def _docstring_actions_extensions() -> set[str]:
    doc = _tool_doc(us.extensions)
    slab = _extract_slab(doc, r"\nAction groups:\s*\n", r"\n\n")
    return _bullet_group_actions(slab)


# ---------------------------------------------------------------------------
# Test cases.
# ---------------------------------------------------------------------------

_PARITY_CASES = [
    pytest.param(
        "universe", us.universe,
        _universe_dispatch_keys(),
        _docstring_actions_universe(),
        id="universe",
    ),
    pytest.param(
        "gates", us.gates,
        set(market_mod._GATES_ACTIONS.keys()),
        _docstring_actions_gates(),
        id="gates",
    ),
    pytest.param(
        "wiki", us.wiki,
        _wiki_dispatch_keys(),
        _docstring_actions_wiki(),
        id="wiki",
    ),
    pytest.param(
        "goals", us.goals,
        set(market_mod._GOAL_ACTIONS.keys()),
        _docstring_actions_goals(),
        id="goals",
    ),
    pytest.param(
        "extensions", us.extensions,
        _extensions_dispatch_keys(),
        _docstring_actions_extensions(),
        id="extensions",
    ),
]


@pytest.mark.parametrize("name,handler,dispatch,documented", _PARITY_CASES)
def test_no_undocumented_dispatch_keys(
    name: str, handler, dispatch: set[str], documented: set[str],
) -> None:
    """Every dispatch key must be documented (mode 1).

    Pre-existing debt is tracked in `KNOWN_DEBT`; everything else fails
    the test the moment a new dispatch key lands without a docstring.
    """
    assert handler.__doc__, f"@mcp.tool {name!r} has no docstring"
    debt = KNOWN_DEBT.get(name, frozenset())
    expected_documented = dispatch - debt
    missing = sorted(expected_documented - documented)
    assert not missing, (
        f"@mcp.tool `{name}`: dispatch keys missing from docstring "
        f"action list: {missing}. Either add a mention to the docstring's "
        f"action list, or — if the omission is intentional and tracked — "
        f"add the key to KNOWN_DEBT[{name!r}] in "
        f"tests/test_mcp_dispatch_docstring_parity.py with a follow-up "
        f"reference."
    )


@pytest.mark.parametrize("name,handler,dispatch,documented", _PARITY_CASES)
def test_no_orphaned_documented_actions(
    name: str, handler, dispatch: set[str], documented: set[str],
) -> None:
    """Every documented action must map to a real dispatch key (mode 2)."""
    orphans = sorted(documented - dispatch)
    assert not orphans, (
        f"@mcp.tool `{name}`: docstring action list mentions actions "
        f"that are not in the dispatch dict: {orphans}. Either remove "
        f"them from the docstring or wire them into dispatch."
    )


def test_wiki_docstring_tells_clients_to_file_bug_directly() -> None:
    """The wiki tool description must not trigger costly filing pre-flight."""
    doc = _tool_doc(us.wiki)

    assert "call `file_bug` directly" in doc
    assert "duplicate detection server-side" in doc
    assert "do NOT need to search/list/read" in doc
    assert "status=\"similar_found\"" in doc
    assert "Start with `action=\"list\"`" not in doc
    assert "Start with `action=\"read\"`" not in doc


@pytest.mark.parametrize("name,handler,dispatch,documented", _PARITY_CASES)
def test_known_debt_does_not_name_missing_keys(
    name: str, handler, dispatch: set[str], documented: set[str],
) -> None:
    """KNOWN_DEBT must only name real dispatch keys (mode 3)."""
    debt = KNOWN_DEBT.get(name, frozenset())
    stale = sorted(debt - dispatch)
    assert not stale, (
        f"KNOWN_DEBT[{name!r}] names keys that no longer exist in "
        f"dispatch: {stale}. Remove them from the allowlist — the "
        f"docstring debt for these keys is already resolved."
    )


@pytest.mark.parametrize("name,handler,dispatch,documented", _PARITY_CASES)
def test_known_debt_does_not_name_documented_keys(
    name: str, handler, dispatch: set[str], documented: set[str],
) -> None:
    """KNOWN_DEBT must not name keys that are already documented (mode 4).

    Forces shrink-or-delete discipline: as docstring debt gets backfilled,
    the corresponding KNOWN_DEBT entry must be removed in the same change.
    """
    debt = KNOWN_DEBT.get(name, frozenset())
    over_broad = sorted(debt & documented)
    assert not over_broad, (
        f"KNOWN_DEBT[{name!r}] names keys that ARE already in the "
        f"docstring action list: {over_broad}. Remove them from the "
        f"allowlist — the docstring already covers them."
    )
