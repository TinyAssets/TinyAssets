/**
 * The doors' answers for a refusal (`src/refused.ts`), one per surface, each from the refusal
 * alone: its status and `Retry-After` are its code's (the one table of them is
 * `contract/guards/refusal.test.ts`), or the refusal's own wait, and its message is handed on
 * whole. The same answers through the real object's RPC are `api-keys.test.ts`'s; the closed
 * gates' on each door, `control.test.ts`'s, `egress.test.ts`'s and `doors-live.test.ts`'s.
 */
import type { AuthRequest } from "@cloudflare/workers-oauth-provider";
import { ProtocolError } from "@modelcontextprotocol/server";
import { describe, expect, it } from "vitest";

import { MAINTENANCE } from "../../contract/src/control";
import { SUBSCRIPTIONS_PER_AGENT } from "../../contract/src/limits";
import { Refusal } from "../../contract/src/refusal";
import {
  answered, asEgress, asEgressFault, asEvent, asInbound, asJson, asRedirect, asShared, asTool,
  EVENT_ERRORS,
} from "../src/refused";

async function said(response: Response): Promise<[number, string | null, unknown]> {
  return [response.status, response.headers.get("Retry-After"), await response.json()];
}

/** An error as it reaches the doors across RPC: a plain `Error` with the refusal's fields. */
function crossed(refusal: Refusal): Error {
  const error = new Error(refusal.message);
  Object.defineProperty(error, "name", { value: "Refusal" });
  return Object.assign(error, { code: refusal.code });
}

describe("answered", () => {
  it("answers a refusal thrown in its work, here or across RPC, as its surface says", async () => {
    const busy = new Refusal("busy", "busy: full");
    for (const thrown of [busy, crossed(busy)]) {
      expect(await said(await answered(asJson, () => Promise.reject(thrown))))
        .toEqual([503, "1", { error: "busy: full" }]);
    }
    const done = new Response("done");
    expect(await answered(asJson, async () => done)).toBe(done);
  });

  it("throws anything else, a bug", async () => {
    const bug = new Error("storage_full: not a refusal");
    await expect(answered(asJson, () => Promise.reject(bug))).rejects.toBe(bug);
  });
});

describe("a JSON surface's answer", () => {
  it("is {error: <its message>}, its code's status and wait, never stored", async () => {
    const response = asJson(new Refusal("storage_busy", "storage_busy: catching up"));
    expect(response.headers.get("Cache-Control")).toBe("no-store");
    expect(await said(response)).toEqual([503, "30", { error: "storage_busy: catching up" }]);
    expect(await said(asJson(new Refusal("refused", "agent not found"))))
      .toEqual([403, null, { error: "agent not found" }]);
  });

  it("carries a refusal's own Retry-After over its code's", async () => {
    expect(await said(asJson(new Refusal("storage_busy", "storage_busy: moved",
                                         { retryAfterS: 1 }))))
      .toEqual([503, "1", { error: "storage_busy: moved" }]);
  });

  it("answers a closed gate as every other refusal: its message, a 503 and a minute",
     async () => {
    expect(await said(asJson(new Refusal("unavailable", MAINTENANCE))))
      .toEqual([503, "60", { error: MAINTENANCE }]);
  });
});

describe("the egress door's answer", () => {
  it("is the gateway's own shape, its code shown, its receipt when kept", async () => {
    expect(await said(asEgress(new Refusal("egress_lease_closed"), "rcpt_1")))
      .toEqual([401, null, { error: { code: "egress_lease_closed",
                                      message: "egress_lease_closed" },
                             receipt: "rcpt_1" }]);
    expect(await said(asEgress(new Refusal("storage_full", "storage_full: used 9 of 9"))))
      .toEqual([507, null, { error: { code: "storage_full",
                                      message: "storage_full: used 9 of 9" } }]);
  });

  it("shows a quiet code as egress_refused, with the refusal's words", async () => {
    expect(await said(asEgress(new Refusal("not_owner", "not this owner's"))))
      .toEqual([403, null, { error: { code: "egress_refused", message: "not this owner's" } }]);
  });

  it("names only its code's wait, and answers a closed gate in its own shape", async () => {
    expect((await said(asEgress(new Refusal("egress_rate"))))[1]).toBeNull();
    expect(await said(asEgress(new Refusal("unavailable", MAINTENANCE))))
      .toEqual([503, "60", { error: { code: "unavailable", message: MAINTENANCE } }]);
  });

  it("answers a path the sync agent may not take by its fault's code", async () => {
    expect(await said(asEgressFault("not_directory")))
      .toEqual([404, null, { error: { code: "not_directory", message: "not_directory" } }]);
  });
});

describe("the inbound hooks' answer", () => {
  it("shows a named code, or a quiet one's message, at its code's status and wait",
     async () => {
    expect(await said(asInbound(new Refusal("inbound_proof_invalid", "the proof fails"))))
      .toEqual([401, null, { error: "inbound_proof_invalid" }]);
    expect(await said(asInbound(new Refusal("refused", "agent not found"))))
      .toEqual([403, null, { error: "agent not found" }]);
    expect(await said(asInbound(new Refusal("unavailable", MAINTENANCE))))
      .toEqual([503, "60", { error: "unavailable" }]);
  });

  it("is a 200 to a shared app, so its vendor's failure budget is never spent", async () => {
    expect(await said(asShared(new Refusal("storage_busy", "storage_busy: catching up"))))
      .toEqual([200, null, { state: "refused", error: "storage_busy" }]);
  });
});

describe("an MCP tool's answer", () => {
  it("is the tool's error result, its text the refusal's message", () => {
    expect(asTool(new Refusal("refused", "agent not found")))
      .toEqual({ isError: true, content: [{ type: "text", text: "agent not found" }] });
  });
});

describe("MCP Events' answer", () => {
  function thrown(refusal: Refusal): ProtocolError {
    try {
      asEvent(refusal);
    } catch (error) {
      return error as ProtocolError;
    }
    throw new Error("asEvent answered nothing");
  }

  it("is the design sketch's error: exhausted, invalid params, or forbidden", () => {
    const full = thrown(new Refusal("subscription_limit", "subscription_limit: 16"));
    expect([full.code, full.data])
      .toEqual([EVENT_ERRORS.exhausted, { limit: "subscriptions", max: SUBSCRIPTIONS_PER_AGENT }]);
    const bad = thrown(new Refusal("invalid", "a room is room:<app>:<room>"));
    expect([bad.code, bad.message]).toEqual([EVENT_ERRORS.invalidParams,
                                             "a room is room:<app>:<room>"]);
    const no = thrown(new Refusal("refused", "no agent here"));
    expect([no.code, no.data]).toEqual([EVENT_ERRORS.forbidden, { reason: "no agent here" }]);
  });
});

describe("a sign-in's answer", () => {
  const original = { responseType: "code", clientId: "https://client.example/c.json",
                     redirectUri: "https://client.example/cb", scope: [], state: "s1" } as
    AuthRequest;
  const redirected = (refusal: Refusal): URL => new URL(
    asRedirect(original, new Headers())(refusal).headers.get("Location")!);

  it("is temporarily_unavailable for any refusal for the moment, a closed gate's or a pool's",
     () => {
    for (const refusal of [new Refusal("unavailable", MAINTENANCE), new Refusal("storage_busy")]) {
      expect(redirected(refusal).searchParams.get("error")).toBe("temporarily_unavailable");
    }
  });

  it("is access_denied for an erased owner, and throws any other refusal, a bug", () => {
    for (const code of ["erasing", "owner_erased"] as const) {
      const url = redirected(new Refusal(code));
      expect([url.searchParams.get("error"), url.searchParams.get("error_description")])
        .toEqual(["access_denied", "this account was erased"]);
    }
    const other = new Refusal("owner_suspended");
    expect(() => redirected(other)).toThrow(other);
  });
});
