/**
 * The owner app's pages (`pages.ts`), served by the doors from their own files: only the
 * named pages and files, each with the strict CSP, no referrer and no sniffing; the return
 * pages' first script is their own, a classic one, and no page holds an inline script or a
 * third-party URL.
 */
import { describe, expect, it } from "vitest";

import { CSP } from "../src/pages";
import { SIGNIN_REFUSALS, SIGNIN_STATES } from "../src/sources";
import { call, url } from "./support";

const PAGES = ["/app", "/app/signed-in", "/app/connected"];
const STATIC = ["app.js", "api.js", "view.js", "signed-in.js", "connected.js", "app.css"];

function get(path: string, init: RequestInit = {}): Promise<Response> {
  return call(new Request(url(path), init));
}

function expectHeaders(response: Response): void {
  expect(response.headers.get("Content-Security-Policy")).toBe(CSP);
  expect(response.headers.get("Referrer-Policy")).toBe("no-referrer");
  expect(response.headers.get("X-Content-Type-Options")).toBe("nosniff");
  expect(response.headers.get("Cross-Origin-Opener-Policy")).toBe("same-origin");
}

/** Each `<script ...>` tag of a page, in order. */
function scripts(html: string): string[] {
  return [...html.matchAll(/<script\b[^>]*>/g)].map((match) => match[0]);
}

describe("the owner app's pages", () => {
  it("are served with the strict CSP, no referrer and no sniffing", async () => {
    for (const path of PAGES) {
      const response = await get(path);
      expect(response.status, path).toBe(200);
      expect(response.headers.get("Content-Type")).toBe("text/html; charset=utf-8");
      expectHeaders(response);
      expect(await response.text()).toMatch(/^<!doctype html>/);
    }
    for (const file of STATIC) {
      const response = await get(`/app/static/${file}`);
      expect(response.status, file).toBe(200);
      expect(response.headers.get("Content-Type")).toMatch(
        file.endsWith(".css") ? /^text\/css/ : /^text\/javascript/);
      expectHeaders(response);
    }
    const head = await get("/app", { method: "HEAD" });
    expect(head.status).toBe(200);
    expect(await head.text()).toBe("");
  });

  it("tell each sign-in refusal's own reason in the records' words, and know every end " +
     "(review L6, M4)", async () => {
    const app = await (await get("/app/static/app.js")).text();
    for (const [reason, words] of Object.entries(SIGNIN_REFUSALS)) {
      expect(app, reason).toContain(`${reason}: ${JSON.stringify(words)}`);
    }
    const ends = /const SIGNIN_ENDS = Object\.freeze\(\[([^\]]*)\]\)/.exec(app)![1]!;
    expect(JSON.parse(`[${ends}]`)).toEqual(SIGNIN_STATES.filter((state) =>
      !["starting", "open", "code_sent"].includes(state)));
  });

  it("allow this origin's own scripts, style and socket, and nothing inline or framed", () => {
    expect(CSP.split("; ")).toEqual([
      "default-src 'none'", "script-src 'self'", "style-src 'self'", "img-src 'self'",
      "connect-src 'self' wss://tinyassets.io", "base-uri 'none'", "form-action 'none'",
      "frame-ancestors 'none'", "require-trusted-types-for 'script'", "trusted-types 'none'",
    ]);
  });

  it("serve only the named pages and files, by GET or HEAD", async () => {
    for (const path of ["/app/index.html", "/app/static/missing.js", "/app/static/",
                        "/app/static/../index.html", "/app/static/app.js/x", "/app/other",
                        "/index.html", "/app.js", "/app/static/index.html"]) {
      const response = await get(path);
      expect(response.status, path).toBe(404);
    }
    const posted = await get("/app", { method: "POST", body: "{}" });
    expect(posted.status).toBe(405);
    expect(posted.headers.get("Allow")).toBe("GET, HEAD");
    // The owner surface stays the owner surface: no page stands in for a route.
    expect((await get("/app/api/account", { method: "POST" })).status).toBe(401);
  });

  it("run each return page's own classic script first, and no inline script anywhere",
     async () => {
    for (const [path, own] of [["/app/signed-in", "signed-in.js"],
                               ["/app/connected", "connected.js"]] as const) {
      const html = await (await get(path)).text();
      const tags = scripts(html);
      expect(tags, path).toEqual([`<script src="/app/static/${own}">`]);
      // In the head, before anything else that could load or run.
      expect(html.indexOf(tags[0]!)).toBeLessThan(html.indexOf("<meta"));
    }
    const shell = await (await get("/app")).text();
    expect(scripts(shell)).toEqual(['<script type="module" src="/app/static/app.js">']);
    for (const path of PAGES) {
      const html = await (await get(path)).text();
      expect(html, path).not.toMatch(/<script\b[^>]*>[^<\s]/);
      expect(html, path).not.toMatch(/\son[a-z]+=/i);
      expect(html, path).not.toMatch(/style=/);
    }
  });
});
