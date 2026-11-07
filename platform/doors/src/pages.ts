/**
 * The owner app's pages (tasks 2, the owner app): `/app` and its two return pages, served by
 * the doors Worker from its own files (`app/`, the `ASSETS` binding), on the same origin as
 * the owner surface `/app/api/*` they call. Decided in the app slice: the doors, not the
 * website's static export, serve `/app`, because
 * - the session cookie is `__Host-`, Strict, and rides only same-origin JSON posts to
 *   `/app/api` (`session.ts`), and the sign-in and connect return pages (`WEB_REDIRECT`,
 *   `CONNECTED_PAGE`) are the doors' own constants;
 * - every page here needs response headers a static host cannot set: `Referrer-Policy:
 *   no-referrer` and a strict CSP with no inline script (the export's runtime injects inline
 *   scripts), `frame-ancestors 'none'` among them;
 * - the pages and the doors they call ship in one deploy, so they never disagree.
 *
 * Only the names below are served, each a `GET` or `HEAD`; any other path under `/app` is
 * `404`, and the assets binding is reached for nothing else (`run_worker_first`). Each
 * answer carries `HEADERS`. The pages load only this origin's own files: no third-party
 * script, style, font or image (`CSP`), and no inline script, so the return pages' own first
 * script is the first to run (it clears the code from the address bar before anything else).
 */
import { type DoorsEnv, PUBLIC_HOST } from "./config";

/** The pages, by path: each is its file. */
const PAGES: Readonly<Record<string, string>> = Object.freeze({
  "/app": "index.html",
  "/app/signed-in": "signed-in.html",
  "/app/connected": "connected.html",
});

/** The pages' own scripts and style, under `/app/static/`. */
const STATIC: Readonly<Record<string, string>> = Object.freeze({
  "app.js": "text/javascript; charset=utf-8",
  "api.js": "text/javascript; charset=utf-8",
  "view.js": "text/javascript; charset=utf-8",
  "signed-in.js": "text/javascript; charset=utf-8",
  "connected.js": "text/javascript; charset=utf-8",
  "app.css": "text/css; charset=utf-8",
});

export const APP_PREFIX = "/app";
const STATIC_PREFIX = "/app/static/";

/**
 * The strict CSP: this origin's own scripts, style and socket, nothing inline, nothing
 * framed, no form posts, and the DOM's script sinks closed (Trusted Types with no policy).
 */
export const CSP = "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; " +
  `connect-src 'self' wss://${PUBLIC_HOST}; base-uri 'none'; form-action 'none'; ` +
  "frame-ancestors 'none'; require-trusted-types-for 'script'; trusted-types 'none'";

export const HEADERS: Readonly<Record<string, string>> = Object.freeze({
  "Content-Security-Policy": CSP,
  "Referrer-Policy": "no-referrer",
  "X-Content-Type-Options": "nosniff",
  "Cross-Origin-Opener-Policy": "same-origin",
  "Cross-Origin-Resource-Policy": "same-origin",
  "Cache-Control": "no-cache",
});

function answer(status: number, body: BodyInit | null, type: string, extra: HeadersInit = {}):
    Response {
  return new Response(body, { status, headers: { ...HEADERS, "Content-Type": type, ...extra } });
}

/** Whether `path` is the app's: `/app` or under it, but never the owner surface. */
export function appPath(path: string): boolean {
  return path === APP_PREFIX || path.startsWith(`${APP_PREFIX}/`);
}

/** The app page or file for `request`, from `ASSETS`, with the pages' headers. */
export async function servePage(request: Request, env: DoorsEnv): Promise<Response> {
  const url = new URL(request.url);
  const page = Object.hasOwn(PAGES, url.pathname) ? PAGES[url.pathname]! : null;
  const name = url.pathname.startsWith(STATIC_PREFIX)
    ? url.pathname.slice(STATIC_PREFIX.length) : null;
  const type = name !== null && Object.hasOwn(STATIC, name) ? STATIC[name]! : null;
  const file = page ?? (type === null ? null : name);
  if (file === null) {
    return answer(404, "not found", "text/plain; charset=utf-8");
  }
  if (request.method !== "GET" && request.method !== "HEAD") {
    return answer(405, "method not allowed", "text/plain; charset=utf-8", { Allow: "GET, HEAD" });
  }
  const served = await env.ASSETS.fetch(new Request(new URL(`/${file}`, url.origin)));
  if (served.status !== 200) {
    // Every named file ships in `app/`: a missing one is a broken deploy, said loudly.
    throw new Error(`the app file ${file} is not in the deployed assets (${served.status})`);
  }
  return answer(200, request.method === "HEAD" ? null : served.body,
                page !== null ? "text/html; charset=utf-8" : type!);
}
