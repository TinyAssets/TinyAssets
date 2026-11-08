# Submitting the TinyAssets plugins

This is the founder's checklist. Submitting a listing is an outward act, so no agent does
it. The plugins in `chatgpt/` and `claude/` are built by `node platform/plugins/build.mjs`
from the starter's skills and `platform/doors/apps.json`. Don't edit them by hand.

## Before either submission

1. **The doors are live.** Mode `routes` has run (the go-ahead deploy, mode `dark`, moves
   no route), and `https://tinyassets.io/mcp` answers a signed-in client.
2. **Dynamic registration is decided.** `DCR_ENABLED` is `"true"` if Grok, Muse or Gemini
   are to connect, and each one's redirect URIs are recorded in `apps.json`
   (`match.redirects`). Until they are, its registration is refused. Claude and ChatGPT use
   CIMD and don't need it.
3. **The live proof passed.** The steps are in `tasks.md`, under "Owed by BRAIN, live".
   Both plugins' prompts were tried on a real account.
4. **The legal pages say what TinyAssets stores.** Both directories link the privacy and
   terms pages, which today are `https://tinyassets.io/legal/`.
5. **Icons and screenshots.** Neither plugin has any yet. Add them under each plugin's
   `assets/` through `build.mjs`, and name them in the manifests.

## ChatGPT and Codex (OpenAI's plugin directory)

- **Format:** the portable Agent Plugins format: `plugin.json` and `mcp.json` with
  OpenAI's `extensions.com.openai` block, and `skills/`. Read
  `developers.openai.com/plugins/build/plugins.md` and `.../deploy/submission` on the day.
- **Submit** through OpenAI's developer portal, **With MCP**, pointing at
  `https://tinyassets.io/mcp`.
- **Authentication:** OAuth, with discovery. Our server advertises CIMD, and ChatGPT
  prefers it.
- **Example prompts:** take them from the manifest's `defaultPrompt`. Put "Set me up on
  TinyAssets" first.
- **The review reads the MCP `instructions` and tool descriptions** (OpenAI: "scanned and
  reviewed"). They sit in `platform/doors/src/instructions.ts` and `platform/contract/doors.json`. Check them
  against the plugin guidelines on the day: no "always", no broad triggering, and nothing
  that redirects the conversation.
- **The tool annotations** are explicit on every tool. `file_append` is
  `destructiveHint: false` because it only adds.
- **The directory profile** (`contract/profiles.json`, `directory`) leaves out `code`, the generic
  executor. Ask OpenAI whether a listed plugin's grants are pinned to it. The founder
  decides on that new door when the listing is accepted.

## Claude (Anthropic's plugin directory)

- **Format:** `.claude-plugin/plugin.json`, `.mcp.json` (the remote server, `type: http`)
  and `skills/`.
- **Submit** at `claude.ai/directory/manage`, choosing **Submit new**, then **Plugin
  bundle**.
- **The directory reads a repository's branch or tag**, and it validates one plugin
  folder. Either submit `platform/plugins/claude` in this repository, or publish it to a
  repository of its own: the checklist flags scripts it can't follow when the plugin is a
  subfolder, and this folder holds none.
- **Run Validate first**, then fix every **Blocking** finding.
- **Name:** `tinyassets`. It is our own product name, and none of Claude's reserved words.
- **Example prompts:** put "Set me up on TinyAssets" first.
- **Tool descriptions** must describe and never "tell Claude how to behave". The workflow
  sits in the plugin's skills, where Anthropic says it belongs.
- **The connector listing is separate.** Submitting the MCP server as a connector puts it in
  Suggested Connectors. Its form asks how users authenticate: choose OAuth with CIMD.

## Meta Muse (Muse Connector Platform)

- Muse's listing route is `platform.muse.ai/docs`. It needs OAuth client credentials, which
  means minting a client for Muse at our authorization server. That is a founder decision:
  it is a new client record, not DCR.
- Its connector guidelines forbid tool text that makes Muse take actions nobody asked for.
  The same descriptions apply.

## Not now

- **Gemini.** Google's Connected Apps policy forbids agent-to-agent interaction, and
  `converse`, rooms and mail are exactly that. A listing would need a profile without them,
  and Gemini isn't one of Direction 7's directories. Gemini stays a custom app.

## After a listing is live

- Add its listing URL to that app's `connect.link` in `apps.json`, then rebuild. The public
  connect page and `agents_list` pick it up.
- Raise the plugin's version with every starter version (`build.mjs` takes it from
  `manifest.json`), and resubmit if the directory doesn't follow updates on its own.
