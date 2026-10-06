# MCP Directory Submission Packet — SUPERSEDED HISTORICAL SNAPSHOT

Date: 2026-05-01
Status: **SUPERSEDED / HISTORICAL — DO NOT EXECUTE**
Owner: lead + codex-gpt5-desktop

> This file preserves the 2026-05-01 submission packet and evidence as
> historical context. It is not a current endpoint, manifest, submission, or
> rollout source. `/mcp-directory*` is retired and must be an ordinary 404
> without redirect, alias, or compatibility behavior. Current work uses the
> exact public name `TinyAssets`, the sole remote endpoint
> `https://tinyassets.io/mcp`, and the exact canonical tool/auth contract in
> `openspec/changes/archive/2026-08-26-reconcile-external-connector-manifests/`.
>
> Do not execute, refresh, or submit any checklist or payload below. Rebuild
> current external connector manifests and submission answers from the
> OpenSpec change instead.

The remainder of this document is the preserved 2026-05-01 snapshot.

This packet is for app-directory and registry reviewers. It separates the full
custom connector endpoint from the narrower review/listing endpoint:

- Full custom connector: `https://tinyassets.io/mcp`
- Directory/review endpoint: `https://tinyassets.io/mcp-directory`
- Website/customer chooser: `https://tinyassets.io/connect`
- Support: `ops@tinyassets.io`
- Security: `security@tinyassets.io`
- Legal/privacy: `https://tinyassets.io/legal`

Execution queue for remaining host/admin, website, and verification work:
`docs/ops/mcp-directory-rollout-action-queue.md`.

## Completion Definition

The rollout is not complete when the code or docs merge. It is complete only
when the user can discover and use TinyAssets through normal host surfaces:

- MCP Registry: `server.json` is published in the official registry and
  TinyAssets is discoverable by registry clients. Completed 2026-05-01 as
  `io.github.Jonnyton/tinyassets-universe-server`.
- Claude: TinyAssets is accepted/listed in the Claude Connectors Directory, and
  a normal logged-in Claude user can add it from the directory without a custom
  MCP URL.
- ChatGPT: TinyAssets is accepted/listed in the ChatGPT App Directory, and a
  normal eligible logged-in ChatGPT user can invoke it without Developer Mode.
- First-use evidence: at least one non-developer-mode user discovery, install,
  and successful read-only tool call is recorded per listed host.

Guest ChatGPT or logged-out hosted-chatbot users are not a supported app/MCP
path today; route them to local/self-hosted or channel-host paths instead.

## Official Requirements Checked

Fresh source snapshot, checked 2026-05-01:

- OpenAI app submission guide: `https://developers.openai.com/apps-sdk/deploy/submission`
- Anthropic Connector Directory FAQ: `https://support.claude.com/en/articles/11596036-anthropic-mcp-directory-faq`
- Anthropic connector submission process: `https://claude.com/docs/connectors/building/submission`
- Anthropic review criteria: `https://claude.com/docs/connectors/building/review-criteria`
- MCP Registry quickstart: `https://modelcontextprotocol.io/registry/quickstart`
- MCP Registry overview: `https://modelcontextprotocol.io/registry/about`

Review-risk implication: directory submissions should not use the legacy
catch-all `action` router tools. The `/mcp-directory` surface exposes narrow,
named tools with explicit annotations instead.

## App Metadata

Name: TinyAssets

Short description: Build durable workflows

Long description:

TinyAssets helps users inspect durable AI workflow state, browse shared goals and
wiki knowledge, and submit bounded requests into a long-running TinyAssets daemon.
It is for AI work that should persist beyond one chat: goals, universes, branch
runs, wiki/status knowledge, and daemon requests.

Category: Productivity

Primary user intent:

- "Use TinyAssets to list durable goals."
- "Use TinyAssets to inspect the active universe."
- "Use TinyAssets to search the project wiki."
- "Use TinyAssets to queue a bounded daemon request."
- "Use TinyAssets to propose a shared goal."

## Directory Tool Surface

Endpoint: `https://tinyassets.io/mcp-directory`

| Tool | Read-only | Open world | Destructive | Purpose |
|---|---:|---:|---:|---|
| `get_workflow_status` | yes | no | no | Return daemon status, routing evidence, and safety caveats. |
| `list_workflow_universes` | yes | no | no | List available TinyAssets universes. |
| `inspect_workflow_universe` | yes | no | no | Inspect durable state for one universe. |
| `list_workflow_goals` | yes | no | no | List shared TinyAssets goals. |
| `search_workflow_goals` | yes | no | no | Search shared TinyAssets goals. |
| `get_workflow_goal` | yes | no | no | Read one goal and its bound branches. |
| `search_workflow_wiki` | yes | no | no | Search TinyAssets wiki knowledge. |
| `read_workflow_wiki_page` | yes | no | no | Read one TinyAssets wiki page. |
| `list_workflow_runs` | yes | no | no | List recent TinyAssets branch runs. |
| `propose_workflow_goal` | no | yes | no | Create a shared goal proposal. |
| `submit_workflow_request` | no | no | no | Queue a bounded daemon request. |

The directory surface intentionally excludes broad legacy tools named
`universe`, `extensions`, `goals`, `gates`, and `wiki` because those combine
multiple read/write operations behind `action` parameters.

## Review Prompts

Use these prompts for manual review and host test cases:

- "Use TinyAssets to check the daemon status and tell me any caveats."
- "Use TinyAssets to search for onboarding goals."
- "Use TinyAssets to list universes and inspect the active one."
- "Use TinyAssets to search the wiki for launch risks and read the best page."
- "Use TinyAssets to propose a public goal named 'Onboard new MCP hosts' with
  tags discovery,onboarding."
- "Use TinyAssets to submit a request asking the daemon to summarize today's
  discoverability blockers."

Negative cases:

- Weather, calendar, email, payments, and general web browsing requests should
  not invoke TinyAssets.
- Requests to delete all data should not invoke the directory surface; it has no
  destructive tools.
- Credential-storage prompts should not invoke TinyAssets; no directory tool asks
  for passwords, API keys, MFA codes, SSNs, or payment fields.

## Submission Tasks

### MCP Registry

Ready artifact:

- `packaging/registry/server.json`

Published command sequence:

```powershell
$publisher = "$env:TEMP\mcp-publisher-v1.7.6\mcp-publisher.exe"
# Authenticated with the existing local GitHub session; token was not logged.
& $publisher publish packaging/registry/server.json
```

Publication proof, 2026-05-01:

- `mcp-publisher` v1.7.6 validated `packaging/registry/server.json`.
- First publish attempt failed because the registry namespace was case-sensitive:
  local auth granted `io.github.Jonnyton/*`, while the draft used
  `io.github.jonnyton/*`.
- After updating the generator and artifact to
  `io.github.Jonnyton/tinyassets-universe-server`, publish succeeded for version
  `0.1.0`.
- Registry API verification:
  `https://registry.modelcontextprotocol.io/v0.1/servers?search=io.github.Jonnyton/tinyassets-universe-server`
  returned one active/latest listing.

### Claude Connectors Directory

Submit:

- Name: TinyAssets
- MCP server URL: `https://tinyassets.io/mcp-directory`
- Website: `https://tinyassets.io/connect`
- Support: `ops@tinyassets.io`
- Security: `security@tinyassets.io`
- Review prompts: use the Review Prompts section above.
- Safety notes: directory endpoint has no destructive tools; write tools only
  create a goal proposal or queue a bounded TinyAssets request.

Acceptance proof:

- Reviewer or normal Claude user can find TinyAssets in the Connectors Directory.
- User can add it from the directory without pasting a custom URL.
- Live Claude.ai conversation invokes at least `get_workflow_status` or
  `list_workflow_goals`.
- Trace is saved to `output/claude_chat_trace.md` and summarized in
  `output/user_sim_session.md`.

2026-05-02 browser check:

- Official docs route to `https://clau.de/mcp-directory-submission`, which
  resolves to the Google Form `MCP Directory Submission Form`.
- Form page 2 is reachable in the in-app browser.
- Stopped before entering required company/contact fields because the form
  records the signed-in Google account identity on upload/submission and
  contact fields transmit personal/professional data to Google/Anthropic.

Blocker: actual submission requires Anthropic's form/review flow, action-time
approval before transmitting contact details, and final Submit confirmation.

### ChatGPT App Directory

Ready artifact:

- `docs/ops/chatgpt-app-submission.json`

Submit:

- Display name: TinyAssets
- Subtitle: Build durable workflows
- Category: Productivity
- MCP server URL: `https://tinyassets.io/mcp-directory`
- Test cases: import from `docs/ops/chatgpt-app-submission.json`

Acceptance proof:

- TinyAssets appears in the ChatGPT App Directory for eligible logged-in users.
- A normal eligible user invokes TinyAssets without Developer Mode.
- At least one read-only tool call succeeds from ChatGPT.

Blockers:

- Actual submission requires the OpenAI app submission/dashboard flow from an
  account with app write/read permissions and completed org verification.
- 2026-05-02 browser check reached `https://platform.openai.com/login` and
  stopped at the OpenAI Platform login screen.
- 2026-05-02 after host login, the authenticated dashboard reached
  `https://platform.openai.com/apps-manage`, created a `TinyAssets` app draft,
  and opened the app submission form. The visible form asks for
  `docs/ops/chatgpt-app-submission.json`, logo assets, app metadata, developer/support
  fields, website/privacy/TOS URLs, demo recording URL, commerce confirmation,
  and later review submission. Browser work stopped before uploading files,
  entering developer/support metadata, or pressing any final review submit.
- OpenAI's submission requirements include a defined CSP for the app. This
  branch prepares the MCP tool surface and submission JSON; a widget/CSP slice
  must land before pressing Submit if the dashboard requires an embedded app
  resource for TinyAssets' listing.
- The dashboard form also asks for screenshots, privacy policy URL, MCP/tool
  information, and localization details. These must be supplied from the
  verified deployment, not guessed from local code.

## Local Evidence From This Branch

- `python -m pytest tests/test_directory_server.py tests/test_universe_server_directory_app.py tests/smoke/test_mcp_tools_list_non_empty.py tests/test_universe_server_metadata.py`
- `node --test worker.test.js` in `deploy/cloudflare-worker`
- `python packaging/registry/generate_server_json.py --check`
- `python packaging/claude-plugin/build_plugin.py`
- Local runtime smoke:
  - `python scripts/mcp_public_canary.py --url http://127.0.0.1:8017/mcp --timeout 10 --verbose`
  - `python scripts/mcp_public_canary.py --url http://127.0.0.1:8017/mcp-directory --timeout 10 --verbose`
  - `python scripts/mcp_probe.py --url http://127.0.0.1:8017/mcp-directory --raw tools`

## Live Evidence

Production evidence, 2026-05-01:

- PR #123 merged as `d6a44eb`; prod deploy run `25233226847` passed.
- PR #124 merged as `de4a921`; Worker workflow can derive account ID from the
  `tinyassets.io` zone when the optional account ID secret is missing.
- PR #125 merged as `e8e0fd0`; manual Worker deploy run `25233386849` passed.
- `python scripts/mcp_public_canary.py --url https://tinyassets.io/mcp --timeout 15 --verbose` returned OK.
- `python scripts/mcp_public_canary.py --url https://tinyassets.io/mcp-directory --timeout 15 --verbose` returned OK.
- `python scripts/mcp_probe.py --url https://tinyassets.io/mcp-directory tools` returned the 11 directory tools listed above.
- `mcp-publisher publish packaging/registry/server.json` published
  `io.github.Jonnyton/tinyassets-universe-server` 0.1.0.
- `https://registry.modelcontextprotocol.io/v0.1/servers?search=io.github.Jonnyton/tinyassets-universe-server`
  returned the active/latest listing with remote URL
  `https://tinyassets.io/mcp-directory`.

For future deployment checks, run:

```powershell
python scripts/mcp_public_canary.py --url https://tinyassets.io/mcp --timeout 15 --verbose
python scripts/mcp_public_canary.py --url https://tinyassets.io/mcp-directory --timeout 15 --verbose
```

Then run a tool-list probe against `/mcp-directory` and verify that only the 11
directory tools above are exposed.
