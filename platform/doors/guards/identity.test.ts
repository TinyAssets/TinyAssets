/**
 * The doors' public identity (`contract/identity.json`) is written once. Its readers that cannot
 * read it are held to it here: the owner app's own scripts, browser modules that build its
 * sign-in from their origin; and every URL the outside world reaches the doors by, and every
 * file and page of the website, against the routes the cutover hands them (`routes`), as
 * Cloudflare matches them (`routeTakes`).
 */
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { PLATFORM, REPO } from "../../ci/paths";
import identity from "../../contract/identity.json" with { type: "json" };
import { routeTakes } from "../../contract/src/identity";

/** The URLs (path and query, as a request carries them) the doors serve, each by its source. */
const TAKEN: readonly string[] = [
  // The app: the site's links and JSON-LD (`WebSite/site-react/lib/site.ts` `SITE.app`), the
  // shells (`mobile/capacitor.config.json` `server.url`, `desktop-app/config.js`), the
  // first-party resource, and its pages' hashes (`#home`, `#data/...`), which no request sends.
  "/app", "/app/", "/app#home", "/app#data/notes/a.md",
  // The site's plan link (`app/page.tsx`, `app/start/page.tsx`), which `doors/app/app.js` reads.
  "/app?upgrade=1",
  // The Android notification, compiled into installed builds (`TinyAssetsMessagingService.java`).
  "/app?request=r&item=i",
  // The first-party sign-in's return (`first_party.redirect`), which reads its query.
  "/app/signed-in?code=c&state=s&iss=x", "/app/signed-in?error=access_denied",
  // A connect's end (`doors/src/connect.ts`), the app's scripts and the owner surface.
  "/app/connected", "/app/connected#done", "/app/static/app.js", "/app/api/account",
  "/app/api/agent/socket?t=1",
  // The old stack's returns and beacon, still in old shells, emails and bookmarks: the doors
  // answer them (the app ignores the query; the rest are the doors' 404).
  "/app?code=c&state=s", "/app?subscribed=1", "/app/client-error", "/app/billing/webhook",
  // The other doors (Fact 11): the model door, the keyed API, the sign-in, the hooks and the
  // authorization documents.
  "/mcp", "/mcp?x=1", "/api", "/api/tools/file_read", "/authorize?client_id=x",
  "/oauth/upstream/callback?code=c", "/oauth/clients/web.json", "/hooks/e2b/lifecycle",
  "/hooks/inbound/x", "/.well-known/oauth-protected-resource/mcp",
  "/.well-known/oauth-authorization-server",
];

/** Every path below `folder` of the website (`WebSite/site-react`), from it. */
function below(folder: string): string[] {
  return (readdirSync(join(REPO, "WebSite", "site-react", folder), { recursive: true }) as string[])
    .map((path) => path.replaceAll("\\", "/"));
}

/**
 * The website's URLs, which no doors route may take: each public file, each page by its folder
 * under `app/` (exported with a trailing slash), and the marks' versioned links.
 */
const SITE: readonly string[] = [
  "/favicon.ico?v=x", "/site.webmanifest?v=x", "/icons/apple-touch-icon.png?v=x",
  ...below("public").map((path) => `/${path}`),
  ...below("app").filter((path) => /(^|\/)page\.tsx$/.test(path) && !/(^|\/)_/.test(path))
    .map((path) => `/${path.slice(0, -"page.tsx".length)}`),
];

function taken(url: string): boolean {
  return identity.routes.some((pattern) => routeTakes(pattern, url));
}

describe("the doors' public identity", () => {
  it("hands the doors, at the cutover, every path the outside world reaches them by", () => {
    for (const path of [identity.mcp, identity.signin.authorize, identity.signin.callback,
                        identity.first_party.client, identity.first_party.redirect,
                        identity.first_party.resource, identity.hook]) {
      expect([path, taken(path)]).toEqual([path, true]);
    }
  });

  it("takes every URL the app, the site and the shells reach the doors by, query and all, and " +
     "no page of the website", () => {
    for (const url of TAKEN) {
      expect([url, taken(url)]).toEqual([url, true]);
    }
    expect(SITE).toEqual(expect.arrayContaining(["/", "/connect/", "/icons/apple-touch-icon.png"]));
    for (const url of SITE) {
      expect([url, taken(url)]).toEqual([url, false]);
    }
    expect(() => routeTakes("/app*/x", "/app")).toThrow("holds a * before its end");
  });

  it("is the owner app's own sign-in, which its scripts build from their origin", () => {
    // Every URL an app script builds from its origin, by the name it is given there: the
    // sign-in's start (`api.js`) and its end (`signed-in.js`), and nothing else.
    const app = join(PLATFORM, "doors", "app");
    const built: Record<string, Record<string, string>> = {};
    for (const file of readdirSync(app).filter((name) => name.endsWith(".js"))) {
      for (const found of readFileSync(join(app, file), "utf8")
        .matchAll(/(\w+)(?: = |: )`\$\{location\.origin\}([^`]*)`/g)) {
        (built[file] ??= {})[found[1]!] = found[2]!;
      }
    }
    const { client, redirect, resource } = identity.first_party;
    expect(built).toEqual({
      "api.js": { CLIENT_ID: client, REDIRECT_URI: redirect, RESOURCE: resource },
      "signed-in.js": { client_id: client, redirect_uri: redirect, resource },
    });
  });
});
