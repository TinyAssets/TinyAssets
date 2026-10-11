"""How the codex adapter launches a served agent turn -- the facts only.

Dependency-free on purpose: the provider (``codex_app_server``) and the image
build's real-CLI gate (``scripts/codex_cli_smoke.py``, run against the pinned
CLI before the image exists) import this one module, so the launch the build
proves is the launch production makes.

With these arguments and the reduced catalog, codex-cli 0.160.0 sends the
model exactly the ``thread/start.dynamicTools`` and ``baseInstructions`` it is
given and no tool of its own (request captures, 2026-10-05).
"""

from __future__ import annotations

import copy

#: Everything the served launch turns off. A renamed feature makes Codex
#: reject the launch, so a CLI upgrade fails closed rather than adding tools.
SERVED_LAUNCH_ARGS: tuple[str, ...] = (
    "app-server",
    "--disable", "shell_tool", "--disable", "unified_exec",
    "--disable", "apps", "--disable", "plugins", "--disable", "remote_plugin",
    "--disable", "multi_agent", "--disable", "sleep_tool", "--disable", "goals",
    "--disable", "image_generation", "--disable", "view_image",
    "--disable", "skill_search",
    "-c", 'web_search="disabled"',
    "-c", "tools.experimental_request_user_input={enabled=false}",
    "-c", "include_permissions_instructions=false",
    "-c", "include_environment_context=false",
    "-c", "include_apps_instructions=false",
    "-c", "include_collaboration_mode_instructions=false",
    "-c", "skills.include_instructions=false",
    # No AGENTS.md from the working directory or its parents reaches the
    # model; ``baseInstructions`` is the whole of what it is told.
    "-c", "project_doc_max_bytes=0",
    # No project `.codex/config.toml` may load from the cell's workspace.
    "-c", 'projects."/tmp/workspace".trust_level="untrusted"',
)

#: The only files of a credential snapshot a served CODEX_HOME receives, by
#: name. Codex sends an ``AGENTS.md`` (or ``AGENTS.override.md``) found in
#: CODEX_HOME to the model on top of ``baseInstructions``, and no 0.160.0
#: setting turns that off; a ``config.toml`` could add MCP servers or tools.
SERVED_HOME_FILES: tuple[str, ...] = ("auth.json",)

#: Catalog fields that pin native tools to a model: code mode (``apply_patch``
#: inside ``exec``), collaboration, search. Cleared, never invented.
_MODEL_TOOL_FIELDS = {
    "multi_agent_version": None, "apply_patch_tool_type": None,
    "experimental_supported_tools": [], "supports_search_tool": False,
}


def reduced_catalog(bundled: dict, model: str | None) -> dict:
    """The bundled catalog (``codex debug models --bundled``) without native tools.

    A static catalog is authoritative, so a requested model it does not list
    would be unavailable; such a slug gets a copy of the first entry under its
    own name rather than Codex's code-mode default.
    """
    catalog = copy.deepcopy(bundled)
    models = catalog.get("models") if isinstance(catalog, dict) else None
    if not isinstance(models, list) or not models:
        raise ValueError("codex bundled model catalog is empty")
    for entry in models:
        entry.pop("tool_mode", None)
        entry.update(copy.deepcopy(_MODEL_TOOL_FIELDS))
    if model and not any(entry.get("slug") == model for entry in models):
        clone = copy.deepcopy(models[0])
        clone.update(slug=model, display_name=model)
        models.append(clone)
    return catalog
