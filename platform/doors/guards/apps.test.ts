/**
 * The AI apps an owner may connect (`apps.json`, slice BRAIN) and the plugins built from the
 * starter's skills (`platform/plugins`): the data's shape, how a signed-in client is known
 * from it, the dynamic-registration rule it carries, and the plugins current with their one
 * source.
 */
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { PLATFORM } from "../../owner/guards/sources";
import raw from "../apps.json" with { type: "json" };
import { appList, checkedApps, connectUrl, registrationRefusal, vendorOf } from "../src/apps";
import { MCP_RESOURCE } from "../src/config";

const PLUGINS = join(PLATFORM, "plugins");

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
      // A vendor whose connect is a chat act gives the sentence; it asks nothing of the
      // vendor's agent but to connect (no save to its memory, no install).
      if (app.connect.say !== undefined) {
        expect(app.connect.say).toContain(MCP_RESOURCE);
        expect(app.connect.say).not.toMatch(/memory|remember|install|skill/i);
      }
    }
    expect(apps.filter((app) => app.registration === "dcr").map((app) => app.id))
      .toEqual(["grok", "muse", "gemini", "cursor"]);
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
