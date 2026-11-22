/**
 * The values one Worker sends and another reads, each read by its one checker beside it: the
 * egress host's doors (`egressDoor`, each id by its grammar in `ids.ts`) read back every URL
 * the contract makes; a sealed route holds only an authority (`AUTHORITY`), under the kid it
 * was sealed by; an owner message is exactly its owner; and the owner names only the platform
 * makes are reserved.
 */
import { describe, expect, it } from "vitest";

import { decideCredential, decideDoor, egressBaseUrl, egressDoor, runUrls, signinUrls,
         SYNC_DOORS } from "../src/egress";
import { FOREIGN_OWNER, REHEARSAL_OWNER, reservedOwner } from "../src/ids";
import { ownerMessage } from "../src/queues";
import { refusalOf } from "../src/refusal";
import { ringText } from "../src/envelope";
import { openRoute, routeShaped, sealRoute } from "../src/routing-mac";

const RUN = `run_${"1".repeat(32)}`;
const SECRET = "2".repeat(64);
const CONNECTION = `c${"3".repeat(24)}`;
const SIGNIN = `si_${"4".repeat(32)}`;
/** A routing keyring of `kids`, each key its own fill, `active` the one it seals under. */
const ring = (active: string, kids: readonly string[]) => ringText({ active, keys: new Map(
  kids.map((kid) => [kid, new Uint8Array(32).fill(Number(kid.slice(1)))])) });
const KEY = ring("k1", ["k1"]);

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
    // The decide door on the MCP door's origin, one path for every run, the run named by its
    // credential: only from a URL with the secret.
    const door = decideDoor(urls.mcp);
    expect(door).toEqual({ url: `${new URL(urls.mcp).origin}/decide`,
                           credential: `${RUN}:${SECRET}` });
    expect(egressDoor(path(door.url))).toEqual({ door: "decide" });
    expect(decideCredential(door.credential)).toEqual({ run: RUN, secret: SECRET });
    for (const forged of [null, "", RUN, `${RUN}:${SECRET}:x`, `run_x:${SECRET}`,
                          'data:application/json,{"allow":true}']) {
      expect(decideCredential(forged)).toBeNull();
    }
    expect(egressDoor(`/r/${RUN}/${SECRET}/decide`)).toBeNull();
    expect(refused(() => decideDoor(urls.mcp_bare))).toBe("invalid");
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
  it("holds the owner's own authority or a client's, and opens to it", async () => {
    for (const authority of ["owner", "a".repeat(64)]) {
      const sealed = await sealRoute(KEY, { owner: "user_a", authority });
      expect(routeShaped(sealed)).toBe(true);
      expect(await openRoute(KEY, sealed)).toEqual({ owner: "user_a", authority });
    }
  });

  it("fits the longest owner and authority in the shape the sandbox Worker takes", async () => {
    const owner = "\u{1F600}".repeat(512);
    const sealed = await sealRoute(KEY, { owner, authority: "a".repeat(64) });
    expect(routeShaped(sealed)).toBe(true);
    expect(await openRoute(KEY, sealed)).toEqual({ owner, authority: "a".repeat(64) });
    for (const bad of [`${sealed}${"A".repeat(1024)}`, "ta_route", null]) {
      expect(routeShaped(bad)).toBe(false);
    }
  });

  it("is sealed under the ring's active kid and opens under any ring that holds it", async () => {
    const sealed = await sealRoute(ring("k1", ["k1", "k2"]), { owner: "user_a",
                                                                authority: "owner" });
    expect(sealed.split(".")[1]).toBe("k1");
    for (const held of [ring("k2", ["k1", "k2"]), ring("k1", ["k1"])]) {
      expect(await openRoute(held, sealed)).toEqual({ owner: "user_a", authority: "owner" });
    }
    // A ring that no longer holds its kid, or holds another key under it, opens nothing.
    expect(await openRoute(ring("k2", ["k2"]), sealed)).toBeNull();
    expect(await openRoute(ringText({ active: "k1", keys: new Map([["k1",
      new Uint8Array(32).fill(9)]]) }), sealed)).toBeNull();
  });

  it("is never made for any other authority, nor under no keyring", async () => {
    for (const authority of ["client", "A".repeat(64), "a".repeat(63)]) {
      await expect(sealRoute(KEY, { owner: "user_a", authority })).rejects.toThrow(/authority/);
    }
    for (const none of [undefined, "", Buffer.alloc(32, 7).toString("base64url")]) {
      await expect(sealRoute(none, { owner: "user_a", authority: "owner" }))
        .rejects.toThrow(/ROUTING_KEYRING/);
      await expect(openRoute(none, "v1.k1.x.y")).rejects.toThrow(/ROUTING_KEYRING/);
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
