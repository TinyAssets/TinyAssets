/**
 * The doors' public identity (`contract/identity.json`) is written once. Its readers that cannot
 * read it are held to it here: the contract, whose `ZONE` and `EGRESS_HOST` the runner the
 * sandbox template ships is built from (so they change only with a new template pair), and
 * the owner app's own scripts, browser modules that build its sign-in from their origin.
 */
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { PLATFORM } from "../../ci/paths";
import { EGRESS_HOST, ZONE } from "../../contract/src/egress";
import identity from "../../contract/identity.json" with { type: "json" };

/** Whether the route pattern `pattern` (from the origin) takes `path`. */
function takes(pattern: string, path: string): boolean {
  return pattern.endsWith("*") ? path.startsWith(pattern.slice(0, -1)) : path === pattern;
}

describe("the doors' public identity", () => {
  it("is the contract's zone and egress host", () => {
    expect([ZONE, EGRESS_HOST]).toEqual([identity.host, identity.egress]);
  });

  it("hands the doors, at the cutover, every path the outside world reaches them by", () => {
    for (const path of [identity.mcp, identity.signin.authorize, identity.signin.callback,
                        identity.first_party.client, identity.hook]) {
      expect([path, identity.routes.some((pattern) => takes(pattern, path))])
        .toEqual([path, true]);
    }
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
