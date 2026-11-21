/**
 * The owner app's files (`app/`) are exactly what `src/pages.ts` serves, name no other
 * origin, and use no HTML string sink or dynamic evaluation: the pages' CSP refuses those
 * anyway, and this holds the source to it. Each page names its touch icon: the website's one
 * file, which the site serves on this origin (`identity.test.ts` holds every website file to no
 * doors route taking it).
 */
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { REPO } from "../../ci/paths";
import { APP_PATH, CONNECTED_PATH, SIGNED_IN_PATH } from "../src/config";
import type { DoorsEnv } from "../src/env";
import { servePage } from "../src/pages";

const DOORS = join(dirname(fileURLToPath(import.meta.url)), "..");
const APP = join(DOORS, "app");

/** The status the doors answer `path` with, each file they read from `app/` put in `read`. */
async function served(path: string, read: string[]): Promise<number> {
  const env = { ASSETS: { fetch: async (request: Request) => {
    const file = new URL(request.url).pathname.slice(1);
    read.push(file);
    return existsSync(join(APP, file)) ? new Response(readFileSync(join(APP, file)))
      : new Response(null, { status: 404 });
  } } } as unknown as DoorsEnv;
  return (await servePage(new Request(`https://doors.test${path}`), env)).status;
}

describe("the owner app's files", () => {
  it("are exactly the pages, scripts and style the doors serve", async () => {
    const files = readdirSync(APP);
    const read: string[] = [];
    for (const page of [APP_PATH, SIGNED_IN_PATH, CONNECTED_PATH]) {
      expect(await served(page, read), page).toBe(200);
    }
    for (const file of files.filter((name) => !name.endsWith(".html"))) {
      expect(await served(`${APP_PATH}/static/${file}`, read), file).toBe(200);
    }
    expect(read.sort()).toEqual(files.sort());
  });

  it("name no other origin, parse no HTML string and evaluate no code", () => {
    for (const file of readdirSync(APP)) {
      const text = readFileSync(join(APP, file), "utf8");
      // The app's own origin is `location.origin`: no file names a host or a
      // protocol-relative URL.
      expect(text, file).not.toMatch(/https?:\/\/|["'`(]\/\/[a-z]/i);
      expect(text, file).not.toMatch(
        /innerHTML|outerHTML|insertAdjacentHTML|document\.write|\beval\(|new Function|setTimeout\(["'`]/);
    }
  });

  it("are pages that each name a touch icon the website holds", () => {
    const pages = readdirSync(APP).filter((file) => file.endsWith(".html"));
    expect(pages.length).toBeGreaterThan(0);
    for (const page of pages) {
      const html = readFileSync(join(APP, page), "utf8");
      const href = /<link rel="apple-touch-icon" href="(\/[^"?#]+)">/.exec(html)?.[1];
      expect([page, href]).toEqual([page, expect.any(String)]);
      expect(existsSync(join(REPO, "WebSite", "site-react", "public", href!)), href).toBe(true);
    }
  });
});
