import { describe, expect, it } from "vitest";
import { E2bTransport } from "../e2b/transport";

function listing(tokens: readonly string[]) {
  const requests: (string | null)[] = [];
  const client = new E2bTransport("fixture-key", async url => {
    const page = requests.length;
    requests.push(new URL(url).searchParams.get("nextToken"));
    const token = tokens[page];
    // Empty intermediate pages are valid when the token continues to advance.
    return Response.json(token ? [] : [{ sandboxID: "sbxfinal" }], {
      headers: token ? { "x-next-token": token } : {},
    });
  });
  return { client, requests };
}

describe("the shared sandbox list", () => {
  it("follows distinct opaque tokens through empty pages", async () => {
    const { client, requests } = listing(["a+/=", "b"]);
    expect(await client.sandboxes()).toEqual([{ sandboxID: "sbxfinal", metadata: {} }]);
    expect(requests).toEqual([null, "a+/=", "b"]);
  });

  it.each([["first", "first"], ["first", "next", "first"]])(
    "stops when a page refers back to a previous token (%s, %s)", async (...tokens) => {
      const { client, requests } = listing(tokens);
      await expect(client.sandboxes()).rejects.toMatchObject({ status: 502 });
      expect(requests).toEqual([null, ...tokens.slice(0, -1)]);
    });
});
