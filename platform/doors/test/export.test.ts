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
    // Its generation is written by the owner's own lane: the stream ends at a cursor line
    // while an object is not yet written, and the app asks again from it.
    type Line = { t: string; owner?: string; table?: string; cursor?: string; export_id?: string };
    const lines: Line[] = [];
    let ask: Record<string, unknown> = {};
    for (let tries = 0; tries < 400; tries++) {
      const response = await appPost("export", app.access_token, app.session, ask);
      expect(response.status).toBe(200);
      expect(response.headers.get("Content-Type")).toBe("application/x-ndjson");
      const got = (await response.text()).trimEnd().split("\n")
        .map((line) => JSON.parse(line) as Line);
      lines.push(...got.filter((line) => line.t !== "cursor"));
      const last = got.at(-1)!;
      if (last.t !== "cursor") {
        break;
      }
      ask = { cursor: last.cursor, export_id: last.export_id };
      await new Promise((resolve) => setTimeout(resolve, 25));
    }
    expect(lines[0]).toMatchObject({ t: "header", format: "tinyassets-export", owner: who });
    expect(lines.at(-1)).toMatchObject({ t: "end" });
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

  it("answers the owner's storage, used and left, at the owner surface", async () => {
    const app = await signInApp({ owner: owner() });
    const response = await appPost("storage", app.access_token, app.session, {});
    expect(response.status).toBe(200);
    expect(await response.json()).toMatchObject({
      used_bytes: 0, breakdown: { files: 0, messages: 0, collections: 0 } });
  });
});
