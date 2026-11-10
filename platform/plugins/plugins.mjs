// The TinyAssets plugins (slice BRAIN, shared_brain_research.md §6.2 item 5,
// autoconnect_research.md §5.1): one for ChatGPT and Codex (the portable Agent Plugins
// format, with OpenAI's extension) and one for Claude (`.claude-plugin/`), each the MCP
// server plus the starter's own skills, byte for byte, so the repo holds one text of each
// (Direction 2: one agent definition). The skills already speak to an agent over MCP as well
// as in a sandbox. Which skills, and the version, are the starter's (`manifest.json`): the
// starter's one build makes these with everything else built from it
// (`node platform/starter/bundle.mjs`). Nothing here is submitted anywhere: SUBMIT.md is
// the founder's checklist.
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
// The doors' public identity: the origin, and the MCP door the plugins connect to.
const IDENTITY = JSON.parse(readFileSync(join(HERE, "..", "contract", "identity.json"),
                                         "utf8"));
const ORIGIN = `https://${IDENTITY.host}`;
const SERVER = `${ORIGIN}${IDENTITY.mcp}`;
const LEGAL = `${ORIGIN}/legal/`;

/** The listings' example prompts: the first makes onboarding the user's own request. */
const PROMPTS = Object.freeze([
  "Set me up on TinyAssets",
  "What do you know about my goals?",
  "Tell my other assistants I prefer short answers",
]);
const NAME = "tinyassets";
const DESCRIPTION = "Your own workspace, shared by every AI assistant you connect: what you " +
  "tell one, the others know; your goals, notes and projects in one place; and a board where " +
  "your assistants claim a task so two never do the same one.";
const AUTHOR = { name: "TinyAssets", url: ORIGIN };

function json(value) {
  return `${JSON.stringify(value, null, 2)}\n`;
}

/**
 * Each plugin's files, by path under `platform/plugins/`: `skills` (each skill file's text,
 * by its path under `skills/`) in both, and each plugin's manifests at the plugin version
 * `1.0.<starter version>`.
 */
export function plugins(skills, starterVersion) {
  const version = `1.0.${starterVersion}`;
  const out = {};
  for (const [path, text] of Object.entries(skills)) {
    out[join("chatgpt", "skills", path)] = text;
    out[join("claude", "skills", path)] = text;
  }
  out["chatgpt/plugin.json"] = json({
    $schema: "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
    name: NAME, version, description: DESCRIPTION, author: AUTHOR,
    homepage: ORIGIN, license: "Proprietary",
    keywords: ["memory", "workspace", "goals", "notes"],
    extensions: { "com.openai": {
      onboardingSkill: "./skills/onboarding/SKILL.md",
      interface: {
        displayName: "TinyAssets",
        shortDescription: "One workspace shared by all your AI assistants",
        longDescription: DESCRIPTION,
        developerName: "TinyAssets",
        category: "Productivity",
        capabilities: ["Read", "Write"],
        websiteURL: ORIGIN,
        privacyPolicyURL: LEGAL,
        termsOfServiceURL: LEGAL,
        defaultPrompt: [...PROMPTS],
      },
    } },
  });
  out["chatgpt/mcp.json"] = json({
    $schema: "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
    mcpServers: { [NAME]: { type: "streamable-http", url: SERVER,
                            extensions: { "com.openai": { auth: { type: "oauth" } } } } },
  });
  out["claude/.claude-plugin/plugin.json"] = json({
    name: NAME, displayName: "TinyAssets", version, description: DESCRIPTION,
    author: AUTHOR, homepage: ORIGIN, license: "Proprietary",
    keywords: ["memory", "workspace", "goals", "notes"],
    privacyPolicyUrl: LEGAL,
    termsOfServiceUrl: LEGAL,
  });
  out["claude/.mcp.json"] = json({ mcpServers: { [NAME]: { type: "http", url: SERVER } } });
  return out;
}
