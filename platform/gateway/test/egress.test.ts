/**
 * Every adapter request goes through the call's endpoint guard (`egress.ts`): Google's
 * adapters, which refuse `options.fetch` and call the global fetch, are held to the
 * sealed origin, paths and methods, and never follow a redirect, like every other API.
 * The security review's key-leak reproduction (a 307 from the sealed origin carrying
 * `x-goog-api-key` to another host) is the first case here.
 */
import { describe, expect, it } from "vitest";

import { builtinCatalog } from "../src/catalog";
import type { Source } from "../src/credential";
import { within } from "../src/egress";
import {
  ALICE, ANTHROPIC, ask, client, drain, envelopeOf, gatewayHandler, GOOGLE, ring, sealed,
} from "./support";

/** What the fake's `evil.test` received that mentions `key`. */
async function evilSaw(key: string): Promise<unknown[]> {
  const all = (await (await fetch("https://status.test/evil")).json()) as unknown[];
  return all.filter((entry) => JSON.stringify(entry).includes(key));
}

function modelOf(provider: string, api: string) {
  return builtinCatalog().provider(provider)!.getModels().find((model) => model.api === api)!;
}

async function call(key: string, text: string, record: Source = GOOGLE) {
  const env = ring("k1");
  const blob = await sealed(env, ALICE, key, record);
  const model = record.upstream.provider === "google"
    ? modelOf("google", "google-generative-ai") : modelOf("anthropic", "anthropic-messages");
  const owner = client(gatewayHandler(env), envelopeOf({ blob }), model.id);
  return drain(owner.stream(ask(text)));
}

describe("Google's adapter, held by the interposed global fetch", () => {
  it.each([["307", "cmd:redirect307"], ["302", "cmd:redirect"]])(
    "never follows a %s off the sealed origin with the key or the body", async (status, text) => {
      const key = `goog-${status}-key`;
      const { message } = await call(key, text);
      expect(message.stopReason).toBe("error");
      expect(message.errorMessage).toBe("upstream_error");
      expect(await evilSaw(key)).toEqual([]);
    });

  it("is held to the sealed paths and methods", async () => {
    const narrow: Source = { ...GOOGLE, endpoints: [{
      origin: "https://generativelanguage.googleapis.com", paths: ["/nothing"], methods: ["GET"] }] };
    const { message } = await call("goog-narrow-key", "cmd:echo", narrow);
    expect(message.errorMessage).toBe("endpoint_refused");
  });

  it("classifies an upstream 401 from the status it saw", async () => {
    const { message } = await call("goog-401-key", "cmd:fail-401");
    expect(message.errorMessage).toBe("upstream_unauthorized");
  });

  it("still streams a normal call, with the key only in its own header", async () => {
    const { message } = await call("goog-ok-key", "cmd:echo");
    expect(message.stopReason).toBe("stop");
    const echo = JSON.parse((message.content[0] as { text: string }).text) as {
      auth: Record<string, string | null> };
    expect(echo.auth["x-goog-api-key"]).toBe("goog-ok-key");
  });

  it("keeps each concurrent call to its own guard and its own key", async () => {
    const [one, two] = await Promise.all([call("goog-one-key", "cmd:echo"),
                                          call("goog-two-key", "cmd:echo")]);
    const keyOf = (result: typeof one) => (JSON.parse(
      (result.message.content[0] as { text: string }).text) as {
      auth: Record<string, string | null> }).auth["x-goog-api-key"];
    expect([keyOf(one), keyOf(two)]).toEqual(["goog-one-key", "goog-two-key"]);
  });
});

describe("the interposer", () => {
  it("holds an adapter that takes options.fetch to the same rule (control)", async () => {
    const { message } = await call("anth-307-key", "cmd:redirect307", ANTHROPIC);
    expect(message.errorMessage).toBe("upstream_error");
    expect(await evilSaw("anth-307-key")).toEqual([]);
  });

  it("is the scope's guard inside a scope, and the runtime's fetch outside any", async () => {
    const refusing = async (): Promise<Response> => new Response("guarded", { status: 418 });
    const inside = await within(refusing, () => fetch("https://status.test/cancelled?id=x"));
    expect(inside.status).toBe(418);
    const after = await within(refusing, async () => {
      await new Promise((resolve) => setTimeout(resolve, 5));
      return fetch("https://status.test/cancelled?id=x");
    });
    expect(after.status).toBe(418);
    const outside = await fetch("https://status.test/cancelled?id=x");
    expect(outside.status).toBe(200);
  });
});
