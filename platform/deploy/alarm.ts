/**
 * The platform canary's alarm (d3 §J2), one path for every run of the canary's workflow:
 * the clock's dispatches, the watchdog's schedule and a run by hand.
 *
 *   node --experimental-strip-types platform/deploy/alarm.ts <result file>
 *
 * It reads the run's outcomes (`canary.ts --result`) and acts only when the canary is armed
 * (`CANARY_ARMED`: the production environment holds the clock's dispatch token, which the
 * founder mints at the deploy) and the doors serve `/mcp`. Before both, every run, the
 * workflow's schedule included, changes nothing and pages no one: that is the canary's state
 * from its merge until the deploy.
 *
 * - **Red** (a check failed): the first red run is only reported. A second red in a row (the
 *   previous completed run of the workflow on main failed within the clock's 30 minutes), or a
 *   failure enough alone (the clock stopped), opens the `platform-down` issue; while one is
 *   open, every red comments on it. It pages the founder at Pushover's emergency priority: at
 *   once when the issue opens, then 1 h, 4 h and 24 h after the last page (and every 24 h
 *   after), unless someone commented on the issue since that page. Each page leaves a
 *   `[PAGED` comment, the ladder's one state.
 * - **Green** (every check passed): the open issue is closed, with a comment.
 *
 * Any failed GitHub or Pushover call stops it loudly.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { DISPATCH, PAGER, pageForm } from "../contract/src/canary.ts";
import { CLOCK_MS, type Result } from "./canary.ts";

type Json = Record<string, unknown>;

/** The alarm's world: replaced whole by its tests. */
export interface World {
  readonly fetch: (url: string, init?: RequestInit) => Promise<Response>;
  readonly env: Readonly<Record<string, string | undefined>>;
  readonly now: () => Date;
}

export const LABEL = "platform-down";
/** The page ladder: how long after the last page the next is sent, while nobody answers. */
const LADDER_S: readonly number[] = Object.freeze([3600, 4 * 3600, 24 * 3600]);
/** What begins the comment each page leaves. */
const PAGED = "[PAGED";
/** The workflow's own token's login, whose comments are never the founder's answer. */
const BOT = "github-actions[bot]";

const API = `https://api.github.com/repos/${DISPATCH.repository}`;

/** One GitHub call with the run's token: its JSON, or a loud stop. */
async function github(world: World, method: string, path: string, body?: Json):
    Promise<unknown> {
  const answer = await world.fetch(`${API}${path}`, {
    method, headers: { Authorization: `Bearer ${world.env["GITHUB_TOKEN"] ?? ""}`,
                       Accept: "application/vnd.github+json",
                       ...(body === undefined ? {} : { "Content-Type": "application/json" }) },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
  if (answer.status < 200 || answer.status > 299) {
    throw new Error(`GitHub answered ${method} ${path} with ${answer.status}`);
  }
  return answer.status === 204 ? null : answer.json();
}

/** Every page of a GitHub listing at `path`. */
async function all(world: World, path: string): Promise<Json[]> {
  const rows: Json[] = [];
  for (let page = 1; ; page += 1) {
    const batch = await github(world, "GET", `${path}${path.includes("?") ? "&" : "?"
      }per_page=100&page=${page}`) as Json[];
    rows.push(...batch);
    if (batch.length < 100) {
      return rows;
    }
  }
}

/** Whether the workflow's previous completed run on main failed within the clock's window. */
async function previousRed(world: World): Promise<boolean> {
  const { workflow_runs: runs } = await github(world, "GET", `/actions/workflows/${
    DISPATCH.workflow}/runs?branch=${DISPATCH.ref}&status=completed&per_page=5`) as
    { workflow_runs: Json[] };
  const previous = runs.find((run) => String(run["id"]) !== world.env["GITHUB_RUN_ID"]);
  return previous !== undefined && previous["conclusion"] === "failure"
    && world.now().getTime() - Date.parse(String(previous["updated_at"])) <= CLOCK_MS;
}

/** Why this tick pages, or null: the ladder over the issue's comments. */
export function pageDue(comments: readonly Json[], now: Date): string | null {
  const at = (comment: Json) => Date.parse(String(comment["created_at"]));
  const marker = (comment: Json) => String(comment["body"] ?? "").trimStart().startsWith(PAGED);
  const pages = comments.filter(marker).map(at).sort((a, b) => a - b);
  const last = pages.at(-1);
  if (last === undefined) {
    return "the first page";
  }
  if (comments.some((comment) => !marker(comment) && at(comment) > last
                     && (comment["user"] as Json | undefined)?.["login"] !== BOT)) {
    return null;
  }
  const rung = LADDER_S[Math.min(pages.length - 1, LADDER_S.length - 1)]!;
  return now.getTime() - last >= rung * 1000 ? `no answer ${rung / 3600} h after the last page`
    : null;
}

/** The alarm for one run's result: what it did, a line each. */
export async function alarm(world: World, result: Result): Promise<string[]> {
  if (world.env["CANARY_ARMED"] !== "true") {
    return [`not armed: the production environment holds no ${DISPATCH.token}, so nothing ` +
            "alarms"];
  }
  if (result.side === "old") {
    return ["dark: the old stack serves /mcp, so nothing alarms"];
  }
  const run = `${world.env["GITHUB_SERVER_URL"] ?? "https://github.com"}/${
    DISPATCH.repository}/actions/runs/${world.env["GITHUB_RUN_ID"] ?? ""}`;
  const failed = result.outcomes.filter((each) => each.state === "failed");
  const open = (await all(world, `/issues?state=open&labels=${LABEL}`))
    .filter((issue) => issue["pull_request"] === undefined)[0];
  const lines = failed.map((each) => `- ${each.check} (exit ${each.code}): ${each.line}`);
  if (failed.length === 0) {
    if (open === undefined) {
      return ["green"];
    }
    await github(world, "POST", `/issues/${open["number"]}/comments`,
                 { body: `GREEN, recovered: ${run}` });
    await github(world, "PATCH", `/issues/${open["number"]}`,
                 { state: "closed", state_reason: "completed" });
    return [`green: closed #${open["number"]}`];
  }
  if (open === undefined && !failed.some((each) => each.alone)
      && !(await previousRed(world))) {
    return ["the first red: the next red in a row opens the issue and pages"];
  }
  let number: unknown;
  let due: string | null;
  if (open === undefined) {
    await github(world, "POST", "/labels", { name: LABEL, color: "d73a4a",
                                             description: "The platform canary's alarm" })
      .catch((error: Error) => {
        if (!/ with 422$/.test(error.message)) {
          throw error;
        }
      });
    const made = await github(world, "POST", "/issues", {
      title: `Platform down: ${failed.map((each) => each.check).join(", ")}`, labels: [LABEL],
      body: [`The platform canary failed twice in a row, or its clock stopped. Run: ${run}`, "",
             ...lines, "", "Each red run comments here, and a green one closes it."].join("\n") });
    number = (made as Json)["number"];
    due = "the first page";
  } else {
    number = open["number"];
    await github(world, "POST", `/issues/${number}/comments`,
                 { body: [`RED: ${run}`, "", ...lines].join("\n") });
    due = pageDue(await all(world, `/issues/${number}/comments`), world.now());
  }
  if (due === null) {
    return [`red: #${number}, no page due`];
  }
  const user = world.env[PAGER.user];
  const token = world.env[PAGER.token];
  if (user === undefined || user === "" || token === undefined || token === "") {
    throw new Error(`red: #${number}, and the page is due but ${PAGER.user} or ${PAGER.token} ` +
                    "is not set");
  }
  const paged = await world.fetch(PAGER.url, pageForm({ user, token }, {
    title: "TinyAssets platform down", url: run, urgent: true,
    message: failed.map((each) => `${each.check}: ${each.line}`).join("\n") }));
  if (paged.status !== 200) {
    throw new Error(`red: #${number}, and Pushover refused the page (${paged.status})`);
  }
  await github(world, "POST", `/issues/${number}/comments`,
               { body: `${PAGED} ${world.now().toISOString()}] ${due}` });
  return [`red: #${number}, paged (${due})`];
}

async function main(argv: readonly string[]): Promise<void> {
  if (argv[0] === undefined) {
    throw new Error("alarm.ts <result file>");
  }
  const result = JSON.parse(readFileSync(argv[0], "utf8")) as Result;
  for (const line of await alarm({ fetch: (url, init) => fetch(url, init), env: process.env,
                                   now: () => new Date() }, result)) {
    console.log(line);
  }
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  main(process.argv.slice(2)).catch((error: Error) => {
    console.error(`the platform canary's alarm stopped: ${error.message}`);
    process.exit(1);
  });
}
