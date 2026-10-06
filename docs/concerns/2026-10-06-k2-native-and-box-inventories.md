---
severity: P1
title: K2 one agent definition is blocked on codex-cli; founder decision needed
filed: '2026-10-06'
summary: claude-code 2.1.290 reduces to exactly our four MCP tools; codex-cli 0.160.0 cannot. Its bundled catalog pins code mode (apply_patch inside exec) and v2 collaboration tools to every GPT-5.6+/GPT-6 model, and three MCP resource tools are added unconditionally whenever any MCP server is configured. One-definition build stopped at step 3 pending a founder decision among the listed options.
---

Founder direction 2026-10-06 (binding): one agent definition supplies tools,
instructions and capabilities to every provider and to every agent; providers
only translate it into their wire format. Step 1 of that lane was to prove,
per installed CLI, a supported configuration that leaves ONLY our MCP tools
model-visible. Step 3 said: if a CLI cannot be reduced, stop and report. It
cannot, so #4517 stays draft and no shared-contract module, all-provider
parity guard or activity-refusal lift was built.

## Evidence (2026-10-05, Windows host, loopback sink, no credentials)

Reproduce with `scripts/native_cli_payload.py` (`--codex-reduced` applies the
most-reduced codex launch, `CODEX_REDUCED_ARGS` plus a catalog override). Use
the real CLI, `"$APPDATA/npm/codex.cmd"`, not the `~/.local/bin` bypass shim.

| Launch | Model-visible tools in the captured request | Resident chars |
|---|---|---|
| claude 2.1.290, production flags (`--tools ""`, `--strict-mcp-config`, `--setting-sources project`, `--permission-mode default`) | `mcp__tinyassets__{bash,edit,read,write}` and nothing else | 2,773 |
| codex 0.160.0, production-like flags, model `gpt-6-astra` (every GPT-5.6+/GPT-6 slug carries the same metadata) | `exec` (nested `apply_patch`, MCP-resource and clock tools; our MCP tools only deferred inside it), `wait`, `request_user_input`, `request_user_input_async`, `clock.sleep`, 6 `collaboration.*` | 49,713 |
| codex, plus every documented switch (see below) | unchanged: `exec`/`wait`/collaboration remain | 45,466 |
| codex, switches plus `model_catalog_json` override | `list_mcp_resources`, `list_mcp_resource_templates`, `read_mcp_resource` + our four | 3,613 |

Claude: `claude --help` documents `--tools ""` as "disable all tools" and
`--strict-mcp-config` as "only use MCP servers from --mcp-config". Instructions
reach the model as ours plus the CLI's one-line SDK preamble and environment
block.

Codex, why each tool persists:

1. Code mode and collaboration are model metadata, not config. The bundled
   catalog (`codex debug models --bundled`) sets `tool_mode =
   "code_mode_only"`, `multi_agent_version = "v1"/"v2"` and
   `apply_patch_tool_type = "freeform"` on every model except `gpt-5.5`.
   `--disable code_mode`, `-c features.code_mode=false`,
   `-c features.code_mode.enabled=false`, `--disable multi_agent` and
   `--disable multi_agent_v2` leave all of them in the request (captured).
   Only `model_catalog_json` (documented: "Optional path to a JSON model
   catalog loaded on startup") with those fields cleared removes them. That
   rewrites OpenAI's own model presentation, it is a provider-specific
   patch, and it is unverified against a credentialed account whose catalog
   refresh may replace it.
2. `list_mcp_resources`, `list_mcp_resource_templates` and `read_mcp_resource`
   are added whenever `mcp.has_servers()` is true, with no switch:
   `codex-rs/core/src/tools/spec_plan.rs` `add_mcp_resource_tools`, tag
   `rust-v0.160.0`. The captured inventory server declared only the `tools`
   capability, and they still appeared. They are protocol calls to our own
   (only) MCP server, so they do cross our route, but they are model-visible
   tools other providers lack.
3. The other leftovers have working switches: `--disable sleep_tool`, `goals`,
   `image_generation`, `view_image`, `skill_search`; `-c web_search="disabled"`;
   `-c tools.experimental_request_user_input={enabled=false}` (a bare `false`
   is a type error); `model_instructions_file` replaces Codex's ~20k-char base
   instructions; and the `include_*_instructions=false` and
   `skills.include_instructions=false` keys. `include_apply_patch_tool` and
   `tools.view_image` are rejected as unrecognized on 0.160.0. The official
   reference (https://developers.openai.com/codex/config-reference, now
   redirecting to learn.chatgpt.com) lists no tool-mode, resource-tool or
   apply_patch switch.

## Options (founder decision)

A. Make the one definition include MCP resource introspection: four tools
   plus `list/read` resources, served by our engine route for every provider.
   This still needs the `model_catalog_json` override for codex, so codex
   runs GPT-6 outside the code mode OpenAI ships it with.
B. Drive codex through `codex app-server` with `thread/start.dynamicTools`
   and no MCP server. Our process executes the tools, so no resource tools
   are added. The field is marked `#[experimental]` in
   `app-server-protocol/src/protocol/v2/thread.rs`, needs
   `experimentalApi: true`, and is unproven here: it needs a capture first.
   The catalog override would still be needed for code mode.
C. Ask OpenAI upstream for switches covering tool_mode, collaboration and
   MCP resource tools. Meanwhile codex is either kept out of the
   one-definition claim and out of activities (today's refusal), or accepted
   with these documented differences.
D. Accept codex's native tool set as is. This contradicts the direction,
   because apply_patch bypasses yield/pause/stop and seed exclusion.

## Still open regardless of the option

The remote-box ta bridge (`agent_loop/served_chat.py`) is a separate lane. Also
open: paired live trials, deployed SHA and an owner app pass. Native activity
runs stay refused (`2026-10-03-native-activity-yield-needs-a-tool-boundary.md`)
until every native tool call is proven to cross our route.
