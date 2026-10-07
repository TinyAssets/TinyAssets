# MCP Directory Rollout Action Queue — SUPERSEDED HISTORICAL SNAPSHOT

Date: 2026-05-01
Status: **SUPERSEDED / HISTORICAL — DO NOT EXECUTE**
Owner: lead + available providers

> This file preserves the 2026-05-01 rollout queue and evidence as historical
> context. It is not an active queue. `/mcp-directory*` is retired and must be
> an ordinary 404 without redirect, alias, or compatibility behavior. Current
> work uses the exact public name `TinyAssets`, the sole remote endpoint
> `https://tinyassets.io/mcp`, and
> `openspec/changes/archive/2026-08-26-reconcile-external-connector-manifests/`.
>
> Do not execute or reopen any task below. Promote any still-relevant external
> host work through the OpenSpec change and current `STATUS.md` coordination.

The remainder of this document is the preserved 2026-05-01 snapshot.

This queue starts after the implementation slice shipped. The live state is:

- Full custom-connector endpoint: `https://tinyassets.io/mcp`
- Directory/review endpoint: `https://tinyassets.io/mcp-directory`
- Public canary: green for both endpoints on 2026-05-01
- Directory tool-list: 11 narrow tools from `/mcp-directory`
- MCP Registry listing: live as `io.github.Jonnyton/tinyassets-universe-server`

The rollout is not complete until TinyAssets is accepted into host-native
directories or registries, normal eligible users can find/add it without
Developer Mode or custom URL friction, and first-use evidence is recorded.

## Completion Definition

Done means all of these are true:

- MCP Registry lists TinyAssets and points clients at `https://tinyassets.io/mcp-directory`.
- Claude lists TinyAssets in the Connectors Directory, not only as a custom MCP URL.
- ChatGPT lists TinyAssets in the App Directory for eligible logged-in users, not
  only in Developer Mode.
- The public site tells each customer which path fits them: directory listing,
  custom URL fallback, no-chatbot-login local/self-hosted path, or planned host.
- The proof registry has a normal-user trace for each listed host.

## Host/Admin Actions

These require account, workspace, or directory submission authority. Do not mark
them complete from code-only evidence.

### MCP Registry Publish

Artifact: `packaging/registry/server.json`

Status: complete on 2026-05-01.

Completed steps:

1. Installed and SHA-verified `mcp-publisher` v1.7.6.
2. Authenticated from the local `gh` session without logging the token.
3. Fixed the registry namespace to the case-sensitive owner namespace
   `io.github.Jonnyton/tinyassets-universe-server`.
4. Ran `mcp-publisher publish packaging/registry/server.json`.
5. Verified the public registry API returns one active latest listing:
   `https://registry.modelcontextprotocol.io/v0.1/servers?search=io.github.Jonnyton/tinyassets-universe-server`.

Follow-up: add registry-aware client proof when a host consumes the listing.

### Claude Connectors Directory Submission

Artifact: `docs/ops/mcp-directory-submission-packet.md`

Submit:

- Name: TinyAssets
- Endpoint: `https://tinyassets.io/mcp-directory`
- Website: `https://tinyassets.io/connect`
- Support: `ops@tinyassets.io`
- Security: `security@tinyassets.io`

Acceptance proof:

- TinyAssets appears in the Claude Connectors Directory.
- A normal logged-in Claude user adds TinyAssets without pasting a custom URL.
- Claude invokes `get_workflow_status` or `list_workflow_goals`.
- Trace goes to `output/claude_chat_trace.md` and summary to
  `output/user_sim_session.md`.

Progress:

- 2026-05-02 in-app browser reached the official Google Form page 2. Stopped
  before entering required company/contact fields or submitting because that
  would transmit contact data and signed-in Google identity to Google/Anthropic.

### ChatGPT App Directory Submission

Artifact: `docs/ops/chatgpt-app-submission.json`

Submit through an OpenAI workspace that has app write/read permissions and org
verification complete.

Known blockers:

- 2026-05-02 in-app browser reached the OpenAI Platform login screen.
- 2026-05-02 after host login, in-app browser reached
  `https://platform.openai.com/apps-manage`, created a `TinyAssets` app draft,
  and stopped at the submission form before uploading
  `docs/ops/chatgpt-app-submission.json`, logo assets, or entering developer/support,
  privacy/TOS, demo recording, commerce, and final review fields.
- BUG-034 tracks the current ChatGPT connector approval/post-approval stall.
- If the App Directory dashboard requires an embedded app resource, a widget/CSP
  slice must land before pressing Submit.
- Screenshots and privacy/safety fields must be captured from the verified live
  deployment.

Acceptance proof:

- TinyAssets appears in the ChatGPT App Directory for eligible logged-in users.
- A normal eligible user invokes TinyAssets without Developer Mode.
- At least one read-only tool call succeeds and is logged in the proof registry.

## Website Freshness

Status: complete for the 2026-05-01 website pass.

- PR #134 added the `/connect` customer chooser and `/llms.txt` distinction
  between `https://tinyassets.io/mcp` and `https://tinyassets.io/mcp-directory`.
- This branch updates the same surfaces for MCP Registry publication while
  keeping Claude directory and ChatGPT App Directory acceptance pending.
- Open WebUI and LibreChat remain the only verified no-hosted-chatbot-login
  hosts. Codex CLI is verified as a developer CLI path. LM Studio, Jan,
  OpenClaw/channel gateway, and other IDE hosts stay planned or partial until
  host-specific proof lands.

## Dev Verification Queue

These can be picked up by any non-website dev with a narrow worktree.

### No-Chatbot-Login Pack — retired 2026-09-03

The 2026-05-01 Open WebUI and LibreChat Docker proofs are historical. The live
endpoint now requires OAuth for every handle, so these hosts are unsupported
until each has a fresh host-specific OAuth proof. The retained setup-pack paths
are retirement notices, not runnable no-login instructions.

Setup packs:

- `docs/ops/open-webui-no-login-pack.md`
- `docs/ops/librechat-no-login-pack.md`

Deliverables:

- Host version and transport notes. Open WebUI 0.9.2 recorded.
- Host version and transport notes. LibreChat v0.8.5 recorded.
- OAuth-capable config for `https://tinyassets.io/mcp`.
- Tool-list proof and one visible named-principal user result.
- Proof registry row. Open WebUI and LibreChat recorded.

Repeat for LM Studio/Jan, OpenClaw/channel gateway, and any custom host that
claims MCP support. If a host needs a bridge, document the bridge truthfully
instead of saying it works natively.

### IDE/Developer Pack

Targets:

- VS Code / Copilot MCP config
- Cursor MCP config/add-button path
- Gemini CLI config
- Cline/Roo/Continue/Windsurf where practical

Acceptance proof per host:

- Config snippet committed or documented.
- Tool list succeeds.
- One safe read-only tool call succeeds.
- Proof registry updated with date, version, and command/UI trace.

Progress:

- Codex CLI 0.104.0 verified on 2026-05-02:
  `docs/ops/mcp-codex-registration-proof-2026-05-02.md` and
  `docs/ops/mcp-codex-runtime-proof-2026-05-02.md`. Runtime proof listed
  TinyAssets tools and called `get_workflow_status`, returning
  `"schema_version": 1`.
- Cursor 3.2.16 registration path verified on 2026-05-01:
  `docs/ops/mcp-cursor-registration-proof-2026-05-01.md`. Tool-list/read-call
  proof is still pending, so Cursor must not be marketed as verified yet.

### Partner Packet

Create a compact package for host/platform maintainers:

- One-paragraph category: "live collaborative tinyassets/node daemon for AI agents"
- Endpoint and transport matrix
- Tool surface summary
- Privacy/safety/support links
- Uptime/proof registry links
- Negative invocation cases

This should reuse `docs/ops/mcp-directory-submission-packet.md` rather than
forking a second source of truth.

## Status Update Rules

When a host moves forward:

1. Update `docs/ops/mcp-host-proof-registry.md`.
2. Update `docs/ops/mcp-directory-submission-packet.md` if reviewer-facing facts
   changed.
3. Update `STATUS.md` only if there is still an active blocker or next action.
4. Do not mark completion until normal-user discovery and first-use proof exist.
