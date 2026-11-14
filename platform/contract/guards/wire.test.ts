/**
 * The values one Worker sends and another reads, each read by its one checker beside it: the
 * egress host's doors (`egressDoor`, each id by its grammar in `ids.ts`) read back every URL
 * the contract makes; a sealed route holds only an authority (`AUTHORITY`); an owner message
 * is exactly its owner; and the owner names only the platform makes are reserved.
 */
import { describe, expect, it } from "vitest";

import { egressBaseUrl, egressDoor, runUrls, signinUrls, SYNC_DOORS } from "../src/egress";
import { FOREIGN_OWNER, REHEARSAL_OWNER, reservedOwner } from "../src/ids";
import { ownerMessage } from "../src/queues";
import { refusalOf } from "../src/refusal";
import { openRoute, SEALED_ROUTE, sealRoute } from "../src/routing-mac";

const RUN = `run_${"1".repeat(32)}`;
const SECRET = "2".repeat(64);
const CONNECTION = `c${"3".repeat(24)}`;
const SIGNIN = `si_${"4".repeat(32)}`;
const KEY = Buffer.alloc(32, 7).toString("base64url");

const path = (url: string) => new URL(url).pathname;

function refused(act: () => unknown): string | undefined {
  try {
    act();
  } catch (error) {
    return refusalOf(error)?.code ?? "not a refusal";
  }
  return undefined;
}

describe("the egress host's doors", () => {
  it("read back every URL the contract makes for a run, a sign-in and a connection", () => {
    const urls = runUrls(RUN, SECRET);
    const signin = signinUrls(SIGNIN);
    expect([urls.mcp, urls.mcp_bare, urls.events, signin.events, signin.signin]
      .map((url) => egressDoor(path(url)))).toEqual([
      { door: "mcp", run: RUN, secret: SECRET },
      { door: "mcp", run: RUN, secret: undefined },
      { door: "events", run: RUN },
      { door: "signin_events", signin: SIGNIN },
      { door: "signin" },
    ]);
    expect(egressDoor(path(egressBaseUrl(CONNECTION, null, RUN, SECRET)))).toEqual(
      { door: "model", run: RUN, secret: SECRET, connection: CONNECTION, path: "/" });
    expect(egressDoor(`/r/${RUN}/c/${CONNECTION}/v1/chat/completions`)).toEqual(
      { door: "model", run: RUN, secret: undefined, connection: CONNECTION,
        path: "/v1/chat/completions" });
    expect(SYNC_DOORS.map((sync) => egressDoor(`/sync/${sync}`)))
      .toEqual(SYNC_DOORS.map((sync) => ({ door: "sync", sync })));
  });

  it.each([
    ["a run out of its grammar", `/r/run_${"1".repeat(31)}/events`],
    ["a secret on the event door", `/r/${RUN}/${SECRET}/events`],
    ["a short secret", `/r/${RUN}/${"2".repeat(63)}/mcp`],
    ["a connection out of its grammar", `/r/${RUN}/c/c${"3".repeat(23)}/v1`],
    ["a connection with no path after it", `/r/${RUN}/c/${CONNECTION}`],
    ["a sign-in out of its grammar", `/signin/si_${"4".repeat(31)}/events`],
    ["a trailing slash on the sign-in report", "/signin/"],
    ["an unknown sync door", "/sync/delete"],
    ["a sync door with more after it", "/sync/list/x"],
    ["a relative path", `r/${RUN}/events`],
    ["the root", "/"],
  ])("name no door for %s", (_what, given) => {
    expect(egressDoor(given)).toBeNull();
  });

  it("are made only for a sign-in in its grammar", () => {
    expect(refused(() => signinUrls("si_x"))).toBe("invalid");
  });
});

describe("a sealed route", () => {
  it("holds the owner's own authority or a client's, and opens to it", () => {
    for (const authority of ["owner", "a".repeat(64)]) {
      const sealed = sealRoute(KEY, { owner: "user_a", authority });
      expect(SEALED_ROUTE.test(sealed)).toBe(true);
      expect(openRoute(KEY, sealed)).toEqual({ owner: "user_a", authority });
    }
  });

  it("is never made for any other authority", () => {
    for (const authority of ["client", "A".repeat(64), "a".repeat(63)]) {
      expect(() => sealRoute(KEY, { owner: "user_a", authority })).toThrow(/authority/);
    }
  });
});

describe("an owner message", () => {
  it("is exactly its owner", () => {
    expect(ownerMessage({ owner: "user_a" })).toEqual({ owner: "user_a" });
    for (const bad of [null, "user_a", ["user_a"], {}, { owner: "user_a", extra: 1 },
                       { owner: " user_a" }]) {
      expect(refused(() => ownerMessage(bad))).toBe("invalid");
    }
  });
});

describe("the reserved owner names", () => {
  it("are the foreign issuers' and the rehearsals'", () => {
    expect([`${FOREIGN_OWNER}abc`, `${REHEARSAL_OWNER}run-1`, "user_a", "isso"]
      .map(reservedOwner)).toEqual([true, true, false, false]);
  });
});
