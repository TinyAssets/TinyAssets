r"""Tests for prompt_template literal-brace escape + build-time validation.

openspec/specs/community-patch-loop/spec.md — "Prompt_template literal-brace escape +
build-time missing-key validation" (navigator-vetted 2026-04-22).

Three additive changes land together:

A. Backslash-escape pass: ``\{ident\}`` renders as literal ``{ident}``
   without substitution.
B. ``BranchDefinition.validate()`` flags ``{ident}`` references that
   are not in ``input_keys ∪ state_schema`` names. Runtime
   ``CompilerError`` stays as the second layer.
C. ``graph_compiler.py`` docstring fix — it falsely claimed
   ``str.format_map`` rendering.

Invariants the 6 required test cases enforce:
- backslash-escape renders literal
- single-brace substitutes
- double-brace (Jinja) normalizes and substitutes
- build-time catches undeclared ref
- build-time does NOT flag escaped refs
- JSON ``{"key": "val"}`` passes through
"""

from __future__ import annotations

import pytest

from tinyassets.branches import BranchDefinition, NodeDefinition
from tinyassets.graph_compiler import (
    _placeholder_keys,
    _render_template,
    _unescape_literal_braces,
)

# ─── Part A: runtime escape handling ──────────────────────────────────────


def test_backslash_escape_renders_literal():
    r"""``\{foo\}`` in source → ``{foo}`` in output; no substitution."""
    out = _render_template(r"write about \{topic\} please", {"topic": "x"})
    assert out == "write about {topic} please"


def test_single_brace_substitutes():
    out = _render_template("write about {topic}", {"topic": "whales"})
    assert out == "write about whales"


def test_double_brace_normalizes_and_substitutes():
    out = _render_template("write about {{topic}}", {"topic": "whales"})
    assert out == "write about whales"


def test_json_braces_pass_through():
    """Non-identifier braces (JSON examples, code fences) survive."""
    template = 'Return JSON like {"title": "x", "body": "y"}'
    out = _render_template(template, {})
    assert out == 'Return JSON like {"title": "x", "body": "y"}'


def test_mixed_escape_and_substitution():
    r"""Both mechanisms coexist in the same template."""
    out = _render_template(
        r"Use \{placeholder\} syntax. Example: {example}",
        {"example": "good"},
    )
    assert out == "Use {placeholder} syntax. Example: good"


def test_escape_with_jinja_sibling():
    r"""``\{literal\}`` + ``{{substituted}}`` both in one template."""
    out = _render_template(
        r"Literal: \{key\}. Substituted: {{val}}.",
        {"val": "hello"},
    )
    assert out == "Literal: {key}. Substituted: hello."


def test_unescape_helper_isolated():
    """The unescape pass is callable standalone for debugging."""
    assert _unescape_literal_braces(r"\{foo\}") == "{foo}"
    assert _unescape_literal_braces("plain text") == "plain text"
    assert _unescape_literal_braces("") == ""


# ─── Static placeholder extraction ─────────────────────────────────────────


def test_placeholder_keys_excludes_escaped_forms():
    """Escaped placeholders are NOT real references — static analysis
    must exclude them so the build-time check doesn't false-flag."""
    refs = _placeholder_keys(r"Use \{literal\}. Real ref: {topic}.")
    assert refs == ["topic"]


def test_placeholder_keys_handles_jinja_normalization():
    refs = _placeholder_keys("Write {{topic}} plus {style}")
    assert refs == ["topic", "style"]


def test_placeholder_keys_dedupes_preserving_order():
    refs = _placeholder_keys("{a} then {b} then {a} again")
    assert refs == ["a", "b"]


# ─── Part B: build-time validation ─────────────────────────────────────────


def _single_node_branch(node: NodeDefinition) -> BranchDefinition:
    return BranchDefinition(
        name="Test", domain_id="workflow", author="tester",
        node_defs=[node],
        graph_nodes=[],
        edges=[],
        entry_point=node.node_id,
    )


def _add_entry_graph_node(
    branch: BranchDefinition, node_id: str,
) -> None:
    from tinyassets.branches import EdgeDefinition, GraphNodeRef
    branch.graph_nodes.append(
        GraphNodeRef(id=node_id, node_def_id=node_id, position=0),
    )
    branch.edges.append(EdgeDefinition(from_node=node_id, to_node="END"))


def test_build_time_catches_undeclared_ref():
    """Validator flags prompt placeholders outside
    input_keys ∪ state_schema names."""
    node = NodeDefinition(
        node_id="n1", display_name="n1",
        input_keys=["topic"],
        output_keys=["draft"],
        prompt_template="Write {topic} in {style}.",  # style not declared
    )
    branch = _single_node_branch(node)
    _add_entry_graph_node(branch, "n1")

    errors = branch.validate()
    assert any("{style}" in e and "n1" in e for e in errors), errors


def test_build_time_accepts_placeholder_in_state_schema():
    """state_schema names count as declared references too."""
    node = NodeDefinition(
        node_id="n1", display_name="n1",
        input_keys=[],
        output_keys=["draft"],
        prompt_template="Retry count: {retry_count}.",
    )
    branch = _single_node_branch(node)
    _add_entry_graph_node(branch, "n1")
    branch.state_schema = [{"name": "retry_count", "type": "int"}]

    errors = branch.validate()
    assert not any("retry_count" in e for e in errors), errors


def test_build_time_does_not_flag_escaped_refs():
    """``\\{ident\\}`` is literal output, not a reference — validator
    must not flag it even if ``ident`` is undeclared."""
    node = NodeDefinition(
        node_id="n1", display_name="n1",
        input_keys=["topic"],
        output_keys=["draft"],
        prompt_template=r"Write {topic}. Escape example: \{placeholder\}.",
    )
    branch = _single_node_branch(node)
    _add_entry_graph_node(branch, "n1")

    errors = branch.validate()
    assert not any("placeholder" in e for e in errors), errors


def test_build_time_does_not_flag_json_braces():
    """JSON-example braces are non-identifier and survive unchanged.
    The validator's regex is identifier-gated, so ``{"key": "val"}``
    is invisible to it."""
    node = NodeDefinition(
        node_id="n1", display_name="n1",
        input_keys=[],
        output_keys=["draft"],
        prompt_template='Return JSON: {"title": "x"}',
    )
    branch = _single_node_branch(node)
    _add_entry_graph_node(branch, "n1")

    errors = branch.validate()
    # No placeholder-related error.
    assert not any("title" in e for e in errors), errors


def test_build_time_flags_every_undeclared_ref():
    """Multiple undeclared references surface as multiple errors."""
    node = NodeDefinition(
        node_id="n1", display_name="n1",
        input_keys=["topic"],
        output_keys=["draft"],
        prompt_template="Use {a} and {b} and {topic}.",
    )
    branch = _single_node_branch(node)
    _add_entry_graph_node(branch, "n1")

    errors = branch.validate()
    flagged = [e for e in errors if "n1" in e and "{" in e]
    flagged_keys = {e for e in flagged if "{a}" in e or "{b}" in e}
    assert len(flagged_keys) == 2, flagged


def test_build_time_handles_jinja_form_in_validation():
    """``{{topic}}`` normalizes to ``{topic}`` before the declaration
    check — otherwise Jinja users would see false-negative on
    undeclared refs."""
    node = NodeDefinition(
        node_id="n1", display_name="n1",
        input_keys=[],  # topic NOT declared
        output_keys=["draft"],
        prompt_template="Write {{topic}}.",
    )
    branch = _single_node_branch(node)
    _add_entry_graph_node(branch, "n1")

    errors = branch.validate()
    assert any("topic" in e and "n1" in e for e in errors), errors


def test_build_time_empty_template_no_errors():
    """Nodes without a prompt_template (source_code nodes, domain
    nodes) are not checked — empty template = no placeholders."""
    node = NodeDefinition(
        node_id="n1", display_name="n1",
        input_keys=[],
        output_keys=["draft"],
        prompt_template="",
        source_code="def run(state): return {'draft': 'x'}",
    ).mark_approved()
    branch = _single_node_branch(node)
    _add_entry_graph_node(branch, "n1")

    errors = branch.validate()
    # No placeholder-related errors (validator skips empty templates).
    assert not any("prompt_template references" in e for e in errors), errors


# ─── Runtime-layer interaction: substitution still raises on missing ───────


def test_runtime_still_raises_on_genuinely_missing_key():
    """Build-time is the first layer; runtime is the second. If the
    branch validator is bypassed or state evolves post-compile, the
    render still raises KeyError (mapped to CompilerError elsewhere)."""
    with pytest.raises(KeyError):
        _render_template("{topic}", {})


def test_validate_and_compiler_placeholder_regexes_stay_in_sync():
    """Belt-and-suspenders guard against drift between the two copies
    of the placeholder regex.

    ``branches.py`` duplicates ``graph_compiler.py``'s placeholder +
    Jinja patterns to avoid a circular import (graph_compiler imports
    BranchDefinition at module load). Both copies carry a "keep in
    sync" comment, but discipline-based comments rot. This test
    surfaces any one-sided edit regardless of whether the behavioral
    tests happen to still pass.
    """
    from tinyassets.branches import (
        _VALIDATE_DOUBLE_PLACEHOLDER_RE,
        _VALIDATE_PLACEHOLDER_RE,
    )
    from tinyassets.graph_compiler import (
        _DOUBLE_PLACEHOLDER_RE,
        _PLACEHOLDER_RE,
    )
    assert _VALIDATE_PLACEHOLDER_RE.pattern == _PLACEHOLDER_RE.pattern
    assert (
        _VALIDATE_DOUBLE_PLACEHOLDER_RE.pattern
        == _DOUBLE_PLACEHOLDER_RE.pattern
    )
