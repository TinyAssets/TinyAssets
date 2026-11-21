/**
 * The handles canary's new side (`scripts/canary_handles.json`, read by the platform's
 * canary, `deploy/canary.ts`): the doors' handle set, held to the doors' real tool
 * table so it cannot drift; and the authorization servers the canary tells the sides apart
 * by: the doors' issuer, which is their origin wherever the identity puts their host, and
 * the old stack's, another.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { REPO } from "../../ci/paths";
import { ORIGIN } from "../../contract/src/identity";
import { ISSUER } from "../src/config";
import { DEFAULT_PROFILE, profileNames, toolNames } from "../../contract/src/doors";

const record = JSON.parse(readFileSync(join(REPO, "scripts", "canary_handles.json"), "utf8")) as
  { old: { authorization_server: string; handles: string[] }; new: { handles: string[] } };
const sorted = (names: readonly string[]) => [...names].sort();

describe("the handles canary's new side", () => {
  it("is the tools a custom connector is served, which is the doors' whole table", () => {
    expect(sorted(record.new.handles)).toEqual(sorted(profileNames(DEFAULT_PROFILE)));
    expect(sorted(record.new.handles)).toEqual(sorted(toolNames()));
    expect(new Set(record.new.handles).size).toBe(record.new.handles.length);
  });

  it("tells the doors by their own issuer, their origin, and the old side by another", () => {
    // The canary reads the doors' resource document as theirs when it names their origin.
    expect(ISSUER).toBe(ORIGIN);
    expect(record.old.authorization_server).not.toBe(ISSUER);
  });

  it("checks absent handles the doors never serve: the old side's own", () => {
    // Each old-only handle is one the canary expects the doors to answer 404 for.
    const oldOnly = record.old.handles.filter((name) => !record.new.handles.includes(name));
    expect(oldOnly.length).toBeGreaterThan(0);
    expect(oldOnly.filter((name) => toolNames().includes(name))).toEqual([]);
  });
});
