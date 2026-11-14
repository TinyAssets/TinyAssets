/**
 * The platform's canary (`canary.ts`) and its alarm (`alarm.ts`), against a fake platform,
 * GitHub, E2B, WorkOS and Pushover: the canary reads which side serves and checks that side
 * alone, a keyed check unchecked is a failure, the full run runs once a day and its ending is
 * carried, and the off-account copy must have passed a night inside two once configured; the alarm acts only when armed and the doors serve, pages at the second red in a
 * row or a stopped clock, climbs its ladder, stops at the founder's answer and closes on green.
 */
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { PLATFORM, REPO } from "../../ci/paths";
import { workflow } from "../../ci/workflows";
import { DISPATCH, PAGER } from "../../contract/src/canary";
import { E2B_API } from "../../sandbox/e2b/transport";
import { TOKEN } from "../actions";
import { alarm, LABEL, pageDue } from "../alarm";
import { canary, exitOf, FULL_RUN, INPUTS, type Options, type Result, type World }
  from "../canary";
import { CALLBACK, E2B_HOOK_URL, EGRESS_DOMAIN, FIRST_PARTY, ORIGIN } from "../facts";
import { NIGHTLY } from "../offsite";

type Json = Record<string, unknown>;

const HANDLES = JSON.parse(readFileSync(join(REPO, "scripts", "canary_handles.json"), "utf8")) as
  Record<"old" | "new", { authorization_server: string; handles: string[] }>;
const NOW = new Date("2026-11-14T09:00:00Z");
const ISSUER = "https://auth.test";
const WORKOS_AUTHORIZE = "https://api.workos.com/user_management/authorize";
const KEYED: Record<string, string> = {
  TA_PROBE_KEY: "probe-key", E2B_API_KEY: "e2b-key-123", OWNER_ISSUER: ISSUER,
  WORKOS_CLIENT_ID: "client_test", [TOKEN]: "run-token" };
const AUTO: Options = { side: null, unkeyed: false, clock: false, full: false };
const DOCUMENT = `${ORIGIN}/.well-known/oauth-protected-resource/mcp`;

/** What the fake platform is: which side serves, and what each part answers. */
interface Platform {
  side: "doors" | "old";
  /** The old stack's anonymous 401 body; empty is the doors'. */
  oldBody: string;
  served: Set<string>;
  /** The full run's record, as the owner's file holds it, or none. */
  record: Json | null;
  runState: string;
  lastDispatch: string | null;
  /** The off-account copy's completed nights on main, newest first. */
  nights: Night[];
  /** What GitHub answers the listing of those nights, when not 200. */
  nightsStatus: number;
}

/** One night of `offsite-copy.yml`: when it ran, and how each of its two jobs ended. */
interface Night {
  at: string;
  settings: "success" | "failure";
  copy: "success" | "failure" | "skipped";
}

const night = (at: string, copy: Night["copy"], settings: Night["settings"] = "success"): Night =>
  ({ at, settings, copy });
const LAST_NIGHT = night("2026-11-14T05:40:00Z", "success");

function platform(change: Partial<Platform> = {}): Platform {
  return { side: "doors", oldBody: '{"error":"authentication_required"}',
           served: new Set(HANDLES.new.handles), record: null, runState: "succeeded",
           lastDispatch: "2026-11-14T08:50:00Z", nights: [LAST_NIGHT], nightsStatus: 200,
           ...change };
}

const json = (body: unknown, status = 200, headers: Record<string, string> = {}) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json",
                                                         ...headers } });

/** The world the canary runs in, over `p`: each request it sent, by method and URL. */
function world(p: Platform, env: Record<string, string> = KEYED): World & { sent: string[] } {
  const sent: string[] = [];
  return {
    sent, env, now: () => NOW,
    fetch: async (url, init) => {
      const method = init?.method ?? "GET";
      const body = init?.body === undefined ? null : JSON.parse(String(init.body)) as Json;
      sent.push(`${method} ${url}`);
      const at = new URL(url);
      if (url === DOCUMENT) {
        return json({ resource: `${ORIGIN}/mcp`, authorization_servers: [
          HANDLES[p.side === "doors" ? "new" : "old"].authorization_server] });
      }
      if (url === `${ORIGIN}/mcp`) {
        return p.side === "doors" ? new Response("", { status: 401, headers: {
          "WWW-Authenticate": `Bearer resource_metadata="${DOCUMENT}"` } })
          : new Response(p.oldBody, { status: 401 });
      }
      if (url.startsWith(E2B_API)) {
        if (at.pathname.startsWith("/templates/aliases/")) {
          return json({ templateID: "t1" });
        }
        if (at.pathname === "/templates/t1/tags/exists") {
          return json({ exists: true });
        }
        return json([{ id: "w1", url: E2B_HOOK_URL, enabled: p.side === "doors" }]);
      }
      if (url === `${ISSUER}/.well-known/openid-configuration`) {
        return json({ issuer: ISSUER, jwks_uri: `${ISSUER}/jwks` });
      }
      if (url === `${ISSUER}/jwks`) {
        return json({ keys: [] });
      }
      if (url.startsWith(`${ORIGIN}/authorize?`)) {
        return new Response(null, { status: 302, headers: { Location: `${WORKOS_AUTHORIZE}?${
          new URLSearchParams({ client_id: "client_test", redirect_uri: CALLBACK })}` } });
      }
      if (url.startsWith(`${WORKOS_AUTHORIZE}?`)) {
        return new Response(null, { status: 302, headers: { Location: `${ISSUER}/sign-in` } });
      }
      if (url.startsWith(FIRST_PARTY.resource)) {
        return new Response("<!doctype html>", { headers: { "Content-Type": "text/html",
                                                            "Content-Security-Policy": "x" } });
      }
      if (url === FIRST_PARTY.client) {
        return json({ client_id: FIRST_PARTY.client });
      }
      if (url === `https://${EGRESS_DOMAIN}/`) {
        return json({ error: { code: "not_found", message: "not found" } }, 404);
      }
      if (url.startsWith(`${ORIGIN}/api/tools/`)) {
        const name = at.pathname.slice("/api/tools/".length);
        if (method === "GET") {
          return new Response("", { status: p.served.has(name) ? 405 : 404 });
        }
        if (name === "file_read") {
          if (body?.["path"] === FULL_RUN) {
            return p.record === null ? json({ error: "no such file" }, 404)
              : json({ text: JSON.stringify(p.record), entry: { etag: "e-record" } });
          }
          return json({ text: lastBeat, entry: { etag: "e-beat" } });
        }
        if (name === "file_write") {
          if (body?.["path"] === FULL_RUN) {
            p.record = JSON.parse(String(body["content"])) as Json;
            return json({ entry: { etag: "e-record" } });
          }
          lastBeat = String(body?.["content"]);
          return json({ entry: { etag: "e-beat" } });
        }
        if (name === "file_remove") {
          return json({ entry: null });
        }
        if (name === "converse") {
          return json({ run_id: "r1", engine: "pi", state: p.runState, code: null,
                        result: p.runState === "succeeded" ? "ok" : null, error: null });
        }
      }
      const repository = `https://api.github.com/repos/${DISPATCH.repository}`;
      if (url.startsWith(repository)
          && new Headers(init?.headers).get("Authorization") !== `Bearer ${env[TOKEN]}`) {
        return json({ message: "Bad credentials" }, 401);
      }
      if (url.startsWith(`${repository}/actions/workflows/${NIGHTLY.workflow}/runs?`)) {
        expect([at.searchParams.get("branch"), at.searchParams.get("status")])
          .toEqual([NIGHTLY.ref, "completed"]);
        return p.nightsStatus !== 200 ? json({ message: "unavailable" }, p.nightsStatus)
          : json({ workflow_runs: at.searchParams.get("page") !== "1" ? []
            : p.nights.map((each, id) => ({ id, created_at: each.at,
                                            updated_at: later(each.at, 20) })) });
      }
      const jobs = /^\/actions\/runs\/(\d+)\/jobs$/.exec(url.slice(repository.length));
      if (jobs !== null) {
        const each = p.nights[Number(jobs[1])]!;
        return json({ jobs: [
          { name: NIGHTLY.settings, conclusion: each.settings, completed_at: later(each.at, 1) },
          { name: NIGHTLY.copy, conclusion: each.copy, completed_at: later(each.at, 20) }] });
      }
      if (url.includes("/actions/workflows/") && url.includes("event=workflow_dispatch")) {
        return json({ workflow_runs: p.lastDispatch === null ? []
          : [{ created_at: p.lastDispatch }] });
      }
      throw new Error(`the fake world has no ${method} ${url}`);
    },
  };
}
let lastBeat = "";

const REPOSITORY = `https://api.github.com/repos/${DISPATCH.repository}`;
const NIGHTS = `${REPOSITORY}/actions/workflows/${NIGHTLY.workflow}/runs`;

/** `minutes` after `at`. */
const later = (at: string, minutes: number) =>
  new Date(Date.parse(at) + minutes * 60_000).toISOString().replace(".000Z", "Z");

const states = (result: Result) => result.outcomes.map((each) => [each.check, each.state]);

describe("the platform canary", () => {
  it("reads the platform dark while the old stack serves, and waits on the doors' checks",
     async () => {
    const w = world(platform({ side: "old" }));
    const result = await canary(w, AUTO);
    expect([result.side, states(result), exitOf(result)]).toEqual(["old", [
      ["the old stack", "passed"], ["E2B", "passed"], ["the doors", "waiting"]], 0]);
    expect(w.sent.filter((each) => each.includes("/api/") || each.includes("/app"))).toEqual([]);
  });

  it("sees a routes run stopped partway: the doors answer /mcp, the old document is served",
     async () => {
    const result = await canary(world(platform({ side: "old", oldBody: "" })), AUTO);
    expect([result.outcomes[0]!.state, exitOf(result)]).toEqual(["failed", 10]);
  });

  it("holds the side asked for", async () => {
    const result = await canary(world(platform({ side: "old" })), { ...AUTO, side: "doors" });
    expect([states(result), exitOf(result)]).toEqual([[["the side", "failed"]], 8]);
  });

  it("checks every part of the doors, and runs the full run when none ran today", async () => {
    const p = platform();
    const w = world(p);
    const result = await canary(w, AUTO);
    expect([result.side, exitOf(result)]).toEqual(["doors", 0]);
    expect(states(result)).toEqual([
      ["the challenge", "passed"], ["the app", "passed"], ["the client document", "passed"],
      ["the egress host", "passed"], ["the sign-in", "passed"], ["E2B", "passed"],
      ["the off-account copy", "passed"], ["the handle set", "passed"],
      ["the owner's files", "passed"], ["the full run", "passed"]]);
    expect(w.sent).toContain(`POST ${ORIGIN}/api/tools/converse`);
    expect(p.record).toEqual({ at: NOW.toISOString(), passed: true,
                               line: "run r1 answered on pi" });
    // GitHub is read for the copy's nights alone: the clock is the watchdog's.
    expect(w.sent.filter((each) => each.includes("api.github.com"))
      .map((each) => each.replace(/\?.*/, "")))
      .toEqual([`GET ${NIGHTS}`, `GET ${REPOSITORY}/actions/runs/0/jobs`]);
  });

  it("carries a failed full run until a day passes, then runs it again", async () => {
    const failed = { at: "2026-11-14T06:00:00Z", passed: false, line: "run r0 ended failed" };
    const p = platform({ record: { ...failed } });
    const w = world(p);
    const carried = await canary(w, AUTO);
    expect([carried.outcomes.at(-1)!.state, exitOf(carried)]).toEqual(["failed", 13]);
    expect(w.sent).not.toContain(`POST ${ORIGIN}/api/tools/converse`);
    p.record = { ...failed, at: "2026-11-13T08:59:00Z" };
    expect(exitOf(await canary(w, AUTO))).toBe(0);
    expect(w.sent).toContain(`POST ${ORIGIN}/api/tools/converse`);
  });

  it("fails a full run that does not succeed, and records it", async () => {
    const p = platform({ runState: "failed" });
    expect(exitOf(await canary(world(p), AUTO))).toBe(13);
    expect(p.record).toMatchObject({ passed: false, line: "run r1 ended failed" });
  });

  it("names handle drift", async () => {
    const served = new Set(HANDLES.new.handles);
    served.delete("file_read");
    const result = await canary(world(platform({ served })), AUTO);
    expect(result.outcomes.find((each) => each.check === "the handle set"))
      .toMatchObject({ state: "failed", code: 4 });
  });

  it("fails without the canary owner's key, and waits on the keyed checks only when asked",
     async () => {
    const { TA_PROBE_KEY: _key, ...unkeyed } = KEYED;
    expect(exitOf(await canary(world(platform(), unkeyed), AUTO))).toBe(7);
    const routes = await canary(world(platform(), unkeyed), { ...AUTO, side: "doors",
                                                             unkeyed: true });
    expect([states(routes).at(-1), exitOf(routes)]).toEqual([["the keyed checks", "waiting"], 0]);
  });

  it("on the watchdog's schedule, sees a stopped clock, a failure enough alone", async () => {
    expect(exitOf(await canary(world(platform()), { ...AUTO, clock: true }))).toBe(0);
    const stopped = await canary(world(platform({ lastDispatch: "2026-11-14T08:20:00Z" })),
                                 { ...AUTO, clock: true });
    expect(stopped.outcomes.at(-1)).toMatchObject({ check: "the clock", state: "failed",
                                                     code: 14, alone: true });
  });

  it("runs in its workflow with exactly what it reads, armed by the clock's own token", () => {
    const file = workflow(DISPATCH.workflow) as { on: Record<string, unknown>; jobs: Record<string,
      { environment: string; env: Record<string, string>; permissions: Record<string, string>;
        steps: { name?: string; if?: string; run?: string; env?: Record<string, string> }[] }> };
    expect(Object.keys(file.on).sort()).toEqual(["schedule", "workflow_dispatch"]);
    const job = file.jobs["canary"]!;
    expect(job.environment).toBe("production");
    expect(Object.keys(job.env).sort())
      .toEqual([...Object.values(INPUTS), "CANARY_ARMED"].sort());
    expect(job.env[TOKEN]).toBe(`\${{ secrets.${TOKEN} }}`);
    // The job's token reads the clock's runs and the copy's nights, and writes the issue.
    expect(job.permissions).toEqual({ contents: "read", actions: "read", issues: "write" });
    expect(job.env["CANARY_ARMED"]).toBe(`\${{ secrets.${DISPATCH.token} != '' }}`);
    const step = (name: string) => job.steps.find((each) => each.name === name)!;
    expect(step("The canary").run).toContain("platform/deploy/canary.ts");
    // Until armed, the schedule runs nothing: neither the canary nor its alarm.
    const armedOrByHand = "github.event_name != 'schedule' || env.CANARY_ARMED == 'true'";
    expect([step("Install the platform's pinned tools").if, step("The canary").if])
      .toEqual([armedOrByHand, armedOrByHand]);
    expect(step("The alarm").if).toBe("always() && steps.canary.outcome != 'skipped'");
    expect(step("The alarm").env).toEqual({
      [PAGER.user]: `\${{ secrets.${PAGER.user} }}`,
      [PAGER.token]: `\${{ secrets.${PAGER.token} }}` });
  });

  it("is the kit's one canary: no kit source runs Python", () => {
    const dir = join(PLATFORM, "deploy");
    for (const file of readdirSync(dir).filter((name) => name.endsWith(".ts"))) {
      expect([file, /python3?\b/.test(readFileSync(join(dir, file), "utf8"))])
        .toEqual([file, false]);
    }
  });
});

describe("the platform canary's watch on the off-account copy", () => {
  /** The copy's outcome, read at `now`, over `nights`. */
  const copy = async (nights: Night[], now = NOW, change: Partial<Platform> = {},
                      env = KEYED) => {
    const result = await canary({ ...world(platform({ nights, ...change }), env),
                                  now: () => now }, AUTO);
    return result.outcomes.find((each) => each.check === "the off-account copy")!;
  };

  /** The 12th passed; the 13th and 14th failed. */
  const missed = [night("2026-11-14T05:40:00Z", "failure"),
                  night("2026-11-13T05:40:00Z", "failure"),
                  night("2026-11-12T05:40:00Z", "success")];

  it("passes while a night passed inside two, a missed night among them", async () => {
    expect(await copy([night("2026-11-14T05:40:00Z", "failure"), LAST_NIGHT]))
      .toMatchObject({ state: "passed", line: "a night last passed at 2026-11-14T06:00:00Z" });
    // Two nights missed, but the second may have run hours late: not yet overdue.
    expect(await copy(missed, new Date("2026-11-14T17:59:00Z")))
      .toMatchObject({ state: "passed", line: "a night last passed at 2026-11-12T06:00:00Z" });
  });

  it("fails alone once no night has passed in two, so the alarm pages at once", async () => {
    const overdue = await copy(missed, new Date("2026-11-14T18:01:00Z"));
    expect(overdue).toMatchObject({ state: "failed", code: 15, alone: true });
    expect(overdue.line)
      .toMatch(/^no night of offsite-copy\.yml has passed since 2026-11-12T06:00:00Z, /);
    // Nights stopped altogether: the last one listed ran three days ago and failed.
    expect(await copy([night("2026-11-11T05:40:00Z", "failure")])).toMatchObject({
      state: "failed", code: 15, alone: true,
      line: expect.stringMatching(/has passed since before 2026-11-11T06:00:00Z/) });
    const { h, w, pages } = hub();
    expect(await alarm(w, { side: "doors", outcomes: [overdue] }))
      .toEqual(["red: #7, paged (the first page)"]);
    expect([pages.length, h.issue?.["title"]]).toEqual([1, "Platform down: the off-account copy"]);
  });

  it("waits, saying so, while the copy is not configured, or newly configured", async () => {
    const dark = await copy([night("2026-11-14T05:40:00Z", "skipped"), LAST_NIGHT]);
    expect(dark).toMatchObject({ state: "waiting" });
    expect(dark.line).toMatch(/^not configured: the night of 2026-11-14T05:40:00Z found none /);
    // Configured after the 13th's dark night, and the 14th's first copy failed: one day in.
    const fresh = await copy([night("2026-11-14T05:40:00Z", "failure"),
                              night("2026-11-13T05:40:00Z", "skipped")]);
    expect(fresh).toMatchObject({ state: "waiting", line: "configured since the night of " +
      "2026-11-13T05:41:00Z; its first pass is due by 2026-11-15T17:41:00.000Z" });
    // A night stored in part fails its settings job: that is red, not unconfigured.
    expect(await copy([night("2026-11-14T05:40:00Z", "skipped", "failure"),
                       night("2026-11-13T05:40:00Z", "skipped")],
                      new Date("2026-11-16T09:00:00Z")))
      .toMatchObject({ state: "failed", code: 15, alone: true });
    expect(await copy([])).toMatchObject({ state: "waiting",
      line: "no night of offsite-copy.yml has completed on main, so nothing says the copy is " +
            "configured" });
    expect(exitOf(await canary(world(platform({ nights: [night("2026-11-14T05:40:00Z",
                                                                 "skipped")] })), AUTO))).toBe(0);
  });

  it("fails, but not alone, when GitHub can't be read", async () => {
    expect(await copy([LAST_NIGHT], NOW, { nightsStatus: 502 })).toMatchObject({
      state: "failed", code: 15, alone: false,
      line: expect.stringMatching(
        /^GitHub answered GET \/actions\/workflows\/offsite-copy\.yml\/runs\?.* with 502$/) });
    const { [TOKEN]: _token, ...tokenless } = KEYED;
    expect(await copy([LAST_NIGHT], NOW, {}, tokenless)).toMatchObject({
      state: "failed", code: 15, alone: false,
      line: expect.stringMatching(/^GITHUB_TOKEN is not set/) });
  });
});

// --- The alarm -----------------------------------------------------------------------------

const RED: Result = { side: "doors", outcomes: [
  { check: "the app", state: "failed", line: "/app answered 502", code: 2, alone: false }] };
const GREEN: Result = { side: "doors", outcomes: [
  { check: "the app", state: "passed", line: "served", code: 0, alone: false }] };
const STOPPED: Result = { side: "doors", outcomes: [
  { check: "the clock", state: "failed", line: "stopped", code: 14, alone: true }] };
const ARMED = { CANARY_ARMED: "true", GITHUB_RUN_ID: "9", GITHUB_TOKEN: "run-token",
                [PAGER.user]: "user-key", [PAGER.token]: "app-token" };
const API = `https://api.github.com/repos/${DISPATCH.repository}`;

/** GitHub's issues and runs, and Pushover, as the alarm meets them. */
interface Hub {
  previous: Json | null;
  issue: Json | null;
  comments: Json[];
}

function hub(change: Partial<Hub> = {}) {
  const h: Hub = { previous: { id: 8, conclusion: "success", updated_at: "2026-11-14T08:50:00Z" },
                   issue: null, comments: [], ...change };
  const sent: string[] = [];
  const pages: URLSearchParams[] = [];
  const w: World = { env: ARMED, now: () => NOW, fetch: async (url, init) => {
    const method = init?.method ?? "GET";
    sent.push(`${method} ${url.replace(API, "")}`);
    const body = typeof init?.body === "string" ? JSON.parse(init.body) as Json : null;
    if (url === PAGER.url) {
      pages.push(new URLSearchParams(String(init!.body)));
      return new Response("{}", { status: 200 });
    }
    const path = url.replace(API, "");
    if (path.startsWith("/actions/workflows/")) {
      return json({ workflow_runs: [{ id: 9 }, ...h.previous === null ? [] : [h.previous]] });
    }
    if (path.startsWith("/issues?")) {
      return json(h.issue === null ? [] : [h.issue]);
    }
    if (path === "/labels") {
      return json({}, 422);
    }
    if (path === "/issues" && method === "POST") {
      h.issue = { number: 7, ...body };
      return json({ number: 7 }, 201);
    }
    if (/^\/issues\/7\/comments/.test(path)) {
      if (method === "POST") {
        h.comments.push({ body: body!["body"], created_at: NOW.toISOString(),
                          user: { login: "github-actions[bot]" } });
        return json({}, 201);
      }
      return json(h.comments);
    }
    if (path === "/issues/7" && method === "PATCH") {
      return json({});
    }
    throw new Error(`no fake for ${method} ${url}`);
  } };
  return { h, w, sent, pages };
}

const page = (at: string, login = "github-actions[bot]") =>
  ({ body: `[PAGED ${at}] x`, created_at: at, user: { login } });

describe("the platform canary's alarm", () => {
  it("does nothing until armed, nor while the platform is dark", async () => {
    for (const [env, result] of [[{ ...ARMED, CANARY_ARMED: "false" }, RED],
                                 [ARMED, { ...RED, side: "old" }]] as const) {
      const { w, sent } = hub();
      expect(await alarm({ ...w, env }, result)).toHaveLength(1);
      expect(sent).toEqual([]);
    }
  });

  it("reports a first red alone, and opens the issue and pages at the second in a row",
     async () => {
    const first = hub();
    expect(await alarm(first.w, RED)).toEqual(
      ["the first red: the next red in a row opens the issue and pages"]);
    expect([first.h.issue, first.pages]).toEqual([null, []]);
    const second = hub({ previous: { id: 8, conclusion: "failure",
                                     updated_at: "2026-11-14T08:50:00Z" } });
    expect(await alarm(second.w, RED)).toEqual(["red: #7, paged (the first page)"]);
    expect(second.h.issue).toMatchObject({ labels: [LABEL], title: "Platform down: the app" });
    expect([second.pages.length, second.pages[0]!.get("priority"), second.pages[0]!.get("user")])
      .toEqual([1, "2", "user-key"]);
    expect(second.h.comments.at(-1)!["body"]).toMatch(/^\[PAGED 2026-11-14T09:00:00\.000Z\] /);
  });

  it("pages at once for a stopped clock, which spans 30 minutes alone", async () => {
    const { w, pages } = hub();
    expect(await alarm(w, STOPPED)).toEqual(["red: #7, paged (the first page)"]);
    expect(pages).toHaveLength(1);
  });

  it("climbs the ladder on an open issue, and stops at the founder's answer", async () => {
    const issue = { number: 7, title: "Platform down: the app" };
    const recent = hub({ issue, comments: [page("2026-11-14T08:30:00Z")] });
    expect(await alarm(recent.w, RED)).toEqual(["red: #7, no page due"]);
    const late = hub({ issue, comments: [page("2026-11-14T07:30:00Z")] });
    expect(await alarm(late.w, RED))
      .toEqual(["red: #7, paged (no answer 1 h after the last page)"]);
    expect(pageDue([page("2026-11-14T07:30:00Z"),
                    { body: "looking", created_at: "2026-11-14T07:40:00Z",
                      user: { login: "founder" } }], NOW)).toBeNull();
    expect(pageDue([page("2026-11-14T03:00:00Z"), page("2026-11-14T06:00:00Z")], NOW))
      .toBeNull();
    expect(pageDue([page("2026-11-13T03:00:00Z"), page("2026-11-13T04:00:00Z")], NOW))
      .toBe("no answer 4 h after the last page");
  });

  it("closes the open issue on green", async () => {
    const { w, sent } = hub({ issue: { number: 7 } });
    expect(await alarm(w, GREEN)).toEqual(["green: closed #7"]);
    expect(sent.slice(-2)).toEqual(["POST /issues/7/comments", "PATCH /issues/7"]);
  });
});
