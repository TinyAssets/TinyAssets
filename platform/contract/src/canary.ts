/**
 * The platform canary's clock and pager (`canary.json`, d3 §J2), what the doors' Cron Trigger
 * (`doors/src/canary-clock.ts`) and the deploy kit (`deploy/actions.ts`, `deploy/alarm.ts`)
 * share: the repository's GitHub API calls, the clock's dispatch among them, and the one page
 * form both send to the founder.
 */
import canary from "../canary.json" with { type: "json" };

// Read as text (templates), so this module holds strings alone, never the shared file
// object (`ci/kit/module-state.ts`).
/** The workflow the clock dispatches, and the secret holding the token it dispatches with. */
export const DISPATCH = Object.freeze({
  repository: `${canary.dispatch.repository}`, workflow: `${canary.dispatch.workflow}`,
  ref: `${canary.dispatch.ref}`, token: `${canary.dispatch.token}` });

/** The pager's API and the secrets holding its two keys. */
export const PAGER = Object.freeze({
  url: `${canary.pager.url}`, user: `${canary.pager.user}`, token: `${canary.pager.token}` });

/** The secrets the clock runs on, each optional: without the token it dispatches nothing. */
export const CLOCK_SECRETS: readonly string[] = Object.freeze(
  [`${canary.dispatch.token}`, `${canary.pager.user}`, `${canary.pager.token}`]);

/** GitHub's REST API version every call of the repository's API is sent at. */
const GITHUB_API_VERSION = "2022-11-28";

/**
 * One call of the repository's GitHub REST API with `token`: its URL and request. The clock's
 * dispatch and the deploy kit's calls (`deploy/actions.ts` `github`) are each one of these.
 */
export function repositoryCall(token: string, method: string, path: string, body?: unknown):
    { readonly url: string; readonly init: RequestInit } {
  return { url: `https://api.github.com/repos/${DISPATCH.repository}${path}`, init: {
    method, headers: { Authorization: `Bearer ${token}`, Accept: "application/vnd.github+json",
                       "User-Agent": "tinyassets-canary",
                       "X-GitHub-Api-Version": GITHUB_API_VERSION,
                       ...(body === undefined ? {} : { "Content-Type": "application/json" }) },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }) } };
}

/** The clock's dispatch of the canary's workflow, on `ref`, with `token`. */
export function dispatchRequest(token: string): Request {
  const { url, init } = repositoryCall(token, "POST",
                                       `/actions/workflows/${DISPATCH.workflow}/dispatches`,
                                       { ref: DISPATCH.ref });
  return new Request(url, init);
}

/** The pager's two keys, as their secrets hold them. */
export interface PagerKeys {
  readonly user: string;
  readonly token: string;
}

/** One page: `urgent` is Pushover's emergency priority, repeated until acknowledged. */
export interface Page {
  readonly title: string;
  readonly message: string;
  readonly url?: string;
  readonly urgent: boolean;
}

/**
 * Pushover's emergency priority repeats every `retry` seconds until acknowledged, for at most
 * `expire` (its API takes 30 at least and 10800 at most); the alarm's own ladder pages again
 * after that.
 */
const URGENT = Object.freeze({ priority: "2", retry: "60", expire: "3600" });
/** High priority: past quiet hours, once. */
const HIGH = Object.freeze({ priority: "1" });

/** The page as Pushover's form takes it, its title and message cut to Pushover's lengths. */
export function pageForm(keys: PagerKeys, page: Page): RequestInit {
  return { method: "POST", body: new URLSearchParams({
    token: keys.token, user: keys.user, title: page.title.slice(0, 250),
    message: page.message.slice(0, 1024), ...(page.urgent ? URGENT : HIGH),
    ...(page.url === undefined ? {} : { url: page.url }) }) };
}
