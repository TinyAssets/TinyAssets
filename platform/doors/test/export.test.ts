/**
 * The owner export at the owner surface (`app.ts`, `owner/src/export.ts`; slice P):
 * newline-delimited JSON for the owner's own app in its session, and no outside credential
 * reaches it.
 */
import { beforeEach, describe, expect, it } from "vitest";

import { OUTSIDE_CREDENTIAL } from "../src/app";
import { mintKeyFor } from "./keys";
import {
  appPost, connect, OPEN, registerClient, serveFirstPartyDocument, setControl, signInApp,
} from "./support";

function owner(): string {
  return `user_${crypto.randomUUID().replace(/-/g, "")}`;
}

let restore = () => {};
beforeEach(async () => {
  await setControl(OPEN);
  restore();
  restore = serveFirstPartyDocument();
});

describe("the owner export door", () => {
  it("streams the owner's whole export, header to end, in its session", async () => {
    const who = owner();
    const app = await signInApp({ owner: who });
    const response = await appPost("export", app.access_token, app.session, {});
    expect(response.status).toBe(200);
    expect(response.headers.get("Content-Type")).toBe("application/x-ndjson");
    const lines = (await response.text()).trimEnd().split("\n")
      .map((line) => JSON.parse(line) as { t: string; owner?: string; table?: string });
    expect(lines[0]).toMatchObject({ t: "header", format: "tinyassets-export", owner: who });
    expect(lines.at(-1)).toEqual({ t: "end" });
    expect(lines.some((line) => line.t === "row" && line.table === "owner_sessions")).toBe(false);
  });

  it("refuses a request without the owner's session, and any outside credential", async () => {
    const who = owner();
    const app = await signInApp({ owner: who });
    expect((await appPost("export", app.access_token, null, {})).status).toBe(403);
    expect((await appPost("export", app.access_token, app.session, { cursor: null, more: 1 }))
      .status).toBe(400);
    const { key } = await mintKeyFor(who);
    const keyed = await appPost("export", key, null, {});
    expect([keyed.status, await keyed.json()]).toEqual([403, { error: OUTSIDE_CREDENTIAL }]);
    const mcp = await connect(await registerClient(), { owner: who });
    const client = await appPost("export", mcp.access_token, null, {});
    expect([client.status, await client.json()]).toEqual([403, { error: OUTSIDE_CREDENTIAL }]);
  });
});
