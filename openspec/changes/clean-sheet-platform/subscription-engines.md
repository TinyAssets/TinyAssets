# Subscription CLIs as agent engines: vendor facts (read 2026-11-01)

Scope: running each vendor's unmodified CLI in the user's E2B sandbox as the agent engine. That means the same
`AGENTS.md`, skills and files for every vendor, TinyAssets controls exposed over MCP, and runs that can be scheduled.
All reads are dated 2026-11-01 unless marked otherwise.

How the evidence is labelled:
- **[E]** quotes or paraphrases a primary source that was actually read.
- **[S]** is primary-site text seen only through the web-search index, because the site was blocked from this session.
- **[3P]** is a third-party source.
- **[I]** is my inference.

Repo sources read: `platform/sources/{claude-code,codex,gemini,kimi}.json` and `platform/doors/src/sources.ts`
(`CliSpec`), plus `docs/decisions/ADR-031-subscription-use-follows-each-vendors-sanctioned-paths.md`.

## 0. Access notes

These hosts failed with ENOTFOUND, so they need to be allowed in this session's network settings:
- developers.openai.com, help.openai.com, openai.com, learn.chatgpt.com
- developers.google.com
- www.kimi.com
- x.ai
- docs.github.com
- docs.mistral.ai
- docs.z.ai, platform.minimax.io
- cursor.com, docs.warp.dev

Fallbacks used instead:
- **Vendor git repos, shallow-cloned on 2026-11-01:**
  - openai/codex @67f0994 (2026-10-31)
  - google-gemini/gemini-cli @6814e00 (2026-10-30)
  - MoonshotAI/kimi-code @a2afdbc (2026-10-30)
  - QwenLM/qwen-code @6e49388 (2026-11-01)
  - github/copilot-cli @e192829 (2026-10-30)
  - github/docs @2026-10-31, content/copilot and site-policy
  - xai-org/grok-build @2bdd1d6 (2026-09-29)
  - mistralai/mistral-vibe @338ad4b (2026-10-30)
- **The npm registry.**
- **Web-search snippets.**

code.claude.com and support.claude.com were reachable and read directly.

**npm latest vs. the pinned records [E]:**

| Package | Latest on npm | Pinned in record |
|---|---|---|
| `@anthropic-ai/claude-code` | 2.1.316 | 2.1.308 |
| `@openai/codex` | 0.166.0 | 0.163.0 |
| `@google/gemini-cli` | 0.66.0 | 0.65.0 |
| `@moonshot-ai/kimi-code` | 2.4.1 | 2.3.0 |
| `@qwen-code/qwen-code` | 0.26.5 | none |
| `@github/copilot` | 1.0.105 | none |

---

## 1. Anthropic: Claude Code (Pro / Max / Ultra)

### Terms [E]

Source: https://code.claude.com/docs/en/legal-and-compliance

- **Hosting in products.** "preinstalling or running Claude Code in your products or services (e.g. in hosted sandboxes or other agent infrastructure) requires agreeing to our Commercial Terms of Service and complying with the conditions below":
  - "The Claude Code binary must not be modified … customers may not remove, disable, or restrict any authentication method built into it (including methods that permit signing in with a Claude account or the user's own API key)."
  - "Customers may not pay for, resell, or intermediate Claude usage on their end users' behalf. Each end user must authenticate with their own Anthropic API key, Claude subscription plan credentials, or 3P inference provider credential."
- **Third-party login and tokens.** "Anthropic does not permit third-party developers to offer Claude.ai login into their own applications, or to route requests through Free, Pro, Max, or Ultra plan credentials on behalf of their users. Moreover, developers may not collect, store, or intermediate Claude.ai credentials or session tokens — sign-in to a Claude account must complete through Anthropic's own flow."
- **The carve-out.** "Nor does it prevent an end user from signing in to the unmodified Claude Code binary with their own Claude subscription, including where a platform hosts Claude Code as described under *Can customers offer Claude Code in their products?*"
- **Usage limits.** "Advertised usage limits for Pro, Max, and Ultra plans assume ordinary, individual usage of Claude Code and the Agent SDK."
- **Enforcement.** "Anthropic reserves the right to take measures to enforce these restrictions and may do so without prior notice."
- **Branding.** You may say your product "runs Claude Code". You may not use the Claude Code or Anthropic names or logos in your product name.

Consumer Terms (https://www.anthropic.com/legal/consumer-terms, effective Oct 8, 2025):
- §3(7) bars access "through automated or non-human means, whether through a bot, script, or otherwise". The exception is access through an Anthropic API key "or where we otherwise explicitly permit it".
- §2 says "You may not share your Account login information … You also may not make your Account available to anyone else."

Unattended and headless use is explicitly documented:
- The `claude -p` and Agent SDK docs.
- `claude setup-token`, "for CI pipelines, scripts".
- The scheduled tasks docs.
- support.claude.com/en/articles/15036540, on the Oct 7, 2026 update: "when you're signed in with your Claude plan, the Agent SDK and `claude -p` still draw from your subscription limits". On the Jun 15, 2026 update: "We've paused the previously-announced changes to Claude Agent SDK usage."

**[I]** No Anthropic text read today limits a hosted Claude Code to turns a human started.

### Headless [E]

Source: https://code.claude.com/docs/en/headless

- **Invocation:** `claude -p … --output-format json|stream-json`. The `json` output carries `result`, `session_id`, `total_cost_usd` and `is_error`. `stream-json` emits NDJSON whose last line is the `result` message, and needs `--verbose` (plus `--include-partial-messages` for token deltas).
- **Exit status:** 0 on success, non-zero on failure. Failures inside the run "print the failure as the result on stdout".
- **Events:** `system/init` lists the model, tools, `mcp_servers` and `mcp_server_errors`. `system/api_retry` events carry an `error` category such as `authentication_failed`, `rate_limit`, `billing_error` or `account_on_hold`.
- **Unattended runs:** use `--permission-mode` (`auto`, `dontAsk` or `acceptEdits`) together with `--permission-prompts none`, which needs v2.1.259 or later.
- **Bare mode:** `--bare` "never reads OAuth credentials". A subscription engine therefore cannot use `--bare` and must run non-bare. A non-bare `-p` "discovers and loads" hooks, skills, `CLAUDE.md` and `.mcp.json` "even in a folder you've never trusted".

### Agent definition [E]

- **Instructions:** reads `CLAUDE.md`. It reads `AGENTS.md` natively when no `CLAUDE.md` or `CLAUDE.local.md` exists, from v2.1.277 (https://code.claude.com/docs/en/memory). `@path` imports work.
- **Prompt flags:** `--system-prompt`, `--append-system-prompt[-file]`.
- **Skills:** `.claude/skills/<name>/SKILL.md` (project, nested, `--add-dir`), `~/.claude/skills/`, and plugins (https://code.claude.com/docs/en/skills). No `.agents/skills` path was seen in the 100k characters read.
- **Subagents:** `.claude/agents/`, or `--agents <json>`.
- **Hooks:** in `settings.json`.

### Tools [E]

- **MCP config:** `--mcp-config <file|json>` plus `--strict-mcp-config`. Both stdio and HTTP (`type: http`) are supported.
- **Restricting built-ins:** `--tools ""`, `--allowedTools`, and permission rules.

### Sessions [E]

- `--resume <session_id>` (or a transcript path), and `--continue`.
- Transcripts live at `~/.claude/projects/<project>/<session-id>.jsonl` and are kept for 30 days by default (`cleanupPeriodDays`).
- `CLAUDE_CONFIG_DIR` plus `CLAUDE_CODE_PROJECT_DIR_NAME` (v2.1.234 or later) pin per-tenant storage.
- `--mcp-config` and `--settings` are not restored on resume. Pass them again.

### Headless sign-in [E]

Source: https://code.claude.com/docs/en/authentication

- **Login flow:** `claude auth login` gives a browser URL. "If your browser shows a login code … paste it into the terminal."
- **Credential store:** credentials go to `~/.claude/.credentials.json` (mode 0600) on Linux, or under `CLAUDE_CONFIG_DIR`.
- **Expiry:** an expiry warning shows 3 days ahead. Background sessions "stop making progress once the credential expires".
- **Long-lived token:** `claude setup-token` makes a "one-year OAuth token" that it prints and does not save. It is used via `CLAUDE_CODE_OAUTH_TOKEN`.
  - **[I]** If the user pasted that token into TinyAssets, the platform would be collecting or storing a session token. Use `auth login` inside the sandbox instead.

### Limits [E]

Source: support.claude.com/en/articles/11145838

- Pro and Max have a 5-hour session limit plus a weekly limit. Max and Ultra also have a separate weekly limit for Fable. Verified Ultra accounts have no 5-hour limit.
- The limits are "shared across Claude and Claude Code".

**The Agent SDK credit:**
- The June 15, 2026 split was paused (articles/15036540).
- Since Oct 7, 2026, Max, Ultra and Team get monthly API credits (articles/17154008): Max $100, Ultra $200, Team $20 per Standard seat and $100 per Premium seat (pooled, capped at $500). Pro gets none.
- The credits cover the API, Managed Agents, the Agent SDK and `claude -p` "when you run them yourself with an API key from your linked Console organization".
- When you are signed in with a plan, usage draws from plan limits, not from the credits.
- Runs from the GitHub Action, an IDE or Desktop don't count toward the credits even with `-p`.

### Egress [E]

Source: https://code.claude.com/docs/en/network-config

- **Required:** api.anthropic.com, claude.ai, claude.com and platform.claude.com. Token exchange and refresh go to platform.claude.com.
- **Optional or feature hosts:**
  - sso.claude.com
  - mcp-proxy.anthropic.com (claude.ai connectors; disable with `ENABLE_CLAUDEAI_MCP_SERVERS=false`)
  - downloads.claude.ai
  - registry.npmjs.org
  - Datadog intake hosts (disabled by `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC`)
- **[I]** Today's record lacks platform.claude.com in `hosts.provider`, though the docs say refresh goes there.

### Native automation [E]

**Cloud scheduled tasks, formerly routines** (https://code.claude.com/docs/en/routines, research preview):
- **Plans and where they run:** Pro, Max, Ultra, Team and Enterprise. They run on Anthropic cloud or a self-hosted environment.
- **Triggers:** a schedule ("Expressions that run more often than once per hour are rejected"), the API (`POST https://api.anthropic.com/v1/claude_code/routines/<trig>/fire`, with a per-task bearer token "shown once … store it … such as your alerting tool's secret store", beta header `experimental-cc-routine-2026-04-01`, and an optional `text` that is wrapped as untrusted), and GitHub events.
- **Rate limits:**
  - Scheduled runs: 100 an hour per account.
  - Run now, API fires and re-run: 30 an hour per task.
  - API fires: 100 an hour per account.
  - "None of these hourly limits has overage."
- **Usage:** runs draw subscription usage. Usage credits can run over the subscription limit.
- **Connectors:** these are the claude.ai connectors. "MCP servers you added locally … do not appear". To use one, add it at claude.ai/customize/connectors.

**Channels** (https://code.claude.com/docs/en/channels, research preview):
- These are MCP servers that push into a *running* session.
- During the preview, `--channels` only accepts plugins on Anthropic's allowlist. Developer testing uses `--dangerously-load-development-channels`.
- Pro, Max and Ultra users without an organization skip the organization checks.

**Session-scoped `/loop`** also exists.

---

## 2. OpenAI: Codex CLI (ChatGPT plans) and Sign in with ChatGPT

### Terms

Primary pages were blocked, so all of this comes through the search index.
- **[S]** developers.openai.com/codex/auth: API keys "are the right default for automation". ChatGPT-managed auth in CI is "advanced", "only if you specifically need to run as your Codex account". You can copy `~/.codex/auth.json` ("handle like a password").
- **[S]** /codex/auth/ci-cd-auth:
  - Keep a persistent `CODEX_HOME`.
  - The session is "stale after roughly 8 days", so refresh it weekly.
  - Ephemeral runners must restore the file and write it back.
- **[S]** /codex/enterprise/access-tokens: `CODEX_ACCESS_TOKEN` is a ChatGPT workspace credential that admins must enable.
- **[S]** help.openai.com/en/articles/20001542:
  - Plan use in "participating commercial apps and sites is only available with Go, Plus, and Pro".
  - "If you're a commercial partner interested in integrating Sign in with ChatGPT, with or without token sharing, please complete this interest form."
  - Users can set a per-app weekly cap.
- **[S]** developers.openai.com/siwc/token-sharing-open-source: plan use is offered for open-source tools.
- **[3P]** AICoder, 2026-09-08: "third-party clients using Sign in With ChatGPT for Codex is not an approved use" unless pure OSS or a partner.
- **[I]** I found no text either permitting or forbidding a platform hosting the unmodified Codex CLI for a user's own ChatGPT sign-in. Treat it as unclear, and partner-gated for a commercial host.

### Headless [E: source]

`codex exec [--json] [--output-last-message F] [--output-schema F] [--ephemeral] [--skip-git-repo-check] PROMPT|-`

JSONL events (`codex-rs/exec/src/exec_events.rs`):
- `thread.started{thread_id}`
- `turn.started`
- `turn.completed{usage}`
- `turn.failed{error.message}`
- `item.started`, `item.updated` and `item.completed`, with these item types: `agent_message`, `reasoning`, `command_execution`, `file_change`, `mcp_tool_call`, `collab_tool_call`, `web_search`, `todo_list` and `error`
- `error{message}`

### Agent definition [E: source]

- **Instructions:** `AGENTS.md`, with `AGENTS.override.md` preferred when present. The combined cap is 32 KiB (`project_doc_max_bytes`). `project_doc_fallback_filenames` adds other names. There are also `developer_instructions` and `model_instructions_file`.
- **Skills:** `.agents/skills` between the repo root and the cwd, `~/.agents/skills`, and the deprecated `$CODEX_HOME/skills`.
- **Subagents:** `[agents.<role>]` with `config_file`, plus `features.multi_agent`.
- **Hooks:** `hooks.json`, "Claude-style". Events: PreToolUse, PostToolUse, PermissionRequest, SessionStart, SessionEnd, UserPromptSubmit, Stop, SubagentStop and PreCompact.

### Tools [E: source]

- **MCP:** `[mcp_servers.<name>]` takes either `command`/`args`/`env` (stdio) or `url` with `bearer_token_env_var` and `http_headers` (streamable HTTP). It also supports `enabled_tools` and `disabled_tools`. You can inject it with `-c mcp_servers.x.url=…`.
- **Restricting built-ins:** the `features.*` flags shown in today's record.

### Sessions [E: source]

- `codex exec resume <SESSION_ID|--last> [PROMPT]` and `codex exec fork`.
- Rollouts are stored in `$CODEX_HOME/sessions`. `--ephemeral` disables persistence, so it must be dropped for engine use.

### Headless sign-in [E: source]

- `codex login --device-auth`, at `{base}/codex/device` on auth.openai.com.
- `codex login --with-access-token` reads from stdin.
- `cli_auth_credentials_store` can be `file`, `keyring`, `auto` or `ephemeral`. The file mode writes `$CODEX_HOME/auth.json`.

### Limits

- **[S]** Plans include Codex with "ChatGPT Work and Codex" sharing usage limits.
- I could not read the per-plan numbers (help.openai.com/articles/11369540 was blocked).

### Egress [E: source]

- auth.openai.com and chatgpt.com (`/backend-api/codex/responses`).
- api.openai.com only for API keys.

### Native automation [S]

**ChatGPT scheduled tasks** (help.openai.com/articles/10291617):
- Active-task caps: 3 for Free and Go, 5 for Plus, 10 for Business and Edu, 15 for Pro and Enterprise.
- Paid plans can recur hourly.
- "Event-triggered tasks" are capped at 30 an hour and 720 a day.
- **MCP Events** (developers.openai.com/plugins/build/mcp-events): ChatGPT calls `events/subscribe` with a callback URL and signing secret (Standard Webhooks). It needs MCP 2026-07-28. It works in Work chats on web and desktop-cloud, and in "dots".

**Codex automations** are managed in ChatGPT web and desktop. The CLI lacks the "Scheduled" UI.

---

## 3. Google: Gemini CLI (Google AI Pro / Ultra)

### Terms [E]

Source: `docs/resources/tos-privacy.md` in the repo.

- "Directly accessing the services powering Gemini CLI (for example, the Gemini Code Assist service) using third-party software, tools, or services (for example, using OpenClaw with Gemini CLI OAuth) is a violation of applicable terms and policies. Such actions may be grounds for suspension or termination of your account."
- AI Pro and Ultra sign-in falls under the Google ToS plus the Google One Additional Terms.
- docs/get-started/authentication.mdx, for headless mode: it "will use your existing authentication method, if an existing authentication credential is cached". Otherwise use an API key or Vertex.
- **[I]** Running the unmodified Gemini CLI is not "third-party software" accessing the service, but a platform orchestrating it isn't addressed. I couldn't read the Code Assist terms and FAQ (developers.google.com was blocked). Status: unclear.

### Headless [E]

Source: docs/cli/headless.md.

- **Invocation:** `gemini -p … --output-format json|stream-json`.
- **`json`:** `{response, stats, error?}`.
- **`stream-json` events:** `init` (session id and model), `message`, `tool_use`, `tool_result`, `error` and `result`.
- **Exit codes:** 0 success, 1 general or API error, 42 input error, 53 turn limit exceeded.

### Agent definition [E]

- **Instructions:** `GEMINI.md`. `context.fileName` can be a list such as `["AGENTS.md", …]`. `GEMINI_SYSTEM_MD` overrides the system prompt.
- **Skills:** `.gemini/skills/` or `.agents/skills/`, at project and user level. The `.agents` alias wins.
- **Subagents:** `.gemini/agents/*.md` and `~/.gemini/agents/`.
- **Hooks:** BeforeTool, AfterTool, BeforeAgent, AfterAgent, BeforeModel and BeforeToolSelection.

### Tools [E]

- **MCP:** `settings.json` `mcpServers` with `command`, `url` (SSE) or `httpUrl` (streamable HTTP), plus `excludeTools`. `--allowed-mcp-server-names` limits which load.
- **Restricting built-ins:** `tools.core` and the Policy Engine. `--allowed-tools` is deprecated.

### Sessions [E]

`-r latest|<index>|<uuid>`. Sessions are stored in `~/.gemini/tmp/<project_hash>/chats/`.

### Headless sign-in [E: source]

`NO_BROWSER=true` gives a manual flow. The redirect goes to `https://codeassist.google.com/authcode` and the CLI asks "Enter the authorization code". Credentials are cached in `oauth_creds.json` (a refresh token). Its lifetime isn't documented in what I read.

### Limits [E]

Source: docs/resources/quota-and-pricing.md. Maximum requests per user per day:

| Plan | Requests per day |
|---|---|
| Code Assist individual | 1,000 |
| Google AI Pro | 1,500 |
| Google AI Ultra | 2,000 |

"Google AI Plus … not supported".

### Egress [E: source]

- accounts.google.com, oauth2.googleapis.com, www.googleapis.com, cloudcode-pa.googleapis.com and codeassist.google.com.
- play.googleapis.com (usage statistics).

### Native automation [S]

Source: support.google.com/gemini 16316416 and 17094710.
- Gemini app "scheduled actions": AI Pro and Ultra, up to 10 active.
- Task schedules: 50 active, 15 running. They are skipped when you are at your usage limit.

---

## 4. xAI: Grok Build (SuperGrok / X Premium+)

### Terms

- **[S]** x.ai news (2026-05-27): Grok Build is "in early beta for all SuperGrok and X Premium Plus subscribers".
- **[S]** x.ai/news/grok-opencode: subscription sign-in in OpenCode, by browser OAuth or a headless code. This is a named partner.
- **[3P]** A GitHub issue claims the consumer terms bar unofficial clients. The consumer ToS was updated 2026-10-12 (search index).
- **[E]** From the repo's `02-authentication.md`: "For CI/CD, automation, or environments without browser access, use an API key".
- **[I]** I couldn't read the x.ai terms for hosting the first-party CLI. Status: unclear.

### Headless [E]

Source: `14-headless-mode.md`.

- **Invocation:** `grok -p … --output-format plain|json|streaming-json|streaming-messages-json`.
- **`json` result:** `{text, stopReason, sessionId, requestId, num_turns, usage, modelUsage, total_cost_usd?}`. Cost is "often omit[ted]" on OAuth paths.
- **Other options:** `--max-turns`, `--yolo`, `--permission-mode`, and `--no-auto-update` for scripts.

### Agent definition [E]

- **Instructions:** `AGENTS.md`, plus `CLAUDE.md` for compatibility (and `~/.claude/`). `--rules <TEXT>`. "Startup loading requires folder trust (`--trust` …)".
- **Skills:** `.grok/skills`, `.agents/skills`, `.claude/skills` and `~/.grok/skills`.
- **Subagents:** `.grok/agents/` and `~/.grok/agents/`. Block them with `--disallowed-tools Agent`.
- **Hooks:** SessionStart, UserPromptSubmit, Pre/PostToolUse, Stop and others.

### Tools [E]

- **MCP:** `~/.grok/config.toml` `[mcp_servers.<n>]`, stdio or streamable HTTP. Manage it with `grok mcp add`.
- **Restricting built-ins:** `--tools` and `--disallowed-tools` (headless only), and `--allow`/`--deny` rules.

### Sessions [E]

`-r <id|title>`, `-c`, `--fork-session`. Stored in `~/.grok/sessions/<encoded-cwd>/<id>/`. `GROK_HOME` relocates it.

### Headless sign-in [E]

- `grok login --device-auth`. The default is OAuth at auth.x.ai.
- Credentials go to `~/.grok/auth.json` (mode 0600). Auto-refresh. "Credentials without a server-provided expiry fall back to a 30-day lifetime."
- There is also an external auth-provider hook.

### Limits [S]

A "single weekly usage allowance" on SuperGrok (docs.x.ai/grok/overview). No numbers found.

### Egress [E: source grep]

- auth.x.ai, accounts.x.ai, cli-chat-proxy.grok.com and grok.com.
- api.x.ai for API keys.
- **[I]** cli-chat-proxy.grok.com is the subscription path (`GROK_CLI_CHAT_PROXY_BASE_URL`).

### Native automation [3P]

Grok "Tasks" in grok.com. I couldn't read xAI's own pages.

---

## 5. Moonshot: Kimi Code CLI (Kimi Code membership)

### Terms

- **[S]** kimi.com/help/kimi-code/third-party-agents (es-419): using your key "with unauthorized platforms or tools may be considered a violation".
- **[S]** kimi.com/code/docs: Claude Code, Roo Code and OpenCode are documented.
- **[3P]** cline issue #10307 shows a 403: "Kimi For Coding is currently only available for Coding Agents such as Kimi CLI, Claude Code, Roo Code, Kilo Code". Issue MoonshotAI/kimi-cli#2416 asks to whitelist Zoo Code.
- **[I]** The first-party CLI is on the whitelist by construction. I found no text on hosted or unattended use.

### Headless [E]

Source: docs/en/reference/kimi-command.md.

- **Invocation:** `kimi -p … --output-format text|stream-json`.
- **`stream-json`:** one JSON message per line, with Assistant messages (and `tool_calls`) followed by Tool messages. Thinking is not written. Notices go to stderr.
- `-p` runs in `auto` permission mode by default, and cannot be combined with `--yolo`.

### Agent definition [E]

- **Instructions:** `AGENTS.md`, project and global (`$KIMI_CODE_HOME/AGENTS.md`, `~/.agents/AGENTS.md`).
- **Agent file:** `--agent-file <md>`. It "cannot be combined with `--session`/`--continue`". The agent is bound at session creation and restored on resume.
- **Skills:** `.kimi-code/skills/`, `.agents/skills`, `$KIMI_CODE_HOME/skills/` and `~/.agents/skills/`.
- **Subagents:** set by an allowlist in the agent file. Agent files live in `.kimi-code/agents/` and `.agents/agents/`.
- **Hooks:** UserPromptSubmit, PreToolUse, Stop, SessionStart, SubagentStart, SessionHeartbeat and others.

### Tools [E]

- **MCP:** `mcp.json`, at user (`$KIMI_CODE_HOME/mcp.json`) or project (`.kimi-code/mcp.json`) level. It supports stdio, HTTP and SSE. Project servers need workspace trust. I found no CLI flag for it.
- **Restricting built-ins:** the agent file's `tools` and `disallowedTools`.

### Sessions [E]

- `--session <id>` (alias `-r`) and `--continue`.
- Stored in `$KIMI_CODE_HOME/sessions/<workspaceId>/<sessionId>/` plus `session_index.jsonl`.
- The `-p` stream doesn't document emitting the session id.

### Headless sign-in [E]

`kimi login` uses the RFC 8628 device code flow. Tokens go to `$KIMI_CODE_HOME/credentials/<name>.json` (directory 0700, files 0600).

### Limits

The membership guide was blocked. **[3P]** figures are stale.

### Egress [E]

auth.kimi.com and api.kimi.com (`/coding/v1`), plus code.kimi.com for install.

---

## 6. Alibaba: Qwen Code

### Terms

- **[E]** docs/users/configuration/auth.md: "Qwen OAuth … free tier was discontinued on 2026-04-15". The subscription is now the Alibaba ModelStudio Coding Plan, an `sk-sp-` *API key* on `https://coding.dashscope.aliyuncs.com/v1`.
- **[S]** alibabacloud.com/help/en/model-studio/coding-plan: it is for "interactive use in programming tools such as Claude Code, Qoder … OpenClaw". The key "should not be used for automated scripts, application backends, or other non-interactive scenarios". Breaking this can lead to suspension or revocation.
- Status: forbidden for unattended or backend use.

### Mechanics [E]

- **Headless:** `qwen -p … --output-format json|stream-json`. `json` is an array of system, assistant and result messages.
- **Sessions:** `--continue` and `--resume <id>`. Stored in `~/.qwen/projects/<cwd>/chats`.
- **Instructions:** `QWEN.md` and `AGENTS.md`, both read by default.
- **Skills:** `.qwen/skills` and `~/.agents/skills`.
- **Subagents:** `.qwen/agents/`.
- **Hooks:** command, http and prompt types.
- **MCP:** `settings.json` `mcpServers`, with `httpUrl` or stdio.
- **Native schedule:** a session-scoped `/loop`.

---

## 7. GitHub Copilot CLI (Pro / Pro+ / Max)

### Terms [E]

GitHub docs, content/site-policy:
- Individuals fall under "Section J (AI Features) of the GitHub Terms of Service".
- The ToS says "Your login may only be used by one person". Machine accounts are allowed.
- The AUP bars "excessive automated bulk activity".
- The docs explicitly document programmatic, CI and Actions use (`automate-copilot-cli/*`).
- **[I]** No clause read forbids hosting the CLI for the user's own sign-in. Status: allowed as far as I read, but GitHub's terms don't address it directly.

### Headless [E]

Source: cli-programmatic-reference.md.

- **Invocation:** `copilot -p PROMPT [-s] --output-format json`. `json` output is JSONL. I didn't find the event schema documented.
- **Tool control:** `--allow-tool`, `--deny-tool`, `--available-tools` and `--excluded-tools`.
- **Unattended options:** `--no-ask-user`, `--assisted-approval`, `--secret-env-vars`.
- **Trust:** `--trust-folder`, which cannot be combined with `--resume`.

### Agent definition [E]

- **Instructions:** `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `.github/copilot-instructions.md` and `.github/instructions/**/*.instructions.md`.
- **Skills:** `.github/skills`, `.claude/skills`, `.agents/skills` and `~/.copilot/skills`.
- **Agents:** `.github/agents` and `~/.copilot/agents`.
- **Hooks:** `.github/hooks/` and `~/.copilot/hooks/`.

### Tools [E]

- **MCP:** `--additional-mcp-config <json|@file>` (local, stdio or http) and `~/.copilot/mcp-config.json`.
- **Built-in MCP:** `--disable-builtin-mcps` turns off the built-in GitHub MCP server.

### Sessions [E]

`--resume`, `--continue`, `--session-id`, `-n`. Stored in `~/.copilot/session-state/`.

### Headless sign-in [E]

- **Device code:** at github.com/login/device. This is the default on headless hosts.
- **Token storage:** in the keychain. Without libsecret it prompts to store a plaintext token in `~/.copilot/config.json`.
- **Personal access token:** a fine-grained PAT (`github_pat_`, with the "Copilot Requests" permission) via `COPILOT_GITHUB_TOKEN`, `GH_TOKEN` or `GITHUB_TOKEN`. Classic `ghp_` tokens are not supported.

### Limits [E]

Monthly AI credits (1 credit = $0.01):

| Plan | Price | Base credits | Flex allotment | Total |
|---|---|---|---|---|
| Pro | $10 | 1,000 | 500 | 1,500 |
| Pro+ | $39 | 3,900 | 3,100 | 7,000 |
| Max | $100 | 10,000 | 10,000 | 20,000 |

The credits are shared across the IDE, github.com and the CLI.

### Egress [E]

api.githubcopilot.com and `*.individual.githubcopilot.com`, plus github.com and api.github.com.

### Native automation [E]

`/every` and `/after` work only in an interactive session. Other routes are GitHub Actions and agentic workflows.

---

## 8. Mistral Vibe CLI (Le Chat Pro, now "Mistral Vibe")

### Terms and credential

- **[E]** From the README: browser sign-in yields a key, and "The credential is still a Mistral API key" (`~/.vibe/.env`, `MISTRAL_API_KEY`).
- **[S]** docs.mistral.ai and help center: a Vibe CLI "plan key" is tied to the plan's Vibe budget. The help center steers API automation to a pay-as-you-go key, and the sources conflict on this.
- Status: unclear.

### Mechanics [E]

- **Headless:** `vibe -p|--prompt … --output text|json|streaming`, with `--max-turns`, `--max-price`, `--time-limit` and `--output-dir`. Exit code 3 means the agent stopped without finishing.
- **Sessions:** `--resume SESSION_ID` (taken from `session_id`) and `--continue`.
- **Instructions:** `AGENTS.md`, plus `--system-prompt-file`.
- **Skills:** `.vibe/skills`, `.agents/skills` and `~/.vibe/skills`.
- **Agents:** `~/.vibe/agents/` and `.vibe/agents/`, with a `task` subagent tool.
- **Hooks:** yes.
- **MCP:** `vibe mcp add --transport stdio|streamable-http`, config in `config.toml`.
- **Tools:** `--enabled-tools` and `--disabled-tools`.
- **Native schedule:** `/loop`, which only runs while Vibe is open and idle.
- **Install:** Python- or uv-based, not npm. **[I]** Today's `CliSpec` only accepts npm.

---

## 9. Z.ai GLM Coding Plan, MiniMax, Cursor, Meta

### Z.ai GLM Coding Plan

- An API key on `https://api.z.ai/api/coding/paas/v4`. I found no first-party CLI.
- **[S]** docs.z.ai/legal-agreement/subscription-terms:
  - Quota is "only used within officially supported tools".
  - "You shall not use the GLM Coding Plan quota for general-purpose API access … your own applications, bots, websites, or SaaS products" without written agreement.
  - No resale or proxying. "licensed only to the individual natural person".
- Status: forbidden.

### MiniMax Token / "M" Plan

- **[S]** platform.minimax.io: "designed for individual, interactive developer use".
- **[3P]** A first-party `MiniMax Code` CLI (`@minimax-ai/code`, repo MiniMax-AI/minimax-code, MIT) was reported 2026-09-21. Not verified.
- Status: unclear, leaning not-for-unattended.

### Cursor `cursor-agent`

[S] cursor.com/docs/cli:
- **Invocation:** `-p/--print --output-format text|json|stream-json`.
- **stream-json events:** `system/init`, `user`, `assistant`, `tool_call` (started or completed), and a terminal `result{result, is_error, duration_ms, session_id}`. A failed run may end without `result`.
- **Auth:** `CURSOR_API_KEY` or `--api-key`.
- **Automations** (cursor.com/docs/cloud-agent/automations): a schedule, GitHub, Slack and Linear events, or a webhook POST with an API key minted per automation.
- I couldn't read the terms (cursor.com was blocked). A **[3P]** forum reply from Cursor staff says usage bills under the plan of the key's owner.

### Meta

- **[3P]** Muse Code (beta ~2026-08-05) signs in with a Meta account or `META_API_KEY`, and is usage-based.
- **[3P]** Consumer Muse has scheduled tasks and connectors (MCP via the Connector Platform, which is reviewed). There's no public API to drive a user's Muse.
- **[I]** This is not a subscription engine.

---

## 10. Can one declarative `CliSpec` drive them all?

**[I]** Yes, for the nine CLIs examined:
- Claude Code, Codex, Gemini, Grok Build, Kimi, Qwen, Copilot, Vibe and cursor-agent.

Every one of them has:
- a one-shot `-p` or `exec` mode with JSON or JSONL output;
- resume by session id;
- an MCP client with streamable HTTP;
- built-in tool filters;
- an `AGENTS.md`-compatible instruction file, through a filename map where needed;
- `SKILL.md` folders.

The differences are all expressible as data. Today's `invoke` is a single *inference* call with tools disabled (`--tools ""`, `features.*=false`, `tools.core: []`, `mcp_servers={}`). An engine needs its own section.

**Fields to add**, as a sketch of shape, not a schema:

| Field | Why | Per-vendor values (evidence above) |
|---|---|---|
| `engine.argv` / `engine.resume_argv` | new turn vs. continue | Claude `--resume {sid}`; Codex `exec resume {sid}` (drop `--ephemeral`); Gemini `-r {sid}`; Grok `-r {sid}`; Kimi `--session {sid}` (cannot be combined with `--agent-file`); Qwen and Copilot `--resume {sid}`; Vibe `--resume {sid}` |
| `engine.events` | parse the stream: `session_id_path`, `final_text`, `error`, `usage`, `tool_call` matchers | Claude `result.session_id`, `is_error`; Codex `thread.started.thread_id`, `turn.failed` or `error`; Gemini `init`, `result`, exit codes 42 and 53; Grok `sessionId`, `stopReason`; Kimi: no id in the stream (needs `session_index.jsonl` or `--continue` in a per-agent cwd); cursor `result.session_id` |
| `instructions` | map the canonical `AGENTS.md` | native for Codex, Kimi, Grok, Vibe, Copilot and Qwen; Claude native only if no `CLAUDE.md` (v2.1.277 or later); Gemini via `context.fileName`; Codex has a 32 KiB cap |
| `skills_paths` | where to project `SKILL.md` folders | `.agents/skills` works for Codex, Gemini, Kimi, Grok, Vibe and Copilot; Claude needs `.claude/skills`; Qwen `.qwen/skills` (project) |
| `mcp` | inject the TinyAssets MCP URL and its auth | flag templates (Claude `--mcp-config` + `--strict-mcp-config`; Copilot `--additional-mcp-config`; Codex `-c mcp_servers.ta.url=… bearer_token_env_var=…`) or file templates (Gemini or Qwen `settings.json`, Kimi `mcp.json`, Grok or Vibe `config.toml`); plus `trust` handling (Gemini `--allowed-mcp-server-names`, Copilot `--trust-folder`, Grok `--trust`, Kimi user-level file) |
| `tools` | allow or deny built-ins | Claude `--tools`/`--allowedTools`; Copilot `--available-tools`; Grok `--tools`/`--disallowed-tools`; Vibe `--enabled-tools`; Codex `features.*`; Gemini `tools.core`; Kimi agent file `tools` |
| `unattended_argv` | no prompts | Claude `--permission-mode … --permission-prompts none`; Gemini `--approval-mode`; Copilot `--no-ask-user`/`--allow-tool`; Grok `--yolo`/`--permission-mode`; Vibe `--auto-approve`; Kimi `-p` is auto |
| `home_env` | per-agent state root (exists partly as `{config}`) | `CLAUDE_CONFIG_DIR` (+`CLAUDE_CODE_PROJECT_DIR_NAME`), `CODEX_HOME`, `GEMINI_CLI_HOME`, `GROK_HOME`, `KIMI_CODE_HOME`, `COPILOT_HOME` |
| `secret_paths` (split out of `state_paths`) | must never leave the sandbox | Claude `.claude/.credentials.json`; Codex `.codex/auth.json`; Gemini `.gemini/oauth_creds.json`; Grok `.grok/auth.json` + `mcp_credentials.json`; Kimi `.kimi-code/credentials/`; Copilot `.copilot/config.json` (plaintext fallback); Vibe `.vibe/.env`; Qwen `.qwen/settings.json` if the key is stored there |
| `session_paths` | persist on the sandbox volume for resume | `.claude/projects/`, `.codex/sessions/`, `.gemini/tmp/*/chats/`, `.grok/sessions/`, `.kimi-code/sessions/` + `session_index.jsonl`, `.copilot/session-state/`, `.qwen/projects/` |
| `credential` | `vendor_login_in_sandbox` vs. `api_key` | Qwen, Z.ai, Vibe, cursor and Copilot PAT are keys (gateway or broker); the rest are vendor OAuth inside the sandbox |
| `trigger` | `human` vs. `unattended_allowed` | ADR-031 already names `trigger: human` |
| `no_update` | pin the binary | `DISABLE_AUTOUPDATER`, `COPILOT_AUTO_UPDATE=false`, `--no-auto-update`, gemini `disableAutoUpdate` |
| `install.manager` | non-npm installs | Vibe (Python), Grok Build and cursor (curl binaries) |
| `hosts.provider` corrections | egress | add platform.claude.com for Claude refresh; `cli-chat-proxy.grok.com` for Grok |

Two engine-specific gotchas:
- **Claude:** subscription auth forbids `--bare`, so isolation has to come from `CLAUDE_CONFIG_DIR` plus `--strict-mcp-config` and settings, not from bare mode.
- **Claude scheduled tasks:** they cannot see local MCP servers. The TinyAssets MCP must be a claude.ai connector.

## 11. Verdicts on the terms

| Vendor | Hosting the unmodified CLI for the user's own sign-in | Unattended or scheduled use on the subscription |
|---|---|---|
| Anthropic | **Allowed** with Commercial Terms, no binary changes, no paying for or intermediating usage, no token custody [E] | Documented (`-p`, SDK, setup-token, scheduled tasks); limits "assume ordinary, individual usage" [E] |
| OpenAI | **Unclear**; commercial SIWC goes through the interest form [S] | API key recommended; ChatGPT auth in CI only on trusted private infrastructure [S] |
| Google | **Unclear**; third-party software using Gemini CLI OAuth is **forbidden** [E] | Not addressed [E] |
| xAI | **Unclear** (x.ai blocked); partner integrations exist [S] | API key for CI [E] |
| Kimi | Likely allowed (first-party CLI) [I]; non-whitelisted clients get 403 [3P] | Unclear |
| Qwen / Alibaba | n/a (API key) | **Forbidden**: "should not be used for automated scripts, application backends" [S] |
| Z.ai | n/a | **Forbidden** outside supported tools, bots or SaaS; no proxying [S] |
| MiniMax | n/a | Unclear: "individual, interactive" [S] |
| GitHub Copilot | No prohibition found [E] | Documented (CI, PAT, Actions) [E] |
| Mistral | n/a (key) | Unclear [S] |
| Cursor | Key-based headless documented [S] | Automations are first-party [S] |

## 12. Where today's terms differ from ADR-031's 2026-10-26 reading

Anthropic (read directly today):
1. **No human-started rule.** The legal page sets no turn-origin condition on a hosted Claude Code. ADR-031 D3 ("human-started") is stricter than the vendor text [E+I]. The text now also says advertised limits "assume ordinary, individual usage", which is new relative to the ADR's quote.
2. **The ADR's quoted wording has changed.** The page now lists "Free, Pro, Max, or Ultra" (the ADR said "Free, Pro, or Max"). It says Anthropic "does not permit third-party developers to offer Claude.ai login into their own applications", with no "unless approved" or "previously approved" carve-out visible. ADR D6's "Anthropic 'previously approved' status" route is not on the page; the page points to "contact sales".
3. **Explicit hosting conditions.** There is now an explicit "Customers may not pay for, resell, or intermediate Claude usage", and the explicit end-user carve-out for platform-hosted Claude Code.
4. **Scheduled-task limits have more detail.** Routines are renamed "scheduled tasks". There are separate 100-an-hour account caps for scheduled runs, Run now, and API fires, and cron faster than hourly is rejected. When the *subscription* limit is hit, usage credits can supply overage (the hourly caps still have none).
5. **API credits for subscribers** now include **Ultra ($200)**, Max $100, and Team $20/$100 per seat capped at $500 pooled. Pro gets none. They cover `claude -p` and the SDK only with a linked-Console API key.

Other vendors (search index only, not re-fetched):
6. **OpenAI:** ChatGPT now has **event-triggered tasks** (30 an hour, 720 a day) and **MCP Events** webhooks. ADR-031 said "ChatGPT scheduled tasks have no webhooks" [S]. Plan use in commercial apps now names Go as well as Plus and Pro [S].
7. **Kimi:** the help center warns that using the key "with unauthorized platforms or tools may be considered a violation", and enforces a client whitelist [S/3P]. That is narrower than ADR-031's "members may use their keys in third-party platforms".
8. **Not covered by ADR-031:**
   - Google's Gemini CLI third-party-OAuth ban [E].
   - The Qwen, Z.ai and MiniMax interactive-only or tool-only clauses [S].
   - Copilot's documented CI and PAT path [E].
   - Gemini scheduled actions [S].
9. **Not re-verifiable today:** xAI (x.ai blocked).

## 13. Native vendor automation (ADR-031 D4 routes)

| Vendor | Feature | Trigger | Limits | Where MCP comes from |
|---|---|---|---|---|
| Claude | Cloud scheduled tasks (formerly routines) | cron (hourly or slower), `/fire` HTTP, GitHub | 100/h account; 30/h per task; draws plan usage | claude.ai connectors only [E] |
| Claude | Channels | MCP push into a running session | allowlisted plugins during preview [E] | local |
| ChatGPT | Scheduled tasks, event-triggered tasks, MCP Events | time, app events, signed webhooks | 3/5/10/15 active; 30/h, 720/day event-triggered [S] | ChatGPT apps |
| Codex | Automations | schedule | managed in ChatGPT web or desktop [S] | Codex config |
| Gemini | Scheduled actions and task schedules | time | 10 active and 50 schedules / 15 running [S] | n/a |
| Grok | Tasks | time | unverified [3P] | n/a |
| Meta Muse | Scheduled tasks + connectors | time | no public API to drive it [3P] | reviewed connector platform |
| Cursor | Automations | schedule, webhook (+API key), GitHub/Slack/Linear | n/a [S] | Cursor config |
| Copilot | `/every`, `/after` (session-only); Actions | time, CI | AI credits [E] | `mcp-config.json` |
