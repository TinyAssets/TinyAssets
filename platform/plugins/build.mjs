// What an outside client gets of the starter (slice BRAIN, shared_brain_research.md §6.2
// item 5, autoconnect_research.md §5.1), so the repo holds one text of each (Direction 2: one
// agent definition):
// - the TinyAssets plugins: one for ChatGPT and Codex (the portable Agent Plugins format,
//   with OpenAI's extension) and one for Claude (`.claude-plugin/`), each the MCP server plus
//   the starter's own skills, copied byte for byte from
//   `platform/starter/workspace/.agents/skills/`. The skills already speak to an agent over
//   MCP as well as in a sandbox. `scratch` is left out: it is about a sandbox's own disk.
// - the onboarding steps the doors' MCP instructions tell a client with no plugin
//   (`doors/onboarding.json`, read by `doors/src/instructions.ts`).
//
// `node platform/plugins/build.mjs` writes them; `--check` exits 1 when a committed file
// differs from what this makes (the doors' plugins guard runs it).
// Nothing here is submitted anywhere: SUBMIT.md is the founder's checklist.
import { mkdirSync, readFileSync, readdirSync, rmSync, statSync, writeFileSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const SKILLS = join(HERE, "..", "starter", "workspace", ".agents", "skills");
const STARTER = JSON.parse(readFileSync(join(HERE, "..", "starter", "manifest.json"), "utf8"));
// The doors' public identity: the origin, the MCP door the plugins connect to, and the
// site's legal page.
const IDENTITY = JSON.parse(readFileSync(join(HERE, "..", "contract", "identity.json"),
                                         "utf8"));
const ORIGIN = `https://${IDENTITY.host}`;
const SERVER = `${ORIGIN}${IDENTITY.mcp}`;
const LEGAL = `${ORIGIN}${IDENTITY.legal}`;
/** The doors' copy of the onboarding steps, by its path under this directory. */
const ONBOARDING = join("..", "doors", "onboarding.json");

/** The skills each plugin carries, in this order: the brain's first. */
export const BUNDLED = Object.freeze(["shared-brain", "onboarding", "goals", "knowledge",
                                      "rooms-and-board", "schedule"]);
/** The listings' example prompts: the first makes onboarding the user's own request. */
export const PROMPTS = Object.freeze([
  "Set me up on TinyAssets",
  "What do you know about my goals?",
  "Tell my other assistants I prefer short answers",
]);
const NAME = "tinyassets";
const DESCRIPTION = "Your own workspace, shared by every AI assistant you connect: what you " +
  "tell one, the others know; your goals, notes and projects in one place; and a board where " +
  "your assistants claim a task so two never do the same one.";
/** Who publishes the plugins, as both manifests say it. */
const PUBLISHER = Object.freeze({
  author: { name: "TinyAssets", url: ORIGIN }, homepage: ORIGIN, license: "Proprietary",
  keywords: ["memory", "workspace", "goals", "notes"],
});
/** The plugin's version: the starter's, whose skills it carries. */
const VERSION = `1.0.${STARTER.version}`;

function json(value) {
  return `${JSON.stringify(value, null, 2)}\n`;
}

/** Every file under `dir`, as paths relative to it. */
function walk(dir, under = dir) {
  return readdirSync(dir).sort().flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? walk(path, under) : [relative(under, path)];
  });
}

/** The skills' files, as `skills/<name>/...`. */
function skillFiles() {
  const files = {};
  for (const skill of BUNDLED) {
    for (const file of walk(join(SKILLS, skill))) {
      files[join("skills", skill, file)] = readFileSync(join(SKILLS, skill, file), "utf8");
    }
  }
  return files;
}

/**
 * The onboarding skill's steps as one clause each, joined: each numbered step's bold lead,
 * in order. A step with no lead is the skill's alone (the pass and brief, which needs the
 * `schedule` skill).
 */
export function onboardingSteps(skill) {
  const leads = [...skill.matchAll(/^\d+\. \*\*([^*]+)\*\*/gm)].map((found) => {
    const lead = found[1].replace(/\s+/g, " ").replaceAll("`", "").replace(/\.$/, "");
    return `${lead[0].toLowerCase()}${lead.slice(1)}`;
  });
  if (leads.length === 0) {
    throw new Error("the onboarding skill has no step with a bold lead");
  }
  return [...leads.slice(0, -1), `then ${leads.at(-1)}`].join("; ");
}

/** Each file this makes, by path under this directory. */
export function plugins() {
  const skills = skillFiles();
  const out = {};
  for (const [path, text] of Object.entries(skills)) {
    out[join("chatgpt", path)] = text;
    out[join("claude", path)] = text;
  }
  out["chatgpt/plugin.json"] = json({
    $schema: "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
    name: NAME, version: VERSION, description: DESCRIPTION, ...PUBLISHER,
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
    name: NAME, displayName: "TinyAssets", version: VERSION, description: DESCRIPTION,
    ...PUBLISHER,
    privacyPolicyUrl: LEGAL,
    termsOfServiceUrl: LEGAL,
  });
  out["claude/.mcp.json"] = json({ mcpServers: { [NAME]: { type: "http", url: SERVER } } });
  out[ONBOARDING] = json({
    about: "Made by platform/plugins/build.mjs from the starter's onboarding skill: its " +
      "steps' bold leads. Edit the skill and run the build, never this file.",
    steps: onboardingSteps(skills["skills/onboarding/SKILL.md"]),
  });
  return out;
}

/** The files `dir` holds now, by path under this directory. */
function committed(dir) {
  try {
    return Object.fromEntries(walk(join(HERE, dir)).map((file) =>
      [join(dir, file), readFileSync(join(HERE, dir, file), "utf8")]));
  } catch {
    return {};
  }
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const made = plugins();
  if (process.argv.includes("--check")) {
    const now = { ...committed("chatgpt"), ...committed("claude"),
                  [ONBOARDING]: readFileSync(join(HERE, ONBOARDING), "utf8") };
    const stale = [...new Set([...Object.keys(made), ...Object.keys(now)])]
      .filter((path) => made[path] !== now[path]).sort();
    if (stale.length > 0) {
      console.error(`platform/plugins is stale (${stale.join(", ")}): run node ` +
                    "platform/plugins/build.mjs");
      process.exit(1);
    }
  } else {
    for (const dir of ["chatgpt", "claude"]) {
      rmSync(join(HERE, dir), { recursive: true, force: true });
    }
    for (const [path, text] of Object.entries(made)) {
      mkdirSync(dirname(join(HERE, path)), { recursive: true });
      writeFileSync(join(HERE, path), text);
    }
  }
}
