/**
 * Mode `logs` (`platform/deploy/logs.ts`), never against a live account: its run against a
 * fake Cloudflare that answers the zone and the telemetry query, the query it sends, the
 * lines it prints, and the redaction every printed line passes through first.
 */
import { describe, expect, it } from "vitest";

import { DARK } from "../../ci/fixtures";
import { HOST, ZONE } from "../../contract/src/identity";
import { TELEMETRY_PERMISSION } from "../cloudflare";
import { type Io, MODES, parse, perform } from "../deploy";
import { NAMES, NAMESPACES, WORKERS } from "../facts";
import { EVENTS, LINE_MAX, MINUTES, MINUTES_MAX, PRINT_MAX, redact } from "../logs";

const NOW = new Date("2026-11-23T12:00:00.000Z");
const ACCOUNT = "a".repeat(32);
const ENV = { CLOUDFLARE_API_TOKEN: "cf-token" };
const QUERY = `/client/v4/accounts/${ACCOUNT}/workers/observability/telemetry/query`;

/** A run's world: the Cloudflare calls it made, the lines it printed, and its Io. */
function world(answer: (body: Record<string, unknown>) => { status: number; body: unknown },
               control: unknown = { ...DARK, outside: "on", canary: ["user_a"] }) {
  const calls: { method: string; path: string; body: Record<string, unknown> | null }[] = [];
  const printed: string[] = [];
  const unused = () => { throw new Error("a logs run reaches only Cloudflare's API"); };
  const io: Io = {
    fetch: async (url: string, init?: RequestInit) => {
      const path = url.replace("https://api.cloudflare.com", "");
      const body = init?.body === undefined ? null
        : JSON.parse(String(init.body)) as Record<string, unknown>;
      calls.push({ method: init?.method ?? "GET", path, body });
      if (path.startsWith("/client/v4/zones?name=")) {
        return Response.json({ success: true, result: [{ id: "z", account: { id: ACCOUNT } }] });
      }
      if (path.startsWith(`/client/v4/accounts/${ACCOUNT}/storage/kv/namespaces?`)) {
        return Response.json({ success: true, result: [{ title: NAMESPACES["CONTROL"], id: "c" }] });
      }
      if (path === `/client/v4/accounts/${ACCOUNT}/storage/kv/namespaces/c/values/c%3Acontrol`) {
        return new Response(JSON.stringify(control));
      }
      if (path === QUERY && init?.method === "POST") {
        const { status, body: answered } = answer(body!);
        return Response.json(answered, { status });
      }
      return Response.json({ success: false, errors: [{ code: 7003 }] }, { status: 404 });
    },
    spawn: unused, exec: unused, bundles: unused, treeImage: unused, commit: unused,
    fileAt: unused, log: (line: string) => { printed.push(line); }, now: () => NOW,
    sleep: async () => {},
  } as unknown as Io;
  return { calls, printed, io };
}

/** One event as Workers Logs answers it. */
function event(at: string, worker: string, level: string, message: string,
               more: Record<string, unknown> = {}) {
  return { timestamp: Date.parse(at), dataset: "cloudflare-workers",
           $metadata: { id: at, service: NAMES[worker], level, message, ...more["meta"] as object },
           source: more["source"] ?? message };
}
const ok = (events: unknown[], count = events.length) =>
  ({ status: 200, body: { success: true, errors: [], result: { events: { events, count } } } });

async function logs(w: ReturnType<typeof world>, flags: string[] = []) {
  return perform(parse(["--mode", "logs", "--public", ...flags], ENV, w.io));
}

describe("redact", () => {
  it("takes out every credential's shape", () => {
    const ring = `{"active":"k2","keys":{"k1":"${"Ab3/+".repeat(8)}abc=","k2":"${
      "x9Y+/".repeat(8)}xyz="}}`;
    const sealed = "Q2xvdWRmbGFyZS1zZWFsZWQtcm91dGUtdmFsdWUtaGVyZQ_-ab12";
    for (const [line, kept] of [
      ["upstream echoed Authorization: Bearer sk12345678 back", "upstream echoed Authorization: [redacted] [redacted] back"],
      ["the provider answered Basic dXNlcjpwYXNz", "the provider answered Basic [redacted]"],
      ["authorization: short-tok", "authorization: [redacted]"],
      ['{"client_secret":"abc12345","ok":1}', '{"client_secret":"[redacted]","ok":1}'],
      ['{\\"x-api-key\\":\\"k-123\\"}', '{\\"x-api-key\\":\\"[redacted]\\"}'],
      ["GET /callback?token=abc123&state=s1", "GET /callback?token=[redacted]&state=s1"],
      ["FILES_KEYRING=k1secret", "FILES_KEYRING=[redacted]"],
      ["cookie: sid=abc; theme=dark", "cookie: [redacted]; theme=dark"],
      ["a jwt eyJhbGciOi.eyJzdWIiOi.c2lnbmF0dXJl in a header", "a jwt [redacted] in a header"],
      ["owner alice.b+x@example.co.uk failed", "owner [redacted] failed"],
      [`the ring ${ring}`, 'the ring {"active":"k2","keys":{"k1":"[redacted]","k2":"[redacted]"}}'],
      [`route ${sealed} did not open`, "route [redacted] did not open"],
      [`object ${"0123456789abcdef".repeat(4)} lost`, "object [redacted] lost"],
      ["sk-ant-api03-AbCdEfGhIjKlMnOpQrStUv", "[redacted]"],
    ] as const) {
      expect(redact(line), line).toBe(kept);
    }
  });

  it("keeps what reads as words: paths, error codes, constants' names and times", () => {
    for (const line of [
      "sandbox_runner_adopt_failed: sandbox sbx-12 keeps its runner",
      "at launch (/home/runner/work/platform/sandbox/src/control_launch.ts:12:3)",
      "the secret OFFSITE_R2_WRITE_SECRET_ACCESS_KEY is not set",
      "2026-11-23T11:59:00.000Z sandbox error the sandbox could not start",
      "a short id 0123abcd and a request 9f8e7d6c5b4a",
    ]) {
      expect(redact(line), line).toBe(line);
    }
  });
});

describe("mode logs", () => {
  it("reads every Worker's error and warn lines of the last hour, and changes nothing", async () => {
    const w = world(() => ok([]));
    const { plan } = await logs(w);
    expect(w.calls.map((call) => `${call.method} ${call.path.split("?")[0]}`))
      .toEqual(["GET /client/v4/zones", `GET /client/v4/accounts/${ACCOUNT}/storage/kv/namespaces`,
                `GET /client/v4/accounts/${ACCOUNT}/storage/kv/namespaces/c/values/c%3Acontrol`,
                `POST ${QUERY}`]);
    const body = w.calls.at(-1)!.body!;
    expect(body).toEqual({
      queryId: "tinyassets-deploy-logs", view: "events", dry: true, limit: EVENTS,
      timeframe: { from: NOW.getTime() - MINUTES * 60_000, to: NOW.getTime() },
      parameters: {
        datasets: ["cloudflare-workers"], filterCombination: "and",
        filters: [
          { key: "$metadata.service", operation: "in", type: "string",
            value: WORKERS.map((worker) => NAMES[worker]).join(",") },
          { kind: "group", filterCombination: "or", filters: [
            { key: "$metadata.level", operation: "in", type: "string", value: "error,warn" },
            { key: "$metadata.error", operation: "exists", type: "string" }] }],
      },
    });
    expect(plan).toEqual([`nothing: it read the error and warn lines of ${WORKERS.join(", ")} ` +
                          `over the last ${MINUTES} minutes, 0 matched and 0 printed in the ` +
                          "job's log"]);
    expect(w.printed).toContain("\nNothing was changed.");
  });

  it("reads one Worker's lines holding a text, over the minutes asked", async () => {
    const w = world(() => ok([]));
    await logs(w, ["--minutes", "15", "--worker", "sandbox", "--filter", "start did not answer"]);
    const { timeframe, parameters } = w.calls.at(-1)!.body! as
      { timeframe: { from: number }; parameters: { filters: { value?: string }[]; needle: unknown } };
    expect(timeframe.from).toBe(NOW.getTime() - 15 * 60_000);
    expect(parameters.filters[0]!.value).toBe(NAMES["sandbox"]);
    expect(parameters.needle).toEqual({ value: "start did not answer", isRegex: false,
                                        matchCase: false });
  });

  it("prints each line oldest first, its error, its stack and its source, redacted", async () => {
    const w = world(() => ok([
      event("2026-11-23T11:58:00.000Z", "owner", "error",
            "job-7's start did not answer: whether it launched is unknown",
            { source: { message: "job-7's start did not answer", error: {
              name: "Error", message: "boom from Bearer sk12345678",
              stack: "Error: boom\n    at launch (index.js:10:5)\n    at run (index.js:20:7)" } } }),
      event("2026-11-23T11:50:00.000Z", "sandbox", "error", "sandbox_start_failed",
            { meta: { error: "E2B refused: authorization: tok-123" } }),
      event("2026-11-23T11:55:00.000Z", "doors", "warn", "skill x is not served"),
    ], 5));
    const { plan } = await logs(w);
    const start = w.printed.findIndex((line) => line.startsWith("The Workers'"));
    expect(w.printed.slice(start, start + 9)).toEqual([
      `The Workers' error and warn lines from 2026-11-23T11:00:00.000Z to ${NOW.toISOString()}, ` +
        "oldest first: 3 of 5 matched",
      "2026-11-23T11:50:00.000Z sandbox error sandbox_start_failed",
      "  error: E2B refused: authorization: [redacted]",
      "2026-11-23T11:55:00.000Z doors warn skill x is not served",
      "2026-11-23T11:58:00.000Z owner error job-7's start did not answer: whether it launched " +
        "is unknown",
      "  Error: boom",
      "  at launch (index.js:10:5)",
      "  at run (index.js:20:7)",
      '  source: {"message":"job-7\'s start did not answer","error":{"name":"Error",' +
        '"message":"boom from Bearer [redacted]"}}',
    ]);
    expect(plan[0]).toContain("5 matched and 3 printed");
  });

  it("bounds each line after its redaction, and the whole printout", async () => {
    const secret = "Zx9".repeat(20);
    const long = `${"word ".repeat((LINE_MAX - 40) / 5)}${secret} tail`;
    const many = Array.from({ length: EVENTS }, (_, at) =>
      event(new Date(NOW.getTime() - (EVENTS - at) * 1000).toISOString(), "files", "error",
            long));
    const w = world(() => ok(many, 900));
    await logs(w);
    const lines = w.printed.filter((line) => line.includes(" files error "));
    expect(lines[0]).toMatch(new RegExp(`\\[redacted\\] tail$|\\[cut at ${LINE_MAX} characters\\]$`));
    expect(lines[0]).not.toContain(secret.slice(0, 8));
    expect(lines.every((line) => line.length <= LINE_MAX + 40)).toBe(true);
    const shown = lines.length;
    expect(shown).toBeLessThan(EVENTS);
    expect(lines.reduce((sum, line) => sum + line.length + 1, 0)).toBeLessThanOrEqual(PRINT_MAX);
    expect(w.printed).toContain(`(${EVENTS - shown} more not printed: the printout's bound is ${
      PRINT_MAX} characters)`);
  });

  it("cuts a line past its bound only after redacting it whole", async () => {
    const secret = "Qw7".repeat(20);
    const at = LINE_MAX - "2026-11-23T11:59:00.000Z files error ".length - 10;
    const w = world(() => ok([event("2026-11-23T11:59:00.000Z", "files", "error",
                                    `${"y".repeat(at)}  ${secret}${"z".repeat(LINE_MAX)}`)]));
    await logs(w);
    const line = w.printed.find((each) => each.includes(" files error "))!;
    expect(line.endsWith(`[cut at ${LINE_MAX} characters]`)).toBe(true);
    expect(line).not.toMatch(/Qw7/);
  });

  it("stops loudly naming the one permission when the token cannot query", async () => {
    for (const status of [401, 403]) {
      const w = world(() => ({ status, body: { success: false, errors: [{ code: 10000 }] } }));
      await expect(logs(w)).rejects.toThrow(
        `the Workers Logs query was refused (${status}, [10000]): the Cloudflare token lacks ` +
        `${TELEMETRY_PERMISSION}, the one permission the query takes`);
    }
    expect(TELEMETRY_PERMISSION).toMatch(/^Workers Observability Write /);
    const failed = world(() => ({ status: 500, body: { success: false, errors: [{ code: 1 }] } }));
    await expect(logs(failed)).rejects.toThrow("the Workers Logs query failed (500, [1])");
  });

  it("reads only while the doors serve the stage's accounts alone: its log is public", async () => {
    // The stage's end (`host` the zone) deletes the mode: after it real owners' lines exist.
    expect(HOST, "the stage is over: delete mode logs").not.toBe(ZONE);
    for (const control of [{ ...DARK, outside: "on", ordinary: "all" }, { ...DARK, ordinary: 1 }]) {
      const w = world(() => ok([]), control);
      await expect(logs(w)).rejects.toThrow("the job's log is public, so mode logs reads only " +
                                            "while the control record admits no owner but its canary");
      expect(w.calls.map((call) => call.path)).not.toContain(QUERY);
    }
  });

  it("stops loudly on an answer out of shape, never reading it as no lines", async () => {
    for (const result of [{}, { events: {} }, { events: { events: [{ $metadata: {} }] } }]) {
      const w = world(() => ({ status: 200, body: { success: true, result } }));
      await expect(logs(w)).rejects.toThrow("the Workers Logs query answered");
    }
  });

  it("takes only its own options, and never --yes", () => {
    const w = world(() => ok([]));
    expect(MODES.logs.applies).toBe(false);
    expect(() => parse(["--mode", "logs", "--yes", "--plan", "p.json"], ENV, w.io))
      .toThrow("mode logs changes nothing: it takes no --yes");
    for (const [flag, value] of [["--minutes", "0"], ["--minutes", String(MINUTES_MAX + 1)],
                                 ["--minutes", "1.5"], ["--worker", "nowhere"],
                                 ["--filter", "a\nb"], ["--filter", "x".repeat(201)]]) {
      expect(() => parse(["--mode", "logs", flag!, value!], ENV, w.io), `${flag} ${value}`)
        .toThrow(`${flag} is `);
    }
    expect(parse(["--mode", "logs", "--minutes", String(MINUTES_MAX)], ENV, w.io).minutes)
      .toBe(MINUTES_MAX);
    expect(() => parse(["--mode", "dark", "--minutes", "5"], ENV, w.io))
      .toThrow("mode dark takes no --minutes: it is mode logs's");
  });
});
