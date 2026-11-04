/**
 * The AI apps an owner may connect (`apps.json`, slice BRAIN) and the plugins built from the
 * starter's skills (`platform/plugins`): the data's shape, how a signed-in client is known
 * from it, the dynamic-registration rule it carries, and the plugins current with their one
 * source.
 */
import { execFileSync } from "node:child_process";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";

import { describe, expect, it } from "vitest";

import { PLATFORM } from "../../owner/guards/sources";
import raw from "../apps.json" with { type: "json" };
import { appList, checkedApps, connectUrl, registrationRefusal, vendorOf } from "../src/apps";
import { MCP_RESOURCE } from "../src/config";
import { CLIENT_INSTRUCTIONS } from "../src/instructions";
import { toolTable } from "../src/tools";

const PLUGINS = join(PLATFORM, "plugins");
/**
 * An ask to save to an agent's own memory or to install something: what text TinyAssets serves
 * to an agent never holds (every directory's rules), and what the user's own connect sentence
 * may.
 */
const ASKS = /\binstall|\bsave (a|the|this)\b[^.]{0,40}\bskill|\b(save|store|keep|write|add)\b[^.]{0,40}\b(your|its) (own )?(app's )?memory|\bremember (this|that)\b/i;

/** Every text TinyAssets serves to an outside agent, by where it is served. */
function served(): [string, string][] {
  const out: [string, string][] = [["CLIENT_INSTRUCTIONS", CLIENT_INSTRUCTIONS]];
  for (const tool of toolTable()) {
    out.push([`${tool.name}'s description`, tool.description]);
    for (const [field, schema] of Object.entries(
      (tool.input["properties"] ?? {}) as Record<string, { description?: string }>)) {
      out.push([`${tool.name}.${field}`, schema.description ?? ""]);
    }
  }
  const walk = (dir: string): string[] => readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? walk(path) : [path];
  });
  for (const plugin of ["chatgpt", "claude"]) {
    for (const file of walk(join(PLUGINS, plugin))) {
      // The OKF spec is the vendored standard, not our text.
      if (!file.endsWith("okf-spec.md")) {
        out.push([relative(PLUGINS, file), readFileSync(file, "utf8")]);
      }
    }
  }
  return out;
}

describe("apps.json", () => {
  it("holds each app to its shape: an id, its registration, match data, a connect step " +
     "and the vendor page it was read from", () => {
    const apps = appList();
    expect(apps.map((app) => app.id)).toEqual(["claude", "chatgpt", "grok", "muse", "gemini",
                                               "cursor"]);
    expect(checkedApps().server).toBe(MCP_RESOURCE);
    for (const app of apps) {
      expect(app.connect.steps.length, app.id).toBeGreaterThan(0);
      expect(app.source, app.id).toMatch(/^https:\/\//);
      // A vendor whose connect is a chat act gives the sentence the user sends: their own
      // request, in their own chat, so it may ask their agent to save the skill.
      if (app.connect.say !== undefined) {
        expect(app.connect.say).toContain(MCP_RESOURCE);
        expect(app.connect.say).toMatch(ASKS);
      }
    }
    expect(apps.filter((app) => app.registration === "dcr").map((app) => app.id))
      .toEqual(["grok", "muse", "gemini", "cursor"]);
  });

  it("keeps the split: the user's own connect sentence may ask their agent to save the " +
     "skill; nothing TinyAssets serves to an agent asks it to save to its memory or install " +
     "anything", () => {
    const texts = served();
    expect(texts.length).toBeGreaterThan(toolTable().length);
    for (const [where, text] of texts) {
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

  it("refuses a file out of shape, loudly", () => {
    const one = raw.apps[0]!;
    for (const [what, apps] of [
      ["a missing field", [{ ...one, source: undefined }]],
      ["an extra field", [{ ...one, extra: 1 }]],
      ["a bad id", [{ ...one, id: "Claude" }]],
      ["a bad registration", [{ ...one, registration: "both" }]],
      ["no steps", [{ ...one, connect: { steps: [] } }]],
      ["an http link", [{ ...one, connect: { ...one.connect, link: "http://claude.ai" } }]],
      ["a host twice", [one, { ...one, id: "again", match: { hosts: one.match.hosts,
                                                             names: ["Other"] } }]],
      ["a bad date", [{ ...one, checked: "yesterday" }]],
    ] as const) {
      expect(() => checkedApps({ ...raw, apps }), what).toThrow(/^apps\.json: /);
    }
  });

  it("knows a CIMD client by its document's host (verified) and any other by its name " +
     "(unverified), and no app as none", () => {
    expect(vendorOf("https://claude.ai/oauth/claude-code-client-metadata", null))
      .toEqual({ id: "claude", verified: true });
    expect(vendorOf("https://auth.chatgpt.com/cimd.json", "anything"))
      .toEqual({ id: "chatgpt", verified: true });
    // A CIMD client is its host's or no one's: its self-declared name is not asked.
    expect(vendorOf("https://example.com/client.json", "Claude")).toBeNull();
    expect(vendorOf("https://notclaude.ai/c.json", null)).toBeNull();
    expect(vendorOf("opaque-registered-id", "Grok Bot")).toEqual({ id: "grok", verified: false });
    expect(vendorOf("opaque-registered-id", "Google GEMINI app"))
      .toEqual({ id: "gemini", verified: false });
    expect(vendorOf("opaque-registered-id", "Musement")).toBeNull();
    expect(vendorOf("opaque-registered-id", null)).toBeNull();
  });

  it("takes a dynamic registration only from a vendor that registers dynamically", () => {
    for (const name of ["Grok", "Gemini", "Muse", "Cursor"]) {
      expect(registrationRefusal({ client_name: name }), name).toBeNull();
    }
    for (const metadata of [{ client_name: "ChatGPT" }, { client_name: "Claude Code" },
                            { client_name: "Unknown" }, {}, { client_name: 7 }]) {
      expect(registrationRefusal(metadata)).toMatch(/^Dynamic registration is only for/);
    }
  });

  it("points each app at its place on the public connect page", () => {
    expect(connectUrl("grok")).toBe("https://tinyassets.io/connect/#grok");
  });
});

describe("the plugins", () => {
  it("are current with the starter's skills and apps.json (build.mjs --check)", () => {
    execFileSync(process.execPath, [join(PLUGINS, "build.mjs"), "--check"], { stdio: "pipe" });
  });

  it("carry the brain's skills byte for byte, the MCP server, and the onboarding prompt " +
     "first", () => {
    const starter = join(PLATFORM, "starter", "workspace", ".agents", "skills");
    for (const plugin of ["chatgpt", "claude"]) {
      for (const skill of ["shared-brain", "onboarding", "goals"]) {
        expect(readFileSync(join(PLUGINS, plugin, "skills", skill, "SKILL.md"), "utf8"))
          .toBe(readFileSync(join(starter, skill, "SKILL.md"), "utf8"));
      }
    }
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
