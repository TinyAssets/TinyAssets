"""Keep server-authored MCP guidance aligned with the advertised tool surface.

Authority: ``openspec/specs/live-mcp-connector-surface/spec.md``, especially
Canonical Advertised Handle Set and Remote Streamable-HTTP MCP Endpoint.
"""

from __future__ import annotations

import ast
import asyncio
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from tinyassets import universe_server

_CANONICAL_ADVERTISED_HANDLES = {
    "converse",
    "get_status",
    "read_graph",
    "read_page",
    "run_graph",
    "write_graph",
    "write_page",
}
_ROUTING_HEAD_STOPWORDS = {
    "a",
    "an",
    "for",
    "or",
    "same",
    "that",
    "the",
    "this",
    "use",
    "with",
}
_RETIRED_STANDALONE_ACTIONS = {
    "add_canon",
    "add_canon_from_path",
    "attach_existing_child_run",
    "build_branch",
    "cancel_run",
    "get_run",
    "grant_effector_consent",
    "list_node_versions",
    "publish_version",
    "resume_run",
    "rollback_node",
    "run_branch",
    "run_canonical",
    "set_premise",
    "submit_request",
    "wait_for_run",
}


def _run(awaitable):
    return asyncio.run(awaitable)


def _advertised_tools():
    return {
        tool.name: tool
        for tool in _run(universe_server.mcp.list_tools(run_middleware=True))
    }


#: Tool names that existed and were retired (2026-09-30). Not registered any
#: more, but still heads a stale instruction might name, so the claim detector
#: must keep recognising them.
RETIRED_TOOL_NAMES = frozenset({"universe", "extensions", "goals", "gates", "wiki"})


def _registered_tool_names() -> set[str]:
    return {
        tool.name
        for tool in _run(universe_server.mcp.list_tools(run_middleware=False))
    } | RETIRED_TOOL_NAMES


def _instruction_surfaces() -> dict[str, str]:
    surfaces = {
        "server instructions": universe_server.mcp.instructions or "",
    }
    for prompt in _run(
        universe_server.mcp.list_prompts(run_middleware=False)
    ):
        rendered = _run(
            universe_server.mcp.render_prompt(
                prompt.name,
                run_middleware=False,
            )
        )
        body = "\n".join(
            getattr(message.content, "text", "")
            for message in rendered.messages
        )
        surfaces[f"prompt:{prompt.name}"] = "\n".join(
            filter(None, (prompt.description or "", body))
        )
    return surfaces


def _claimed_tool_names(text: str, registered_names: set[str]) -> set[str]:
    """Extract syntactic tool claims, including unknown/retired tool heads."""
    code_span_heads: set[str] = set()
    for code in re.findall(r"`([^`\n]+)`", text):
        match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_-]*)", code.strip())
        if match is None:
            match = re.match(
                r"([A-Za-z_][A-Za-z0-9_-]*)"
                r"(?:\s+(?:action|target)\s*=|\(\s*action\s*=)",
                code.strip(),
            )
        if match is not None:
            code_span_heads.add(match.group(1))
    claims = {
        name
        for name in registered_names
        if name in code_span_heads
        or re.search(
            rf"(?<![\w-]){re.escape(name)}\s+action\s*=",
            text,
        )
        or re.search(
            rf"\b(?:call|use|via|through)\s+(?:the\s+)?"
            rf"{re.escape(name)}\b",
            text,
        )
        or re.search(
            rf"\b{re.escape(name)}\s+(?:handle|tool)\b",
            text,
        )
    }
    claims.update(
        name
        for name in re.findall(
            r"(?<![\w-])([A-Za-z_][A-Za-z0-9_-]*)\s+"
            r"(?:action|target)\s*=",
            text,
        )
        if name.lower() not in _ROUTING_HEAD_STOPWORDS
    )
    claims.update(
        name
        for name in re.findall(
            r"(?<![\w-])([A-Za-z_][A-Za-z0-9_-]*)\s*"
            r"\(\s*action\s*=",
            text,
        )
        if name.lower() not in _ROUTING_HEAD_STOPWORDS
    )
    claims.update(
        re.findall(
            r"(?m)^\s*\d+\.\s+\*\*`([A-Za-z_][A-Za-z0-9_-]*)`\*\*",
            text,
        )
    )
    claims.update(
        re.findall(
            r"`([A-Za-z_][A-Za-z0-9_-]*)`\s+(?:handle|tool)\b",
            text,
        )
    )
    claims.update(
        name
        for name in re.findall(
            r"\b(?:call|use|via|through|with)\s+(?:the\s+)?"
            r"([A-Za-z_][A-Za-z0-9_-]*)\s+"
            r"(?:action|read|write|list|search|inspect|run)\b",
            text,
            flags=re.IGNORECASE,
        )
        if name in registered_names
    )
    for action in _RETIRED_STANDALONE_ACTIONS:
        if (
            re.search(
                rf"\b(?:call|calling|invoke|run|use|using)\s+"
                rf"(?:the\s+)?`?{re.escape(action)}`?\b",
                text,
                flags=re.IGNORECASE,
            )
            or re.search(
                rf"\b{re.escape(action)}\s+call\b",
                text,
                flags=re.IGNORECASE,
            )
            or re.search(
                rf"\b(?:[A-Za-z_][A-Za-z0-9_]*_)?action\s*=\s*"
                rf"[\"']?{re.escape(action)}\b",
                text,
                flags=re.IGNORECASE,
            )
            or re.search(
                rf"\b{re.escape(action)}\s*\(",
                text,
                flags=re.IGNORECASE,
            )
        ):
            claims.add(action)
    return claims


def _payload_text(payload: object) -> str:
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (TypeError, ValueError):
            return payload
    if isinstance(payload, dict):
        return "\n".join(_payload_text(value) for value in payload.values())
    if isinstance(payload, (list, tuple)):
        return "\n".join(_payload_text(value) for value in payload)
    return ""


def _runtime_source_string_surfaces() -> dict[str, str]:
    """Collect non-docstring runtime literals so rare payload branches count."""
    package_root = Path(universe_server.__file__).resolve().parent
    surfaces: dict[str, str] = {}
    paths = [
        *sorted((package_root / "api").rglob("*.py")),
        *sorted((package_root / "effectors").rglob("*.py")),
        *sorted((package_root / "producers").rglob("*.py")),
        package_root / "graph_compiler.py",
        package_root / "runs.py",
        package_root / "universe_bundle.py",
        package_root / "universe_server.py",
    ]
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        joined_string_parts = {
            id(value)
            for joined in ast.walk(tree)
            if isinstance(joined, ast.JoinedStr)
            for value in joined.values
            if isinstance(value, ast.Constant)
        }
        docstrings: set[int] = set()
        owners = [
            tree,
            *(
                node
                for node in ast.walk(tree)
                if isinstance(
                    node,
                    (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
                )
            ),
        ]
        for owner in owners:
            if (
                owner.body
                and isinstance(owner.body[0], ast.Expr)
                and isinstance(owner.body[0].value, ast.Constant)
                and isinstance(owner.body[0].value.value, str)
            ):
                docstrings.add(id(owner.body[0].value))
        for node in ast.walk(tree):
            if isinstance(node, ast.JoinedStr):
                combined = "".join(
                    value.value
                    if isinstance(value, ast.Constant)
                    and isinstance(value.value, str)
                    else "<value>"
                    for value in node.values
                )
                relative = path.relative_to(package_root)
                surfaces[f"{relative}:{node.lineno}:fstring"] = combined
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in docstrings
                and id(node) not in joined_string_parts
            ):
                relative = path.relative_to(package_root)
                surfaces[f"{relative}:{node.lineno}"] = node.value
            if isinstance(node, ast.Dict):
                for key, value in zip(node.keys, node.values, strict=True):
                    if (
                        isinstance(key, ast.Constant)
                        and isinstance(key.value, str)
                        and key.value.endswith("_action")
                        and isinstance(value, ast.Constant)
                        and isinstance(value.value, str)
                    ):
                        relative = path.relative_to(package_root)
                        surfaces[
                            f"{relative}:{node.lineno}:{key.value}"
                        ] = f"{key.value}={value.value}"
    return surfaces


def _assert_surfaces_claim_only_advertised_handles(
    surfaces: dict[str, object],
) -> None:
    advertised = set(_advertised_tools())
    registered = _registered_tool_names()
    violations: dict[str, list[str]] = {}
    for surface, payload in surfaces.items():
        claimed = _claimed_tool_names(_payload_text(payload), registered)
        hidden = sorted(claimed - advertised)
        if hidden:
            violations[surface] = hidden
    assert not violations, (
        "surfaces claim tools hidden from tools/list: "
        + json.dumps(violations, sort_keys=True)
    )


def _catalog_claims(control_station: str) -> set[str]:
    match = re.search(
        r"(?ms)^## Tool Catalog\b.*?(?=^## |\Z)",
        control_station,
    )
    assert match, "control_station has no Tool Catalog section"
    return set(
        re.findall(
            r"(?m)^\s*\d+\.\s+\*\*`([A-Za-z_][A-Za-z0-9_-]*)`\*\*",
            match.group(0),
        )
    )


def test_instruction_surfaces_claim_only_live_advertised_handles() -> None:
    advertised = set(_advertised_tools())
    assert advertised == _CANONICAL_ADVERTISED_HANDLES
    assert "extensions" not in advertised
    surfaces = _instruction_surfaces()
    _assert_surfaces_claim_only_advertised_handles(surfaces)
    assert (
        _catalog_claims(surfaces["prompt:control_station"])
        == _CANONICAL_ADVERTISED_HANDLES
    )


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ('`foo_graph target="branch"`', "foo_graph"),
        ("extensions action=build_branch", "extensions"),
        ("Use wiki list to see available drafts.", "wiki"),
        ("Write the replacement first with wiki write.", "wiki"),
        ('Continue with wiki(action="read", page="notes/example").', "wiki"),
    ],
)
def test_claimed_tool_names_detect_unregistered_routing_heads(
    mutation: str,
    expected: str,
) -> None:
    """M3/M7: retirement must not make stale or invented routes invisible."""
    advertised = set(_advertised_tools())
    registered = _registered_tool_names()
    assert expected not in advertised
    assert expected in _claimed_tool_names(mutation, registered)


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("extensions action=build_branch", "extensions"),
        ('Continue with wiki(action="read").', "wiki"),
        ("Run list_node_versions to see available targets.", "list_node_versions"),
        ("A later add_canon call replaces the source.", "add_canon"),
        (
            "reclaim_action=attach_existing_child_run",
            "attach_existing_child_run",
        ),
    ],
)
def test_retired_routes_remain_detectable_after_deregistration(
    mutation: str,
    expected: str,
) -> None:
    """M7: removing legacy registration cannot blind the invariant."""
    assert expected in _claimed_tool_names(mutation, set())


def test_runtime_response_payloads_claim_only_live_advertised_handles(
    tmp_path,
    monkeypatch,
) -> None:
    """Canonical results and their delegated envelopes obey the same invariant."""
    from tinyassets.api import selector_dispatch, universe
    from tinyassets.api.visibility import set_universe_visibility
    from tinyassets.daemon_server import ensure_universe_registered
    from tinyassets.exceptions import AllProvidersExhaustedError

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(data_dir))
    wiki_root = tmp_path / "wiki"
    long_page = wiki_root / "pages" / "notes" / "long-response-probe.md"
    long_page.parent.mkdir(parents=True)
    long_page.write_text(
        "---\ntitle: Long response probe\ntype: note\n---\n\n"
        + ("response invariant payload " * 6_000),
        encoding="utf-8",
    )
    monkeypatch.setenv("TINYASSETS_WIKI_PATH", str(wiki_root))

    universe_id = "u-response-invariant"
    universe_dir = data_dir / universe_id
    universe_dir.mkdir()
    ensure_universe_registered(
        data_dir,
        universe_id=universe_id,
        universe_path=universe_dir,
    )
    set_universe_visibility(universe_id, "public", source="owner")

    exhaustion = AllProvidersExhaustedError(
        "all providers exhausted",
        attempts=[],
        chain_state={"role": "writer"},
    )
    monkeypatch.setattr(
        universe,
        "universe_has_assigned_engine",
        lambda _universe_dir: False,
    )

    selector_version = SimpleNamespace(status="rolled_back")
    monkeypatch.setattr(
        selector_dispatch,
        "resolve_selector_branch_version_id",
        lambda _base_path, *, goal_id: {
            "ok": True,
            "branch_version_id": f"{goal_id}-selector",
            "source": "goal_binding",
        },
    )
    monkeypatch.setattr(
        "tinyassets.branch_versions.get_branch_version",
        lambda _base_path, _branch_version_id: selector_version,
    )

    # An exact wiki path: a slashed page name must name the .md file under
    # pages/ or drafts/ (`_resolve_page`, since #2121).
    truncated_page_response = universe_server.read_page(
        page="pages/notes/long-response-probe.md",
    )
    assert json.loads(truncated_page_response)["truncated"] is True

    surfaces = {
        "read_page query response": universe_server.read_page(
            query="response-invariant-probe",
        ),
        "read_page truncated-page response": truncated_page_response,
        "get_status missing-universe response": universe_server.get_status(
            command_center_id="u-missing-response-invariant",
        ),
        "read_graph graph response": universe_server.read_graph(
            target="graph",
            graph_id=universe_id,
        ),
        "converse engine-setup envelope": universe.engine_setup_required_payload(
            universe_id,
            exhaustion,
        ),
        "selector dispatch response": selector_dispatch.dispatch_selector(
            data_dir,
            goal_id="g-response-invariant",
            candidate_branches=[],
            provider_call=lambda *_args, **_kwargs: "",
        ),
    }
    _assert_surfaces_claim_only_advertised_handles(surfaces)


def test_runtime_source_strings_claim_only_live_advertised_handles() -> None:
    """Rare response branches cannot escape the dynamic payload samples."""
    _assert_surfaces_claim_only_advertised_handles(
        _runtime_source_string_surfaces(),
    )


def test_runtime_source_route_heads_use_valid_first_parameter() -> None:
    """Executable response examples start with a real handle parameter."""
    advertised = _advertised_tools()
    handle_pattern = "|".join(
        sorted(map(re.escape, advertised), key=len, reverse=True)
    )
    route_head = re.compile(
        rf"(?<![\w-])({handle_pattern})\s+"
        r"([A-Za-z_][A-Za-z0-9_]*)\s*=",
    )
    violations: dict[str, str] = {}
    for surface, text in _runtime_source_string_surfaces().items():
        for handle, parameter in route_head.findall(text):
            properties = advertised[handle].parameters.get("properties", {})
            if parameter not in properties:
                violations[surface] = f"{handle} has no {parameter} parameter"
    assert not violations, json.dumps(violations, sort_keys=True)


#: `write_graph target="branch"` gained operation=create/remix/patch/publish,
#: so one requirement set no longer describes the handle. create/remix carry
#: the whole spec in `payload_json`; only patch takes `branch_id` +
#: `changes_json`; publish freezes a named branch and needs `branch_id` alone.
#: Requiring the patch arguments everywhere rejected valid create examples.
_BRANCH_WRITE_REQUIREMENTS: dict[str, set[str]] = {
    "create": {"payload_json"},
    "remix": {"payload_json"},
    "patch": {"branch_id", "changes_json"},
    "publish": {"branch_id"},
}
#: No explicit operation means the default patch shape.
_BRANCH_WRITE_DEFAULT: set[str] = {"branch_id", "changes_json"}


def _branch_write_requirements(argument_text: str) -> set[str] | None:
    """Required companion arguments for one `write_graph target="branch"` hit.

    Returns ``None`` when the mention carries no arguments at all: prose that
    merely names the handle ("use `write_graph target=\"branch\"`") is not an
    example anyone can copy, so there is nothing to hold executable. Any hit
    that does carry arguments is still checked strictly, against the set its
    own ``operation`` requires.
    """
    if not argument_text.strip():
        return None
    match = re.search(r"""operation=["']?([a-z_]+)""", argument_text)
    if match is None:
        return set(_BRANCH_WRITE_DEFAULT)
    return set(_BRANCH_WRITE_REQUIREMENTS.get(match.group(1), _BRANCH_WRITE_DEFAULT))


def test_runtime_graph_routes_include_required_companion_arguments() -> None:
    """Response examples must be executable, not merely canonical-looking."""
    route = re.compile(
        r"(?<![\w-])(?P<handle>read_graph|write_graph)\s+"
        r"target=[\"'](?P<target>[A-Za-z_-]+)[\"']"
        r"(?P<arguments>.*?)(?=`|(?<!\.)\.(?!\.)(?:\s|$)|$)",
        flags=re.DOTALL,
    )
    required_all = {
        ("write_graph", "branch"): {"branch_id", "changes_json"},
        ("write_graph", "goal"): {"name"},
        ("write_graph", "request"): {"text", "idempotency_key"},
    }
    required_any = {
        ("read_graph", "branch"): {"branch_id", "graph_id"},
        ("read_graph", "run"): {"run_id", "graph_id"},
    }
    violations: dict[str, str] = {}
    for surface, text in _runtime_source_string_surfaces().items():
        for example in route.finditer(text):
            key = (example.group("handle"), example.group("target"))
            assigned = set(
                re.findall(
                    r"\b([A-Za-z_][A-Za-z0-9_]*)\s*=",
                    example.group("arguments"),
                )
            )
            if key == ("write_graph", "branch"):
                needed = _branch_write_requirements(example.group("arguments"))
                if needed is None:
                    continue
            else:
                needed = required_all.get(key, set())
            missing = needed - assigned
            if missing:
                violations[surface] = f"{key} misses {sorted(missing)}"
            alternatives = required_any.get(key)
            if alternatives and not assigned & alternatives:
                violations[surface] = (
                    f"{key} needs one of {sorted(alternatives)}"
                )
    assert not violations, json.dumps(violations, sort_keys=True)


def test_instruction_routing_examples_use_valid_handle_parameters() -> None:
    advertised = _advertised_tools()
    surfaces = _instruction_surfaces()
    example_pattern = re.compile(
        r"(?<![\w-])([A-Za-z_][A-Za-z0-9_-]*)\s+"
        r"(action|target)\s*=\s*[\"']?([A-Za-z_][A-Za-z0-9_-]*)"
    )

    for surface, text in surfaces.items():
        for handle, parameter, value in example_pattern.findall(text):
            assert handle in advertised, (
                f"{surface} routes through unadvertised handle {handle}"
            )
            properties = advertised[handle].parameters.get("properties", {})
            assert parameter in properties, (
                f"{surface} routes {handle} with unsupported "
                f"parameter {parameter}"
            )
            if parameter != "target":
                continue
            implementation = getattr(universe_server, handle)
            response = json.loads(implementation(target="__invalid_target__"))
            assert value in response["allowed_targets"], (
                f"{surface} routes {handle} to unsupported target {value}"
            )


def test_meet_command_center_description_is_relay_first() -> None:
    prompts = {
        prompt.name: prompt
        for prompt in _run(
            universe_server.mcp.list_prompts(run_middleware=False)
        )
    }
    description = prompts["meet_command_center"].description or ""
    assert "converse" in description
    assert "relay" in description.lower()
    assert "get_status" not in description
    assert "AS the universe" not in description


def test_graph_target_examples_include_semantic_companion_arguments() -> None:
    pattern = re.compile(
        r"`(?P<handle>read_graph|write_graph)\s+"
        r"target=[\"'](?P<target>[A-Za-z_-]+)[\"']"
        r"(?P<arguments>[^`]*)`"
    )
    required_all = {
        ("read_graph", "goal"): {"goal_id"},
        ("write_graph", "goal"): {"name"},
        ("write_graph", "branch"): {"branch_id", "changes_json"},
        ("write_graph", "request"): {"text", "idempotency_key"},
    }
    required_any = {
        ("read_graph", "branch"): {"branch_id", "graph_id"},
        ("read_graph", "run"): {"run_id", "graph_id"},
    }
    for surface, text in _instruction_surfaces().items():
        examples = list(pattern.finditer(text))
        for example in examples:
            key = (example.group("handle"), example.group("target"))
            assigned = set(
                re.findall(
                    r"\b([A-Za-z_][A-Za-z0-9_]*)\s*=",
                    example.group("arguments"),
                )
            )
            if key == ("write_graph", "branch"):
                needed = _branch_write_requirements(example.group("arguments"))
                if needed is not None:
                    missing = needed - assigned
                    assert not missing, (
                        f"{surface} omits {sorted(missing)} from "
                        f"{example.group(0)}"
                    )
            elif key in required_all:
                missing = required_all[key] - assigned
                assert not missing, (
                    f"{surface} omits {sorted(missing)} from "
                    f"{example.group(0)}"
                )
            if key in required_any:
                assert assigned & required_any[key], (
                    f"{surface} needs one of {sorted(required_any[key])} in "
                    f"{example.group(0)}"
                )
