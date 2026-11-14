/**
 * The deploy kit's GitHub specifics, in one place: a job's summary, outputs, annotations and
 * masks, and the run's id and page, each read from a variable Actions sets (GitHub's
 * "Variables reference" and "Workflow commands"); and the repository's REST API, called with
 * the job's own token. Run by hand, outside Actions, the kit writes no summary or output,
 * masks nothing, and its run is named by the time.
 *
 * Node runs this file as it is (type stripping), as the kit does.
 */
import { appendFileSync } from "node:fs";

import { repositoryCall } from "../contract/src/canary.ts";

type Env = Readonly<Record<string, string | undefined>>;
type Json = Record<string, unknown>;

/** `name`'s value in `env`, as a job hands it: an empty one is unset. */
export function set(env: Env, name: string): string | undefined {
  const value = env[name];
  return value === undefined || value === "" ? undefined : value;
}

/** `text` appended as it is to the file Actions names in `variable`; by hand, nowhere. */
function append(env: Env, variable: string, text: string): void {
  const file = set(env, variable);
  if (file !== undefined) {
    appendFileSync(file, text);
  }
}

/** `text` appended as it is to the job's summary (`GITHUB_STEP_SUMMARY`); by hand, nowhere. */
export function summarize(env: Env, text: string): void {
  append(env, "GITHUB_STEP_SUMMARY", text);
}

/** The step's output `name` (`GITHUB_OUTPUT`), which a later job reads; by hand, nowhere. */
export function output(env: Env, name: string, value: string): void {
  append(env, "GITHUB_OUTPUT", `${name}=${value}\n`);
}

/**
 * The workflow command that masks `value` in the job's log from the next line on
 * (`::add-mask::`), or null outside Actions, where nothing is masked.
 */
export function maskLine(env: Env, value: string): string | null {
  return set(env, "GITHUB_ACTIONS") === undefined ? null : `::add-mask::${value}`;
}

/** A workflow command's text, escaped as the runner reads it back. */
const escaped = (text: string) =>
  text.replaceAll("%", "%25").replaceAll("\r", "%0D").replaceAll("\n", "%0A");

/**
 * The line that annotates the run with `message` under `title` (`::notice` or `::error`); by
 * hand, the title and the message as they are.
 */
export function annotate(env: Env, level: "notice" | "error", title: string, message: string):
    string {
  return set(env, "GITHUB_ACTIONS") === undefined ? `${title}: ${message}`
    : `::${level} title=${escaped(title).replaceAll(":", "%3A").replaceAll(",", "%2C")}::${
      escaped(message)}`;
}

/** A run of the kit: its id, and the attempt of it. */
export interface JobRun {
  readonly id: string;
  readonly attempt: string | null;
}

/**
 * The run: the workflow run's id (`GITHUB_RUN_ID`), which a re-run attempt keeps, and its
 * attempt; run by hand, `local-` and the time `now`, and no attempt.
 */
export function runOf(env: Env, now: Date): JobRun {
  return { id: set(env, "GITHUB_RUN_ID") ?? `local-${now.toISOString().replace(/[-:]|\.\d+/g, "")}`,
           attempt: set(env, "GITHUB_RUN_ATTEMPT") ?? null };
}

/** The workflow run's page, or null by hand. */
export function runLink(env: Env): string | null {
  const [server, repository, id] = ["GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID"]
    .map((name) => set(env, name));
  return server === undefined || repository === undefined || id === undefined ? null
    : `${server}/${repository}/actions/runs/${id}`;
}

/** The job's own token, which the job's `permissions:` scope. */
export const TOKEN = "GITHUB_TOKEN";

/** What a call of the repository's API reaches: the network, and the job's variables. */
export interface Reach {
  readonly fetch: (url: string, init?: RequestInit) => Promise<Response>;
  readonly env: Env;
}

/** A GitHub call's refusal, with its status. */
export class GitHubRefused extends Error {
  readonly status: number;
  constructor(method: string, path: string, status: number) {
    super(`GitHub answered ${method} ${path} with ${status}`);
    this.status = status;
  }
}

/**
 * One call of the repository's API (`contract/src/canary.ts` `repositoryCall`) with the job's
 * token: its JSON, null for no content, or a loud stop.
 */
export async function github(reach: Reach, method: string, path: string, body?: Json):
    Promise<unknown> {
  const token = set(reach.env, TOKEN);
  if (token === undefined) {
    throw new Error(`${TOKEN} is not set, so GitHub can't be asked ${method} ${path}`);
  }
  const { url, init } = repositoryCall(token, method, path, body);
  const answer = await reach.fetch(url, init);
  if (!answer.ok) {
    throw new GitHubRefused(method, path, answer.status);
  }
  return answer.status === 204 ? null : answer.json();
}

/** The most a page of a GitHub listing holds. */
const PAGE = 100;

/**
 * The listing at `path`, a page at a time, in GitHub's order: each answer, or its `field`
 * where GitHub wraps the listing (`workflow_runs`).
 */
export async function* pages(reach: Reach, path: string, field?: string):
    AsyncGenerator<Json[]> {
  for (let page = 1; ; page += 1) {
    const answer = await github(reach, "GET", `${path}${path.includes("?") ? "&" : "?"
      }per_page=${PAGE}&page=${page}`);
    const rows = (field === undefined ? answer : (answer as Json)[field]) as Json[];
    yield rows;
    if (rows.length < PAGE) {
      return;
    }
  }
}
