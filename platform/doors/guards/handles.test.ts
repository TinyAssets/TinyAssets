/**
 * The handles canary's new side (`scripts/canary_handles.json`, read by
 * `scripts/cutover_handles_canary.py`): the doors' handle set, held to the doors' real tool
 * table so it cannot drift, and the authorization server the canary tells the sides apart
 * by, held to the doors' own issuer.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { REPO } from "../../ci/paths";
import { ISSUER } from "../src/config";
import { DEFAULT_PROFILE, profileNames, toolNames } from "../src/tools";

interface Side {
  readonly authorization_server: string;
  readonly handles: readonly string[];
}
const record = JSON.parse(readFileSync(join(REPO, "scripts", "canary_handles.json"), "utf8")) as
  { old: Side; new: Side };
const sorted = (names: readonly string[]) => [...names].sort();

describe("the handles canary's new side", () => {
  it("is the tools a custom connector is served, which is the doors' whole table", () => {
    expect(sorted(record.new.handles)).toEqual(sorted(profileNames(DEFAULT_PROFILE)));
    expect(sorted(record.new.handles)).toEqual(sorted(toolNames()));
    expect(new Set(record.new.handles).size).toBe(record.new.handles.length);
  });

  it("names the doors' own issuer, and the old side another", () => {
    expect(record.new.authorization_server).toBe(ISSUER);
    expect(record.old.authorization_server).not.toBe(ISSUER);
  });

  it("checks absent handles the doors never serve: the old side's own", () => {
    // Each old-only handle is one the canary expects the doors to answer 404 for.
    const oldOnly = record.old.handles.filter((name) => !record.new.handles.includes(name));
    expect(oldOnly.length).toBeGreaterThan(0);
    expect(oldOnly.filter((name) => toolNames().includes(name))).toEqual([]);
  });
});
