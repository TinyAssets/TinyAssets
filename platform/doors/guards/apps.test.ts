/**
 * The AI apps an owner may connect (`apps.json`, slice BRAIN) and the plugins built from the
 * starter's skills (`platform/plugins`, which `npm run generate` writes): the data's shape,
 * how a signed-in client is known from it, the dynamic-registration rule it carries, and what
 * the plugins connect to.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";

import { describe, expect, it } from "vitest";

import { PLATFORM } from "../../ci/paths";
import raw from "../../contract/apps.json" with { type: "json" };
import { appList, checkedApps, connectUrl, refusedRegistrationLine, registrationRefusal,
         vendorOf } from "../../contract/src/apps";
import { MCP_RESOURCE } from "../src/config";
import { CLIENT_INSTRUCTIONS } from "../src/instructions";
import { toolTable } from "../../contract/src/doors";

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

describe("apps.json", () => {
  it("holds each app to its shape: an id, its registration, match data, a connect step " +
     "and the vendor page it was read from", () => {
    const apps = appList();
    expect(apps.map((app) => app.id)).toEqual(["claude", "chatgpt", "grok", "muse", "gemini",
                                               "cursor"]);
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

  it("knows a CIMD client by its document's host, verified only when every redirect is the " +
     "app's own, and any other by its name (unverified), and no app as none", () => {
    const claudeCode = "https://claude.ai/oauth/claude-code-client-metadata";
    expect(vendorOf(claudeCode, null, ["http://127.0.0.1:53111/callback",
                                       "http://localhost/callback"]))
      .toEqual({ id: "claude", verified: true });
    expect(vendorOf("https://claude.ai/oauth/web", null,
                    ["https://claude.ai/api/mcp/auth_callback"]))
      .toEqual({ id: "claude", verified: true });
    expect(vendorOf("https://auth.chatgpt.com/cimd.json", "anything",
                    ["https://chatgpt.com/connector/oauth/abc123"]))
      .toEqual({ id: "chatgpt", verified: true });
    // A document on the vendor's host that sends access elsewhere is not the vendor's (M2):
    // the app it claims, unverified.
    for (const redirects of [["https://attacker.example/cb"],
                             ["https://chatgpt.com/connector/oauth/x", "https://evil.test/cb"],
                             ["https://chatgpt.com.evil.test/connector/oauth/x"],
                             ["https://chatgpt.com/connector_platform_oauth_redirect/more"],
                             ["https://chatgpt.com/connector/oauth/x?next=evil"], []]) {
      expect(vendorOf("https://chatgpt.com/some/user/path/cimd.json", "ChatGPT", redirects),
             JSON.stringify(redirects)).toEqual({ id: "chatgpt", verified: false });
    }
    // A CIMD client is its host's or no one's: its self-declared name is not asked.
    expect(vendorOf("https://example.com/client.json", "Claude")).toBeNull();
    expect(vendorOf("https://notclaude.ai/c.json", null)).toBeNull();
    expect(vendorOf("opaque-registered-id", "Grok Bot")).toEqual({ id: "grok", verified: false });
    expect(vendorOf("opaque-registered-id", "Google GEMINI app"))
      .toEqual({ id: "gemini", verified: false });
    expect(vendorOf("opaque-registered-id", "Musement")).toBeNull();
    expect(vendorOf("opaque-registered-id", null)).toBeNull();
  });

  it("takes a dynamic registration only from a vendor that registers dynamically, sending " +
     "access only to that vendor's own redirects; one with none recorded takes none", () => {
    // As apps.json stands: no dcr vendor's redirects are read yet, so every one is off.
    for (const name of ["Grok", "Gemini", "Muse", "Cursor"]) {
      expect(registrationRefusal({ client_name: name, redirect_uris: ["https://x.test/cb"] }),
             name).toMatch(/is off until its redirect URIs are recorded/);
    }
    for (const metadata of [{ client_name: "ChatGPT" }, { client_name: "Claude Code" },
                            { client_name: "Unknown" }, {}, { client_name: 7 }]) {
      expect(registrationRefusal(metadata)).toMatch(/^Dynamic registration is only for/);
    }
    // Once a vendor's redirects are recorded (the live test reads them), only those.
    const apps = checkedApps({ ...raw, apps: raw.apps.map((app) => app.id !== "grok" ? app
      : { ...app, match: { ...app.match, redirects: ["https://grok.example/oauth/",
                                                     "grokapp://oauth/callback"] } }) }).apps;
    for (const uris of [["https://grok.example/oauth/callback"],
                        ["grokapp://oauth/callback", "https://grok.example/oauth/x/y"]]) {
      expect(registrationRefusal({ client_name: "Grok", redirect_uris: uris }, apps)).toBeNull();
    }
    // The review's phishing registrations (M3): the vendor's word in the name, a stranger's
    // redirect, or none at all.
    for (const [name, uris] of [
      ["Grok official TinyAssets support", ["https://phish.example/cb"]],
      ["Grok by phish.example", ["https://grok.example/oauth/cb", "https://phish.example/cb"]],
      ["Grok", ["https://grok.example.phish.test/oauth/cb"]],
      ["Grok", ["https://grok.example/oauthx"]],
      ["Grok", ["grokapp://oauth/callback/../../x?y=1"]],
      ["Grok", []], ["Grok", "https://grok.example/oauth/cb"],
    ] as const) {
      expect(registrationRefusal({ client_name: name, redirect_uris: uris }, apps),
             `${name} ${JSON.stringify(uris)}`).not.toBeNull();
    }
  });

  it("logs a refused registration naming a dcr vendor, bounded and escaped, and no other " +
     "(review R2-M1)", () => {
    const line = refusedRegistrationLine({ client_name: "Grok Bot", token_endpoint_auth_method:
      "none", redirect_uris: ["https://grok.example/cb\n\"forged\": 1", 7,
                              ...Array.from({ length: 10 }, (_, i) => `https://x.test/${i}`)] });
    expect(line).toMatch(/^dcr_refused \{/);
    const logged = JSON.parse(line!.slice("dcr_refused ".length));
    expect(logged).toEqual({ app: "grok", client_name: "Grok Bot", more: 4,
      redirect_uris: ["https://grok.example/cb\n\"forged\": 1", "(number)",
                      ...Array.from({ length: 6 }, (_, i) => `https://x.test/${i}`)] });
    // One line: the newline inside a URI is escaped, never a second log line.
    expect(line!.split("\n").length).toBe(1);
    const long = refusedRegistrationLine({ client_name: `Gemini ${"x".repeat(1000)}`,
                                           redirect_uris: [`https://g.test/${"y".repeat(1000)}`] });
    expect(long!.length).toBeLessThan(700);
    for (const metadata of [{ client_name: "ChatGPT" }, { client_name: "Some Agent" }, {},
                            { client_name: 7 }]) {
      expect(refusedRegistrationLine(metadata)).toBeNull();
    }
  });

  it("holds each redirect pattern to an https, loopback or custom scheme URI with no query",
     () => {
    const one = raw.apps[0]!;
    for (const pattern of ["http://claude.ai/cb", "https://claude.ai/cb?x=1", "not a uri",
                           "https://claude.ai/cb#f"]) {
      expect(() => checkedApps({ ...raw, apps: [{ ...one, match: { ...one.match,
        redirects: [pattern] } }] }), pattern).toThrow(/match\.redirects/);
    }
  });

  it("points each app at its place on the public connect page", () => {
    expect(connectUrl("grok")).toBe("https://tinyassets.io/connect/#grok");
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
