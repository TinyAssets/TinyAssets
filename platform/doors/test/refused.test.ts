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

import { CANARY_ONLY, MAINTENANCE } from "../../contract/src/control";
import { ERASED_SIGN_IN } from "../../contract/src/signin";
import { Refusal, refusalOf } from "../../contract/src/refusal";
import {
  answered, asEgress, asEgressFault, asEvent, asInbound, asJson, asRedirect, asRequest, asShared,
  asTool, EVENT_ERRORS,
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
    expect((await said(asEgress(new Refusal("egress_rate"))))[1]).toBe("1");
    expect((await said(asEgress(new Refusal("egress_aborted"))))[1]).toBeNull();
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

  it("is a 200 to a shared app, so its vendor's failure budget is never spent, unless it is " +
     "for the moment only, when the vendor is told to send the event again", async () => {
    expect(await said(asShared(new Refusal("binding_withdrawn"))))
      .toEqual([200, null, { state: "refused", error: "binding_withdrawn" }]);
    expect(await said(asShared(new Refusal("storage_busy", "storage_busy: catching up"))))
      .toEqual([503, "30", { state: "refused", error: "storage_busy" }]);
  });
});

/** A tool's error result's text, each line. */
function toolText(refusal: Refusal): string[] {
  const result = asTool(refusal);
  expect(result.isError).toBe(true);
  expect(result.content).toHaveLength(1);
  return (result.content[0] as { text: string }).text.split("\n");
}

describe("an MCP tool's answer", () => {
  it("is the tool's error result: the refusal's message, then that it is final", () => {
    expect(asTool(new Refusal("refused", "agent not found"))).toEqual({ isError: true,
      content: [{ type: "text", text: "agent not found\nThis refusal is final: the same call " +
                                      "is refused again until what it names changes." }] });
    expect(toolText(new Refusal("storage_full", "storage_full: used 9 of 9",
                                { retryAfterS: 2 })))
      .toEqual(["storage_full: used 9 of 9", "This refusal is final: the same call is " +
                "refused again until what it names changes."]);
    expect(toolText(new Refusal("invalid", "a room is room:<app>:<room>")))
      .toEqual(["a room is room:<app>:<room>", "This refusal is final: the call is malformed, " +
                "and is refused again until it is corrected."]);
  });

  it("tells the model to call again, and in how many seconds, for any refusal for the moment",
     () => {
    for (const [refusal, wait] of [[new Refusal("unavailable", MAINTENANCE), "60 seconds"],
                                   [new Refusal("restoring", "restoring"), "60 seconds"],
                                   [new Refusal("storage_busy", "full", { retryAfterS: 7 }),
                                    "7 seconds"],
                                   [refusalOf(Object.assign(new Error("overloaded"),
                                                            { overloaded: true }))!, "1 second"],
                                  ] as const) {
      expect(toolText(refusal)).toEqual(
        [refusal.message, `This refusal is temporary: try the same call again in ${wait}.`]);
    }
  });
});

function thrown(surface: (refusal: Refusal) => never, refusal: Refusal): ProtocolError {
  try {
    surface(refusal);
  } catch (error) {
    return error as ProtocolError;
  }
  throw new Error("the surface answered nothing");
}

/** A JSON-RPC error as the SDK sends it: its code, message and data. */
function rpcError(surface: (refusal: Refusal) => never, refusal: Refusal): unknown[] {
  const error = thrown(surface, refusal);
  return [error.code, error.message, error.data];
}

describe("every MCP request's answer but a tool call's", () => {
  it("is invalid params for an invalid request, else an internal error, with its reason",
     () => {
    for (const surface of [asRequest, asEvent]) {
      expect(rpcError(surface, new Refusal("invalid", "a room is room:<app>:<room>")))
        .toEqual([-32602, "a room is room:<app>:<room>",
                  { reason: "a room is room:<app>:<room>" }]);
    }
    expect(rpcError(asRequest, new Refusal("refused", "no agent here")))
      .toEqual([-32603, "no agent here", { reason: "no agent here" }]);
    // A final refusal names no wait to a client, as it tells a model none (`asTool`).
    expect(rpcError(asRequest, new Refusal("storage_full", "full", { retryAfterS: 2 })))
      .toEqual([-32603, "full", { reason: "full" }]);
  });

  it("is a recoverable failure with its reason and wait for any refusal for the moment, on " +
     "MCP Events too, never Forbidden, which a client stops on", () => {
    for (const [refusal, ms] of [[new Refusal("unavailable", MAINTENANCE), 60_000],
                                 [new Refusal("restoring", "restoring"), 60_000],
                                 [new Refusal("storage_busy", "full", { retryAfterS: 7 }), 7_000],
                                 [refusalOf(Object.assign(new Error("overloaded"),
                                                          { overloaded: true }))!, 1_000],
                                ] as const) {
      for (const surface of [asRequest, asEvent]) {
        expect(rpcError(surface, refusal)).toEqual(
          [-32603, refusal.message, { reason: refusal.message, retryAfterMs: ms }]);
      }
    }
  });
});

describe("MCP Events' answer", () => {
  it("is the design sketch's error for any other refusal: exhausted, or forbidden", () => {
    // The owner's limit, as its refusal carries it: never the doors' own figure.
    const limit = { limit: "subscriptions", max: 3 };
    const full = thrown(asEvent, new Refusal("subscription_limit", "subscription_limit: 3",
                                             { facts: limit }));
    expect([full.code, full.data]).toEqual([EVENT_ERRORS.exhausted, limit]);
    for (const code of ["refused", "owner_suspended"] as const) {
      expect(rpcError(asEvent, new Refusal(code, "no agent here")))
        .toEqual([EVENT_ERRORS.forbidden, "Forbidden", { reason: "no agent here" }]);
    }
  });

  it("throws a full agent's refusal that carries no limit, a bug, rather than answer it", () => {
    for (const facts of [undefined, {}, { limit: "subscriptions" }, { max: 3 },
                         { limit: "subscriptions", max: "3" }]) {
      const missing = thrown(asEvent, new Refusal("subscription_limit", "subscription_limit: 3",
                                                  facts === undefined ? {} : { facts }));
      expect(missing).not.toBeInstanceOf(ProtocolError);
      expect(missing.message).toBe("a subscription_limit refusal carries no {limit, max}");
    }
  });
});

describe("a sign-in's answer", () => {
  const original = { responseType: "code", clientId: "https://client.example/c.json",
                     redirectUri: "https://client.example/cb", scope: [], state: "s1" } as
    AuthRequest;
  const redirected = (refusal: Refusal): URL => new URL(
    asRedirect(original, new Headers())(refusal).headers.get("Location")!);

  it("is temporarily_unavailable for any refusal for the moment, a closed gate's or a pool's, " +
     "its message the reason", () => {
    for (const refusal of [new Refusal("unavailable", MAINTENANCE),
                           new Refusal("unavailable", CANARY_ONLY), new Refusal("storage_busy")]) {
      const url = redirected(refusal);
      expect([url.searchParams.get("error"), url.searchParams.get("error_description")])
        .toEqual(["temporarily_unavailable", refusal.message]);
    }
  });

  it("is access_denied for an erased owner, and throws any other refusal, a bug", () => {
    for (const code of ["erasing", "owner_erased"] as const) {
      const url = redirected(new Refusal(code));
      expect([url.searchParams.get("error"), url.searchParams.get("error_description")])
        .toEqual(["access_denied", ERASED_SIGN_IN]);
    }
    const other = new Refusal("owner_suspended");
    expect(() => redirected(other)).toThrow(other);
  });
});
