/**
 * What the doors serve an outside agent (slice BRAIN): the MCP instructions, each tool's
 * text, the plugins built from the starter's skills (`platform/plugins`, which `npm run
 * generate` writes) and what they connect to, and each app's connect sentence
 * (`contract/apps.json`, whose data `contract/guards/apps.test.ts` holds). None asks an agent
 * to save to its memory or install anything, and none triggers past the user's own intent.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";

import { describe, expect, it } from "vitest";

import { PLATFORM } from "../../ci/paths";
import { appList } from "../../contract/src/apps";
import { toolTable } from "../../contract/src/doors";
import { MCP_RESOURCE } from "../src/config";
import { CLIENT_INSTRUCTIONS } from "../src/instructions";

const PLUGINS = join(PLATFORM, "plugins");
/**
 * An ask to save to an agent's own memory or to install something: what text TinyAssets serves
 * to an agent never holds (every directory's rules), and what the user's own connect sentence
 * may.
 */
const ASKS = /\binstall|\bsave (a|the|this)\b[^.]{0,40}\bskill|\b(save|store|keep|write|add)\b[^.]{0,40}\b(your|its) (own )?(app's )?memory|\bremember (this|that)\b/i;

/**
 * Wording that makes a tool or a skill fire beyond the user's own intent: what OpenAI's
 * plugin guidelines refuse ("must not recommend overly broad triggering"), Anthropic's
 * review ("don't tell Claude how to behave") and Muse's ("unrequested actions").
 */
const TRIGGERS = /\balways\b|\bevery ?time\b|\beach time\b|\bwhenever\b|\bbefore (?:every|each|any) (?:answer|reply|response|message|request|turn)\b|\b(?:for|in|on) (?:every|each|any) (?:message|request|question|conversation|turn)\b|\bno matter what\b|\bat (?:the )?start of every\b/i;

/** Every description in a JSON Schema, nested ones included, by where it sits. */
function schemaTexts(schema: unknown, at: string): [string, string][] {
  if (typeof schema !== "object" || schema === null) {
    return [];
  }
  const out: [string, string][] = [];
  for (const [key, value] of Object.entries(schema as Record<string, unknown>)) {
    if ((key === "description" || key === "title") && typeof value === "string") {
      out.push([`${at}.${key}`, value]);
    } else {
      out.push(...schemaTexts(value, `${at}.${key}`));
    }
  }
  return out;
}

/** A skill file's frontmatter description: what decides when its agent loads it. */
function skillDescription(text: string): string | null {
  return /^---\n[\s\S]*?^description: (.*)$[\s\S]*?^---$/m.exec(text)?.[1] ?? null;
}

/**
 * Every text TinyAssets serves to an outside agent, by where it is served, and whether it
 * decides when something fires (`trigger`): the instructions, each tool's title, description
 * and every description in its arguments, each plugin's manifests, and each skill's
 * frontmatter description. A skill's body is served too, but its own rules ("always ask
 * before spending") are the owner's safety, not a trigger, so only the asks are held there.
 */
function served(): { where: string; text: string; trigger: boolean }[] {
  const out = [{ where: "CLIENT_INSTRUCTIONS", text: CLIENT_INSTRUCTIONS, trigger: true }];
  for (const tool of toolTable()) {
    out.push({ where: `${tool.name}.title`, text: tool.title, trigger: true },
             { where: `${tool.name}.description`, text: tool.description, trigger: true },
             ...schemaTexts(tool.input, tool.name).map(([where, text]) =>
               ({ where, text, trigger: true })));
  }
  const walk = (dir: string): string[] => readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? walk(path) : [path];
  });
  for (const plugin of ["chatgpt", "claude"]) {
    for (const file of walk(join(PLUGINS, plugin))) {
      // The OKF spec is the vendored standard, not our text.
      if (file.endsWith("okf-spec.md")) {
        continue;
      }
      const text = readFileSync(file, "utf8");
      const where = relative(PLUGINS, file);
      if (file.endsWith("SKILL.md")) {
        out.push({ where, text, trigger: false },
                 { where: `${where} description`, text: skillDescription(text) ?? "",
                   trigger: true });
      } else {
        out.push({ where, text, trigger: true });
      }
    }
  }
  return out;
}

describe("what the doors serve", () => {
  it("gives each chat-act app the sentence the user sends: their own request, in their own " +
     "chat, so it may ask their agent to save the skill", () => {
    const says = appList().flatMap((app) => app.connect.say ?? []);
    expect(says.length).toBeGreaterThan(0);
    for (const say of says) {
      expect(say).toContain(MCP_RESOURCE);
      expect(say).toMatch(ASKS);
    }
  });

  it("keeps the split: the user's own connect sentence may ask their agent to save the " +
     "skill; nothing TinyAssets serves to an agent asks it to save to its memory or install " +
     "anything", () => {
    const texts = served();
    expect(texts.length).toBeGreaterThan(3 * toolTable().length);
    expect(texts.filter((each) => each.where.endsWith("SKILL.md description")
                                  && each.text !== "").length).toBe(12);
    for (const { where, text } of texts) {
      expect(text, where).not.toMatch(ASKS);
    }
    // The pattern sees each kind of ask, and lets the skills' own words about memory be.
    for (const ask of ["Install the TinyAssets plugin.", "and save the TinyAssets skill",
                       "Save this to your memory.", "add it to your app's memory",
                       "Remember this for next time."]) {
      expect(ask).toMatch(ASKS);
    }
    expect("A newer entry in TinyAssets is the source over your own memory or your app's " +
           "memory: use it").not.toMatch(ASKS);
  });

  it("serves no broad trigger: no always, no every time, nothing that fires past the user's " +
     "own intent, in the instructions, any tool's text or a plugin's manifests and skill " +
     "descriptions", () => {
    for (const { where, text, trigger } of served()) {
      if (trigger) {
        expect(text, where).not.toMatch(TRIGGERS);
      }
    }
    for (const broad of ["Always call this tool first.", "Use this every time the user asks.",
                         "Call it before every answer.", "Search on every message.",
                         "Use whenever anything comes up."]) {
      expect(broad).toMatch(TRIGGERS);
    }
    expect("Use this when the user's request may depend on what they already saved.")
      .not.toMatch(TRIGGERS);
  });
});

describe("the plugins", () => {
  it("carry the doors' MCP server, and the onboarding prompt first", () => {
    const openai = JSON.parse(readFileSync(join(PLUGINS, "chatgpt", "plugin.json"), "utf8"));
    const interface_ = openai.extensions["com.openai"].interface;
    expect(interface_.defaultPrompt[0]).toBe("Set me up on TinyAssets");
    expect(openai.extensions["com.openai"].onboardingSkill).toBe("./skills/onboarding/SKILL.md");
    expect(JSON.parse(readFileSync(join(PLUGINS, "chatgpt", "mcp.json"), "utf8"))
      .mcpServers.tinyassets.url).toBe(MCP_RESOURCE);
    expect(JSON.parse(readFileSync(join(PLUGINS, "claude", ".mcp.json"), "utf8"))
      .mcpServers.tinyassets).toEqual({ type: "http", url: MCP_RESOURCE });
    const claude = JSON.parse(readFileSync(join(PLUGINS, "claude", ".claude-plugin",
                                                "plugin.json"), "utf8"));
    expect(claude.name).toBe("tinyassets");
    expect(claude.version).toBe(openai.version);
  });
});
