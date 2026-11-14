/**
 * The AI apps an owner may connect (`apps.json`, slice BRAIN): the data's shape, how a
 * signed-in client is known from it, and the dynamic-registration rule it carries. What the
 * doors serve an outside agent, the apps' connect sentences and the plugins among it, is the
 * doors' own guard (`doors/guards/served.test.ts`).
 */
import { describe, expect, it } from "vitest";

import raw from "../apps.json" with { type: "json" };
import { appList, checkedApps, connectUrl, refusedRegistrationLine, registrationRefusal,
         vendorOf } from "../src/apps";

describe("apps.json", () => {
  it("holds each app to its shape: an id, its registration, match data, a connect step " +
     "and the vendor page it was read from", () => {
    const apps = appList();
    expect(apps.map((app) => app.id)).toEqual(["claude", "chatgpt", "grok", "muse", "gemini",
                                               "cursor"]);
    for (const app of apps) {
      expect(app.connect.steps.length, app.id).toBeGreaterThan(0);
      expect(app.source, app.id).toMatch(/^https:\/\//);
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
