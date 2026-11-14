/**
 * The platform canary's clock and pager (`canary.json`, d3 §J2), what the doors' Cron Trigger
 * (`doors/src/canary-clock.ts`) and the canary's alarm (`deploy/alarm.ts`) share: the
 * workflow dispatch the clock sends, and the one page form both send to the founder.
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

/** GitHub's REST API version the dispatch is sent at. */
const GITHUB_API_VERSION = "2022-11-28";

/** The clock's dispatch of the canary's workflow, on `ref`, with `token`. */
export function dispatchRequest(token: string): Request {
  return new Request(`https://api.github.com/repos/${DISPATCH.repository}/actions/workflows/${
    DISPATCH.workflow}/dispatches`, {
    method: "POST", body: JSON.stringify({ ref: DISPATCH.ref }),
    headers: { Authorization: `Bearer ${token}`, Accept: "application/vnd.github+json",
               "Content-Type": "application/json", "User-Agent": "tinyassets-canary-clock",
               "X-GitHub-Api-Version": GITHUB_API_VERSION } });
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
