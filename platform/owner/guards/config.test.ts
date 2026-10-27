/**
 * The owner Worker's config: `OwnerDO` is a SQLite-backed class, and nothing binds the
 * Worker to its own namespace, so its code cannot reach another owner's object
 * (ADR-032). Edge Workers bind it from their own configs.
 */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import ts from "typescript";
import { describe, expect, it } from "vitest";

type Binding = { name?: string; class_name?: string; script_name?: string };
type Config = {
  name?: string | undefined;
  durable_objects?: { bindings?: Binding[] };
  migrations?: { new_sqlite_classes?: string[]; new_classes?: string[] }[];
  env?: Record<string, Config>;
};

function parse(text: string): Config {
  const parsed = ts.parseConfigFileTextToJson("wrangler.jsonc", text);
  if (parsed.error) {
    throw new Error(ts.flattenDiagnosticMessageText(parsed.error.messageText, "\n"));
  }
  return parsed.config as Config;
}

/** Each binding in `config` (and its environments) that reaches its own OwnerDO class. */
export function selfBindings(config: Config): string[] {
  const own = (config.durable_objects?.bindings ?? []).filter((binding) =>
    binding.class_name === "OwnerDO"
    && (binding.script_name === undefined || binding.script_name === config.name));
  return [
    ...own.map((binding) => binding.name ?? "?"),
    ...Object.entries(config.env ?? {}).flatMap(([env, inner]) =>
      selfBindings({ name: config.name, ...inner }).map((name) => `${env}.${name}`)),
  ];
}

const WRANGLER = join(dirname(fileURLToPath(import.meta.url)), "..", "wrangler.jsonc");
const config = parse(readFileSync(WRANGLER, "utf8"));

describe("owner Worker config", () => {
  it("never binds OwnerDO to its own namespace", () => {
    expect(selfBindings(config)).toEqual([]);
  });

  it("declares OwnerDO a SQLite-backed class", () => {
    const sqlite = (config.migrations ?? []).flatMap((m) => m.new_sqlite_classes ?? []);
    const kv = (config.migrations ?? []).flatMap((m) => m.new_classes ?? []);
    expect(sqlite).toContain("OwnerDO");
    expect(kv).not.toContain("OwnerDO");
  });

  it("sees a self-binding, in an environment too", () => {
    const bound = { name: "w", durable_objects: { bindings: [{ name: "OWNER", class_name: "OwnerDO" }] } };
    expect(selfBindings(bound)).toEqual(["OWNER"]);
    expect(selfBindings({ name: "w", env: { staging: bound } })).toEqual(["staging.OWNER"]);
    expect(selfBindings({ name: "w", durable_objects: { bindings: [
      { name: "OWNER", class_name: "OwnerDO", script_name: "w" }] } })).toEqual(["OWNER"]);
    expect(selfBindings({ name: "edge", durable_objects: { bindings: [
      { name: "OWNER", class_name: "OwnerDO", script_name: "tinyassets-owner" }] } }))
      .toEqual([]);
  });
});
