/**
 * The canary's clock (`src/canary-clock.ts`), through the entry's `scheduled`: without its
 * token it sends nothing; with it, one dispatch of the canary's workflow on main; a failed
 * dispatch fails the tick and pages the founder on the half-hour's ticks alone.
 */
import { env } from "cloudflare:workers";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DISPATCH, PAGER } from "../../contract/src/canary";
import type { DoorsEnv } from "../src/env";
import doors from "../src/index";

const DOORS = env as unknown as DoorsEnv;
const ARMED = { ...DOORS, [DISPATCH.token]: "dispatch-token" } as DoorsEnv;
const PAGED = { ...ARMED, [PAGER.user]: "user-key", [PAGER.token]: "app-token" } as DoorsEnv;
/** A tick at minute `minute` of an hour. */
const at = (minute: number) => Date.UTC(2026, 10, 14, 9, minute);

/** What each tick sent, its method, URL, headers and body; `answer` answers each by URL. */
function sent(answer: (url: string) => Response | Error) {
  const requests: { method: string; url: string; headers: Record<string, string>;
                    body: string }[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const request = new Request(input, init);
    const form = init?.body instanceof URLSearchParams;
    requests.push({ method: request.method, url: request.url,
                    headers: Object.fromEntries(request.headers),
                    body: form ? String(init!.body) : await request.text() });
    const answered = answer(request.url);
    if (answered instanceof Error) {
      throw answered;
    }
    return answered;
  });
  return requests;
}

function tick(on: DoorsEnv, minute: number): Promise<void> {
  return doors.scheduled({ scheduledTime: at(minute), cron: "*/10 * * * *", noRetry() {} },
                         on);
}

const DISPATCHES = `https://api.github.com/repos/${DISPATCH.repository}/actions/workflows/${
  DISPATCH.workflow}/dispatches`;

afterEach(() => {
  vi.restoreAllMocks();
});

describe("the canary's clock", () => {
  it("sends nothing without its token, and says so", async () => {
    const log = vi.spyOn(console, "log").mockImplementation(() => {});
    const requests = sent(() => new Response(null, { status: 204 }));
    await tick(DOORS, 0);
    expect(requests).toEqual([]);
    expect(log).toHaveBeenCalledWith(
      `the canary's clock has no ${DISPATCH.token}: nothing is dispatched`);
  });

  it("dispatches the canary's workflow on main with its token, once a tick", async () => {
    const requests = sent(() => new Response(null, { status: 204 }));
    await tick(PAGED, 10);
    expect(requests).toEqual([{
      method: "POST", url: DISPATCHES, body: JSON.stringify({ ref: "main" }),
      headers: expect.objectContaining({ authorization: "Bearer dispatch-token",
                                         accept: "application/vnd.github+json",
                                         "x-github-api-version": "2022-11-28" }) }]);
  });

  it("fails a tick whose dispatch fails, and pages only on the half-hour's ticks", async () => {
    const requests = sent((url) => url === DISPATCHES ? new Response("{}", { status: 401 })
      : new Response("{}", { status: 200 }));
    await expect(tick(PAGED, 10)).rejects.toThrow(
      `GitHub answered the dispatch of ${DISPATCH.workflow} 401`);
    expect(requests.map((request) => request.url)).toEqual([DISPATCHES]);
    await expect(tick(PAGED, 30)).rejects.toThrow("401");
    expect(requests.map((request) => request.url)).toEqual([DISPATCHES, DISPATCHES, PAGER.url]);
    const form = new URLSearchParams(requests[2]!.body);
    expect([form.get("user"), form.get("token"), form.get("priority")])
      .toEqual(["user-key", "app-token", "1"]);
    expect(form.get("message")).toMatch(/^GitHub answered the dispatch of .* 401\. /);
  });

  it("fails loudly with no pager, and names a page that failed too", async () => {
    sent((url) => url === DISPATCHES ? new TypeError("network down")
      : new Response("{}", { status: 500 }));
    await expect(tick(ARMED, 0)).rejects.toThrow(/reached no answer: network down$/);
    await expect(tick(PAGED, 0)).rejects.toThrow(/; the page failed too \(500\)$/);
  });
});
