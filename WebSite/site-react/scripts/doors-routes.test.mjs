// The platform's doors take their routes on this zone (`platform/contract/identity.json`
// `routes`) at the cutover, and a route takes every URL it begins, query and all. A site file
// under one would be served by the doors, not the site, so the site serves none: its public
// files and its pages (`app/`, exported with a trailing slash) are each held to every route.
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { dirname, relative, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const siteRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const identity = JSON.parse(
  readFileSync(resolve(siteRoot, "../../platform/contract/identity.json"), "utf8"),
);

// Cloudflare's matching, as the contract's `routeTakes` (`platform/contract/src/identity.ts`)
// holds it: a pattern ending in `*` takes every URL it begins, any other only itself. Node 20
// runs this file, which cannot load the contract's TypeScript.
function takes(pattern, url) {
  return pattern.endsWith("*") ? url.startsWith(pattern.slice(0, -1)) : url === pattern;
}

function filesBelow(directory) {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = resolve(directory, entry.name);
    return entry.isDirectory() ? filesBelow(path) : [path];
  });
}

/** The URL of each public file, and of each page, by its folder under `app/`. */
function siteUrls() {
  const files = filesBelow(resolve(siteRoot, "public")).map((path) =>
    `/${relative(resolve(siteRoot, "public"), path).replaceAll("\\", "/")}`);
  const pages = filesBelow(resolve(siteRoot, "app"))
    .map((path) => relative(resolve(siteRoot, "app"), path).replaceAll("\\", "/"))
    .filter((path) => /(^|\/)page\.tsx$/.test(path) && !/(^|\/)_/.test(path))
    .map((path) => `/${path.replace(/page\.tsx$/, "")}`);
  return [...files, ...pages];
}

test("the site serves no URL under a route the doors take", () => {
  const urls = siteUrls();
  assert.ok(urls.includes("/icons/apple-touch-icon.png") && urls.includes("/connect/"));
  assert.ok(identity.routes.length > 0);
  for (const url of urls) {
    const taken = identity.routes.filter((pattern) => takes(pattern, url));
    assert.deepEqual([url, taken], [url, []]);
  }
});
