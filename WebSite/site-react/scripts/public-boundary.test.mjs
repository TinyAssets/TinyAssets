// public-boundary.test.mjs — the authenticated-read boundary the site must keep.
//
// A public browser makes no MCP request. It never downloads operator status,
// asks for goals or runs, defaults a missing visibility to public, or labels a
// checked-in snapshot as a live read.

import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { discoverableRows } from "../lib/discoverable.js";

const here = dirname(fileURLToPath(import.meta.url));
const siteRoot = resolve(here, "..");

function source(path) {
  return readFileSync(resolve(siteRoot, path), "utf8");
}

function readTree(directory) {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    if ([".next", "node_modules", "out", "scripts"].includes(entry.name)) return [];
    const path = resolve(directory, entry.name);
    if (entry.isDirectory()) return readTree(path);
    if (!/\.(?:[cm]?[jt]sx?)$/.test(entry.name)) return [];
    return [{ path, body: readFileSync(path, "utf8") }];
  });
}

const live = source("lib/live.ts");
const shapes = source("components/PublicShapes.tsx");
const reach = source("components/Reachability.tsx");
const pages = readTree(siteRoot);

test("the live client exposes only two fail-closed browser readers", () => {
  const exported = [...live.matchAll(/^export (?:async )?function (\w+)/gm)].map((m) => m[1]);
  assert.deepEqual(exported.sort(), ["fetchPublicUniverses", "fetchVitals"]);
  assert.match(live, /PUBLIC_READ_NEEDS_SIGN_IN/);
  assert.match(live, /authRequired:\s*true/);
  assert.doesNotMatch(live, /\bfetch\s*\(|publicGraphCall\(|requirePublicUniverseCollection\(/);
});

test("the live client never requests private-capable targets or operator status", () => {
  assert.doesNotMatch(live, /publicGraphCall\(\s*["'](?:run|runs|goal|goals)["']/);
  assert.doesNotMatch(live, /\bfetchPublicGoals?\b|\bfetchPublicRuns?\b|\bfetchLive\b/);
  assert.doesNotMatch(live, /callTool\(\s*["']get_status["']/);
  assert.doesNotMatch(live, /\brequireObjectResult\b/);
  assert.doesNotMatch(live, /read_graph runs?\b/i);
});

test("vitals report authorization without deriving activity", () => {
  assert.doesNotMatch(live, /lastSignalSource\?:\s*["']run["']|lastSignalSource\s*=\s*["']run["']/);
  assert.doesNotMatch(live, /\bactiveRun\b/);
  assert.match(live, /activityVisible:\s*false/);
  assert.doesNotMatch(live, /last_activity_at/);
});

test("the public browser opens no MCP session", () => {
  assert.doesNotMatch(live, /\bfetch\s*\(|\binitialize\b|notifications\/initialized|tools\/call/);
});

test("the checked-in snapshot fails closed on visibility, like a live read", () => {
  // Drive the REAL function both render paths call, with fixtures, rather than
  // regexing the component: a rule that is only asserted against source text
  // survives a refactor that removes the rule.
  const good = {
    id: "u-real",
    visibility: "public",
    phase: "idle",
    word_count: 3,
    last_activity_at: "2026-09-01T00:00:00Z",
  };
  assert.deepEqual(discoverableRows([good]), [
    { id: "u-real", phase: "idle", word_count: 3, last_activity_at: "2026-09-01T00:00:00Z" },
  ]);

  for (const bad of [
    { ...good, visibility: undefined },
    { ...good, visibility: "private" },
    { ...good, visibility: "" },
    { ...good, visibility: "PUBLIC" },
    { ...good, id: "" },
    { visibility: "public" },
    null,
    "u-real",
  ]) {
    assert.deepEqual(
      discoverableRows([bad]),
      [],
      `a record with ${JSON.stringify(bad)} must not render as public`,
    );
  }

  // One bad record is dropped; it does not blank the whole list.
  assert.equal(discoverableRows([good, { ...good, id: "u-2", visibility: "private" }]).length, 1);
  assert.deepEqual(discoverableRows("not an array"), []);

  // The checked-in render path goes through it. Live reads belong to a
  // signed-in connector, not this public browser component.
  assert.match(shapes, /const bakedRows: Row\[\] = discoverableRows\(baked\.command_centers\)/);

  // And the checked-in snapshot itself carries only discoverable records.
  const snapshot = JSON.parse(readFileSync(resolve(siteRoot, "lib/mcp-snapshot.json"), "utf8"));
  assert.ok(Array.isArray(snapshot.command_centers));
  assert.equal(discoverableRows(snapshot.command_centers).length, snapshot.command_centers.length);
});

test("public pages never surface untrusted error detail", () => {
  for (const file of [{ path: "lib/live.ts", body: live }, ...pages]) {
    assert.doesNotMatch(file.body, /json\.error\.message|res\.statusText/, file.path);
    assert.doesNotMatch(file.body, /error:\s*error(?:\?\.|\.)(?:message|stack)/, file.path);
  }
});

test("the public list labels its snapshot and sign-in boundary", () => {
  assert.doesNotMatch(shapes, /\bfetchPublicUniverses\b/);
  assert.match(shapes, /mcp-snapshot\.json/);
  assert.match(shapes, /checked-in snapshot from/);
  assert.match(shapes, /PUBLIC_READ_NEEDS_SIGN_IN/);
  assert.match(shapes, /No public command centers/);
  assert.doesNotMatch(shapes, /visibility\s*\?\?\s*["']public["']/);
  assert.doesNotMatch(shapes, /visibility\s*!==\s*["']private["']/);
  assert.doesNotMatch(shapes, /Refresh MCP|live read from/);
});

test("the reachability strip reports the sign-in boundary", () => {
  assert.match(reach, /\bfetchVitals\b/);
  assert.match(reach, /sign-in required/);
  assert.match(reach, /connector supplies a bearer/);
  assert.doesNotMatch(reach, /get_status/);
  assert.doesNotMatch(reach, /\bactive run\b|\ba run is moving\b/i);
});

test("public pages do not offer an unauthenticated MCP refresh", () => {
  const labels = [...pages.flatMap((f) => [...f.body.matchAll(/["'`]Refresh [A-Za-z]+["'`]/g)].map((m) => m[0]))];
  for (const label of labels) {
    assert.notEqual(label, '"Refresh MCP"');
    assert.notEqual(label, "'Refresh MCP'");
    assert.notEqual(label, "`Refresh MCP`");
  }
});

test("no page imports a reader the client does not export", () => {
  for (const file of pages) {
    assert.doesNotMatch(file.body, /\bfetchLive\b|\bfetchPublicGoals?\b|\bliveToSnapshotShape\b/, file.path);
  }
});

test("plans are stated in exactly one place, and never overstate enforcement", () => {
  // The gate that would refuse an action is dark (usage_policy.enforcement_enabled
  // defaults off), so no page may describe hitting a limit as a live consequence,
  // and the price/benefit must not be restated anywhere it can drift.
  const finePrint = source("app/fine-print/page.tsx");
  assert.match(finePrint, /\$20 a month/);
  assert.match(finePrint, /usage is metered/);
  assert.match(finePrint, /is not switched on/);
  // the three dimensions must be named as the policy implements them
  assert.match(finePrint, /daily allowance/i);
  assert.match(finePrint, /guard against a runaway loop/i);
  assert.match(finePrint, /capacity cap/i);
  assert.doesNotMatch(finePrint, /\b5,?000\b|\b12,?000\b|\b20 GB\b/, "no allowance numbers while the gate is dark");

  const others = pages.filter((f) => !f.path.replace(/\\/g, "/").includes("app/fine-print/"));
  for (const file of others) {
    assert.doesNotMatch(file.body, /\$20\b/, `${file.path} must not restate the price`);
  }

  // Public text assets may point at the section but must not restate it.
  for (const asset of ["public/llms.txt", "public/robots.txt"]) {
    const body = source(asset);
    assert.doesNotMatch(body, /\$?\bUSD 20\b|\$20\b/, `${asset} must not restate the price`);
    assert.doesNotMatch(body, /raises the daily/i, `${asset} must not restate the premium benefit`);
  }
  assert.match(source("public/llms.txt"), /fine-print\/#plans/);
});

test("robots.txt keeps every named crawler in the group that carries the exclusions", () => {
  // RFC 9309: a crawler obeys its most specific matching group and does NOT
  // fall back to "*". A named agent in its own group silently loses these.
  const robots = source("public/robots.txt");
  const groups = robots
    .split(/\n\s*\n/)
    .filter((block) => /^\s*User-agent:/im.test(block));
  assert.equal(groups.length, 1, "one group, so no agent can miss the exclusions");
  const [group] = groups;
  for (const agent of ["*", "Googlebot", "GPTBot", "ClaudeBot", "PerplexityBot", "OAI-SearchBot"]) {
    assert.ok(
      group.includes(`User-agent: ${agent}`),
      `${agent} must sit in the group carrying the exclusions`,
    );
  }
  for (const path of ["/account", "/auth/", "/editor/", "/admin/"]) {
    assert.ok(group.includes(`Disallow: ${path}`), `${path} stays out of every index`);
  }
  assert.match(robots, /Sitemap: https:\/\/tinyassets\.io\/sitemap\.xml/);
});

test("the inline mark is generated from the one geometry source", () => {
  const mark = source("components/TinyAssetsMark.tsx");
  assert.match(mark, /GENERATED by WebSite\/brand\/render_marks\.py/);
  assert.match(mark, /Do not hand-edit/);
  // Its numbers must equal the Python constants, so a drifted checkout is red.
  const iconGen = readFileSync(
    resolve(siteRoot, "../../tinyassets/desktop/icon_gen.py"),
    "utf8",
  );
  const scalar = (name) => {
    const m = iconGen.match(new RegExp(`^${name}\\s*=\\s*([0-9.]+)`, "m"));
    assert.ok(m, `icon_gen.py defines ${name}`);
    return Number(m[1]);
  };

  // The badge's own numbers must be the Python ones, so a drifted checkout is red.
  const svgNumbers = new Set(
    [...mark.matchAll(/(?:cx|cy|r|rx|x|y|width|height)="([\d.]+)"/g)].map((m) => Number(m[1])),
  );
  for (const [label, value] of [
    ["viewBox", scalar("VIEWBOX")],
    ["disc radius", scalar("DISC_R")],
    ["rim radius", scalar("RIM_R")],
    ["tile radius", scalar("TILE_RADIUS")],
  ]) {
    assert.ok(svgNumbers.has(value), `${label} (${value}) must come from icon_gen.py`);
  }

  // The compact scene itself. `render_marks.py` writes BOTH this component and
  // WebSite/brand/mark-{compact,tile}.svg from the same optical layer list, so
  // the two must agree shape for shape. Comparing them catches every hand-edit
  // change -- a redrawn mountain, a moved circle, a dropped layer, a reordered
  // one -- which the earlier version of this test, comparing 40-character path
  // prefixes, did not: the broken mark passed all 235 tests.
  const normalise = (svg) =>
    svg
      .replace(/^[\s\S]*?<svg[^>]*>/, "")
      .replace(/<\/svg>[\s\S]*$/, "")
      .replace(/<defs>[\s\S]*?<\/defs>/, "")
      .replace(/\s*(clip-path|clipPath)=(?:"[^"]*"|\{`url\(#\$\{clipId\}\)`\})/g, "")
      .replace(/\s*\/>/g, "/>")
      .replace(/\s+/g, " ")
      .trim();

  const componentBodies = [...mark.matchAll(/<>([\s\S]*?)<\/>/g)].map((m) => m[1]);
  assert.equal(componentBodies.length, 2, "the component renders a tile body and a disc body");
  const [tileBody, discBody] = componentBodies;

  for (const [label, body, file] of [
    ["disc", discBody, "../brand/mark-compact.svg"],
    ["tile", tileBody, "../brand/mark-tile.svg"],
  ]) {
    const exported = normalise(readFileSync(resolve(siteRoot, file), "utf8"));
    const inComponent = normalise(body);
    // JSX camel-cases these; the exported SVG keeps them kebab-cased.
    const asSvg = inComponent
      .replace(/strokeWidth=/g, "stroke-width=")
      .replace(/strokeLinecap=/g, "stroke-linecap=");
    assert.equal(
      asSvg,
      exported,
      `the component's ${label} body must be byte-identical to ${file}; ` +
        `re-run WebSite/brand/render_marks.py rather than editing either by hand`,
    );
  }

  // And the mountain in particular is present, by its distinctive summit plateau.
  assert.match(iconGen, /_SNOW = \(/);
  assert.match(iconGen, /summit plateau|SUMMIT PLATEAU/i);

  // No element may carry BOTH a transform and a clip-path. SVG resolves
  // clip-path in the element's own user space, so the two together drag the
  // badge outline along with the shape: it cut the galaxy out of the sky and
  // clipped the wolf, while the Pillow renderer drew them correctly. The clip
  // belongs on an outer group. This is the one way the two renderers of the
  // same layer list can silently disagree, so it is pinned here.
  for (const [element] of mark.matchAll(/<(?:circle|path|rect|g)\b[^>]*>/g)) {
    const hasTransform = /\btransform=/.test(element);
    const hasClip = /\bclipPath=|\bclip-path=/.test(element);
    assert.ok(
      !(hasTransform && hasClip),
      `an element carries both a transform and a clip, which SVG applies in the ` +
        `wrong order: ${element.slice(0, 120)}`,
    );
  }
});

test("no public copy claims goals or runs are read live, or that the platform runs a model", () => {
  const prose = pages.map((f) => f.body).join("\n").replace(/\s+/g, " ");
  assert.doesNotMatch(prose, /\b(?:goal|goals)\b.{0,48}\bread live\b|\bread live\b.{0,48}\b(?:goal|goals)\b/i);
  assert.doesNotMatch(prose, /\ba run is moving\b|\bactiveRun\b/i);
  assert.doesNotMatch(prose, /powered by (?:claude|gpt|openai|anthropic)/i);
  assert.doesNotMatch(prose, /AI-powered workflow engine/i);
});
