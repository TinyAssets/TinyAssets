/**
 * The doors' public identity (`src/identity.json`) is written once. Its readers that cannot
 * read it are held to it here: the contract, whose `ZONE` and `EGRESS_HOST` the runner the
 * sandbox template ships is built from (so they change only with a new template pair), and
 * the owner app's own script, a browser module that builds its sign-in from its origin.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { PLATFORM } from "../../ci/paths";
import { EGRESS_HOST, ZONE } from "../../contract/src/egress";
import identity from "../src/identity.json" with { type: "json" };

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

  it("is the owner app's own sign-in, which its script builds from its origin", () => {
    const script = readFileSync(join(PLATFORM, "doors", "app", "api.js"), "utf8");
    const path = (name: string) => new RegExp(
      `export const ${name} = \`\\$\\{location\\.origin\\}([^\`]*)\`;`).exec(script)?.[1];
    expect([path("CLIENT_ID"), path("REDIRECT_URI"), path("RESOURCE")])
      .toEqual([identity.first_party.client, identity.first_party.redirect,
                identity.first_party.resource]);
  });
});
