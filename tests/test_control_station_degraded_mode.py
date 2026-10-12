"""Tests for control_station directives (hard rules 10 + 11).

Rule 10 (degraded-mode, task #13):
- Devin Session 2 §6 (2026-04-18): chatbot fabricated tier-2 routing
  claims when `get_status` was absent.
- P0 uptime canary probe (2026-04-19): chatbot fabricated a 6-node
  workflow JSON + session history when MCP returned Session terminated.

Rule 11 (shared-account / cross-session, task #5):
- Devin Session 2 (first incident): chatbot asserted host session memory
  as Devin's own history.
- P0 uptime probe: chatbot claimed fabricated prior work from another
  session as the bare-user's lived experience.
- DO-cutover echo: third incident confirmed recurring pattern.

Refs:
- `2026-04-19-devin-session-2.md (archived in git)` §6
- `2026-04-19-p0-uptime-canary-probe.md (archived in git)` §2.2
- `2026-04-19-shared-account-tier2-ux.md (archived in git)`

The directives live in `tinyassets.api.prompts._CONTROL_STATION_PROMPT`.
These tests catch silent removal or accidental regression of key phrases.
"""

from __future__ import annotations

import asyncio
import re

from tinyassets.api.prompts import _CONTROL_STATION_PROMPT
from tinyassets.universe_server import control_station, mcp


def _prompt_text() -> str:
    """Canonical: prompt body as the chatbot receives it."""
    return control_station()


def test_prompt_body_matches_module_constant():
    """Sanity — the @mcp.prompt function must return the module constant."""
    assert _prompt_text() == _CONTROL_STATION_PROMPT


def test_control_station_routes_supported_creation_without_repo_fallback():
    """The prompt must create through the canonical graph, never repo YAML."""
    body = _prompt_text()
    assert "GitHub Actions YAML" not in body
    assert 'write_graph target="branch"' in body
    assert "operation `create`" in body
    assert "publish its immutable version before cloud activation" in body
    assert "standalone node registration" in body
    assert "unavailable" in body


# ---- directive presence ---------------------------------------------------


def test_degraded_mode_directive_is_present():
    """The degraded-mode hard rule must exist as a numbered rule."""
    body = _prompt_text()
    # Hard rule 10 is the degraded-mode directive per task #13 landing.
    assert re.search(
        r"^\s*10\.\s*Degraded-mode",
        body,
        re.MULTILINE,
    ), "Hard rule 10 (degraded-mode) missing from control_station prompt"


def test_degraded_mode_directive_lives_in_hard_rules_block():
    """Directive must be inside the `## Hard Rules` section, not elsewhere."""
    body = _prompt_text()
    hard_rules_start = body.find("## Hard Rules")
    tool_catalog_start = body.find("## Tool Catalog")
    assert hard_rules_start != -1, "Hard Rules heading missing"
    assert tool_catalog_start != -1, "Tool Catalog heading missing"
    assert hard_rules_start < tool_catalog_start, "section order regressed"

    hard_rules_block = body[hard_rules_start:tool_catalog_start]
    assert "Degraded-mode" in hard_rules_block, (
        "degraded-mode directive escaped the Hard Rules block"
    )


# ---- trigger-phrase coverage ---------------------------------------------


def test_directive_names_session_terminated_trigger():
    """The directive MUST name 'Session terminated' — the P0 evidence string."""
    body = _prompt_text()
    assert "Session terminated" in body, (
        "'Session terminated' trigger phrase missing — the exact symptom "
        "the P0 probe reported must be recognized by the chatbot"
    )


def test_directive_names_multiple_failure_triggers():
    """Multiple failure-signature phrases — Session terminated is only one shape."""
    body = _prompt_text()
    # These cover the likely shapes of live tool failure the chatbot will see.
    required_triggers = ["Session terminated", "tool error", "not reachable"]
    missing = [t for t in required_triggers if t not in body]
    assert not missing, (
        f"directive trigger phrases missing: {missing}. "
        f"Each shape the chatbot might see must be named so the rule fires."
    )


def test_directive_covers_every_advertised_handle_without_stale_enumeration():
    """The degraded-mode rule applies to the dynamic advertised surface."""
    body = _prompt_text()
    # Find the rule-10 block for targeted assertion.
    rule_10_match = re.search(
        r"10\.\s*Degraded-mode.*?(?=^\s*\d+\.|^## )",
        body,
        re.MULTILINE | re.DOTALL,
    )
    assert rule_10_match, "could not locate rule 10 block"
    rule_10 = rule_10_match.group(0)
    advertised = {
        tool.name
        for tool in asyncio.run(mcp.list_tools(run_middleware=True))
    }
    assert advertised
    assert "any advertised handle" in rule_10


# ---- forbidden-action coverage -------------------------------------------


def test_directive_forbids_fabrication():
    """'fabricate' is the load-bearing verb AND must be in imperative-negative context.

    Substring-check alone was verifier-flagged as weak — it would pass if someone
    wrote 'You may fabricate anything you want.' Regex now requires a negative
    imperative ('Do NOT fabricate', 'never fabricate', 'must not fabricate').
    """
    body = _prompt_text()
    assert "fabricate" in body.lower(), (
        "'fabricate' forbidden-action verb missing from prompt — "
        "the rule's teeth depend on this word"
    )
    # Imperative-negative context — protects against rule dilution to permissive.
    assert re.search(
        r"(do\s+not|never|must\s+not|don['\u2019]t)\s+\w*\s*fabricat",
        body,
        re.IGNORECASE,
    ), (
        "'fabricate' is present but not in a negative-imperative context "
        "('Do NOT fabricate' / 'never fabricate' / 'must not fabricate'). "
        "Rule dilution risk — strengthen the forbidden-action phrasing."
    )


def test_directive_forbids_session_history_fabrication():
    """Rule must explicitly forbid claiming prior-turn session history.

    This is the Devin-S2 + P0-probe sub-finding: chatbot claimed
    'pick up from the X node you began earlier' with no prior evidence.
    """
    body = _prompt_text()
    # Either "session history" or the specific "pick up from" pattern must appear.
    assert (
        "session history" in body.lower()
        or "pick up from" in body.lower()
    ), (
        "rule must explicitly forbid session-history fabrication — "
        "the Devin-S2 + P0-probe cross-session pattern"
    )


def test_directive_requires_plain_failure_disclosure():
    """Rule must tell the chatbot to TELL THE USER the connector is degraded."""
    body = _prompt_text()
    rule_10_match = re.search(
        r"10\.\s*Degraded-mode.*?(?=^\s*\d+\.|^## )",
        body,
        re.MULTILINE | re.DOTALL,
    )
    assert rule_10_match
    rule_10 = rule_10_match.group(0).lower()
    # The honest-disclosure clause. Accept several natural phrasings.
    assert any(
        phrase in rule_10
        for phrase in ["tell the user", "degraded", "connector isn't responding", "can't reach"]
    ), "rule must instruct the chatbot to disclose the failure to the user"


# ---- rule-interaction -----------------------------------------------------


def test_directive_names_rules_it_overrides():
    """Rule 10 must explicitly override rule 2 (use tools) + rule 7 (assume).

    Without explicit override language, chatbot may resolve the rules
    in the wrong order and keep trying to fabricate through failure.
    """
    body = _prompt_text()
    rule_10_match = re.search(
        r"10\.\s*Degraded-mode.*?(?=^\s*\d+\.|^## )",
        body,
        re.MULTILINE | re.DOTALL,
    )
    assert rule_10_match
    rule_10 = rule_10_match.group(0)
    # Rule 2 is "Always use tools"; rule 7 is "Assume TinyAssets on plausible intent."
    # Rule 10 must call those out by number so the precedence is unambiguous.
    assert "rule 2" in rule_10.lower(), "rule 10 must override rule 2 explicitly"
    assert "rule 7" in rule_10.lower(), "rule 10 must override rule 7 explicitly"


# ---- Rule 11: shared-account / cross-session directive -------------------
#
# Three live incidents (Devin S2 + P0 probe + DO-cutover echo) showed the
# chatbot asserting another session's memory as the current user's fact.
# Rule 11 must tell the chatbot to ASK rather than ASSERT when cross-session
# context is detected.


def _rule_11_block(body: str) -> str:
    m = re.search(
        r"11\.\s*Shared-account.*?(?=^\s*\d+\.|^## )",
        body,
        re.MULTILINE | re.DOTALL,
    )
    assert m, "could not locate rule 11 block in control_station prompt"
    return m.group(0)


def test_shared_account_rule_is_present():
    """Hard rule 11 (shared-account) must exist as a numbered rule."""
    body = _prompt_text()
    assert re.search(
        r"^\s*11\.\s*Shared-account",
        body,
        re.MULTILINE,
    ), "Hard rule 11 (shared-account) missing from control_station prompt"


def test_shared_account_rule_in_hard_rules_block():
    """Rule 11 must sit inside the ## Hard Rules section, before ## Tool Catalog."""
    body = _prompt_text()
    hard_rules_start = body.find("## Hard Rules")
    tool_catalog_start = body.find("## Tool Catalog")
    assert hard_rules_start != -1
    assert tool_catalog_start != -1
    hard_rules_block = body[hard_rules_start:tool_catalog_start]
    assert "Shared-account" in hard_rules_block, (
        "rule 11 escaped the Hard Rules block"
    )


def test_shared_account_rule_says_ask_not_assert():
    """Rule 11 must instruct the chatbot to ASK rather than assert prior history."""
    rule = _rule_11_block(_prompt_text())
    # The core behavioral requirement: ask / question the user, don't assert.
    assert re.search(
        r"\b(ask|question|confirm|redirect)\b",
        rule,
        re.IGNORECASE,
    ), "rule 11 must instruct chatbot to ask/confirm rather than silently assert"


def test_shared_account_rule_forbids_asserting_cross_session_history():
    """Rule 11 must forbid asserting cross-session memory as the current user's fact."""
    rule = _rule_11_block(_prompt_text())
    # Must have a negative-imperative around assert/claim/treat.
    assert re.search(
        r"(do\s+not|never|must\s+not|don['\u2019]t)\s+\w*\s*(assert|claim|treat)",
        rule,
        re.IGNORECASE,
    ), (
        "rule 11 must contain a negative-imperative ('do NOT assert', 'never claim', "
        "etc.) to prevent cross-session history bleed"
    )


def test_shared_account_rule_uses_user_vocabulary():
    """Rule 11 must phrase the user-facing question in plain language, not engine terms.

    The user-vocabulary discipline (feedback_user_vocabulary_discipline) forbids
    surfacing engine terms to users. The chatbot's suggested question in rule 11
    should say 'different person sharing this account' or similar plain language,
    NOT 'cross-session memory' or 'context bleed'.
    """
    rule = _rule_11_block(_prompt_text())
    # Plain-language user-question must appear.
    assert re.search(
        r"(sharing this account|different (user|person)|previous session)",
        rule,
        re.IGNORECASE,
    ), (
        "rule 11's user-facing question must use plain language "
        "('sharing this account', 'different user', 'previous session') "
        "not engine vocabulary"
    )
    # Engine vocabulary must NOT appear in the user-facing suggested question.
    # We allow these terms in the rule body for internal description, but
    # the chatbot's literal suggested question (in quotes) must be clean.
    quoted = re.findall(r'"([^"]+)"', rule)
    for q in quoted:
        assert "cross-session" not in q.lower(), (
            "user-facing question quote contains 'cross-session' — engine vocabulary"
        )
        assert "memory bleed" not in q.lower(), (
            "user-facing question quote contains 'memory bleed' — engine vocabulary"
        )


def test_shared_account_rule_covers_silent_action_case():
    """Rule 11 must allow SILENT ACTION when current prompt is self-contained.

    Over-asking is the failure mode for households using TinyAssets weekly.
    The rule must say when NOT to ask (zero-friction path) as well as when to ask.
    """
    rule = _rule_11_block(_prompt_text())
    assert re.search(
        r"silent\s+action|self.contained|irrelevant",
        rule,
        re.IGNORECASE,
    ), (
        "rule 11 must specify the zero-friction path (silent action when "
        "prior context is not load-bearing) to prevent over-asking"
    )


# ---- Rule 13: prior-run re-anchor directive ------------------------------
#
# 2026-04-23 sweep identified chatbots asserting results from prior runs
# without calling list_runs + get_run_output first. Priya S2, Devin M27
# both probe this pattern. Rule 13 must force tool-first re-anchor.


def _rule_13_block(body: str) -> str:
    m = re.search(
        r"13\.\s*Re-anchor.*?(?=^\s*\d+\.|^## )",
        body,
        re.MULTILINE | re.DOTALL,
    )
    assert m, "could not locate rule 13 block in control_station prompt"
    return m.group(0)


def test_rule_13_is_present():
    """Hard rule 13 (re-anchor to prior runs) must exist as a numbered rule."""
    body = _prompt_text()
    assert re.search(
        r"^\s*13\.\s*Re-anchor",
        body,
        re.MULTILINE,
    ), "Hard rule 13 (re-anchor to prior runs via tools) missing from control_station prompt"


def test_rule_13_in_hard_rules_block():
    """Rule 13 must sit inside the ## Hard Rules section, before ## Tool Catalog."""
    body = _prompt_text()
    hard_rules_start = body.find("## Hard Rules")
    tool_catalog_start = body.find("## Tool Catalog")
    assert hard_rules_start != -1
    assert tool_catalog_start != -1
    hard_rules_block = body[hard_rules_start:tool_catalog_start]
    assert "Re-anchor" in hard_rules_block, (
        "rule 13 escaped the Hard Rules block"
    )


def test_rule_13_names_canonical_run_listing():
    """Rule 13 must use the canonical runs read first."""
    rule = _rule_13_block(_prompt_text())
    assert 'read_graph target="runs"' in rule


def test_rule_13_names_canonical_run_result_read():
    """Rule 13 must retrieve the actual result through read_graph."""
    rule = _rule_13_block(_prompt_text())
    assert 'read_graph target="run"' in rule


def test_rule_13_forbids_asserting_from_memory():
    """Rule 13 must contain a negative-imperative forbidding assertion from memory."""
    rule = _rule_13_block(_prompt_text())
    assert re.search(
        r"(do\s+not|never|must\s+not|don['’]t)\s+\w*\s*(assert|claim)",
        rule,
        re.IGNORECASE,
    ), (
        "rule 13 must contain a negative-imperative ('do NOT assert', 'never claim') "
        "against asserting prior-run results from memory"
    )


def test_rule_13_names_vague_reference_triggers():
    """Rule 13 must name the canonical vague-reference phrasings that trigger it.

    The 2026-04-23 sweep identified 'extend the sweep', 'pick up from where we
    left off', and 'add RF to what you ran' as Priya-style trigger phrases.
    These must appear in the rule (matching across line-wraps in the prompt).
    """
    rule = _rule_13_block(_prompt_text())
    # Collapse whitespace/newlines for phrase matching — prompt may line-wrap.
    rule_collapsed = " ".join(rule.split())
    triggers = [
        "extend the sweep",
        "pick up from where we left off",
        "add RF to what you ran",
    ]
    missing = [t for t in triggers if t not in rule_collapsed]
    assert not missing, (
        f"rule 13 missing Priya-style trigger phrases: {missing}. "
        "These are the canonical vague-reference phrasings from the 2026-04-23 sweep."
    )


def test_rule_13_specifies_no_matching_run_response():
    """Rule 13 must specify what to say when no matching run exists.

    Without this, chatbots invent a plausible run or silently scaffold a new one.
    The rule must instruct: say 'no matching run' and offer to start fresh.
    """
    rule = _rule_13_block(_prompt_text())
    assert re.search(
        r"no\s+matching\s+run|start\s+fresh|offer\s+to\s+start",
        rule,
        re.IGNORECASE,
    ), (
        "rule 13 must specify the honest-disclosure path when no run matches: "
        "'say so and offer to start fresh'"
    )


# 2026-04-26 visuals-first rule pinned after Maya LIVE-F3 + Devin LIVE-F7 showed
# chatbots describing structure in prose where a diagram would match the user's
# mental model. Rule 14 must enforce lead-with-visual for all multi-part artifacts.


def _rule_14_block(body: str) -> str:
    m = re.search(
        r"14\.\s*Visuals.*?(?=^\s*\d+\.|^## )",
        body,
        re.MULTILINE | re.DOTALL,
    )
    assert m, "could not locate rule 14 block in control_station prompt"
    return m.group(0)


def test_rule_14_is_present():
    """Hard rule 14 (visuals-first) must exist as a numbered rule."""
    body = _prompt_text()
    assert re.search(
        r"^\s*14\.\s*Visuals",
        body,
        re.MULTILINE,
    ), "Hard rule 14 (visuals-first) missing from control_station prompt"


def test_rule_14_in_hard_rules_block():
    """Rule 14 must sit inside the ## Hard Rules section, before ## Tool Catalog."""
    body = _prompt_text()
    hard_rules_start = body.find("## Hard Rules")
    tool_catalog_start = body.find("## Tool Catalog")
    assert hard_rules_start != -1
    assert tool_catalog_start != -1
    hard_rules_block = body[hard_rules_start:tool_catalog_start]
    assert "Visuals" in hard_rules_block, "rule 14 escaped the Hard Rules block"


def test_rule_14_contains_visuals_first_trigger():
    """Rule 14 must contain the 'Visuals-first' trigger phrase."""
    rule = _rule_14_block(_prompt_text())
    assert "Visuals-first" in rule, (
        "rule 14 must contain the trigger phrase 'Visuals-first'"
    )


def test_rule_14_names_mermaid():
    """Rule 14 must name 'mermaid' as a required visual format."""
    rule = _rule_14_block(_prompt_text())
    assert "mermaid" in rule.lower(), (
        "rule 14 must name 'mermaid' as a required diagram format"
    )


def test_rule_14_names_table():
    """Rule 14 must name 'table' as a required visual format."""
    rule = _rule_14_block(_prompt_text())
    assert "table" in rule.lower(), (
        "rule 14 must name 'table' (markdown table) as a required visual format"
    )


def test_rule_14_names_diagram():
    """Rule 14 must reference 'diagram' as a visual deliverable."""
    rule = _rule_14_block(_prompt_text())
    assert "diagram" in rule.lower(), (
        "rule 14 must reference 'diagram' as a visual deliverable"
    )
